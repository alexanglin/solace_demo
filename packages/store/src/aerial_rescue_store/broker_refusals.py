"""Append-only, body-free evidence for permanently malformed Guaranteed ingress."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import BROKER_REFUSAL

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

STORED_MEMBER_COUNT: Final = 7
type BrokerRefusalSelection = Select[tuple[object, ...]]


class BrokerRefusalDecision(Enum):
    """Whether this exact malformed delivery was recorded now or already existed."""

    STORED = "stored"
    DUPLICATE = "exact duplicate"


class BrokerRefusalRefusal(Enum):
    """Why a refusal fact cannot be persisted or safely reused."""

    IDENTITY_VANISHED = "the conflicting refusal identity has no durable row"
    IDENTITY_CONFLICT = "the refusal identity was reused for different bounded context"
    UNREADABLE_ROW = "the broker refusal row does not match its migrated typed shape"


class BrokerRefusalError(StoreError):
    """A refusal-ledger operation that fails closed without retaining raw ingress bytes."""


@dataclass(frozen=True)
class StoredBrokerRefusal:
    """Bounded evidence for one exact raw body on one consumer-owned receiver channel."""

    consumer: str
    source: str | None
    family: str | None
    channel: str
    refusal_code: str
    raw_digest: str
    observed_at: str


@dataclass(frozen=True)
class BrokerRefusalCandidate:
    """Body-free refusal context awaiting the store composition's trusted observation clock."""

    consumer: str
    source: str | None
    family: str | None
    channel: str
    refusal_code: str
    raw_digest: str


@dataclass(frozen=True)
class BrokerRefusalOutcome:
    """A new fact or the exact first observation reused by a redelivery."""

    decision: BrokerRefusalDecision
    fact: StoredBrokerRefusal


def record_statement(fact: StoredBrokerRefusal) -> Insert:
    """Return an immutable insert contending on consumer, channel, and exact raw bytes."""
    proposed = postgresql_insert(BROKER_REFUSAL).values(**fact.__dict__)
    inserted = proposed.on_conflict_do_nothing(
        index_elements=[
            BROKER_REFUSAL.c.consumer,
            BROKER_REFUSAL.c.channel,
            BROKER_REFUSAL.c.raw_digest,
        ]
    )
    return inserted.returning(BROKER_REFUSAL.c.raw_digest)


def identity_statement(
    consumer: str,
    channel: str,
    raw_digest: str,
) -> BrokerRefusalSelection:
    """Return the complete first fact for one malformed delivery identity."""
    statement = select(*BROKER_REFUSAL.c).where(
        BROKER_REFUSAL.c.consumer == consumer,
        BROKER_REFUSAL.c.channel == channel,
        BROKER_REFUSAL.c.raw_digest == raw_digest,
    )
    return cast("BrokerRefusalSelection", statement)


class BrokerRefusalRows(Protocol):
    """The exact refusal row returned by SQLAlchemy."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return one exact row or ``None``."""


class BrokerRefusalSession(Protocol):
    """The async SQLAlchemy Core operations required by refusal persistence."""

    async def scalar(self, statement: Insert, /) -> object:
        """Return the inserted digest, or ``None`` after an identity conflict."""

    async def execute(self, statement: BrokerRefusalSelection, /) -> BrokerRefusalRows:
        """Return one exact stored refusal fact."""


async def record(
    session: BrokerRefusalSession,
    fact: StoredBrokerRefusal,
) -> BrokerRefusalOutcome:
    """Store one bounded fact, reuse a redelivery, and reject changed context."""
    inserted = await session.scalar(record_statement(fact))
    if inserted is not None:
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, fact)
    selected = await session.execute(
        identity_statement(fact.consumer, fact.channel, fact.raw_digest)
    )
    row = selected.one_or_none()
    identity = (fact.consumer, fact.channel, fact.raw_digest)
    if row is None:
        raise BrokerRefusalError(BrokerRefusalRefusal.IDENTITY_VANISHED, identity)
    stored = _stored(row, identity)
    if _immutable_context(stored) != _immutable_context(fact):
        raise BrokerRefusalError(BrokerRefusalRefusal.IDENTITY_CONFLICT, identity)
    return BrokerRefusalOutcome(BrokerRefusalDecision.DUPLICATE, stored)


def _immutable_context(fact: StoredBrokerRefusal) -> tuple[str | None, str | None, str]:
    """Return context which may not change when the same raw delivery is redelivered."""
    return fact.source, fact.family, fact.refusal_code


def _stored(
    row: Sequence[object],
    identity: tuple[str, str, str],
) -> StoredBrokerRefusal:
    """Map every migrated member without coercion, defaulting, or body recovery."""
    valid = (
        len(row) == STORED_MEMBER_COUNT
        and isinstance(row[0], str)
        and (row[1] is None or isinstance(row[1], str))
        and (row[2] is None or isinstance(row[2], str))
        and all(isinstance(row[index], str) for index in range(3, STORED_MEMBER_COUNT))
    )
    if not valid:
        raise BrokerRefusalError(BrokerRefusalRefusal.UNREADABLE_ROW, identity)
    return StoredBrokerRefusal(
        consumer=cast("str", row[0]),
        source=cast("str | None", row[1]),
        family=cast("str | None", row[2]),
        channel=cast("str", row[3]),
        refusal_code=cast("str", row[4]),
        raw_digest=cast("str", row[5]),
        observed_at=cast("str", row[6]),
    )
