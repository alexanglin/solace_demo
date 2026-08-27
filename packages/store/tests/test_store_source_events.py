"""Immutable complete source events used for independent provenance recomputation."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.processing.source_events import (
    SourceEventDecision,
    SourceEventError,
    SourceEventRefusal,
    StoredSourceEvent,
    identity_statement,
    load_for,
    lookup_statement,
    record,
    record_statement,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
EVENT: Final = StoredSourceEvent(
    source="urn:aerial-rescue:fleet:drone-1",
    event_id="source-event-1",
    mission_id="mission-1",
    topic="aerial-rescue/v1/mission-1/drone/event/drone-1",
    canonical_digest="1" * 64,
    canonical_payload=b'{"canonicalizationVersion":1,"event":{"id":"source-event-1"}}',
    observed_at="2026-08-25T12:00:00.000Z",
)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without connecting."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _row(event: StoredSourceEvent = EVENT) -> tuple[object, ...]:
    """Return one source event in migrated column order."""
    return (
        event.source,
        event.event_id,
        event.mission_id,
        event.topic,
        event.canonical_digest,
        event.canonical_payload,
        event.observed_at,
    )


@dataclass
class _Rows:
    """Script selected rows using the two SQLAlchemy result operations under test."""

    rows: Sequence[Sequence[object]]

    def one_or_none(self) -> Sequence[object] | None:
        """Return the only scripted row, or ``None``."""
        if not self.rows:
            return None
        return self.rows[0]

    def all(self) -> Sequence[Sequence[object]]:
        """Return every scripted lookup row."""
        return self.rows


@dataclass
class _Session:
    """Record repository statements and return scripted durable outcomes."""

    scalar_value: object = None
    rows: Sequence[Sequence[object]] = ()
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert, /) -> object:
        """Record an insert and return its identity."""
        self.statements.append(_rendered(statement))
        return self.scalar_value

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record a read and return its rows."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows)


class SourceEventStatementTests(unittest.TestCase):
    def test_record_contends_on_the_complete_cloudevent_identity_without_an_update(self) -> None:
        # Arrange
        event = EVENT

        # Act
        rendered = _rendered(record_statement(event))

        # Assert
        self.assertEqual(
            (True, True, False),
            (
                "ON CONFLICT (source, event_id) DO NOTHING" in rendered,
                rendered.endswith("RETURNING source_event.event_id"),
                "UPDATE" in rendered,
            ),
        )

    def test_identity_and_mission_lookups_are_exact_and_the_latter_is_bounded(self) -> None:
        # Arrange
        identity = identity_statement(EVENT.source, EVENT.event_id)
        mission = lookup_statement(EVENT.mission_id, EVENT.event_id)

        # Act
        rendered = (_rendered(identity), _rendered(mission))

        # Assert
        self.assertEqual(
            (True, True, True, True),
            (
                "source_event.source = " in rendered[0],
                "source_event.event_id = " in rendered[0],
                "ORDER BY source_event.source" in rendered[1],
                "LIMIT" in rendered[1],
            ),
        )


class RecordSourceEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_complete_source_event_is_stored_once(self) -> None:
        # Arrange
        session = _Session(scalar_value=EVENT.event_id)

        # Act
        decision = await record(session, EVENT)

        # Assert
        self.assertEqual((SourceEventDecision.STORED, 1), (decision, len(session.statements)))

    async def test_an_exact_event_already_committed_by_a_prior_process_is_idempotent(self) -> None:
        # Arrange
        session = _Session(rows=(_row(),))

        # Act
        decision = await record(session, EVENT)

        # Assert
        self.assertEqual(
            (SourceEventDecision.DUPLICATE, 2, True),
            (decision, len(session.statements), session.statements[1].startswith("SELECT ")),
        )

    async def test_reused_identity_with_changed_immutable_content_is_a_hard_conflict(self) -> None:
        # Arrange
        changed = (
            replace(EVENT, mission_id="mission-2"),
            replace(EVENT, topic="aerial-rescue/v1/mission-2/drone/event/drone-1"),
            replace(EVENT, canonical_digest="2" * 64),
            replace(EVENT, canonical_payload=b"different complete event"),
            replace(EVENT, observed_at="2026-08-25T12:00:01.000Z"),
        )

        # Act
        refusals = []
        for stored in changed:
            with self.subTest(stored=stored):
                with pytest.raises(SourceEventError) as captured:
                    await record(_Session(rows=(_row(stored),)), EVENT)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([SourceEventRefusal.IDENTITY_CONFLICT] * len(changed), refusals)

    async def test_a_conflict_whose_row_vanished_is_refused_without_a_blind_retry(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(SourceEventError) as captured:
            await record(session, EVENT)

        # Assert
        self.assertEqual(
            (SourceEventRefusal.IDENTITY_VANISHED, 2),
            (captured.value.refusal, len(session.statements)),
        )


class LoadSourceEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_unique_mission_event_identity_returns_the_complete_canonical_fact(
        self,
    ) -> None:
        # Arrange
        session = _Session(rows=(_row(),))

        # Act
        loaded = await load_for(session, EVENT.mission_id, EVENT.event_id)

        # Assert
        self.assertEqual(EVENT, loaded)

    async def test_an_absent_or_multi_source_event_identity_is_refused_not_guessed(self) -> None:
        # Arrange
        other_source = replace(EVENT, source="urn:aerial-rescue:fleet:drone-2")
        sessions = (_Session(), _Session(rows=(_row(), _row(other_source))))

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(rows=session.rows):
                with pytest.raises(SourceEventError) as captured:
                    await load_for(session, EVENT.mission_id, EVENT.event_id)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [SourceEventRefusal.NOT_FOUND, SourceEventRefusal.AMBIGUOUS_IDENTITY],
            refusals,
        )

    async def test_malformed_or_mismatched_selected_rows_fail_closed(self) -> None:
        # Arrange
        wrong_mission = replace(EVENT, mission_id="mission-2")
        wrong_payload = list(_row())
        wrong_payload[5] = "not canonical bytes"
        sessions = (
            _Session(rows=(("short",),)),
            _Session(rows=(wrong_payload,)),
            _Session(rows=(_row(wrong_mission),)),
        )

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(rows=session.rows):
                with pytest.raises(SourceEventError) as captured:
                    await load_for(session, EVENT.mission_id, EVENT.event_id)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([SourceEventRefusal.UNREADABLE_ROW] * len(sessions), refusals)


if __name__ == "__main__":
    unittest.main()
