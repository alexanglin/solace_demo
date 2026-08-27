"""The central command outbox: staging under a counted bound, and moving along one edge.

[ADR-0093](../../../../docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md) decides
the bound, the states, and what an overflow does. Two properties of this module are that record
made executable.

**The capacity guard is inside the staging statement.** A count read first and inserted against
afterwards is the check-then-write `packages/store/AGENTS.md` forbids, and under the
`READ COMMITTED` that
[ADR-0089](../../../../docs/adr/0089-state-read-committed-rather-than-inherit-it.md) states, two
callers could both find room. One statement that carries its own count cannot be
raced that way -- though it can still be overshot by one record per concurrently staging session,
which the pool bounds and ADR-0093 states as a consequence rather than hiding.

**No state is ever accepted from a caller.** Staging writes ``STAGED`` because staging is what
happened, and a move is asked for as the state it came from plus what the broker reported: this
module hands that pair to ``aerial_rescue_domain.outbox.transition`` and writes what the domain
returns. An edge the table does not have is the domain's refusal, raised before any statement
runs. The write is then conditional on the row still holding the state the caller moved from, so
a record that moved on in between is refused rather than overwritten.

**An overflow writes nothing and audits nothing.** The continuity-breach audit record belongs to
the caller, in its own transaction, because ADR-0006's atomic set does not include the audit
append and an append made here would roll back with the refusal it exists to record.

Nothing here opens a transaction, and nothing here publishes. A publisher reads committed
records; this module never hands one an uncommitted object.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_domain.outbox import INITIAL_STATE, OutboxEvent, OutboxState, transition
from sqlalchemy import func, insert, literal, select, update

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import COMMAND_OUTBOX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

MAXIMUM_UNCONFIRMED_RECORDS: Final = 500
"""ADR-0093's bound: the workload ADR-0084's instrument uses and the backlog probe measured."""

COMMAND_PUBLICATION_BATCH_SIZE: Final = 50
"""The maximum oldest-first command rows one connected recovery iteration may read."""

COMMAND_COLUMN: Final = "command_id"
STATE_COLUMN: Final = "state"

_OUTBOX_ROWS: Final = COMMAND_OUTBOX

_STAGED_COLUMNS: Final = (
    COMMAND_COLUMN,
    "mission_id",
    "drone_id",
    "payload",
    STATE_COLUMN,
    "correlation_id",
    "causation_id",
    "traceparent",
    "staged_at",
)

STORED_MEMBER_COUNT: Final = len(_STAGED_COLUMNS)
type PendingSelection = Select[tuple[object, ...]]


class StagedCommandRefusal(Enum):
    """Why a durable outbox operation did not happen."""

    AT_CAPACITY = (
        "the outbox already holds every unconfirmed record ADR-0093 allows, so the command is "
        "refused rather than staged; a critical record is never silently dropped"
    )
    NOT_IN_EXPECTED_STATE = (
        "the record was no longer in the state the caller moved it from, so nothing changed and "
        "a publication outcome computed against a stale state was not written over a newer one"
    )
    INVALID_READ_LIMIT = "the command-outbox read limit must be between one and fifty"
    TERMINAL_READ = "confirmed commands are terminal and cannot enter a recovery batch"
    READ_LIMIT_EXCEEDED = "the command-outbox read returned more rows than its requested bound"
    UNREADABLE_ROW = "a command-outbox row does not match its migrated typed shape"


class StagedCommandError(StoreError):
    """An outbox operation this module refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class StagedCommand:
    """One command to publish, exactly as the columns hold it.

    There is no state member. A record exists because staging happened, so its first state is
    not a caller's to choose.
    """

    command_id: str
    mission_id: str
    drone_id: str
    payload: bytes
    correlation_id: str
    causation_id: str | None
    traceparent: str
    staged_at: str


@dataclass(frozen=True)
class CommandOutboxRecord:
    """One exact command and the recoverable publication state read with it."""

    command: StagedCommand
    state: OutboxState


def stage_statement(command: StagedCommand) -> Insert:
    """Return the statement that stages ``command`` if the outbox has room for it.

    Args:
        command: The command to publish, with every value already accepted at its own trust
            boundary.

    Returns:
        The conditional insert. Its row count is evaluated inside the statement, so no caller
        can read a count and act on it afterwards, and it returns the identifier it wrote so a
        refused write is a missing value rather than an inferred one.
    """
    unconfirmed = (
        select(func.count())
        .select_from(_OUTBOX_ROWS)
        .where(_OUTBOX_ROWS.c[STATE_COLUMN] != OutboxState.CONFIRMED.value)
        .scalar_subquery()
    )
    proposed = select(
        literal(command.command_id),
        literal(command.mission_id),
        literal(command.drone_id),
        literal(command.payload),
        literal(INITIAL_STATE.value),
        literal(command.correlation_id),
        literal(command.causation_id),
        literal(command.traceparent),
        literal(command.staged_at),
    ).where(unconfirmed < MAXIMUM_UNCONFIRMED_RECORDS)
    written = insert(_OUTBOX_ROWS).from_select(list(_STAGED_COLUMNS), proposed)
    return written.returning(_OUTBOX_ROWS.c[COMMAND_COLUMN])


def publication_statement(command_id: str, was: OutboxState, became: OutboxState) -> Update:
    """Return the statement that moves one record from ``was`` to ``became``.

    Args:
        command_id: The staged command whose publication outcome is being recorded.
        was: The state the caller read before deciding. The write is conditional on it, so a
            record that moved on in the meantime is not overwritten.
        became: What the domain's table returned for that state and the reported outcome.

    Returns:
        The compare-and-set. It returns the identifier it changed, so a write that matched no
        row is visible rather than inferred.
    """
    return (
        update(_OUTBOX_ROWS)
        .where(_OUTBOX_ROWS.c[COMMAND_COLUMN] == command_id)
        .where(_OUTBOX_ROWS.c[STATE_COLUMN] == was.value)
        .values(state=became.value)
        .returning(_OUTBOX_ROWS.c[COMMAND_COLUMN])
    )


def pending_statement(state: OutboxState, limit: int) -> PendingSelection:
    """Return recoverable rows in deterministic oldest-first order."""
    statement = (
        select(*_OUTBOX_ROWS.c)
        .where(_OUTBOX_ROWS.c[STATE_COLUMN] == state.value)
        .order_by(_OUTBOX_ROWS.c.staged_at, _OUTBOX_ROWS.c.command_id)
        .limit(limit)
    )
    return cast("PendingSelection", statement)


class OutboxSession(Protocol):
    """What this repository needs of the caller's session, and nothing more."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return the single value the statement produces, or ``None`` if it produced no row."""


class PendingRows(Protocol):
    """The bounded ordered command rows selected for recovery."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return every selected row in database order."""


class OutboxReadSession(Protocol):
    """The one SQLAlchemy read command-outbox recovery requires."""

    async def execute(self, statement: PendingSelection, /) -> PendingRows:
        """Run one bounded state-scoped selection."""


async def stage(session: OutboxSession, command: StagedCommand) -> None:
    """Stage ``command`` for publication, refusing rather than exceeding the bound.

    Args:
        session: The caller's open session. Staging is atomic with the approval consumption and
            the idempotency claim because that transaction spans all three.
        command: The command to publish.

    Raises:
        StagedCommandError: With ``AT_CAPACITY`` when the outbox is full. Nothing is written,
            and the continuity-breach audit record is the caller's to append in a transaction of
            its own -- one appended here would roll back with this refusal.
    """
    staged = await session.scalar(stage_statement(command))
    if staged is None:
        raise StagedCommandError(StagedCommandRefusal.AT_CAPACITY, command.command_id)


async def record_publication(
    session: OutboxSession, command_id: str, was: OutboxState, event: OutboxEvent
) -> OutboxState:
    """Record what the broker reported about one staged command.

    Args:
        session: The caller's open session.
        command_id: The staged command.
        was: The state the caller read before the publication attempt.
        event: What the broker adapter reported. A refused publication is not one of these:
            it leaves the record staged and retryable.

    Returns:
        The state the record now holds.

    Raises:
        OutboxError: Unchanged from the domain, when the table has no such edge. It is raised
            before any statement runs, so an illegal move writes nothing.
        StagedCommandError: With ``NOT_IN_EXPECTED_STATE`` when the compare-and-set matched no
            row, because the record had already moved on.
    """
    became = transition(was, event)
    moved = await session.scalar(publication_statement(command_id, was, became))
    if moved is None:
        raise StagedCommandError(StagedCommandRefusal.NOT_IN_EXPECTED_STATE, command_id)
    return became


async def pending(
    session: OutboxReadSession,
    state: OutboxState,
    limit: int,
) -> tuple[CommandOutboxRecord, ...]:
    """Read a bounded batch of staged or ambiguous commands for one recovery action."""
    if type(limit) is not int or not 1 <= limit <= COMMAND_PUBLICATION_BATCH_SIZE:
        raise StagedCommandError(StagedCommandRefusal.INVALID_READ_LIMIT, "redacted-limit")
    if state is OutboxState.CONFIRMED:
        raise StagedCommandError(StagedCommandRefusal.TERMINAL_READ, state)
    selected = await session.execute(pending_statement(state, limit))
    rows = selected.all()
    if len(rows) > limit:
        raise StagedCommandError(StagedCommandRefusal.READ_LIMIT_EXCEEDED, "redacted-batch")
    return tuple(_stored(row, state) for row in rows)


def _stored(row: Sequence[object], expected_state: OutboxState) -> CommandOutboxRecord:
    """Map one command row without coercing its identity, bytes, or publication state."""
    if len(row) != STORED_MEMBER_COUNT:
        raise StagedCommandError(StagedCommandRefusal.UNREADABLE_ROW, "redacted-row")
    (
        command_id,
        mission_id,
        drone_id,
        payload,
        state_value,
        correlation_id,
        causation_id,
        traceparent,
        staged_at,
    ) = row
    required_text = (
        command_id,
        mission_id,
        drone_id,
        state_value,
        correlation_id,
        traceparent,
        staged_at,
    )
    valid = (
        all(isinstance(value, str) for value in required_text)
        and isinstance(payload, bytes)
        and (causation_id is None or isinstance(causation_id, str))
        and state_value == expected_state.value
    )
    if not valid:
        raise StagedCommandError(StagedCommandRefusal.UNREADABLE_ROW, "redacted-row")
    command = StagedCommand(
        command_id=cast("str", command_id),
        mission_id=cast("str", mission_id),
        drone_id=cast("str", drone_id),
        payload=cast("bytes", payload),
        correlation_id=cast("str", correlation_id),
        causation_id=cast("str | None", causation_id),
        traceparent=cast("str", traceparent),
        staged_at=cast("str", staged_at),
    )
    return CommandOutboxRecord(command, expected_state)
