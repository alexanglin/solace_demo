"""Typed immutable storage for complete source events and their canonical provenance bytes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import SOURCE_EVENT

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

STORED_MEMBER_COUNT: Final = 7
LOOKUP_LIMIT: Final = 2
type SourceEventSelection = Select[tuple[object, ...]]


class SourceEventDecision(Enum):
    """Whether a complete immutable source event was inserted or replayed exactly."""

    STORED = "stored"
    DUPLICATE = "exact duplicate"


class SourceEventRefusal(Enum):
    """Why a complete source event cannot be persisted or selected."""

    IDENTITY_VANISHED = "the conflicting source-event identity has no durable row"
    IDENTITY_CONFLICT = "the source-event identity was reused for different immutable content"
    NOT_FOUND = "no source event is stored for that mission and event identity"
    AMBIGUOUS_IDENTITY = "multiple producers used that event identity in the mission"
    UNREADABLE_ROW = "the stored source event does not match its migrated typed shape"


class SourceEventError(StoreError):
    """A source-event repository operation this package refuses."""


@dataclass(frozen=True)
class StoredSourceEvent:
    """The complete accepted CloudEvent bytes and immutable provenance identity."""

    source: str
    event_id: str
    mission_id: str
    topic: str
    canonical_digest: str
    canonical_payload: bytes
    observed_at: str


def record_statement(event: StoredSourceEvent) -> Insert:
    """Return an insert that contends on and never overwrites CloudEvent identity."""
    proposed = postgresql_insert(SOURCE_EVENT).values(
        source=event.source,
        event_id=event.event_id,
        mission_id=event.mission_id,
        topic=event.topic,
        canonical_digest=event.canonical_digest,
        canonical_payload=event.canonical_payload,
        observed_at=event.observed_at,
    )
    inserted = proposed.on_conflict_do_nothing(
        index_elements=[SOURCE_EVENT.c.source, SOURCE_EVENT.c.event_id]
    )
    return inserted.returning(SOURCE_EVENT.c.event_id)


def identity_statement(source: str, event_id: str) -> SourceEventSelection:
    """Return the complete stored fact for one exact CloudEvent identity."""
    statement = select(*SOURCE_EVENT.c).where(
        SOURCE_EVENT.c.source == source,
        SOURCE_EVENT.c.event_id == event_id,
    )
    return cast("SourceEventSelection", statement)


def lookup_statement(mission_id: str, event_id: str) -> SourceEventSelection:
    """Return at most two producer identities so an ambiguous event ID is detectable."""
    statement = (
        select(*SOURCE_EVENT.c)
        .where(
            SOURCE_EVENT.c.mission_id == mission_id,
            SOURCE_EVENT.c.event_id == event_id,
        )
        .order_by(SOURCE_EVENT.c.source)
        .limit(LOOKUP_LIMIT)
    )
    return cast("SourceEventSelection", statement)


class SourceEventRows(Protocol):
    """The SQLAlchemy row operations used by exact and source-agnostic selections."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return one exact-identity row, or ``None``."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return the bounded mission-and-event lookup rows."""


class SourceEventSession(Protocol):
    """The async SQLAlchemy operations required by source-event persistence."""

    async def scalar(self, statement: Insert, /) -> object:
        """Return the inserted event identifier, or ``None`` on identity conflict."""

    async def execute(self, statement: SourceEventSelection, /) -> SourceEventRows:
        """Return rows selected through package-owned table metadata."""


async def record(session: SourceEventSession, event: StoredSourceEvent) -> SourceEventDecision:
    """Store once, replay exact bytes idempotently, and reject every changed member."""
    inserted = await session.scalar(record_statement(event))
    if inserted is not None:
        return SourceEventDecision.STORED
    selected = await session.execute(identity_statement(event.source, event.event_id))
    row = selected.one_or_none()
    identity = (event.source, event.event_id)
    if row is None:
        raise SourceEventError(SourceEventRefusal.IDENTITY_VANISHED, identity)
    stored = _stored(row, event.event_id)
    if stored != event:
        raise SourceEventError(SourceEventRefusal.IDENTITY_CONFLICT, identity)
    return SourceEventDecision.DUPLICATE


async def load_for(
    session: SourceEventSession, mission_id: str, event_id: str
) -> StoredSourceEvent:
    """Load a unique source event without guessing which producer owns a repeated event ID."""
    selected = await session.execute(lookup_statement(mission_id, event_id))
    rows = selected.all()
    identity = (mission_id, event_id)
    if not rows:
        raise SourceEventError(SourceEventRefusal.NOT_FOUND, identity)
    if len(rows) != 1:
        raise SourceEventError(SourceEventRefusal.AMBIGUOUS_IDENTITY, identity)
    stored = _stored(rows[0], event_id)
    if stored.mission_id != mission_id or stored.event_id != event_id:
        raise SourceEventError(SourceEventRefusal.UNREADABLE_ROW, identity)
    return stored


def _stored(row: Sequence[object], event_id: str) -> StoredSourceEvent:
    """Map every persisted member without coercing or re-encoding canonical bytes."""
    if len(row) != STORED_MEMBER_COUNT:
        raise SourceEventError(SourceEventRefusal.UNREADABLE_ROW, event_id)
    valid = all(isinstance(row[index], str) for index in (0, 1, 2, 3, 4, 6)) and isinstance(
        row[5], bytes
    )
    if not valid:
        raise SourceEventError(SourceEventRefusal.UNREADABLE_ROW, event_id)
    return StoredSourceEvent(
        source=cast("str", row[0]),
        event_id=cast("str", row[1]),
        mission_id=cast("str", row[2]),
        topic=cast("str", row[3]),
        canonical_digest=cast("str", row[4]),
        canonical_payload=cast("bytes", row[5]),
        observed_at=cast("str", row[6]),
    )
