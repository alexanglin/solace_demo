"""Broker deduplication and bounded ordered dashboard-event reads."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast

import pytest
from aerial_rescue_store import dashboard_events
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard_events import (
    MAXIMUM_EVENT_PAGE_SIZE,
    BrokerEvent,
    BrokerEventOutcome,
    DashboardEventError,
    DashboardEventRefusal,
    SnapshotBasis,
    StoredDashboardEvent,
    append_broker_event,
    broker_event_statement,
    capture_snapshot_basis,
    ensure_source_statement,
    event_page_statement,
    known_broker_event_statement,
    locked_source_statement,
    read_event_page,
    read_suffix_page,
    source_advance_statement,
    watermark_statement,
)
from aerial_rescue_store.dashboard_runs import DashboardRun, RunMode
from aerial_rescue_store.migration import (
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    DASHBOARD_BROKER_EVENT_TABLE,
    DASHBOARD_BROKER_SOURCE_TABLE,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
SOURCE: Final = "urn:aerial-rescue:mission-lifecycle:run-store-0001"
EVENT_ID: Final = "event-store-0001"
MISSION: Final = "mission-store-0001"
RUN: Final = "run-store-0001"
SEQUENCE: Final = 7
ORDINAL: Final = 11
DIGEST: Final = "ab" * 32
OTHER_DIGEST: Final = "cd" * 32
PREPARED: Final = b'{"canonicalizationVersion":1,"stateVersion":1}'
PAYLOAD: Final = (
    b'{"data":{"lifecycle":"SEARCHING"},"eventClass":"MISSION",'
    b'"kind":"missionLifecycle","mission":"mission-store-0001",'
    b'"time":"2026-08-25T12:00:00.000Z"}'
)

BROKER_EVENT: Final = BrokerEvent(
    source=SOURCE,
    event_id=EVENT_ID,
    source_sequence=SEQUENCE,
    payload_digest=DIGEST,
)
AUDIT_EVENT: Final = AuditRecord(
    mission_id=MISSION,
    kind="missionLifecycle",
    occurred_at="2026-08-25T12:00:00.000Z",
    payload=PAYLOAD,
    correlation_id="correlation-store-0001",
    causation_id=None,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01",
)
LIVE_RUN: Final = DashboardRun(
    run_identity=RUN,
    mode=RunMode.DEGRADED_LIVE,
    scenario_id="wilderness-search",
    scenario_revision=1,
    mission_id=MISSION,
    run_id=RUN,
    session_id=None,
    prepared_initial_state=PREPARED,
)
REPLAY_RUN: Final = DashboardRun(
    run_identity="session-store-0001",
    mode=RunMode.REPLAY,
    scenario_id="wilderness-search",
    scenario_revision=1,
    mission_id=None,
    run_id=None,
    session_id="session-store-0001",
    prepared_initial_state=PREPARED,
)


def _rendered(statement: ClauseElement) -> str:
    """Return SQL for the package's pinned PostgreSQL dialect."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return values the statement binds instead of interpolating."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _run_row(run: DashboardRun) -> tuple[object, ...]:
    """Return a current-run join row in its selected order."""
    return (
        run.run_identity,
        run.mode.value,
        run.scenario_id,
        run.scenario_revision,
        run.mission_id,
        run.run_id,
        run.session_id,
        run.prepared_initial_state,
    )


@dataclass
class _Rows:
    """One fake SQLAlchemy result supporting single-row and page reads."""

    one: Sequence[object] | None = None
    many: Sequence[Sequence[object]] = ()

    def one_or_none(self) -> Sequence[object] | None:
        """Return the configured single row."""
        return self.one

    def all(self) -> Sequence[Sequence[object]]:
        """Return the configured page rows."""
        return self.many


@dataclass
class _RecordingSession:
    """A caller-owned transaction fake with ordered canned results."""

    scalar_answers: list[object] = field(default_factory=list)
    row_answers: list[_Rows] = field(default_factory=list)
    scalars: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    async def scalar(self, statement: ClauseElement, /) -> object:
        """Record and answer a scalar statement."""
        self.scalars.append(_rendered(statement))
        return self.scalar_answers.pop(0) if self.scalar_answers else None

    async def execute(self, statement: ClauseElement, /) -> _Rows:
        """Record and answer an effect, single-row read, or page read."""
        self.executed.append(_rendered(statement))
        return self.row_answers.pop(0) if self.row_answers else _Rows()


class BrokerStatementTests(unittest.TestCase):
    def test_source_creation_is_conflict_safe_and_does_not_advance_high_water(self) -> None:
        # Arrange
        source = SOURCE

        # Act
        statement = ensure_source_statement(source)
        rendered = _rendered(statement)
        bound = _parameters(statement)

        # Assert
        self.assertEqual(
            (True, source, None),
            (
                "ON CONFLICT (source) DO NOTHING" in rendered,
                bound["source"],
                bound.get("high_water_sequence"),
            ),
        )

    def test_source_high_water_is_read_under_its_own_row_lock(self) -> None:
        # Arrange
        source = SOURCE

        # Act
        rendered = _rendered(locked_source_statement(source))

        # Assert
        self.assertTrue(rendered.endswith("FOR UPDATE"))

    def test_source_advance_is_guarded_by_the_high_water_the_domain_observed(self) -> None:
        # Arrange
        expected = SEQUENCE - 1

        # Act
        statement = source_advance_statement(SOURCE, expected, SEQUENCE)
        rendered = _rendered(statement)
        bound = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, (SEQUENCE, SOURCE, expected)),
            (f"{DASHBOARD_BROKER_SOURCE_TABLE}.high_water_sequence =" in rendered, bound),
        )

    def test_broker_event_identity_carries_digest_sequence_and_exact_audit_link(self) -> None:
        # Arrange
        event = BROKER_EVENT

        # Act
        bound = _parameters(broker_event_statement(event, MISSION, ORDINAL))

        # Assert
        self.assertEqual(
            (SEQUENCE, DIGEST, MISSION, ORDINAL),
            (
                bound["source_sequence"],
                bound["payload_digest"],
                bound["audit_mission_id"],
                bound["audit_ordinal"],
            ),
        )

    def test_known_identity_reads_the_digest_and_audit_link_together(self) -> None:
        # Arrange
        event = BROKER_EVENT

        # Act
        rendered = _rendered(known_broker_event_statement(event.source, event.event_id))

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                f"{DASHBOARD_BROKER_EVENT_TABLE}.payload_digest" in rendered,
                f"{DASHBOARD_BROKER_EVENT_TABLE}.audit_mission_id" in rendered,
                f"{DASHBOARD_BROKER_EVENT_TABLE}.audit_ordinal" in rendered,
            ),
        )


class BrokerAppendTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_advancing_identity_updates_source_then_appends_and_links_one_audit_row(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answers=[SOURCE, ORDINAL, EVENT_ID],
            row_answers=[_Rows(), _Rows(one=(SEQUENCE - 1,)), _Rows(), _Rows()],
        )

        # Act
        receipt = await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        combined = session.executed + session.scalars
        self.assertEqual(
            (BrokerEventOutcome.ACCEPTED, MISSION, ORDINAL, True, True),
            (
                receipt.outcome,
                receipt.audit_mission_id,
                receipt.audit_ordinal,
                any(AUDIT_SEQUENCE_TABLE in statement for statement in combined),
                any(
                    f"INSERT INTO {DASHBOARD_BROKER_EVENT_TABLE}" in statement
                    for statement in combined
                ),
            ),
        )

    async def test_an_exact_known_identity_returns_its_existing_audit_link_without_an_append(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(
            row_answers=[_Rows(), _Rows(one=(SEQUENCE,)), _Rows(one=(DIGEST, MISSION, ORDINAL))]
        )

        # Act
        receipt = await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            (BrokerEventOutcome.DUPLICATE, MISSION, ORDINAL, []),
            (receipt.outcome, receipt.audit_mission_id, receipt.audit_ordinal, session.scalars),
        )

    async def test_a_known_identity_with_divergent_payload_is_a_permanent_refusal(self) -> None:
        # Arrange
        session = _RecordingSession(
            row_answers=[
                _Rows(),
                _Rows(one=(SEQUENCE,)),
                _Rows(one=(OTHER_DIGEST, MISSION, ORDINAL)),
            ]
        )

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.DIVERGENT_DUPLICATE, []),
            (refused.value.refusal, session.scalars),
        )

    async def test_a_new_identity_reusing_the_high_water_sequence_is_refused_before_audit(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(row_answers=[_Rows(), _Rows(one=(SEQUENCE,)), _Rows()])

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.SEQUENCE_REUSED, []),
            (refused.value.refusal, session.scalars),
        )

    async def test_a_stale_source_sequence_is_refused_before_audit(self) -> None:
        # Arrange
        session = _RecordingSession(row_answers=[_Rows(), _Rows(one=(SEQUENCE + 1,)), _Rows()])

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.STALE_SEQUENCE, []),
            (refused.value.refusal, session.scalars),
        )

    async def test_a_source_that_moved_after_its_domain_decision_is_refused_before_audit(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answers=[None],
            row_answers=[_Rows(), _Rows(one=(SEQUENCE - 1,)), _Rows()],
        )

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            DashboardEventRefusal.SOURCE_MOVED,
            refused.value.refusal,
        )

    async def test_a_first_source_event_advances_from_an_empty_high_water(self) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answers=[SOURCE, ORDINAL, EVENT_ID],
            row_answers=[_Rows(), _Rows(one=(None,)), _Rows(), _Rows()],
        )

        # Act
        receipt = await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            (BrokerEventOutcome.ACCEPTED, ORDINAL), (receipt.outcome, receipt.audit_ordinal)
        )

    async def test_a_source_that_cannot_be_locked_is_refused_before_identity_lookup(self) -> None:
        # Arrange
        session = _RecordingSession(row_answers=[_Rows(), _Rows()])

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.SOURCE_VANISHED, 2),
            (refused.value.refusal, len(session.executed)),
        )

    async def test_an_audit_row_that_cannot_be_linked_is_refused_for_transaction_rollback(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answers=[SOURCE, ORDINAL, None],
            row_answers=[_Rows(), _Rows(one=(SEQUENCE - 1,)), _Rows(), _Rows()],
        )

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)

        # Assert
        self.assertEqual(DashboardEventRefusal.EVENT_WRITE_REJECTED, refused.value.refusal)

    async def test_malformed_source_and_known_identity_rows_are_refused_without_coercion(
        self,
    ) -> None:
        # Arrange
        sessions = (
            _RecordingSession(row_answers=[_Rows(), _Rows(one=(True,))]),
            _RecordingSession(row_answers=[_Rows(), _Rows(one=(1, 2))]),
            _RecordingSession(row_answers=[_Rows(), _Rows(one=(SEQUENCE,)), _Rows(one=(DIGEST,))]),
            _RecordingSession(
                row_answers=[_Rows(), _Rows(one=(SEQUENCE,)), _Rows(one=(7, MISSION, ORDINAL))]
            ),
            _RecordingSession(
                row_answers=[_Rows(), _Rows(one=(SEQUENCE,)), _Rows(one=(DIGEST, 7, ORDINAL))]
            ),
            _RecordingSession(
                row_answers=[_Rows(), _Rows(one=(SEQUENCE,)), _Rows(one=(DIGEST, MISSION, 0))]
            ),
        )
        refusals: list[DashboardEventRefusal] = []

        # Act
        for session in sessions:
            try:
                await append_broker_event(session, BROKER_EVENT, AUDIT_EVENT)
            except DashboardEventError as refused:
                refusals.append(cast("DashboardEventRefusal", refused.refusal))

        # Assert
        self.assertEqual(
            (
                DashboardEventRefusal.UNREADABLE_SOURCE,
                DashboardEventRefusal.UNREADABLE_SOURCE,
                DashboardEventRefusal.UNREADABLE_EVENT,
                DashboardEventRefusal.UNREADABLE_EVENT,
                DashboardEventRefusal.UNREADABLE_EVENT,
                DashboardEventRefusal.UNREADABLE_EVENT,
            ),
            tuple(refusals),
        )


class OrderedReadStatementTests(unittest.TestCase):
    def test_store_exposes_only_the_bounded_ordered_event_read_surface(self) -> None:
        # Arrange
        specialized_timeline_names = (
            "MAXIMUM_TIMELINE_PAGE_SIZE",
            "TimelinePageRequest",
            "timeline_page_statement",
            "read_timeline_page",
        )

        # Act
        exported_names = vars(dashboard_events)
        refusal_names = DashboardEventRefusal.__members__

        # Assert
        self.assertEqual(
            (True, False),
            (
                all(name not in exported_names for name in specialized_timeline_names),
                "EMPTY_TIMELINE_KINDS" in refusal_names,
            ),
        )

    def test_watermark_is_the_latest_linked_dashboard_audit_ordinal(self) -> None:
        # Arrange
        mission = MISSION

        # Act
        rendered = _rendered(watermark_statement(mission))

        # Assert
        self.assertEqual(
            (True, True),
            (
                f"max({AUDIT_RECORD_TABLE}.ordinal)" in rendered,
                DASHBOARD_BROKER_EVENT_TABLE in rendered,
            ),
        )

    def test_snapshot_page_is_ordered_and_bounded_through_its_atomic_watermark(self) -> None:
        # Arrange
        through = ORDINAL

        # Act
        rendered = _rendered(event_page_statement(MISSION, 0, through, MAXIMUM_EVENT_PAGE_SIZE))

        # Assert
        self.assertEqual(
            (True, True, True),
            ("ORDER BY" in rendered, "<= " in rendered, "LIMIT" in rendered),
        )


class OrderedReadRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_basis_captures_live_run_prepared_bytes_and_audit_watermark(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(
            scalar_answers=[ORDINAL], row_answers=[_Rows(one=_run_row(LIVE_RUN))]
        )

        # Act
        basis = await capture_snapshot_basis(session)

        # Assert
        self.assertEqual(SnapshotBasis(run=LIVE_RUN, audit_watermark=ORDINAL), basis)

    async def test_replay_basis_has_no_operational_audit_watermark(self) -> None:
        # Arrange
        session = _RecordingSession(row_answers=[_Rows(one=_run_row(REPLAY_RUN))])

        # Act
        basis = await capture_snapshot_basis(session)

        # Assert
        self.assertIsInstance(basis, SnapshotBasis)
        assert basis is not None
        self.assertEqual((0, []), (basis.audit_watermark, session.scalars))

    async def test_an_empty_pointer_and_an_eventless_live_run_both_have_honest_empty_bases(
        self,
    ) -> None:
        # Arrange
        no_pointer = _RecordingSession(row_answers=[_Rows()])
        eventless = _RecordingSession(
            scalar_answers=[None], row_answers=[_Rows(one=_run_row(LIVE_RUN))]
        )

        # Act
        missing = await capture_snapshot_basis(no_pointer)
        live = await capture_snapshot_basis(eventless)

        # Assert
        self.assertIsNone(missing)
        self.assertEqual(SnapshotBasis(run=LIVE_RUN, audit_watermark=0), live)

    async def test_event_page_returns_exact_canonical_payload_bytes_in_audit_order(self) -> None:
        # Arrange
        rows = ((ORDINAL - 1, "droneTelemetry", b"{}"), (ORDINAL, "missionLifecycle", PAYLOAD))
        session = _RecordingSession(row_answers=[_Rows(many=rows)])

        # Act
        page = await read_event_page(session, MISSION, 0, ORDINAL, MAXIMUM_EVENT_PAGE_SIZE)

        # Assert
        self.assertEqual(
            (
                StoredDashboardEvent(ORDINAL - 1, "droneTelemetry", b"{}"),
                StoredDashboardEvent(ORDINAL, "missionLifecycle", PAYLOAD),
            ),
            page,
        )

    async def test_suffix_read_has_no_upper_watermark_and_remains_bounded(self) -> None:
        # Arrange
        session = _RecordingSession(
            row_answers=[_Rows(many=((ORDINAL + 1, "missionLifecycle", PAYLOAD),))]
        )

        # Act
        page = await read_suffix_page(session, MISSION, ORDINAL, 1)

        # Assert
        self.assertEqual((ORDINAL + 1,), tuple(event.audit_ordinal for event in page))

    async def test_an_unbounded_event_page_is_refused_before_sql_runs(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        with pytest.raises(DashboardEventError) as refused:
            await read_event_page(session, MISSION, 0, ORDINAL, MAXIMUM_EVENT_PAGE_SIZE + 1)

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.INVALID_PAGE_SIZE, []),
            (refused.value.refusal, session.executed),
        )

    async def test_empty_and_boolean_page_sizes_are_refused_before_sql_runs(self) -> None:
        # Arrange
        session = _RecordingSession()
        refusals: list[DashboardEventRefusal] = []

        # Act
        for limit in (0, True):
            try:
                await read_suffix_page(session, MISSION, ORDINAL, limit)
            except DashboardEventError as refused:
                refusals.append(cast("DashboardEventRefusal", refused.refusal))

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.INVALID_PAGE_SIZE,) * 2,
            tuple(refusals),
        )

    async def test_malformed_ordered_rows_are_refused_without_payload_coercion(self) -> None:
        # Arrange
        pages = (
            ((ORDINAL, "missionLifecycle"),),
            ((ORDINAL, 7, PAYLOAD),),
            ((ORDINAL, "missionLifecycle", "not bytes"),),
            ((0, "missionLifecycle", PAYLOAD),),
        )
        refusals: list[DashboardEventRefusal] = []

        # Act
        for rows in pages:
            session = _RecordingSession(row_answers=[_Rows(many=rows)])
            try:
                await read_event_page(session, MISSION, 0, ORDINAL, 1)
            except DashboardEventError as refused:
                refusals.append(cast("DashboardEventRefusal", refused.refusal))

        # Assert
        self.assertEqual(
            (DashboardEventRefusal.UNREADABLE_EVENT,) * len(pages),
            tuple(refusals),
        )


if __name__ == "__main__":
    unittest.main()
