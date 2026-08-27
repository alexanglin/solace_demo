"""The append-only audit log, and the per-mission ordinal that orders the mission timeline.

[ADR-0088](../../../../docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md)
decides both statements below and rejects the obvious alternative outright: a generated identity
column assigns its value when the row is inserted rather than when the transaction commits, so
two concurrent appends can take 6 and 7 and commit in the opposite order, and a reader polling
above its high-water mark never sees 6 again. A rolled-back transaction consumes a number too,
leaving a gap indistinguishable from that one.

The conditional upsert has neither failure. It takes a row lock held until commit, so a second
appender for the same mission waits and the two ordinals are issued in commit order; and a
rollback releases the lock without advancing the counter, so the sequence is gap-free. Both
properties belong to the *transaction*, which is why nothing here opens one: the caller's
transaction is what makes the guarantee, and this module refuses to own it.

**There is no update and no delete.** ADR-0088 makes append-only a property enforced by the
absence of a method rather than by a permission, and adding either here would remove it.

The values are persisted exactly as accepted. The instant is the canonical millisecond text and
the payload the canonical bytes, because ADR-0027 makes both part of what a digest covers and a
re-encoding through a native column type would let the database decide them. This module runs
no validator of its own for the same reason `packages/contracts` owns the identifier grammar:
a second home for a rule is a second answer to it.

Every statement is a typed expression over the complete package-owned table metadata. Importing
that metadata emits no DDL; Alembic remains the only schema authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import AUDIT_RECORD, AUDIT_SEQUENCE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

FIRST_ORDINAL: Final = 1
"""What the upsert proposes, so a mission's first record needs no separate initialisation."""

ORDINAL_STEP: Final = 1

MISSION_COLUMN: Final = "mission_id"
NEXT_ORDINAL_COLUMN: Final = "next_ordinal"

_SEQUENCE_ROWS: Final = AUDIT_SEQUENCE
_RECORD_ROWS: Final = AUDIT_RECORD
AUDIT_MEMBER_COUNT: Final = 8
type AuditSelection = Select[tuple[object, ...]]


class AuditRefusal(Enum):
    """Why an append did not produce a record."""

    NO_ORDINAL_ISSUED = (
        "the counter returned no ordinal, so the timeline's ordering authority did not answer "
        "and no record is written on an ordinal this process chose for itself"
    )
    INVALID_READ_LIMIT = "the ordered audit read limit must be a positive integer"
    INVALID_AFTER_ORDINAL = "the audit recovery checkpoint must be a nonnegative integer"
    READ_LIMIT_EXCEEDED = "the database returned more audit rows than the requested bound"
    UNREADABLE_ROW = "the stored audit row does not match its migrated typed shape"
    ORDER_VIOLATION = "the stored audit rows are not strictly ascending by ordinal"


class AuditError(StoreError):
    """An append this module refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class AuditRecord:
    """One entry in the mission timeline, without the ordinal the counter has yet to issue."""

    mission_id: str
    kind: str
    occurred_at: str
    payload: bytes
    correlation_id: str
    causation_id: str | None
    traceparent: str


@dataclass(frozen=True)
class StoredAuditRecord:
    """One complete authoritative audit row including its durable mission ordinal."""

    mission_id: str
    ordinal: int
    kind: str
    occurred_at: str
    payload: bytes
    correlation_id: str
    causation_id: str | None
    traceparent: str


def next_ordinal_statement(mission_id: str) -> Insert:
    """Return the statement that issues this mission's next ordinal, exactly as ADR-0088 sets it.

    Args:
        mission_id: The mission whose counter advances.

    Returns:
        The conditional upsert. It proposes the first ordinal and, on conflict, advances the
        counter's own value -- never the proposed one, which is what makes the issued numbers
        consecutive rather than merely distinct.
    """
    proposed = postgresql_insert(_SEQUENCE_ROWS).values(
        mission_id=mission_id, next_ordinal=FIRST_ORDINAL
    )
    return proposed.on_conflict_do_update(
        index_elements=[_SEQUENCE_ROWS.c[MISSION_COLUMN]],
        set_={NEXT_ORDINAL_COLUMN: _SEQUENCE_ROWS.c[NEXT_ORDINAL_COLUMN] + ORDINAL_STEP},
    ).returning(_SEQUENCE_ROWS.c[NEXT_ORDINAL_COLUMN])


def record_statement(record: AuditRecord, ordinal: int) -> Insert:
    """Return the statement that appends ``record`` at the ordinal the counter issued.

    Args:
        record: The entry, with every value already accepted at its own trust boundary.
        ordinal: What ``next_ordinal_statement`` returned in this same transaction.

    Returns:
        The insert. It carries no default and no generated value, so the row is exactly what
        the caller accepted plus the ordinal the counter issued.
    """
    return insert(_RECORD_ROWS).values(
        mission_id=record.mission_id,
        ordinal=ordinal,
        kind=record.kind,
        occurred_at=record.occurred_at,
        payload=record.payload,
        correlation_id=record.correlation_id,
        causation_id=record.causation_id,
        traceparent=record.traceparent,
    )


class OrdinalSession(Protocol):
    """What appending needs of the caller's session, and nothing more.

    Both statements this member issues are inserts, so the port says so rather than accepting
    anything executable. A real ``AsyncSession`` takes the wider type and satisfies it.
    """

    async def scalar(self, statement: Insert, /) -> int | None:
        """Return the single value the statement produces, or ``None`` if it produced no row."""

    async def execute(self, statement: Insert, /) -> object:
        """Run the statement for its effect."""


async def append(session: OrdinalSession, record: AuditRecord) -> int:
    """Issue this mission's next ordinal and append ``record`` at it, in that order.

    Both statements run inside the caller's transaction. That is the whole mechanism: the
    counter's row lock is held until that transaction commits, so a concurrent appender for the
    same mission waits, and a rollback advances nothing.

    Args:
        session: The caller's open session. Its transaction, and its commit, belong to the
            caller -- ADR-0088's guarantee is a property of that transaction, not of this call.
        record: The entry to append.

    Returns:
        The ordinal the counter issued.

    Raises:
        AuditError: With ``NO_ORDINAL_ISSUED`` when the counter answers with no row. Nothing is
            written in that case: an ordinal this process chose for itself would be exactly the
            silent gap ADR-0088 exists to prevent.
    """
    ordinal = await session.scalar(next_ordinal_statement(record.mission_id))
    if ordinal is None:
        raise AuditError(AuditRefusal.NO_ORDINAL_ISSUED, record.mission_id)
    await session.execute(record_statement(record, ordinal))
    return ordinal


def ordered_statement(mission_id: str, limit: int) -> AuditSelection:
    """Return a mission-bounded audit read ordered only by its authoritative ordinal."""
    statement = (
        select(*AUDIT_RECORD.c)
        .where(AUDIT_RECORD.c.mission_id == mission_id)
        .order_by(AUDIT_RECORD.c.ordinal)
        .limit(limit)
    )
    return cast("AuditSelection", statement)


def ordered_after_statement(mission_id: str, after_ordinal: int, limit: int) -> AuditSelection:
    """Return one keyset-paginated audit suffix with no offset race."""
    statement = (
        select(*AUDIT_RECORD.c)
        .where(
            AUDIT_RECORD.c.mission_id == mission_id,
            AUDIT_RECORD.c.ordinal > after_ordinal,
        )
        .order_by(AUDIT_RECORD.c.ordinal)
        .limit(limit)
    )
    return cast("AuditSelection", statement)


class AuditRows(Protocol):
    """The bounded ordered rows returned by SQLAlchemy."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return rows in the statement's ordinal order."""


class AuditReadSession(Protocol):
    """The read-only SQLAlchemy operation required for audit export."""

    async def execute(self, statement: AuditSelection, /) -> AuditRows:
        """Execute one bounded read."""


async def read_ordered(
    session: AuditReadSession, mission_id: str, limit: int
) -> tuple[StoredAuditRecord, ...]:
    """Return no more than ``limit`` complete audit rows in strict ordinal order."""
    if type(limit) is not int or limit <= 0:
        raise AuditError(AuditRefusal.INVALID_READ_LIMIT, "redacted-limit")
    selected = await session.execute(ordered_statement(mission_id, limit))
    rows = selected.all()
    if len(rows) > limit:
        raise AuditError(AuditRefusal.READ_LIMIT_EXCEEDED, mission_id)
    records = tuple(_stored_record(row, mission_id) for row in rows)
    if any(current.ordinal <= previous.ordinal for previous, current in pairwise(records)):
        raise AuditError(AuditRefusal.ORDER_VIOLATION, mission_id)
    return records


async def read_ordered_after(
    session: AuditReadSession,
    mission_id: str,
    after_ordinal: int,
    limit: int,
) -> tuple[StoredAuditRecord, ...]:
    """Return one bounded suffix strictly after a durable dashboard checkpoint."""
    if type(after_ordinal) is not int or after_ordinal < 0:
        raise AuditError(AuditRefusal.INVALID_AFTER_ORDINAL, "redacted-ordinal")
    if type(limit) is not int or limit <= 0:
        raise AuditError(AuditRefusal.INVALID_READ_LIMIT, "redacted-limit")
    selected = await session.execute(ordered_after_statement(mission_id, after_ordinal, limit))
    rows = selected.all()
    if len(rows) > limit:
        raise AuditError(AuditRefusal.READ_LIMIT_EXCEEDED, mission_id)
    records = tuple(_stored_record(row, mission_id) for row in rows)
    if records and records[0].ordinal <= after_ordinal:
        raise AuditError(AuditRefusal.ORDER_VIOLATION, mission_id)
    if any(current.ordinal <= previous.ordinal for previous, current in pairwise(records)):
        raise AuditError(AuditRefusal.ORDER_VIOLATION, mission_id)
    return records


def _stored_record(row: Sequence[object], mission_id: str) -> StoredAuditRecord:
    """Map one complete row without coercing canonical bytes or durable identity."""
    if len(row) != AUDIT_MEMBER_COUNT:
        raise AuditError(AuditRefusal.UNREADABLE_ROW, mission_id)
    valid = (
        all(isinstance(row[index], str) for index in (0, 2, 3, 5, 7))
        and type(row[1]) is int
        and row[1] > 0
        and isinstance(row[4], bytes)
        and (row[6] is None or isinstance(row[6], str))
        and row[0] == mission_id
    )
    if not valid:
        raise AuditError(AuditRefusal.UNREADABLE_ROW, mission_id)
    return StoredAuditRecord(
        mission_id=cast("str", row[0]),
        ordinal=cast("int", row[1]),
        kind=cast("str", row[2]),
        occurred_at=cast("str", row[3]),
        payload=cast("bytes", row[4]),
        correlation_id=cast("str", row[5]),
        causation_id=cast("str | None", row[6]),
        traceparent=cast("str", row[7]),
    )
