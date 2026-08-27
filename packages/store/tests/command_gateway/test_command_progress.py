"""Durable command progress follows the domain lifecycle under one compare-and-set."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_domain.commands import (
    CommandError,
    CommandEvent,
    CommandProgress,
    CommandState,
    SendBudget,
)
from aerial_rescue_store.command_progress import (
    MAXIMUM_SEND_COUNT,
    CommandIdentity,
    CommandProgressError,
    CommandProgressRefusal,
    StoredCommandProgress,
    TransitionFacts,
    initialize,
    initialize_statement,
    load,
    load_statement,
    record_transition,
    transition_statement,
)
from aerial_rescue_store.migration import COMMAND_PROGRESS_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
IDENTITY: Final = CommandIdentity("command-1", "mission-1", "drone-1")
CREATED_AT: Final = "2026-08-25T12:00:00.000Z"
SENT_AT: Final = "2026-08-25T12:00:01.000Z"
DEADLINE_AT: Final = "2026-08-25T12:00:07.000Z"
UPDATED_AT: Final = "2026-08-25T12:00:02.000Z"
BUDGET: Final = SendBudget(MAXIMUM_SEND_COUNT)
INITIAL: Final = StoredCommandProgress(
    identity=IDENTITY,
    progress=CommandProgress(CommandState.ACCEPTED, 0),
    last_sent_at=None,
    deadline_at=None,
    result_id=None,
    updated_at=CREATED_AT,
)
SEND_FACTS: Final = TransitionFacts(SENT_AT, DEADLINE_AT, None, UPDATED_AT)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without opening a connection."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return values bound by one expression."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _stored(progress: StoredCommandProgress = INITIAL) -> tuple[object, ...]:
    """Return one command-progress row in migrated column order."""
    return (
        progress.identity.command_id,
        progress.identity.mission_id,
        progress.identity.drone_id,
        progress.progress.state.value,
        progress.progress.sends,
        progress.last_sent_at,
        progress.deadline_at,
        progress.result_id,
        progress.updated_at,
    )


@dataclass
class _Rows:
    """One scripted selected row."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the scripted row."""
        return self.row


@dataclass
class _Session:
    """Record repository statements and return scripted outcomes."""

    scalars: list[object] = field(default_factory=list)
    row: Sequence[object] | None = None
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record and return the next scalar result."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record and return the scripted command-progress row."""
        self.statements.append(_rendered(statement))
        return _Rows(self.row)


class CommandProgressStatementTests(unittest.TestCase):
    def test_initialize_writes_only_the_domain_initial_progress_once(self) -> None:
        # Arrange
        identity = IDENTITY

        # Act
        statement = initialize_statement(identity, CREATED_AT)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, True, False),
            (
                rendered.startswith(f"INSERT INTO {COMMAND_PROGRESS_TABLE} "),
                "ON CONFLICT (command_id) DO NOTHING" in rendered,
                CommandState.ACCEPTED.value in values,
                0 in values,
                "command_outbox" in rendered,
            ),
        )

    def test_transition_is_compare_and_set_on_the_prior_state_and_send_count(self) -> None:
        # Arrange
        current = INITIAL
        became = CommandProgress(CommandState.IN_FLIGHT, 1)

        # Act
        statement = transition_statement(current, became, SEND_FACTS)
        rendered = _rendered(statement)
        values = _parameters(statement)

        # Assert
        self.assertEqual(
            (True, True, True, True, True),
            (
                rendered.startswith(f"UPDATE {COMMAND_PROGRESS_TABLE} "),
                f"{COMMAND_PROGRESS_TABLE}.state =" in rendered,
                f"{COMMAND_PROGRESS_TABLE}.send_count =" in rendered,
                (values["state"], values["state_1"])
                == (CommandState.IN_FLIGHT.value, CommandState.ACCEPTED.value),
                (values["send_count"], values["send_count_1"]) == (1, 0),
            ),
        )

    def test_load_selects_the_progress_table_without_publication_state(self) -> None:
        # Arrange
        command_id = IDENTITY.command_id

        # Act
        rendered = _rendered(load_statement(command_id))

        # Assert
        self.assertEqual(
            (True, False),
            (
                rendered.startswith(f"SELECT {COMMAND_PROGRESS_TABLE}.command_id"),
                "outbox" in rendered,
            ),
        )


class InitializeCommandProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_validated_command_starts_accepted_and_unsent(self) -> None:
        # Arrange
        session = _Session(scalars=[IDENTITY.command_id])

        # Act
        stored = await initialize(session, IDENTITY, CREATED_AT)

        # Assert
        self.assertEqual((INITIAL, 1), (stored, len(session.statements)))

    async def test_an_existing_command_progress_identity_is_not_overwritten(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(CommandProgressError) as captured:
            await initialize(session, IDENTITY, CREATED_AT)

        # Assert
        self.assertEqual(
            (CommandProgressRefusal.ALREADY_EXISTS, IDENTITY.command_id),
            (captured.value.refusal, captured.value.value),
        )


class LoadCommandProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_maps_the_persisted_domain_progress_and_adapter_facts(self) -> None:
        # Arrange
        session = _Session(row=_stored())

        # Act
        stored = await load(session, IDENTITY.command_id)

        # Assert
        self.assertEqual(INITIAL, stored)

    async def test_missing_unknown_and_out_of_bound_progress_rows_are_refused(self) -> None:
        # Arrange
        unknown = list(_stored())
        unknown[3] = "published"
        over_bound = list(_stored())
        over_bound[4] = MAXIMUM_SEND_COUNT + 1
        sessions = (
            _Session(),
            _Session(row=unknown),
            _Session(row=over_bound),
            _Session(row=("short",)),
        )

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(row=session.row):
                with pytest.raises(CommandProgressError) as captured:
                    await load(session, IDENTITY.command_id)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [CommandProgressRefusal.NOT_FOUND] + [CommandProgressRefusal.UNREADABLE_ROW] * 3,
            refusals,
        )


class RecordCommandTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_uses_the_domain_transition_and_persists_one_counted_send(self) -> None:
        # Arrange
        session = _Session(scalars=[IDENTITY.command_id])

        # Act
        stored = await record_transition(
            session,
            INITIAL,
            CommandEvent.SEND,
            BUDGET,
            SEND_FACTS,
        )

        # Assert
        self.assertEqual(
            (
                CommandProgress(CommandState.IN_FLIGHT, 1),
                SENT_AT,
                DEADLINE_AT,
                1,
            ),
            (
                stored.progress,
                stored.last_sent_at,
                stored.deadline_at,
                len(session.statements),
            ),
        )

    async def test_the_fifth_timeout_is_persisted_as_domain_abandonment(self) -> None:
        # Arrange
        current = StoredCommandProgress(
            identity=IDENTITY,
            progress=CommandProgress(CommandState.IN_FLIGHT, MAXIMUM_SEND_COUNT),
            last_sent_at=SENT_AT,
            deadline_at=DEADLINE_AT,
            result_id=None,
            updated_at=UPDATED_AT,
        )
        facts = TransitionFacts(SENT_AT, None, None, "2026-08-25T12:00:08.000Z")
        session = _Session(scalars=[IDENTITY.command_id])

        # Act
        stored = await record_transition(
            session,
            current,
            CommandEvent.TIME_OUT,
            BUDGET,
            facts,
        )

        # Assert
        self.assertEqual(
            CommandProgress(CommandState.ABANDONED, MAXIMUM_SEND_COUNT), stored.progress
        )

    async def test_an_illegal_domain_transition_is_refused_before_database_io(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(CommandError):
            await record_transition(
                session,
                INITIAL,
                CommandEvent.ACKNOWLEDGE,
                BUDGET,
                SEND_FACTS,
            )

        # Assert
        self.assertEqual([], session.statements)

    async def test_a_sixth_send_is_refused_before_the_schema_has_to_reject_it(self) -> None:
        # Arrange
        current = StoredCommandProgress(
            identity=IDENTITY,
            progress=CommandProgress(CommandState.ACCEPTED, MAXIMUM_SEND_COUNT),
            last_sent_at=SENT_AT,
            deadline_at=None,
            result_id=None,
            updated_at=UPDATED_AT,
        )
        session = _Session()

        # Act
        with pytest.raises(CommandProgressError) as captured:
            await record_transition(
                session,
                current,
                CommandEvent.SEND,
                SendBudget(MAXIMUM_SEND_COUNT + 1),
                SEND_FACTS,
            )

        # Assert
        self.assertEqual(
            (CommandProgressRefusal.SEND_COUNT_OUT_OF_RANGE, [], MAXIMUM_SEND_COUNT + 1),
            (captured.value.refusal, session.statements, captured.value.value),
        )

    async def test_an_invalid_prior_send_count_is_refused_before_domain_or_database_work(
        self,
    ) -> None:
        # Arrange
        current = StoredCommandProgress(
            identity=IDENTITY,
            progress=CommandProgress(CommandState.ACCEPTED, -1),
            last_sent_at=None,
            deadline_at=None,
            result_id=None,
            updated_at=UPDATED_AT,
        )
        session = _Session()

        # Act
        with pytest.raises(CommandProgressError) as captured:
            await record_transition(
                session,
                current,
                CommandEvent.SEND,
                BUDGET,
                SEND_FACTS,
            )

        # Assert
        self.assertEqual(
            (CommandProgressRefusal.SEND_COUNT_OUT_OF_RANGE, -1, []),
            (captured.value.refusal, captured.value.value, session.statements),
        )

    async def test_a_stale_progress_compare_and_set_never_overwrites_a_newer_transition(
        self,
    ) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(CommandProgressError) as captured:
            await record_transition(
                session,
                INITIAL,
                CommandEvent.SEND,
                BUDGET,
                SEND_FACTS,
            )

        # Assert
        self.assertEqual(
            (CommandProgressRefusal.STALE_PROGRESS, IDENTITY.command_id),
            (captured.value.refusal, captured.value.value),
        )


if __name__ == "__main__":
    unittest.main()
