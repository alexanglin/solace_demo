"""Durable per-drone command receipts for effect-once result replay.

The caller claims a command identity inside the same transaction that applies the simulated
drone effect.  Completion stores the exact result and applied sequence before that transaction
commits.  A later delivery, including one after process restart, returns those exact bytes and
never asks the caller to apply the effect again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import DRONE_COMMAND_RECEIPT

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

STORED_MEMBER_COUNT: Final = 5
type ReceiptSelection = Select[tuple[object, ...]]


class ReceiptDecision(Enum):
    """Whether this transaction owns a new effect or observed a completed duplicate."""

    CLAIMED = "claimed before effect"
    DUPLICATE = "exact completed duplicate"


class ReceiptRefusal(Enum):
    """Why a durable receipt cannot authorize work or replay a prior result."""

    CLAIM_VANISHED = "the conflicting command receipt no longer has a durable row"
    IDENTITY_CONFLICT = "the command receipt identity is bound to another mission"
    DIGEST_CONFLICT = "the command receipt identity was reused with different canonical bytes"
    INCOMPLETE = "the durable command receipt has no committed effect result"
    UNREADABLE_ROW = "the durable command receipt does not match its migrated typed shape"
    NOT_CLAIMED = "the command receipt is missing, conflicting, or already completed"
    INVALID_SEQUENCE = "the applied effect sequence must be a nonnegative integer"


class ReceiptError(StoreError):
    """A durable command-receipt operation this package refuses."""


@dataclass(frozen=True)
class CommandReceiptIdentity:
    """The drone-scoped command identity and canonical bytes it binds."""

    drone_id: str
    command_id: str
    mission_id: str
    command_digest: str


@dataclass(frozen=True)
class ReceiptOutcome:
    """A claim decision and exact prior completion facts for a duplicate."""

    decision: ReceiptDecision
    result: bytes | None
    applied_sequence: int | None
    processed_at: str | None


@dataclass(frozen=True)
class CompletedReceipt:
    """The exact effect result committed for one claimed command."""

    identity: CommandReceiptIdentity
    result: bytes
    applied_sequence: int
    processed_at: str


def claim_statement(identity: CommandReceiptIdentity) -> Insert:
    """Return an insert that claims identity and digest before any effect is applied."""
    proposed = postgresql_insert(DRONE_COMMAND_RECEIPT).values(
        drone_id=identity.drone_id,
        command_id=identity.command_id,
        mission_id=identity.mission_id,
        command_digest=identity.command_digest,
        result=None,
        applied_sequence=None,
        processed_at=None,
    )
    claimed = proposed.on_conflict_do_nothing(
        index_elements=[DRONE_COMMAND_RECEIPT.c.drone_id, DRONE_COMMAND_RECEIPT.c.command_id]
    )
    return claimed.returning(DRONE_COMMAND_RECEIPT.c.command_id)


def stored_statement(identity: CommandReceiptIdentity) -> ReceiptSelection:
    """Return mission, digest, and completion facts for one conflicting identity."""
    return cast(
        "ReceiptSelection",
        select(
            DRONE_COMMAND_RECEIPT.c.mission_id,
            DRONE_COMMAND_RECEIPT.c.command_digest,
            DRONE_COMMAND_RECEIPT.c.result,
            DRONE_COMMAND_RECEIPT.c.applied_sequence,
            DRONE_COMMAND_RECEIPT.c.processed_at,
        ).where(
            DRONE_COMMAND_RECEIPT.c.drone_id == identity.drone_id,
            DRONE_COMMAND_RECEIPT.c.command_id == identity.command_id,
        ),
    )


def completion_statement(
    identity: CommandReceiptIdentity,
    result: bytes,
    applied_sequence: int,
    processed_at: str,
) -> Update:
    """Return a compare-and-set that completes one exact unfinished claim once."""
    return (
        update(DRONE_COMMAND_RECEIPT)
        .where(
            DRONE_COMMAND_RECEIPT.c.drone_id == identity.drone_id,
            DRONE_COMMAND_RECEIPT.c.command_id == identity.command_id,
            DRONE_COMMAND_RECEIPT.c.mission_id == identity.mission_id,
            DRONE_COMMAND_RECEIPT.c.command_digest == identity.command_digest,
            DRONE_COMMAND_RECEIPT.c.result.is_(None),
            DRONE_COMMAND_RECEIPT.c.applied_sequence.is_(None),
            DRONE_COMMAND_RECEIPT.c.processed_at.is_(None),
        )
        .values(
            result=result,
            applied_sequence=applied_sequence,
            processed_at=processed_at,
        )
        .returning(DRONE_COMMAND_RECEIPT.c.command_id)
    )


class ReceiptRows(Protocol):
    """The selected durable receipt after a claim conflict."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the row or ``None``."""


class ReceiptSession(Protocol):
    """The injected SQLAlchemy operations durable receipts require."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return one changed command identity or ``None``."""

    async def execute(self, statement: ReceiptSelection, /) -> ReceiptRows:
        """Return one selected receipt row."""


async def claim(session: ReceiptSession, identity: CommandReceiptIdentity) -> ReceiptOutcome:
    """Claim a new effect or return exact completion facts for an exact duplicate."""
    claimed = await session.scalar(claim_statement(identity))
    if claimed is not None:
        return ReceiptOutcome(ReceiptDecision.CLAIMED, None, None, None)
    selected = await session.execute(stored_statement(identity))
    row = selected.one_or_none()
    if row is None:
        raise ReceiptError(ReceiptRefusal.CLAIM_VANISHED, identity.command_id)
    return _duplicate(identity, row)


def _duplicate(identity: CommandReceiptIdentity, row: Sequence[object]) -> ReceiptOutcome:
    """Validate a conflicting row as one exact completed duplicate."""
    if len(row) != STORED_MEMBER_COUNT:
        raise ReceiptError(ReceiptRefusal.UNREADABLE_ROW, identity.command_id)
    mission_id, command_digest, result, applied_sequence, processed_at = row
    if not isinstance(mission_id, str) or not isinstance(command_digest, str):
        raise ReceiptError(ReceiptRefusal.UNREADABLE_ROW, identity.command_id)
    if mission_id != identity.mission_id:
        raise ReceiptError(ReceiptRefusal.IDENTITY_CONFLICT, identity.command_id)
    if command_digest != identity.command_digest:
        raise ReceiptError(ReceiptRefusal.DIGEST_CONFLICT, identity.command_id)
    if result is None and applied_sequence is None and processed_at is None:
        raise ReceiptError(ReceiptRefusal.INCOMPLETE, identity.command_id)
    valid = (
        isinstance(result, bytes)
        and type(applied_sequence) is int
        and applied_sequence >= 0
        and isinstance(processed_at, str)
    )
    if not valid:
        raise ReceiptError(ReceiptRefusal.UNREADABLE_ROW, identity.command_id)
    return ReceiptOutcome(
        ReceiptDecision.DUPLICATE,
        cast("bytes", result),
        cast("int", applied_sequence),
        cast("str", processed_at),
    )


async def complete(
    session: ReceiptSession,
    identity: CommandReceiptIdentity,
    result: bytes,
    applied_sequence: int,
    processed_at: str,
) -> CompletedReceipt:
    """Complete the claimed effect once inside the caller's open transaction."""
    if type(applied_sequence) is not int or applied_sequence < 0:
        raise ReceiptError(ReceiptRefusal.INVALID_SEQUENCE, applied_sequence)
    completed = await session.scalar(
        completion_statement(identity, result, applied_sequence, processed_at)
    )
    if completed is None:
        raise ReceiptError(ReceiptRefusal.NOT_CLAIMED, identity.command_id)
    return CompletedReceipt(identity, result, applied_sequence, processed_at)
