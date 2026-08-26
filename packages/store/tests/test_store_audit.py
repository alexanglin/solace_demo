"""The audit append, and the ordinal it issues inside the caller's transaction.

[ADR-0088](../../../docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md)
does not merely say the timeline is ordered; it names the exact statement that orders it, and
rejects `bigserial` because a value assigned before commit delivers neither commit order nor
gap-freedom. The two statements are therefore the decision, and they are asserted here as
compiled text against the PostgreSQL dialect -- the whole distinction being that the counter
advances from `audit_sequence.next_ordinal` and never from the row the insert proposed.

What this file cannot establish is everything the statement exists for. It runs no database, so
the row lock, the commit ordering, the gap-freedom of a rolled-back append, and the serialising
of two writers on one mission are all
[ADR-0086](../../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)'s
live class, in `tests/integration/test_durable_store_live.py`. The fake here proves that the
ordinal is issued before the record is written and that an ordinal the counter did not issue is
refused. It proves call order, not concurrency.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.audit import (
    FIRST_ORDINAL,
    AuditError,
    AuditRecord,
    AuditRefusal,
    append,
    next_ordinal_statement,
    record_statement,
)
from aerial_rescue_store.migration import AUDIT_RECORD_TABLE, AUDIT_SEQUENCE_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
"""The dialect the member's own driver pin resolves to, so what is asserted is what asyncpg
would receive rather than what a generic PostgreSQL dialect would. Nothing connects: an engine
is lazy, and only its dialect is ever touched."""


MISSION: Final = "m-store-unit"
ISSUED_ORDINAL: Final = 7

RECORD: Final = AuditRecord(
    mission_id=MISSION,
    kind="probe",
    occurred_at="2026-08-23T12:00:00.000Z",
    payload=b'{"probe":true}',
    correlation_id="c-store-unit",
    causation_id=None,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01",
)

DECLARED_COLUMNS: Final = (
    "causation_id",
    "correlation_id",
    "kind",
    "mission_id",
    "occurred_at",
    "ordinal",
    "payload",
    "traceparent",
)


def _rendered(statement: Insert) -> str:
    """Return the statement as PostgreSQL would receive it, with no database involved."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: Insert) -> Mapping[str, object]:
    """Return the values the statement would bind."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _RecordingSession:
    """A session that records the statements it is given and issues a canned ordinal."""

    issues: int | None = ISSUED_ORDINAL
    scalars: list[str] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert, /) -> int | None:
        """Record the statement whose single value was asked for, and answer it."""
        self.scalars.append(_rendered(statement))
        return self.issues

    async def execute(self, statement: Insert, /) -> object:
        """Record the statement that was executed for its effect."""
        self.executed.append(_rendered(statement))
        return None


class OrdinalStatementTests(unittest.TestCase):
    def test_the_counter_advances_from_its_own_row_and_not_from_the_proposed_one(self) -> None:
        # Arrange
        mission = MISSION

        # Act
        rendered = _rendered(next_ordinal_statement(mission))

        # Assert
        self.assertIn(
            f"DO UPDATE SET next_ordinal = ({AUDIT_SEQUENCE_TABLE}.next_ordinal +",
            rendered,
        )

    def test_the_conflict_is_taken_on_the_mission_and_the_issued_value_is_returned(self) -> None:
        # Arrange
        mission = MISSION

        # Act
        rendered = _rendered(next_ordinal_statement(mission))

        # Assert
        self.assertEqual(
            (True, True),
            (
                "ON CONFLICT (mission_id)" in rendered,
                rendered.endswith(f"RETURNING {AUDIT_SEQUENCE_TABLE}.next_ordinal"),
            ),
        )

    def test_the_first_record_of_a_mission_needs_no_separate_initialisation(self) -> None:
        # Arrange
        mission = MISSION

        # Act
        statement = next_ordinal_statement(mission)

        # Assert
        self.assertEqual(
            (mission, FIRST_ORDINAL),
            (_parameters(statement)["mission_id"], _parameters(statement)["next_ordinal"]),
        )


class RecordStatementTests(unittest.TestCase):
    def test_every_column_the_revision_declares_is_bound(self) -> None:
        # Arrange
        record = RECORD

        # Act
        bound = _parameters(record_statement(record, ISSUED_ORDINAL))

        # Assert
        self.assertEqual(DECLARED_COLUMNS, tuple(sorted(bound)))

    def test_the_ordinal_written_is_the_one_the_counter_issued(self) -> None:
        # Arrange
        record = RECORD

        # Act
        bound = _parameters(record_statement(record, ISSUED_ORDINAL))

        # Assert
        self.assertEqual(ISSUED_ORDINAL, bound["ordinal"])

    def test_the_instant_and_the_payload_are_persisted_exactly_as_accepted(self) -> None:
        # Arrange
        record = RECORD

        # Act
        bound = _parameters(record_statement(record, ISSUED_ORDINAL))

        # Assert
        self.assertEqual(
            (record.occurred_at, record.payload),
            (bound["occurred_at"], bound["payload"]),
        )

    def test_the_record_is_written_to_the_append_only_table(self) -> None:
        # Arrange
        record = RECORD

        # Act
        rendered = _rendered(record_statement(record, ISSUED_ORDINAL))

        # Assert
        self.assertTrue(rendered.startswith(f"INSERT INTO {AUDIT_RECORD_TABLE} "))


class AppendTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_ordinal_is_issued_before_the_record_is_written(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await append(session, RECORD)

        # Assert
        self.assertEqual(
            (1, 1, True, True),
            (
                len(session.scalars),
                len(session.executed),
                AUDIT_SEQUENCE_TABLE in session.scalars[0],
                session.executed[0].startswith(f"INSERT INTO {AUDIT_RECORD_TABLE} "),
            ),
        )

    async def test_the_issued_ordinal_is_what_the_append_returns(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        ordinal = await append(session, RECORD)

        # Assert
        self.assertEqual(ISSUED_ORDINAL, ordinal)

    async def test_an_ordinal_the_counter_did_not_issue_is_refused_before_anything_is_written(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(issues=None)

        # Act
        with pytest.raises(AuditError) as refused:
            await append(session, RECORD)

        # Assert
        self.assertEqual(
            (AuditRefusal.NO_ORDINAL_ISSUED, MISSION, []),
            (refused.value.refusal, refused.value.value, session.executed),
        )


if __name__ == "__main__":
    unittest.main()
