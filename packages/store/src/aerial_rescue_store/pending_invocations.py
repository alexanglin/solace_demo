"""Immutable trusted context recorded before Agent Mesh work begins."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import PENDING_INVOCATION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

STORED_MEMBER_COUNT: Final = 6
type PendingInvocationSelection = Select[tuple[object, ...]]


class PendingInvocationDecision(Enum):
    """Whether trusted context was stored now or already existed exactly."""

    STORED = "stored"
    DUPLICATE = "exact duplicate"


class PendingInvocationRefusal(Enum):
    """Why trusted invocation context cannot be stored or loaded."""

    CLAIM_VANISHED = "the conflicting invocation identity has no durable row"
    IDENTITY_CONFLICT = "the invocation identity was reused for different trusted context"
    NOT_FOUND = "no pending invocation is stored for that identity"
    UNREADABLE_ROW = "the pending invocation row does not match its migrated typed shape"


class PendingInvocationError(StoreError):
    """A pending-invocation repository operation this package refuses."""


@dataclass(frozen=True)
class StoredPendingInvocation:
    """The complete trusted context persisted before model work starts."""

    invocation_id: str
    mission_id: str
    agent_name: str
    correlation_id: str
    source_event_id: str
    source_event_digest: str


def record_statement(invocation: StoredPendingInvocation) -> Insert:
    """Return an immutable insert for one trusted invocation identity."""
    proposed = postgresql_insert(PENDING_INVOCATION).values(**invocation.__dict__)
    inserted = proposed.on_conflict_do_nothing(index_elements=[PENDING_INVOCATION.c.invocation_id])
    return inserted.returning(PENDING_INVOCATION.c.invocation_id)


def load_statement(invocation_id: str) -> PendingInvocationSelection:
    """Return every trusted-context member for one invocation identity."""
    return cast(
        "PendingInvocationSelection",
        select(*PENDING_INVOCATION.c).where(PENDING_INVOCATION.c.invocation_id == invocation_id),
    )


class PendingInvocationRows(Protocol):
    """The exact pending-invocation row returned by SQLAlchemy."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the row or ``None``."""


class PendingInvocationSession(Protocol):
    """The typed SQLAlchemy operations this immutable repository requires."""

    async def scalar(self, statement: Insert, /) -> object:
        """Return the inserted identity, or ``None`` after a conflict."""

    async def execute(self, statement: PendingInvocationSelection, /) -> PendingInvocationRows:
        """Return one exact trusted-context row."""


async def record(
    session: PendingInvocationSession,
    invocation: StoredPendingInvocation,
) -> PendingInvocationDecision:
    """Store trusted context once, accepting only an exact immutable duplicate."""
    inserted = await session.scalar(record_statement(invocation))
    if inserted is not None:
        return PendingInvocationDecision.STORED
    selected = await session.execute(load_statement(invocation.invocation_id))
    row = selected.one_or_none()
    if row is None:
        raise PendingInvocationError(
            PendingInvocationRefusal.CLAIM_VANISHED, invocation.invocation_id
        )
    if _stored(row, invocation.invocation_id) != invocation:
        raise PendingInvocationError(
            PendingInvocationRefusal.IDENTITY_CONFLICT, invocation.invocation_id
        )
    return PendingInvocationDecision.DUPLICATE


async def load(
    session: PendingInvocationSession,
    invocation_id: str,
) -> StoredPendingInvocation:
    """Load exact trusted context without filling or coercing any member."""
    selected = await session.execute(load_statement(invocation_id))
    row = selected.one_or_none()
    if row is None:
        raise PendingInvocationError(PendingInvocationRefusal.NOT_FOUND, invocation_id)
    return _stored(row, invocation_id)


def _stored(row: Sequence[object], invocation_id: str) -> StoredPendingInvocation:
    """Map a complete migrated row and fail closed on every incompatible value."""
    if len(row) != STORED_MEMBER_COUNT or not all(isinstance(value, str) for value in row):
        raise PendingInvocationError(PendingInvocationRefusal.UNREADABLE_ROW, invocation_id)
    return StoredPendingInvocation(*cast("tuple[str, str, str, str, str, str]", tuple(row)))
