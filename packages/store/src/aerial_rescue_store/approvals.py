"""The durable approval record, and the one path by which it becomes executed.

[ADR-0091](../../../../docs/adr/0091-consume-an-approval-under-its-own-row-lock.md) selects the
mechanism and rejects three measured alternatives. Two of its statements are the whole decision:
the row is taken with ``SELECT ... FOR UPDATE`` -- plain, so a second consumer *waits* rather
than being refused before the wait or handed no row at all -- and the write is conditional on
the row still being approved.

The lock is held across the caller's clock reads and its call into
``aerial_rescue_domain.approvals.consume``, because
[ADR-0040](../../../../docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)
requires the decision to be made inside the transaction. That is the gap a lost update would
live in, and the lock is what closes it: under ``READ COMMITTED`` a second consumer that waited
re-reads the committed row, is handed ``executed``, and is refused by the protocol itself.

**The load is keyed on the proposal alone.** Adding the mission to the predicate would turn the
domain's ``MISSION`` refusal into "no such approval" and destroy the refusal order ADR-0040
fixes. The mission is carried on the record so the domain can judge it; it is not a filter.

**``EXECUTED`` has exactly one way in.** ``record`` refuses it and ``persist_consumed`` requires
it, and the latter's write matches only a row that is still approved. This is the shape
`packages/store/AGENTS.md` asks for when it forbids "a generic repository update that turns a
caller-supplied state into dispatch authority". It bounds one direct-write path rather than the
class: a writer holding database credentials is catalogue case B24 and is not closed here.

Nothing here opens a transaction. As with the audit append, the caller's transaction is what
makes the guarantee, and this module refuses to own it.

Every statement is a typed expression over the complete package-owned table metadata. Importing
that metadata emits no DDL; Alembic remains the only schema authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from aerial_rescue_domain.approvals import ApprovalState
from sqlalchemy import insert, select, update

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import APPROVAL

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

MISSION_COLUMN: Final = "mission_id"
PROPOSAL_COLUMN: Final = "proposal_id"
STATE_COLUMN: Final = "state"

_APPROVAL_ROWS: Final = APPROVAL
_MISSION: Final = APPROVAL.c[MISSION_COLUMN]
_PROPOSAL: Final = APPROVAL.c[PROPOSAL_COLUMN]
_STATE: Final = APPROVAL.c[STATE_COLUMN]
_OPERATOR: Final = APPROVAL.c.operator_identity
_ISSUED_WALL: Final = APPROVAL.c.issued_wall
_ISSUED_MONOTONIC: Final = APPROVAL.c.issued_monotonic_milliseconds
_TIME_TO_LIVE: Final = APPROVAL.c.time_to_live_milliseconds
_DIGEST: Final = APPROVAL.c.proposal_digest

DECISION_STATES: Final = frozenset({ApprovalState.APPROVED, ApprovalState.REJECTED})
"""What an operator's decision may be. Every other state is reached by another party."""

type ApprovalSelection = Select[tuple[str, str, str, str, str, int, int, str]]
"""The selected columns, in the order :func:`load_for_update` maps them positionally."""

type ApprovalRead = ApprovalSelection | Insert
"""What runs for a row or for an effect, named rather than left as anything executable."""


class StoredApprovalRefusal(Enum):
    """Why a durable approval operation did not happen."""

    NOT_A_DECISION = (
        "only an operator's own decision is recorded here; every other state is reached by "
        "supersession, expiry, or the guarded consumption that owns the executed state"
    )
    NOT_FOUND = "no approval is stored for that proposal, which is not the same as a denial"
    UNKNOWN_STATE = (
        "the stored state is not one the approval protocol names, so the record is refused "
        "rather than mapped onto the nearest state that is"
    )
    NOT_EXECUTED = (
        "only a record the domain has consumed is persisted as consumed; this repository never "
        "converts a caller-supplied state into dispatch authority"
    )
    NOT_CONSUMABLE = (
        "the row was no longer approved when the write reached it, so nothing changed and the "
        "attempt is a denial rather than a silent success"
    )


class StoredApprovalError(StoreError):
    """An approval operation this module refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class StoredApproval:
    """One durable decision record, exactly as the columns hold it.

    The clock readings stay in their persisted forms -- the wall reading as the canonical
    millisecond text and the monotonic reading as a duration in milliseconds -- because the
    caller owns the canonical representation and this member persists what it accepted. Mapping
    them into a domain ``Approval`` is the caller's step, not this one's.
    """

    mission_id: str
    proposal_id: str
    state: ApprovalState
    operator_identity: str
    issued_wall: str
    issued_monotonic_milliseconds: int
    time_to_live_milliseconds: int
    proposal_digest: str


def lock_statement(proposal_id: str) -> ApprovalSelection:
    """Return the statement that takes one approval row and holds it until commit.

    Args:
        proposal_id: The proposal whose approval is being consumed.

    Returns:
        The locking select. It is plain: ``NOWAIT`` would refuse a second consumer before the
        wait ADR-0090 bounds, and ``SKIP LOCKED`` would hand it no row, which is
        indistinguishable from an approval that was never issued.
    """
    selection = select(
        _MISSION,
        _PROPOSAL,
        _STATE,
        _OPERATOR,
        _ISSUED_WALL,
        _ISSUED_MONOTONIC,
        _TIME_TO_LIVE,
        _DIGEST,
    )
    taken = selection.where(_APPROVAL_ROWS.c[PROPOSAL_COLUMN] == proposal_id)
    return taken.with_for_update()


def record_statement(approval: StoredApproval) -> Insert:
    """Return the statement that writes an operator's decision.

    Args:
        approval: The decision, with every value already accepted at its own trust boundary.

    Returns:
        The insert. It carries no default and no generated value, so the row is exactly what the
        caller accepted.
    """
    return insert(_APPROVAL_ROWS).values(
        mission_id=approval.mission_id,
        proposal_id=approval.proposal_id,
        state=approval.state.value,
        operator_identity=approval.operator_identity,
        issued_wall=approval.issued_wall,
        issued_monotonic_milliseconds=approval.issued_monotonic_milliseconds,
        time_to_live_milliseconds=approval.time_to_live_milliseconds,
        proposal_digest=approval.proposal_digest,
    )


def consume_statement(consumed: StoredApproval) -> Update:
    """Return the statement that moves one still-approved row to executed.

    Args:
        consumed: The record the domain returned from consumption.

    Returns:
        The conditional update. It returns the proposal it changed, so a write that matched no
        row is visible as a missing value rather than inferred from a row count.
    """
    return (
        update(_APPROVAL_ROWS)
        .where(_APPROVAL_ROWS.c[PROPOSAL_COLUMN] == consumed.proposal_id)
        .where(_APPROVAL_ROWS.c[STATE_COLUMN] == ApprovalState.APPROVED.value)
        .values(state=consumed.state.value)
        .returning(_APPROVAL_ROWS.c[PROPOSAL_COLUMN])
    )


class SelectedRows(Protocol):
    """What loading needs of a result: the single row, or nothing."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the one row the statement selected, or ``None`` if it selected none."""


class ApprovalSession(Protocol):
    """What this repository needs of the caller's session, and nothing more.

    ``execute`` takes the two statements run for a row or for an effect, and ``scalar`` the one
    run for the single value that says whether the conditional write matched anything.
    """

    async def execute(self, statement: ApprovalRead, /) -> SelectedRows:
        """Run the statement and return its rows."""

    async def scalar(self, statement: Update, /) -> object:
        """Return the single value the statement produces, or ``None`` if it produced no row."""


async def record(session: ApprovalSession, approval: StoredApproval) -> None:
    """Write an operator's decision, refusing any state that is not one.

    Args:
        session: The caller's open session; its transaction and its commit belong to the caller.
        approval: The decision to persist.

    Raises:
        StoredApprovalError: With ``NOT_A_DECISION`` when the state is not one an operator
            reaches. Writing ``EXECUTED`` here would create a second path to dispatch authority,
            which is the one thing this module exists to prevent.
    """
    if approval.state not in DECISION_STATES:
        raise StoredApprovalError(StoredApprovalRefusal.NOT_A_DECISION, approval.state)
    await session.execute(record_statement(approval))


async def load_for_update(session: ApprovalSession, proposal_id: str) -> StoredApproval:
    """Take the approval row for ``proposal_id`` and hold it until the caller's transaction ends.

    Args:
        session: The caller's open session. The lock this takes is released by that
            transaction's commit or rollback and by nothing else.
        proposal_id: The proposal being consumed. The mission is deliberately not part of the
            predicate: the domain judges it, in the order ADR-0040 fixes.

    Returns:
        The record, mapped into the protocol's own closed state set.

    Raises:
        StoredApprovalError: With ``NOT_FOUND`` when no such approval is stored, or
            ``UNKNOWN_STATE`` when the persisted state is outside the protocol.
    """
    result = await session.execute(lock_statement(proposal_id))
    row = result.one_or_none()
    if row is None:
        raise StoredApprovalError(StoredApprovalRefusal.NOT_FOUND, proposal_id)
    return _stored(row)


async def persist_consumed(session: ApprovalSession, consumed: StoredApproval) -> None:
    """Persist a consumed approval, refusing a row that was no longer approved.

    Args:
        session: The caller's open session, holding the lock ``load_for_update`` took.
        consumed: What ``aerial_rescue_domain.approvals.consume`` returned.

    Raises:
        StoredApprovalError: With ``NOT_EXECUTED`` when the record is not a consumed one, or
            ``NOT_CONSUMABLE`` when the conditional write matched no row. The second is
            unreachable through this module's own API once the row is locked; it is what stops a
            caller that reached here without taking the lock.
    """
    if consumed.state is not ApprovalState.EXECUTED:
        raise StoredApprovalError(StoredApprovalRefusal.NOT_EXECUTED, consumed.state)
    changed = await session.scalar(consume_statement(consumed))
    if changed is None:
        raise StoredApprovalError(StoredApprovalRefusal.NOT_CONSUMABLE, consumed.proposal_id)


def _stored(row: Sequence[object]) -> StoredApproval:
    """Map one selected row into the typed record, failing closed on a state outside the set."""
    mission, proposal, state, operator, wall, monotonic, time_to_live, digest = row
    try:
        protocol_state = ApprovalState(state)
    except ValueError as unknown:
        raise StoredApprovalError(StoredApprovalRefusal.UNKNOWN_STATE, state) from unknown
    return StoredApproval(
        mission_id=str(mission),
        proposal_id=str(proposal),
        state=protocol_state,
        operator_identity=str(operator),
        issued_wall=str(wall),
        issued_monotonic_milliseconds=int(str(monotonic)),
        time_to_live_milliseconds=int(str(time_to_live)),
        proposal_digest=str(digest),
    )
