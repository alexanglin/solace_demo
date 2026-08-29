"""Durable command dispatch progress, separate from broker publication state.

The domain command lifecycle decides every transition and counts sends.  This repository only
persists that decision under a compare-and-set over the prior state and send count, so a stale
dispatcher cannot overwrite a newer result.  Timestamps and result identity are adapter facts
supplied by the caller; this module reads no clock and never accesses either outbox table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_domain.commands import (
    INITIAL_PROGRESS,
    CommandEvent,
    CommandProgress,
    CommandState,
    SendBudget,
    advance,
)
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import COMMAND_PROGRESS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

MAXIMUM_SEND_COUNT: Final = 5
"""The migrated defence-in-depth ceiling for ADR-0081's five-send budget."""

STORED_MEMBER_COUNT: Final = 9
type ProgressSelection = Select[tuple[object, ...]]


class CommandProgressRefusal(Enum):
    """Why durable command progress cannot be created, read, or advanced."""

    ALREADY_EXISTS = "durable progress already exists for that command identity"
    NOT_FOUND = "no durable progress exists for that command identity"
    UNREADABLE_ROW = "the durable command progress row does not match its migrated typed shape"
    SEND_COUNT_OUT_OF_RANGE = "the command send count exceeds the migrated five-send bound"
    STALE_PROGRESS = "the durable command progress no longer matches the compared state and count"


class CommandProgressError(StoreError):
    """A command-progress persistence operation this package refuses."""


@dataclass(frozen=True)
class CommandIdentity:
    """The immutable mission and drone binding of one command identifier."""

    command_id: str
    mission_id: str
    drone_id: str


@dataclass(frozen=True)
class TransitionFacts:
    """Adapter-owned facts stored alongside one domain transition."""

    last_sent_at: str | None
    deadline_at: str | None
    result_id: str | None
    updated_at: str


@dataclass(frozen=True)
class StoredCommandProgress:
    """One command's domain progress and the adapter facts persisted with it."""

    identity: CommandIdentity
    progress: CommandProgress
    last_sent_at: str | None
    deadline_at: str | None
    result_id: str | None
    updated_at: str


def initialize_statement(identity: CommandIdentity, updated_at: str) -> Insert:
    """Return an insert that records the domain's one initial progress value once."""
    proposed = postgresql_insert(COMMAND_PROGRESS).values(
        command_id=identity.command_id,
        mission_id=identity.mission_id,
        drone_id=identity.drone_id,
        state=INITIAL_PROGRESS.state.value,
        send_count=INITIAL_PROGRESS.sends,
        last_sent_at=None,
        deadline_at=None,
        result_id=None,
        updated_at=updated_at,
    )
    inserted = proposed.on_conflict_do_nothing(index_elements=[COMMAND_PROGRESS.c.command_id])
    return inserted.returning(COMMAND_PROGRESS.c.command_id)


def load_statement(command_id: str) -> ProgressSelection:
    """Return every durable progress column for one command identity."""
    return cast(
        "ProgressSelection",
        select(*COMMAND_PROGRESS.c).where(COMMAND_PROGRESS.c.command_id == command_id),
    )


def transition_statement(
    current: StoredCommandProgress,
    became: CommandProgress,
    facts: TransitionFacts,
) -> Update:
    """Return a compare-and-set over the prior domain state and send count."""
    return (
        update(COMMAND_PROGRESS)
        .where(
            COMMAND_PROGRESS.c.command_id == current.identity.command_id,
            COMMAND_PROGRESS.c.state == current.progress.state.value,
            COMMAND_PROGRESS.c.send_count == current.progress.sends,
        )
        .values(
            state=became.state.value,
            send_count=became.sends,
            last_sent_at=facts.last_sent_at,
            deadline_at=facts.deadline_at,
            result_id=facts.result_id,
            updated_at=facts.updated_at,
        )
        .returning(COMMAND_PROGRESS.c.command_id)
    )


class ProgressRows(Protocol):
    """The selected durable command-progress row."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the row or ``None``."""


class CommandProgressSession(Protocol):
    """The injected SQLAlchemy operations command progress requires."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return one changed command identity or ``None``."""

    async def execute(self, statement: ProgressSelection, /) -> ProgressRows:
        """Return one selected command-progress row."""


async def initialize(
    session: CommandProgressSession,
    identity: CommandIdentity,
    updated_at: str,
) -> StoredCommandProgress:
    """Persist and return accepted, unsent progress for a newly validated command."""
    inserted = await session.scalar(initialize_statement(identity, updated_at))
    if inserted is None:
        raise CommandProgressError(CommandProgressRefusal.ALREADY_EXISTS, identity.command_id)
    return StoredCommandProgress(identity, INITIAL_PROGRESS, None, None, None, updated_at)


async def load(session: CommandProgressSession, command_id: str) -> StoredCommandProgress:
    """Load durable progress without coercing unknown or malformed persisted values."""
    selected = await session.execute(load_statement(command_id))
    row = selected.one_or_none()
    if row is None:
        raise CommandProgressError(CommandProgressRefusal.NOT_FOUND, command_id)
    return _stored(row, command_id)


def _stored(row: Sequence[object], command_id: str) -> StoredCommandProgress:
    """Validate and map one row in package-metadata order."""
    if len(row) != STORED_MEMBER_COUNT:
        raise CommandProgressError(CommandProgressRefusal.UNREADABLE_ROW, command_id)
    (
        stored_command_id,
        mission_id,
        drone_id,
        state_value,
        send_count,
        last_sent_at,
        deadline_at,
        result_id,
        updated_at,
    ) = row
    required_text = (stored_command_id, mission_id, drone_id, state_value, updated_at)
    optional_text = (last_sent_at, deadline_at, result_id)
    valid = (
        all(isinstance(value, str) for value in required_text)
        and all(value is None or isinstance(value, str) for value in optional_text)
        and type(send_count) is int
        and 0 <= send_count <= MAXIMUM_SEND_COUNT
    )
    if not valid:
        raise CommandProgressError(CommandProgressRefusal.UNREADABLE_ROW, command_id)
    try:
        state = CommandState(cast("str", state_value))
    except ValueError as error:
        raise CommandProgressError(CommandProgressRefusal.UNREADABLE_ROW, command_id) from error
    return StoredCommandProgress(
        identity=CommandIdentity(
            cast("str", stored_command_id), cast("str", mission_id), cast("str", drone_id)
        ),
        progress=CommandProgress(state, cast("int", send_count)),
        last_sent_at=cast("str | None", last_sent_at),
        deadline_at=cast("str | None", deadline_at),
        result_id=cast("str | None", result_id),
        updated_at=cast("str", updated_at),
    )


async def record_transition(
    session: CommandProgressSession,
    current: StoredCommandProgress,
    event: CommandEvent,
    budget: SendBudget,
    facts: TransitionFacts,
) -> StoredCommandProgress:
    """Persist exactly one domain-decided transition under a state-and-count CAS."""
    if not 0 <= current.progress.sends <= MAXIMUM_SEND_COUNT:
        raise CommandProgressError(
            CommandProgressRefusal.SEND_COUNT_OUT_OF_RANGE, current.progress.sends
        )
    became = advance(current.progress, event, budget)
    if not 0 <= became.sends <= MAXIMUM_SEND_COUNT:
        raise CommandProgressError(CommandProgressRefusal.SEND_COUNT_OUT_OF_RANGE, became.sends)
    changed = await session.scalar(transition_statement(current, became, facts))
    if changed is None:
        raise CommandProgressError(
            CommandProgressRefusal.STALE_PROGRESS, current.identity.command_id
        )
    return StoredCommandProgress(
        identity=current.identity,
        progress=became,
        last_sent_at=facts.last_sent_at,
        deadline_at=facts.deadline_at,
        result_id=facts.result_id,
        updated_at=facts.updated_at,
    )
