"""Exact application publications, bounded ordered drains, and per-row confirmation CAS."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    ApplicationOutboxError,
    ApplicationOutboxRefusal,
    StagedApplicationEvent,
    pending,
    pending_statement,
    reconciliation,
    reconciliation_statement,
    record_publication,
    stage,
    stage_statement,
)
from aerial_rescue_store.migration import APPLICATION_OUTBOX_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
EVENT: Final = StagedApplicationEvent(
    producer="evidence-service",
    event_id="event-1",
    family="evidence-decision",
    topic="aerial-rescue/v1/mission-1/evidence/decision/proposal-1",
    headers=b'{"trace":"propagated"}',
    payload=b'{"specversion":"1.0"}',
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01",
    tracestate=None,
    correlation_id="correlation-1",
    causation_id="event-0",
    staged_at="2026-08-25T12:00:00.000Z",
)
CONFIRMED_AT: Final = "2026-08-25T12:00:01.000Z"
IDENTITY: Final = ApplicationEventIdentity(EVENT.producer, EVENT.event_id)


def _rendered(statement: ClauseElement) -> str:
    """Render one expression without opening a database connection."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return values bound by one expression."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _Rows:
    """Script a sequence of selected rows."""

    rows: Sequence[Sequence[object]]

    def all(self) -> Sequence[Sequence[object]]:
        """Return every scripted row."""
        return self.rows


@dataclass
class _Session:
    """Record SQL expressions and return scripted outcomes."""

    scalars: list[object] = field(default_factory=list)
    rows: Sequence[Sequence[object]] = ()
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record and return the next scalar."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record and return the scripted rows."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows)


def _stored(
    event: StagedApplicationEvent = EVENT,
    state: OutboxState = OutboxState.STAGED,
) -> tuple[object, ...]:
    """Return one row in repository selection order."""
    return (
        event.producer,
        event.event_id,
        event.family,
        event.topic,
        event.headers,
        event.payload,
        state.value,
        event.traceparent,
        event.tracestate,
        event.correlation_id,
        event.causation_id,
        event.staged_at,
        None,
    )


class ApplicationOutboxStatementTests(unittest.TestCase):
    def test_stage_writes_exact_bytes_and_the_initial_state_with_one_identity_conflict(
        self,
    ) -> None:
        # Arrange
        event = EVENT

        # Act
        statement = stage_statement(event)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, True),
            (
                rendered.startswith(f"INSERT INTO {APPLICATION_OUTBOX_TABLE} "),
                "ON CONFLICT (producer, event_id) DO NOTHING" in rendered,
                event.payload in values,
                OutboxState.STAGED.value in values,
            ),
        )

    def test_pending_reads_only_staged_rows_in_deterministic_bounded_order(self) -> None:
        # Arrange
        producer = EVENT.producer

        # Act
        statement = pending_statement(producer)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                f"{APPLICATION_OUTBOX_TABLE}.state =" in rendered,
                f"ORDER BY {APPLICATION_OUTBOX_TABLE}.staged_at, "
                f"{APPLICATION_OUTBOX_TABLE}.event_id" in rendered,
                APPLICATION_OUTBOX_BATCH_SIZE in values,
            ),
        )

    def test_reconciliation_reads_only_ambiguous_rows_under_the_same_bound(self) -> None:
        # Arrange
        producer = EVENT.producer

        # Act
        statement = reconciliation_statement(producer)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True),
            (
                OutboxState.RECONCILIATION_NEEDED.value in values,
                APPLICATION_OUTBOX_BATCH_SIZE in values,
            ),
        )


class StageTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_event_is_staged_once(self) -> None:
        # Arrange
        session = _Session(scalars=[EVENT.event_id])

        # Act
        await stage(session, EVENT)

        # Assert
        self.assertEqual(
            (1, True), (len(session.statements), session.statements[0].startswith("INSERT "))
        )

    async def test_an_existing_event_identity_is_refused_instead_of_overwritten(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(ApplicationOutboxError) as captured:
            await stage(session, EVENT)

        # Assert
        self.assertEqual(
            (ApplicationOutboxRefusal.ALREADY_STAGED, EVENT.event_id),
            (captured.value.refusal, captured.value.value),
        )


class PendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_maps_exact_rows_in_database_order(self) -> None:
        # Arrange
        later = StagedApplicationEvent(
            **{**EVENT.__dict__, "event_id": "event-2", "staged_at": "2026-08-25T12:00:02.000Z"}
        )
        session = _Session(rows=(_stored(EVENT), _stored(later)))

        # Act
        events = await pending(session, EVENT.producer)

        # Assert
        self.assertEqual((EVENT, later), events)

    async def test_malformed_or_nonstaged_persisted_rows_are_refused(self) -> None:
        # Arrange
        malformed = list(_stored())
        malformed[6] = OutboxState.CONFIRMED.value
        sessions = (_Session(rows=(tuple(malformed),)), _Session(rows=(("short",),)))

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(rows=session.rows):
                with pytest.raises(ApplicationOutboxError) as captured:
                    await pending(session, EVENT.producer)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [ApplicationOutboxRefusal.UNREADABLE_ROW, ApplicationOutboxRefusal.UNREADABLE_ROW],
            refusals,
        )

    async def test_reconciliation_maps_only_ambiguous_rows_without_republishing_them(self) -> None:
        # Arrange
        session = _Session(rows=(_stored(state=OutboxState.RECONCILIATION_NEEDED),))

        # Act
        events = await reconciliation(session, EVENT.producer)

        # Assert
        self.assertEqual((EVENT,), events)


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmation_records_evidence_and_ambiguity_records_no_false_success(
        self,
    ) -> None:
        # Arrange
        confirmed = _Session(scalars=[EVENT.event_id])
        ambiguous = _Session(scalars=[EVENT.event_id])

        # Act
        confirmed_state = await record_publication(
            confirmed,
            IDENTITY,
            OutboxState.STAGED,
            OutboxEvent.CONFIRM,
            CONFIRMED_AT,
        )
        ambiguous_state = await record_publication(
            ambiguous,
            IDENTITY,
            OutboxState.STAGED,
            OutboxEvent.AMBIGUOUS,
            None,
        )

        # Assert
        self.assertEqual(
            (OutboxState.CONFIRMED, OutboxState.RECONCILIATION_NEEDED),
            (confirmed_state, ambiguous_state),
        )

    async def test_confirmation_requires_an_instant_and_ambiguity_forbids_one(self) -> None:
        # Arrange
        cases = ((OutboxEvent.CONFIRM, None), (OutboxEvent.AMBIGUOUS, CONFIRMED_AT))

        # Act
        refusals = []
        for event, instant in cases:
            with self.subTest(event=event):
                with pytest.raises(ApplicationOutboxError) as captured:
                    await record_publication(
                        _Session(),
                        IDENTITY,
                        OutboxState.STAGED,
                        event,
                        instant,
                    )
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                ApplicationOutboxRefusal.CONFIRMATION_EVIDENCE,
                ApplicationOutboxRefusal.CONFIRMATION_EVIDENCE,
            ],
            refusals,
        )

    async def test_compare_and_set_refuses_a_row_that_moved_on(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(ApplicationOutboxError) as captured:
            await record_publication(
                session,
                IDENTITY,
                OutboxState.STAGED,
                OutboxEvent.CONFIRM,
                CONFIRMED_AT,
            )

        # Assert
        self.assertEqual(ApplicationOutboxRefusal.NOT_IN_EXPECTED_STATE, captured.value.refusal)


if __name__ == "__main__":
    unittest.main()
