"""The durable idempotency claim, and what a repeat of it means.

[ADR-0092](../../../../docs/adr/0092-claim-an-idempotency-key-with-one-conflicting-insert.md)
makes the claim one statement. ``INSERT ... ON CONFLICT DO NOTHING RETURNING`` either takes the
key or reports that somebody else holds it, so there is no read-then-write and no lost-update
window -- which is why this needs none of the held row lock
[ADR-0091](../../../../docs/adr/0091-consume-an-approval-under-its-own-row-lock.md) requires for
consumption. The difference is that a claim has no caller decision in the middle of it.

**The meaning of a repeat is not decided here.**
``aerial_rescue_domain.idempotency.idempotency_decision`` owns it, and this module calls it. A
branch on ``IdempotencyKind`` in this file would be the decision table copied into a repository,
which `packages/store/AGENTS.md` forbids in as many words.

**What fails comparison is refused rather than answered.** A stored kind or body digest that
differs is not a repeat of anything: answering it from the stored record would return one
operation's result for another operation's request. The digest arrives already computed under
``aerial_rescue_contracts.digest.Context.IDEMPOTENCY_BODY``; this member compares it and holds no
canonicalizer of its own.

**A claim is not what makes an approval single-use.** A repeat consumption can arrive under a
fresh key, in which case this table has nothing to say and the approval row refuses it. The
``DENY`` outcome here is a second line of defence, not the line.

Nothing here opens a transaction. The caller's transaction is what makes the claim atomic with
the work it protects, and a claim rolled back with that work leaves the key claimable again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from aerial_rescue_domain.idempotency import (
    IdempotencyDecision,
    IdempotencyKind,
    idempotency_decision,
)
from sqlalchemy import LargeBinary, String, column, select, table, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.migration import IDEMPOTENCY_CLAIM_TABLE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

KEY_COLUMN: Final = "idempotency_key"
KIND_COLUMN: Final = "kind"
DIGEST_COLUMN: Final = "body_digest"
RESULT_COLUMN: Final = "result"

_KEY: Final = column(KEY_COLUMN, String)
_KIND: Final = column(KIND_COLUMN, String)
_DIGEST: Final = column(DIGEST_COLUMN, String)
_MISSION: Final = column("mission_id", String)
_RESULT: Final = column(RESULT_COLUMN, LargeBinary)
_CLAIMED_AT: Final = column("claimed_at", String)

_CLAIM_ROWS: Final = table(
    IDEMPOTENCY_CLAIM_TABLE, _KEY, _KIND, _DIGEST, _MISSION, _RESULT, _CLAIMED_AT
)

type ClaimSelection = Select[tuple[str, str, bytes]]
"""The stored members a repeat reads, in the order :func:`claim` maps them positionally."""

type ClaimRead = ClaimSelection
"""What runs for a row, named rather than left as anything executable."""


class StoredClaimRefusal(Enum):
    """Why a claim or a result did not happen."""

    CLAIM_VANISHED = (
        "the key conflicted and then held no row, so the claim is refused rather than retried "
        "as a first sight of an identifier somebody else has already used"
    )
    UNKNOWN_KIND = (
        "the stored kind is not one the domain names, so the record is refused rather than "
        "mapped onto the nearest kind that is"
    )
    KIND_MISMATCH = (
        "the key is claimed for the other operation; a command and an approval consumption "
        "have different repeat outcomes and one may never answer for the other"
    )
    BODY_MISMATCH = (
        "the key is claimed for a different request body, so this is not a repeat and is "
        "refused rather than answered from another request's record"
    )
    RESULT_NOT_RECORDED = (
        "the command holding this key has not recorded a result yet, so there is nothing to "
        "return and dispatching again would be the duplicate the key exists to prevent"
    )
    UNREADABLE_RESULT = (
        "the stored result is not the bytes it was written as, so it is refused rather than "
        "coerced into something a caller would treat as the prior answer"
    )
    RESULT_ALREADY_RECORDED = (
        "a result is already stored for this key and is never overwritten; the first answer is "
        "the one every repeat receives"
    )


class StoredClaimError(StoreError):
    """A claim this module refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class StoredClaim:
    """What a caller presents to claim a key, exactly as the columns hold it."""

    idempotency_key: str
    kind: IdempotencyKind
    body_digest: str
    mission_id: str
    claimed_at: str


@dataclass(frozen=True)
class ClaimOutcome:
    """What the caller does next, and the prior result when there is one to return."""

    decision: IdempotencyDecision
    result: bytes | None


def claim_statement(request: StoredClaim) -> Insert:
    """Return the statement that takes the key, or changes nothing because somebody holds it.

    Args:
        request: The claim, with every value already accepted at its own trust boundary.

    Returns:
        The conditional insert. It carries no result, because none is known yet, and returns the
        key it wrote so a first claim is visible without a second read.
    """
    proposed = postgresql_insert(_CLAIM_ROWS).values(
        idempotency_key=request.idempotency_key,
        kind=request.kind.value,
        body_digest=request.body_digest,
        mission_id=request.mission_id,
        claimed_at=request.claimed_at,
    )
    taken = proposed.on_conflict_do_nothing(index_elements=[_CLAIM_ROWS.c[KEY_COLUMN]])
    return taken.returning(_CLAIM_ROWS.c[KEY_COLUMN])


def stored_statement(idempotency_key: str) -> ClaimSelection:
    """Return the statement that reads what the holder of ``idempotency_key`` claimed."""
    selection = select(_KIND, _DIGEST, _RESULT)
    return selection.where(_CLAIM_ROWS.c[KEY_COLUMN] == idempotency_key)


def result_statement(idempotency_key: str, result: bytes) -> Update:
    """Return the statement that records a result once, and never a second time.

    Args:
        idempotency_key: The claim being answered.
        result: The canonical bytes of the answer every repeat will receive.

    Returns:
        The conditional update. The guard is in the ``WHERE`` clause rather than in a preceding
        read, so two callers cannot both find the result absent.
    """
    return (
        update(_CLAIM_ROWS)
        .where(_CLAIM_ROWS.c[KEY_COLUMN] == idempotency_key)
        .where(_CLAIM_ROWS.c[RESULT_COLUMN].is_(None))
        .values(result=result)
        .returning(_CLAIM_ROWS.c[KEY_COLUMN])
    )


class SelectedRows(Protocol):
    """What a repeat needs of a result: the stored row, or nothing."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the one row the statement selected, or ``None`` if it selected none."""


class ClaimSession(Protocol):
    """What this repository needs of the caller's session, and nothing more."""

    async def execute(self, statement: ClaimRead, /) -> SelectedRows:
        """Run the statement and return its rows."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return the single value the statement produces, or ``None`` if it produced no row."""


async def claim(session: ClaimSession, request: StoredClaim) -> ClaimOutcome:
    """Take the key, or say what the operation holding it means for this caller.

    Args:
        session: The caller's open session. The claim is atomic with the work it protects
            because that transaction spans both, and a rollback releases the key.
        request: The claim being presented.

    Returns:
        The decision the domain reaches for this kind, with the prior result when the decision
        is to return one.

    Raises:
        StoredClaimError: With ``CLAIM_VANISHED``, ``UNKNOWN_KIND``, ``KIND_MISMATCH``,
            ``BODY_MISMATCH``, ``RESULT_NOT_RECORDED``, or ``UNREADABLE_RESULT``. Each is a
            refusal rather than a repeat outcome: none of them is this request happening a
            second time.
    """
    taken = await session.scalar(claim_statement(request))
    if taken is not None:
        return ClaimOutcome(idempotency_decision(request.kind, known=False), None)
    return _repeat(request, await _stored(session, request.idempotency_key))


async def record_result(session: ClaimSession, idempotency_key: str, result: bytes) -> None:
    """Record the answer every repeat of ``idempotency_key`` will receive.

    Args:
        session: The caller's open session.
        idempotency_key: The claim being answered.
        result: The canonical bytes of the answer.

    Raises:
        StoredClaimError: With ``RESULT_ALREADY_RECORDED`` when the conditional write matched no
            row, because a stored answer is never replaced by a later one.
    """
    recorded = await session.scalar(result_statement(idempotency_key, result))
    if recorded is None:
        raise StoredClaimError(StoredClaimRefusal.RESULT_ALREADY_RECORDED, idempotency_key)


async def _stored(session: ClaimSession, idempotency_key: str) -> Sequence[object]:
    """Return the row behind a conflict, refusing a conflict that now holds none."""
    found = await session.execute(stored_statement(idempotency_key))
    row = found.one_or_none()
    if row is None:
        raise StoredClaimError(StoredClaimRefusal.CLAIM_VANISHED, idempotency_key)
    return row


def _repeat(request: StoredClaim, row: Sequence[object]) -> ClaimOutcome:
    """Map a stored row into this caller's outcome, refusing everything that is not a repeat."""
    stored_kind, stored_digest, stored_result = row
    try:
        kind = IdempotencyKind(stored_kind)
    except ValueError as unknown:
        raise StoredClaimError(StoredClaimRefusal.UNKNOWN_KIND, stored_kind) from unknown
    if kind is not request.kind:
        raise StoredClaimError(StoredClaimRefusal.KIND_MISMATCH, request.kind)
    if str(stored_digest) != request.body_digest:
        raise StoredClaimError(StoredClaimRefusal.BODY_MISMATCH, request.body_digest)
    decision = idempotency_decision(kind, known=True)
    if decision is IdempotencyDecision.DENY:
        return ClaimOutcome(decision, None)
    if stored_result is None:
        raise StoredClaimError(StoredClaimRefusal.RESULT_NOT_RECORDED, request.idempotency_key)
    if not isinstance(stored_result, bytes):
        raise StoredClaimError(StoredClaimRefusal.UNREADABLE_RESULT, type(stored_result).__name__)
    return ClaimOutcome(decision, stored_result)
