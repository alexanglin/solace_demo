"""Recorder deduplication and bounded ordered dashboard-event reads.

The recorder serializes one producer on ``dashboard_broker_source``, asks the pure domain
sequence rule whether the candidate advances, and only then consumes the next audit ordinal.
Known ``source``/``event_id`` pairs return their existing audit link when the caller-provided
payload digest matches and permanently refuse divergent content. The payload digest arrives
already computed; this member neither validates nor canonicalizes broker input.

Read paths join broker provenance to the append-only audit record and return its exact canonical
payload bytes. Snapshot capture shares the singleton pointer lock while it captures a committed
watermark. The caller reads bounded pages through that watermark in the same transaction, then
releases it before folding and asking for a later suffix.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_domain.idempotency import SequenceVerdict, Stream, receive
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.audit import AuditRecord, OrdinalSession, append
from aerial_rescue_store.dashboard.runs import DashboardRun, current_run
from aerial_rescue_store.database.schema import (
    AUDIT_RECORD,
    DASHBOARD_BROKER_EVENT,
    DASHBOARD_BROKER_SOURCE,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.elements import ClauseElement
    from sqlalchemy.sql.selectable import Select

MAXIMUM_EVENT_PAGE_SIZE: Final = 512
"""The largest ordered read page, equal to the accepted replay-event bound."""

EVENT_ROW_MEMBERS: Final = 3

_SOURCE_ROWS: Final = DASHBOARD_BROKER_SOURCE
_SOURCE: Final = DASHBOARD_BROKER_SOURCE.c.source
_HIGH_WATER: Final = DASHBOARD_BROKER_SOURCE.c.high_water_sequence

_BROKER_ROWS: Final = DASHBOARD_BROKER_EVENT
_EVENT_SOURCE: Final = DASHBOARD_BROKER_EVENT.c.source
_EVENT_ID: Final = DASHBOARD_BROKER_EVENT.c.event_id
_SOURCE_SEQUENCE: Final = DASHBOARD_BROKER_EVENT.c.source_sequence
_PAYLOAD_DIGEST: Final = DASHBOARD_BROKER_EVENT.c.payload_digest
_AUDIT_MISSION: Final = DASHBOARD_BROKER_EVENT.c.audit_mission_id
_AUDIT_ORDINAL: Final = DASHBOARD_BROKER_EVENT.c.audit_ordinal

_AUDIT_ROWS: Final = AUDIT_RECORD
_RECORD_MISSION: Final = AUDIT_RECORD.c.mission_id
_RECORD_ORDINAL: Final = AUDIT_RECORD.c.ordinal
_RECORD_KIND: Final = AUDIT_RECORD.c.kind
_RECORD_PAYLOAD: Final = AUDIT_RECORD.c.payload


class BrokerEventOutcome(Enum):
    """Whether this transaction appended an event or found the exact prior one."""

    ACCEPTED = "accepted and linked to a new audit ordinal"
    DUPLICATE = "same broker identity and payload already has an audit link"


class DashboardEventRefusal(Enum):
    """Why broker persistence or an ordered read was refused."""

    SOURCE_VANISHED = "the ensured producer source row could not be locked"
    SOURCE_MOVED = "the producer high water changed after the caller observed it"
    DIVERGENT_DUPLICATE = "the broker identity is already linked to different payload content"
    SEQUENCE_REUSED = "a new broker identity reused the producer high-water sequence"
    STALE_SEQUENCE = "the broker event sequence is behind its producer high-water mark"
    EVENT_WRITE_REJECTED = "the accepted broker event identity was not linked to its audit row"
    UNREADABLE_SOURCE = "the producer high-water row has an incompatible representation"
    UNREADABLE_EVENT = "a broker identity or audit payload row has an incompatible representation"
    INVALID_PAGE_SIZE = "the ordered read page is empty or exceeds its accepted bound"


class DashboardEventError(StoreError):
    """A recorder or ordered-read refusal with structured context."""


@dataclass(frozen=True)
class BrokerEvent:
    """Validated broker identity and digest values supplied by the recorder."""

    source: str
    event_id: str
    source_sequence: int
    payload_digest: str


@dataclass(frozen=True)
class BrokerEventReceipt:
    """The durable audit link for a new or exact duplicate broker event."""

    outcome: BrokerEventOutcome
    audit_mission_id: str
    audit_ordinal: int


@dataclass(frozen=True)
class StoredDashboardEvent:
    """One ordered event as exact canonical audit payload bytes."""

    audit_ordinal: int
    kind: str
    payload: bytes


@dataclass(frozen=True)
class SnapshotBasis:
    """The current prepared run and watermark captured under the shared pointer lock."""

    run: DashboardRun
    audit_watermark: int


type SourceSelection = Select[tuple[int]]
type KnownEventSelection = Select[tuple[str, str, int]]
type EventPageSelection = Select[tuple[int, str, bytes]]


def ensure_source_statement(source: str) -> Insert:
    """Create an initially empty high-water row without racing another first event."""
    proposed = postgresql_insert(_SOURCE_ROWS).values(source=source, high_water_sequence=None)
    return proposed.on_conflict_do_nothing(index_elements=[_SOURCE]).returning(_SOURCE)


def locked_source_statement(source: str) -> SourceSelection:
    """Lock one producer before comparing its identity and sequence."""
    return select(_HIGH_WATER).where(_SOURCE_ROWS.c["source"] == source).with_for_update()


def source_advance_statement(source: str, expected: int | None, sequence: int) -> Update:
    """Advance from the high water the domain judged, with the guard in the write."""
    statement = update(_SOURCE_ROWS).where(_SOURCE_ROWS.c["source"] == source)
    statement = (
        statement.where(_HIGH_WATER.is_(None))
        if expected is None
        else statement.where(_SOURCE_ROWS.c["high_water_sequence"] == expected)
    )
    return statement.values(high_water_sequence=sequence).returning(_SOURCE)


def known_broker_event_statement(source: str, event_id: str) -> KnownEventSelection:
    """Read the content identity and audit link held by a known broker identity."""
    return select(_PAYLOAD_DIGEST, _AUDIT_MISSION, _AUDIT_ORDINAL).where(
        _BROKER_ROWS.c["source"] == source, _BROKER_ROWS.c["event_id"] == event_id
    )


def broker_event_statement(event: BrokerEvent, audit_mission_id: str, audit_ordinal: int) -> Insert:
    """Link a newly accepted broker identity to exactly one existing audit record."""
    return (
        insert(_BROKER_ROWS)
        .values(
            source=event.source,
            event_id=event.event_id,
            source_sequence=event.source_sequence,
            payload_digest=event.payload_digest,
            audit_mission_id=audit_mission_id,
            audit_ordinal=audit_ordinal,
        )
        .returning(_EVENT_ID)
    )


def watermark_statement(mission_id: str) -> Select[tuple[int]]:
    """Read the greatest recorder-linked audit ordinal for one operational mission."""
    joined = _AUDIT_ROWS.join(
        _BROKER_ROWS,
        (_RECORD_MISSION == _AUDIT_MISSION) & (_RECORD_ORDINAL == _AUDIT_ORDINAL),
    )
    return (
        select(func.max(_RECORD_ORDINAL))
        .select_from(joined)
        .where(_AUDIT_ROWS.c["mission_id"] == mission_id)
    )


def event_page_statement(
    mission_id: str,
    after_ordinal: int,
    through_ordinal: int | None,
    limit: int,
) -> EventPageSelection:
    """Read one bounded audit-ordered page, optionally through a captured watermark."""
    _require_page_size(limit, MAXIMUM_EVENT_PAGE_SIZE)
    statement = _ordered_event_selection(mission_id, after_ordinal)
    if through_ordinal is not None:
        statement = statement.where(_AUDIT_ROWS.c["ordinal"] <= through_ordinal)
    return statement.order_by(_RECORD_ORDINAL).limit(limit)


def _ordered_event_selection(mission_id: str, after_ordinal: int) -> EventPageSelection:
    """Build the shared provenance join for snapshot and suffix reads."""
    joined = _AUDIT_ROWS.join(
        _BROKER_ROWS,
        (_RECORD_MISSION == _AUDIT_MISSION) & (_RECORD_ORDINAL == _AUDIT_ORDINAL),
    )
    return (
        select(_RECORD_ORDINAL, _RECORD_KIND, _RECORD_PAYLOAD)
        .select_from(joined)
        .where(_AUDIT_ROWS.c["mission_id"] == mission_id)
        .where(_AUDIT_ROWS.c["ordinal"] > after_ordinal)
    )


class SelectedRows(Protocol):
    """The SQLAlchemy result surface used by single and bounded page reads."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return one row, or none."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return every row from the bounded statement."""


class EventSession(Protocol):
    """The caller-owned transaction surface shared with audit and current-run repositories."""

    async def scalar(self, statement: ClauseElement, /) -> object:
        """Return one value from a guarded write or watermark read."""

    async def execute(self, statement: ClauseElement, /) -> SelectedRows:
        """Run an effect, single-row read, or bounded page read."""


async def append_broker_event(
    session: EventSession, event: BrokerEvent, record: AuditRecord
) -> BrokerEventReceipt:
    """Deduplicate, advance one source, append audit, and link it in one caller transaction."""
    await session.execute(ensure_source_statement(event.source))
    source_result = await session.execute(locked_source_statement(event.source))
    source_row = source_result.one_or_none()
    if source_row is None:
        raise DashboardEventError(DashboardEventRefusal.SOURCE_VANISHED, event.source)
    high_water = _high_water(source_row)
    known_result = await session.execute(known_broker_event_statement(event.source, event.event_id))
    known = known_result.one_or_none()
    if known is not None:
        return _known_receipt(event, known)
    reception = receive(Stream(high_water), event.source_sequence)
    if reception.verdict is SequenceVerdict.DUPLICATE:
        raise DashboardEventError(DashboardEventRefusal.SEQUENCE_REUSED, event.source_sequence)
    if reception.verdict is SequenceVerdict.STALE:
        raise DashboardEventError(DashboardEventRefusal.STALE_SEQUENCE, event.source_sequence)
    advanced = await session.scalar(
        source_advance_statement(event.source, high_water, event.source_sequence)
    )
    if advanced is None:
        raise DashboardEventError(DashboardEventRefusal.SOURCE_MOVED, event.source)
    ordinal = await append(cast("OrdinalSession", session), record)
    linked = await session.scalar(broker_event_statement(event, record.mission_id, ordinal))
    if linked is None:
        raise DashboardEventError(DashboardEventRefusal.EVENT_WRITE_REJECTED, event.event_id)
    return BrokerEventReceipt(BrokerEventOutcome.ACCEPTED, record.mission_id, ordinal)


async def capture_snapshot_basis(session: EventSession) -> SnapshotBasis | None:
    """Capture current run and committed watermark while the caller holds one transaction."""
    run = await current_run(session, shared=True)
    if run is None:
        return None
    if run.mission_id is None:
        return SnapshotBasis(run=run, audit_watermark=0)
    return SnapshotBasis(
        run=run,
        audit_watermark=await recording_watermark(session, run.mission_id),
    )


async def recording_watermark(session: EventSession, mission_id: str) -> int:
    """Capture the greatest recorder-linked ordinal, or zero for an eventless mission."""
    value = await session.scalar(watermark_statement(mission_id))
    return 0 if value is None else _ordinal(value)


async def read_event_page(
    session: EventSession,
    mission_id: str,
    after_ordinal: int,
    through_ordinal: int,
    limit: int,
) -> tuple[StoredDashboardEvent, ...]:
    """Read canonical events through the transaction's captured watermark."""
    return await _read_page(
        session, event_page_statement(mission_id, after_ordinal, through_ordinal, limit)
    )


async def read_suffix_page(
    session: EventSession, mission_id: str, after_ordinal: int, limit: int
) -> tuple[StoredDashboardEvent, ...]:
    """Read canonical events committed after a folded snapshot watermark."""
    return await _read_page(session, event_page_statement(mission_id, after_ordinal, None, limit))


async def _read_page(
    session: EventSession, statement: EventPageSelection
) -> tuple[StoredDashboardEvent, ...]:
    """Map one bounded result page without decoding or re-encoding its payloads."""
    selected = await session.execute(statement)
    return tuple(_stored_event(row) for row in selected.all())


def _known_receipt(event: BrokerEvent, row: Sequence[object]) -> BrokerEventReceipt:
    """Return an exact duplicate's prior link or refuse divergent durable content."""
    if len(row) != EVENT_ROW_MEMBERS:
        raise DashboardEventError(DashboardEventRefusal.UNREADABLE_EVENT, len(row))
    digest, mission, ordinal = row
    if not isinstance(digest, str) or not isinstance(mission, str):
        raise DashboardEventError(DashboardEventRefusal.UNREADABLE_EVENT, event.event_id)
    linked_ordinal = _ordinal(ordinal)
    if not hmac.compare_digest(digest, event.payload_digest):
        raise DashboardEventError(DashboardEventRefusal.DIVERGENT_DUPLICATE, event.event_id)
    return BrokerEventReceipt(BrokerEventOutcome.DUPLICATE, mission, linked_ordinal)


def _high_water(row: Sequence[object]) -> int | None:
    """Map the nullable source high water without accepting booleans as integers."""
    if len(row) != 1:
        raise DashboardEventError(DashboardEventRefusal.UNREADABLE_SOURCE, len(row))
    value = row[0]
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise DashboardEventError(DashboardEventRefusal.UNREADABLE_SOURCE, value)


def _ordinal(value: object) -> int:
    """Map a positive audit ordinal returned by PostgreSQL."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    raise DashboardEventError(DashboardEventRefusal.UNREADABLE_EVENT, value)


def _stored_event(row: Sequence[object]) -> StoredDashboardEvent:
    """Map one ordered row while preserving its exact canonical payload bytes."""
    if len(row) != EVENT_ROW_MEMBERS:
        raise DashboardEventError(DashboardEventRefusal.UNREADABLE_EVENT, len(row))
    ordinal, kind, payload = row
    if not isinstance(kind, str) or not isinstance(payload, bytes):
        raise DashboardEventError(DashboardEventRefusal.UNREADABLE_EVENT, ordinal)
    return StoredDashboardEvent(_ordinal(ordinal), kind, payload)


def _require_page_size(limit: int, maximum: int) -> None:
    """Refuse an unbounded or empty read before constructing its statement."""
    if isinstance(limit, bool) or not 1 <= limit <= maximum:
        raise DashboardEventError(DashboardEventRefusal.INVALID_PAGE_SIZE, limit)
