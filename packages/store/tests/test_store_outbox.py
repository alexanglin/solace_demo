"""Staging a command under the counted bound, and moving it only along an edge the domain allows.

[ADR-0093](../../../docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md) puts the
capacity guard inside the staging statement rather than in a preceding read, for the reason
`READ COMMITTED` gives, and keeps the publication lifecycle in `packages/domain`. Both are
asserted here: the emitted statement carries its own count, and every state this module writes
came from ``aerial_rescue_domain.outbox.transition`` rather than from a caller.

Two things this module deliberately cannot do are asserted as absences. It never accepts a state
on the way in -- a staged record is staged because staging is what happened -- and it never moves
a record whose stored state is not the one the caller says it moved from.

The bound itself is live evidence, not this file's: whether 501 records are refused, and by how
much concurrent staging overshoots, belongs to
[ADR-0086](../../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)'s
live class.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_domain.outbox import OutboxError, OutboxEvent, OutboxState
from aerial_rescue_store.migration import COMMAND_OUTBOX_TABLE
from aerial_rescue_store.outbox import (
    COMMAND_PUBLICATION_BATCH_SIZE,
    MAXIMUM_UNCONFIRMED_RECORDS,
    CommandOutboxRecord,
    StagedCommand,
    StagedCommandError,
    StagedCommandRefusal,
    pending,
    pending_statement,
    publication_statement,
    record_publication,
    stage,
    stage_statement,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
"""The dialect the member's own driver pin resolves to. Nothing connects: an engine is lazy."""

COMMAND: Final = "c-store-unit"
MISSION: Final = "m-store-unit"
DRONE: Final = "drone-07"
PAYLOAD: Final = b'{"commandType":"escalate_rescue"}'
CORRELATION: Final = "r-store-unit"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"
STAGED_AT: Final = "2026-08-24T12:00:00.000Z"

STAGED: Final = StagedCommand(
    command_id=COMMAND,
    mission_id=MISSION,
    drone_id=DRONE,
    payload=PAYLOAD,
    correlation_id=CORRELATION,
    causation_id=None,
    traceparent=TRACEPARENT,
    staged_at=STAGED_AT,
)


def _rendered(statement: ClauseElement) -> str:
    """Return the statement as PostgreSQL would receive it, with no database involved."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the values the statement would bind."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _RecordingSession:
    """A session that records the statements it is given and answers with a canned value."""

    written: str | None = COMMAND
    scalars: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record the statement whose single value was asked for, and answer it."""
        self.scalars.append(_rendered(statement))
        return self.written


@dataclass
class _Rows:
    """Return scripted command-outbox rows in database order."""

    rows: Sequence[Sequence[object]]

    def all(self) -> Sequence[Sequence[object]]:
        """Return the scripted rows."""
        return self.rows


@dataclass
class _ReadSession:
    """Record a bounded pending read without opening PostgreSQL."""

    rows: Sequence[Sequence[object]]
    statements: list[str] = field(default_factory=list)

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record the typed select and return the scripted rows."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows)


def _row(
    command: StagedCommand = STAGED,
    state: OutboxState = OutboxState.STAGED,
) -> tuple[object, ...]:
    """Return one migrated command-outbox row in metadata order."""
    return (
        command.command_id,
        command.mission_id,
        command.drone_id,
        command.payload,
        state.value,
        command.correlation_id,
        command.causation_id,
        command.traceparent,
        command.staged_at,
    )


class StageStatementTests(unittest.TestCase):
    def test_the_statement_carries_its_own_count_rather_than_trusting_a_prior_read(self) -> None:
        # Arrange
        command = STAGED

        # Act
        rendered = _rendered(stage_statement(command))

        # Assert
        self.assertIn("SELECT count(*)", rendered)

    def test_the_count_covers_every_record_the_broker_has_not_confirmed(self) -> None:
        # Arrange
        command = STAGED

        # Act
        rendered = _rendered(stage_statement(command))

        # Assert
        self.assertIn(f"{COMMAND_OUTBOX_TABLE}.state !=", rendered)

    def test_the_bound_is_the_one_the_parameter_row_carries(self) -> None:
        # Arrange
        command = STAGED

        # Act
        bound = _parameters(stage_statement(command))

        # Assert
        self.assertIn(MAXIMUM_UNCONFIRMED_RECORDS, tuple(bound.values()))

    def test_the_statement_returns_the_command_so_a_refused_write_is_visible(self) -> None:
        # Arrange
        command = STAGED

        # Act
        rendered = _rendered(stage_statement(command))

        # Assert
        self.assertTrue(rendered.endswith(f"RETURNING {COMMAND_OUTBOX_TABLE}.command_id"))

    def test_a_staged_record_is_staged_because_staging_is_what_happened(self) -> None:
        # Arrange
        command = STAGED

        # Act
        bound = _parameters(stage_statement(command))

        # Assert
        self.assertIn(OutboxState.STAGED.value, tuple(bound.values()))

    def test_the_payload_and_the_instant_are_persisted_exactly_as_accepted(self) -> None:
        # Arrange
        command = STAGED

        # Act
        bound = tuple(_parameters(stage_statement(command)).values())

        # Assert
        self.assertEqual((True, True), (command.payload in bound, command.staged_at in bound))


class PublicationStatementTests(unittest.TestCase):
    def test_the_move_is_conditional_on_the_state_the_caller_moved_from(self) -> None:
        # Arrange
        was = OutboxState.STAGED

        # Act
        bound = tuple(
            _parameters(publication_statement(COMMAND, was, OutboxState.CONFIRMED)).values()
        )

        # Assert
        self.assertEqual((OutboxState.CONFIRMED.value, COMMAND, was.value), bound)

    def test_the_statement_returns_what_it_changed_so_no_change_is_visible(self) -> None:
        # Arrange
        was = OutboxState.STAGED

        # Act
        rendered = _rendered(publication_statement(COMMAND, was, OutboxState.CONFIRMED))

        # Assert
        self.assertTrue(rendered.endswith(f"RETURNING {COMMAND_OUTBOX_TABLE}.command_id"))


class PendingStatementTests(unittest.TestCase):
    def test_the_read_is_state_scoped_oldest_first_and_bounded(self) -> None:
        # Arrange
        limit = COMMAND_PUBLICATION_BATCH_SIZE

        # Act
        rendered = _rendered(pending_statement(OutboxState.STAGED, limit))
        bound = tuple(_parameters(pending_statement(OutboxState.STAGED, limit)).values())

        # Assert
        self.assertEqual(
            (True, True, (OutboxState.STAGED.value, limit)),
            (
                "ORDER BY command_outbox.staged_at, command_outbox.command_id" in rendered,
                "LIMIT" in rendered,
                bound[-2:],
            ),
        )


class PendingReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_staged_and_ambiguous_rows_are_read_as_distinct_recovery_states(self) -> None:
        # Arrange
        staged_session = _ReadSession((_row(),))
        ambiguous_session = _ReadSession((_row(state=OutboxState.RECONCILIATION_NEEDED),))

        # Act
        staged = await pending(
            staged_session,
            OutboxState.STAGED,
            COMMAND_PUBLICATION_BATCH_SIZE,
        )
        ambiguous = await pending(
            ambiguous_session,
            OutboxState.RECONCILIATION_NEEDED,
            COMMAND_PUBLICATION_BATCH_SIZE,
        )

        # Assert
        self.assertEqual(
            (
                (CommandOutboxRecord(STAGED, OutboxState.STAGED),),
                (CommandOutboxRecord(STAGED, OutboxState.RECONCILIATION_NEEDED),),
            ),
            (staged, ambiguous),
        )

    async def test_invalid_bounds_and_terminal_state_are_refused_before_sql(self) -> None:
        # Arrange
        cases = (
            (OutboxState.STAGED, 0),
            (OutboxState.STAGED, COMMAND_PUBLICATION_BATCH_SIZE + 1),
            (OutboxState.CONFIRMED, COMMAND_PUBLICATION_BATCH_SIZE),
        )
        sessions = tuple(_ReadSession(()) for _case in cases)

        # Act
        refusals = []
        for session, (state, limit) in zip(sessions, cases, strict=True):
            with pytest.raises(StagedCommandError) as captured:
                await pending(session, state, limit)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            (
                [
                    StagedCommandRefusal.INVALID_READ_LIMIT,
                    StagedCommandRefusal.INVALID_READ_LIMIT,
                    StagedCommandRefusal.TERMINAL_READ,
                ],
                ([], [], []),
            ),
            (refusals, tuple(session.statements for session in sessions)),
        )


class StageTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_command_is_staged_by_the_conditional_insert(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await stage(session, STAGED)

        # Assert
        self.assertEqual(
            (1, True),
            (
                len(session.scalars),
                session.scalars[0].startswith(f"INSERT INTO {COMMAND_OUTBOX_TABLE} "),
            ),
        )

    async def test_a_write_the_bound_refused_is_a_refusal_and_never_a_silent_drop(self) -> None:
        # Arrange
        session = _RecordingSession(written=None)

        # Act
        with pytest.raises(StagedCommandError) as refused:
            await stage(session, STAGED)

        # Assert
        self.assertEqual(
            (StagedCommandRefusal.AT_CAPACITY, COMMAND),
            (refused.value.refusal, refused.value.value),
        )


class RecordPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_state_written_is_the_one_the_domain_reached(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        became = await record_publication(session, COMMAND, OutboxState.STAGED, OutboxEvent.CONFIRM)

        # Assert
        self.assertEqual(OutboxState.CONFIRMED, became)

    async def test_an_ambiguous_outcome_is_recorded_as_needing_reconciliation(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        became = await record_publication(
            session, COMMAND, OutboxState.STAGED, OutboxEvent.AMBIGUOUS
        )

        # Assert
        self.assertEqual(OutboxState.RECONCILIATION_NEEDED, became)

    async def test_an_edge_the_domain_refuses_is_refused_before_anything_is_written(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        with pytest.raises(OutboxError):
            await record_publication(session, COMMAND, OutboxState.CONFIRMED, OutboxEvent.CONFIRM)

        # Assert
        self.assertEqual([], session.scalars)

    async def test_a_row_that_had_moved_on_is_refused_rather_than_overwritten(self) -> None:
        # Arrange
        session = _RecordingSession(written=None)

        # Act
        with pytest.raises(StagedCommandError) as refused:
            await record_publication(session, COMMAND, OutboxState.STAGED, OutboxEvent.CONFIRM)

        # Assert
        self.assertEqual(
            (StagedCommandRefusal.NOT_IN_EXPECTED_STATE, COMMAND),
            (refused.value.refusal, refused.value.value),
        )


if __name__ == "__main__":
    unittest.main()
