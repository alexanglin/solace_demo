"""Durable broker identity claims and exact duplicate outcomes.

The claim insert runs before domain effects in the caller's transaction.  Completion updates
that same row after those effects, and the transaction commits both or neither.  A conflicting
insert waits for the first transaction; after it commits, an exact redelivery receives the
stored result while changed bytes under the same identity are refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import BROKER_INBOX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

type StoredSelection = Select[tuple[str, bytes | None, str | None]]

STORED_MEMBER_COUNT: Final = 3


class InboxDecision(Enum):
    """Whether this transaction owns new work or observed an exact prior result."""

    CLAIMED = "claimed"
    DUPLICATE = "exact duplicate"


class InboxRefusal(Enum):
    """Why a broker identity cannot be processed as new work or an exact duplicate."""

    CLAIM_VANISHED = "the conflicting inbox identity no longer has a durable row"
    DIGEST_CONFLICT = "the inbox identity was reused with different canonical bytes"
    INCOMPLETE = "the durable inbox row has no committed processing result"
    UNREADABLE_RESULT = "the durable inbox result is not its declared binary form"
    NOT_CLAIMED = "the inbox claim was missing, conflicting, or already completed"


class InboxError(StoreError):
    """An inbox operation this repository refuses."""


@dataclass(frozen=True)
class InboxIdentity:
    """The identity and canonical digest claimed before applying one broker effect."""

    consumer: str
    source: str
    event_id: str
    mission_id: str
    canonical_digest: str


@dataclass(frozen=True)
class InboxOutcome:
    """The claim decision and the exact prior result for an exact duplicate."""

    decision: InboxDecision
    result: bytes | None


def claim_statement(identity: InboxIdentity) -> Insert:
    """Return one insert that claims the complete broker message identity."""
    proposed = postgresql_insert(BROKER_INBOX).values(
        consumer=identity.consumer,
        source=identity.source,
        event_id=identity.event_id,
        mission_id=identity.mission_id,
        canonical_digest=identity.canonical_digest,
        result=None,
        processed_at=None,
    )
    claimed = proposed.on_conflict_do_nothing(
        index_elements=[
            BROKER_INBOX.c.consumer,
            BROKER_INBOX.c.source,
            BROKER_INBOX.c.event_id,
        ]
    )
    return claimed.returning(BROKER_INBOX.c.event_id)


def stored_statement(identity: InboxIdentity) -> StoredSelection:
    """Return the stored digest and result for one complete message identity."""
    return select(
        BROKER_INBOX.c.canonical_digest,
        BROKER_INBOX.c.result,
        BROKER_INBOX.c.processed_at,
    ).where(
        BROKER_INBOX.c.consumer == identity.consumer,
        BROKER_INBOX.c.source == identity.source,
        BROKER_INBOX.c.event_id == identity.event_id,
    )


def completion_statement(identity: InboxIdentity, result: bytes, processed_at: str) -> Update:
    """Return a compare-and-set that completes this transaction's exact claim once."""
    return (
        update(BROKER_INBOX)
        .where(
            BROKER_INBOX.c.consumer == identity.consumer,
            BROKER_INBOX.c.source == identity.source,
            BROKER_INBOX.c.event_id == identity.event_id,
            BROKER_INBOX.c.canonical_digest == identity.canonical_digest,
            BROKER_INBOX.c.result.is_(None),
        )
        .values(result=result, processed_at=processed_at)
        .returning(BROKER_INBOX.c.event_id)
    )


class StoredRows(Protocol):
    """The one stored inbox row selected after a claim conflict."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the stored row or ``None`` if it vanished."""


class InboxSession(Protocol):
    """The SQLAlchemy session operations this repository requires."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return one inserted or updated identity, or ``None``."""

    async def execute(self, statement: StoredSelection, /) -> StoredRows:
        """Return the stored row after a claim conflict."""


async def claim(session: InboxSession, identity: InboxIdentity) -> InboxOutcome:
    """Claim new work or return the exact committed result of a duplicate."""
    claimed = await session.scalar(claim_statement(identity))
    if claimed is not None:
        return InboxOutcome(InboxDecision.CLAIMED, None)
    result = await session.execute(stored_statement(identity))
    row = result.one_or_none()
    if row is None:
        raise InboxError(InboxRefusal.CLAIM_VANISHED, identity.event_id)
    return _duplicate(identity, row)


def _duplicate(identity: InboxIdentity, row: Sequence[object]) -> InboxOutcome:
    """Validate a stored conflicting row as an exact completed duplicate."""
    if len(row) != STORED_MEMBER_COUNT or not isinstance(row[0], str):
        raise InboxError(InboxRefusal.UNREADABLE_RESULT, identity.event_id)
    if row[0] != identity.canonical_digest:
        raise InboxError(InboxRefusal.DIGEST_CONFLICT, identity.event_id)
    stored_result, processed_at = row[1], row[2]
    if stored_result is None and processed_at is None:
        raise InboxError(InboxRefusal.INCOMPLETE, identity.event_id)
    if not isinstance(stored_result, bytes) or not isinstance(processed_at, str):
        raise InboxError(InboxRefusal.UNREADABLE_RESULT, identity.event_id)
    return InboxOutcome(InboxDecision.DUPLICATE, stored_result)


async def complete(
    session: InboxSession,
    identity: InboxIdentity,
    result: bytes,
    processed_at: str,
) -> None:
    """Store the processing outcome exactly once inside the claiming transaction."""
    completed = await session.scalar(completion_statement(identity, result, processed_at))
    if completed is None:
        raise InboxError(InboxRefusal.NOT_CLAIMED, identity.event_id)
