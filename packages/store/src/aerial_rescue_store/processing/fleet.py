"""Durable simulated-drone sequence, effect, receipt, and critical outbox processing.

One row per drone is the serialization point for both its producer high-water and critical
outbox admission.  A stage operation holds that row lock while it measures only unconfirmed
rows, so concurrent admissions cannot independently spend the same final record or byte of
capacity.  Exact topic, header, and body octets are measured; telemetry never enters this path.

The purpose-specific transaction exposes receipt, immutable effect, exact result staging, and
receipt completion over one injected SQLAlchemy session.  It opens no connection itself and
never commits between those operations; the shared transaction boundary commits all of them or
rolls all of them back.
"""

from __future__ import annotations

import re
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_domain.idempotency import SequenceVerdict, Stream, receive
from aerial_rescue_domain.outbox import OutboxState
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.application_outbox import (
    ApplicationOutboxError,
    ApplicationOutboxSession,
    StagedApplicationEvent,
    stage,
)
from aerial_rescue_store.database.schema import (
    APPLICATION_OUTBOX,
    DRONE_COMMAND_EFFECT,
    DRONE_STREAM_STATE,
)
from aerial_rescue_store.receipts import (
    CommandReceiptIdentity,
    ReceiptOutcome,
    ReceiptSession,
    claim,
    complete,
)
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

CRITICAL_OUTBOX_MAX_RECORDS: Final = 500
CRITICAL_OUTBOX_MAX_BYTES: Final = 2 * 1024 * 1024
MAXIMUM_PRODUCER_SEQUENCE: Final = 10**15 - 1

_CRITICAL_FAMILIES: Final = frozenset({"drone-event", "drone-command-result", "sector-event"})
_UNCONFIRMED_STATES: Final = (
    OutboxState.STAGED.value,
    OutboxState.RECONCILIATION_NEEDED.value,
)
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_STREAM_MEMBER_COUNT: Final = 2
_USAGE_MEMBER_COUNT: Final = 2

type FleetSelection = Select[tuple[object, ...]]


class FleetStoreRefusal(Enum):
    """Why durable simulated-drone processing cannot proceed."""

    INVALID_SEQUENCE = "the producer sequence is outside the canonical fifteen-digit range"
    IDENTITY_CONFLICT = "the drone stream is absent, malformed, or bound to another producer"
    STREAM_CHANGED = "the locked high-water row did not accept its compared update"
    UNREADABLE_USAGE = "the measured critical outbox usage is not a nonnegative integer pair"
    NONCRITICAL_FAMILY = "only critical fleet families may use the durable edge outbox"
    RECORD_CAPACITY = "the drone already holds five hundred unconfirmed critical records"
    BYTE_CAPACITY = "the critical publication would exceed two mebibytes for this drone"
    EVENT_CONFLICT = "the drone already staged that critical event identity"
    INVALID_EFFECT = "the durable command effect is malformed or inconsistently bound"
    EFFECT_CONFLICT = "the command already has a durable effect and cannot be overwritten"
    RESULT_BINDING = "the receipt result must be the last staged critical result"


class FleetStoreError(StoreError):
    """A fleet persistence operation refused with a closed, secret-safe reason."""


class CommandEffectOutcome(Enum):
    """The two immutable outcomes a simulated command effect may record."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class DroneStreamIdentity:
    """One drone and the producer URI whose sequence and capacity it owns."""

    drone_id: str
    producer: str


@dataclass(frozen=True)
class DurableCommandEffect:
    """One append-only simulated effect bound to its claimed command receipt."""

    identity: CommandReceiptIdentity
    outcome: CommandEffectOutcome
    effect_payload: bytes
    applied_sequence: int
    applied_at: str


class FleetRows(Protocol):
    """One selected row from a lock or aggregate read."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the selected row or ``None``."""


class FleetSession(Protocol):
    """The SQLAlchemy operations used by fleet persistence repositories."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return one inserted or updated identity, or ``None``."""

    async def execute(self, statement: FleetSelection, /) -> FleetRows:
        """Return one locked or aggregate row."""


def register_stream_statement(identity: DroneStreamIdentity) -> Insert:
    """Insert the drone-to-producer binding without changing an established identity."""
    proposed = postgresql_insert(DRONE_STREAM_STATE).values(
        drone_id=identity.drone_id,
        producer=identity.producer,
        high_water=None,
    )
    registered = proposed.on_conflict_do_nothing()
    return registered.returning(DRONE_STREAM_STATE.c.drone_id)


def stream_lock_statement(identity: DroneStreamIdentity) -> FleetSelection:
    """Lock one drone row before sequence or capacity admission."""
    statement = (
        select(DRONE_STREAM_STATE.c.producer, DRONE_STREAM_STATE.c.high_water)
        .where(DRONE_STREAM_STATE.c.drone_id == identity.drone_id)
        .with_for_update()
    )
    return cast("FleetSelection", statement)


def advance_stream_statement(
    identity: DroneStreamIdentity,
    previous: int | None,
    candidate: int,
) -> Update:
    """Compare and update the exact high-water value observed under the row lock."""
    comparison = (
        DRONE_STREAM_STATE.c.high_water.is_(None)
        if previous is None
        else DRONE_STREAM_STATE.c.high_water == previous
    )
    return (
        update(DRONE_STREAM_STATE)
        .where(
            DRONE_STREAM_STATE.c.drone_id == identity.drone_id,
            DRONE_STREAM_STATE.c.producer == identity.producer,
            comparison,
        )
        .values(high_water=candidate)
        .returning(DRONE_STREAM_STATE.c.drone_id)
    )


async def _locked_high_water(
    session: FleetSession,
    identity: DroneStreamIdentity,
) -> int | None:
    """Register and lock one exact stream, refusing malformed or conflicting state."""
    await session.scalar(register_stream_statement(identity))
    selected = await session.execute(stream_lock_statement(identity))
    row = selected.one_or_none()
    if row is None or len(row) != _STREAM_MEMBER_COUNT:
        raise FleetStoreError(FleetStoreRefusal.IDENTITY_CONFLICT, identity.drone_id)
    producer, high_water = row
    valid_high_water = high_water is None or (
        type(high_water) is int and 0 <= high_water <= MAXIMUM_PRODUCER_SEQUENCE
    )
    if producer != identity.producer or not valid_high_water:
        raise FleetStoreError(FleetStoreRefusal.IDENTITY_CONFLICT, identity.drone_id)
    return cast("int | None", high_water)


def _valid_sequence(sequence: object) -> bool:
    """Return whether a candidate fits the canonical fifteen-digit producer range."""
    return type(sequence) is int and 0 <= sequence <= MAXIMUM_PRODUCER_SEQUENCE


async def admit_sequence(
    session: FleetSession,
    identity: DroneStreamIdentity,
    sequence: int,
) -> SequenceVerdict:
    """Persist one producer-scoped high-water advance and return the domain verdict."""
    if not _valid_sequence(sequence):
        raise FleetStoreError(FleetStoreRefusal.INVALID_SEQUENCE, sequence)
    previous = await _locked_high_water(session, identity)
    reception = receive(Stream(previous), sequence)
    if reception.verdict is not SequenceVerdict.ADVANCES:
        return reception.verdict
    changed = await session.scalar(advance_stream_statement(identity, previous, sequence))
    if changed is None:
        raise FleetStoreError(FleetStoreRefusal.STREAM_CHANGED, identity.drone_id)
    return reception.verdict


def critical_usage_statement(identity: DroneStreamIdentity) -> FleetSelection:
    """Measure unconfirmed exact topic, header, and body octets for one producer."""
    byte_count = (
        func.octet_length(APPLICATION_OUTBOX.c.topic)
        + func.octet_length(APPLICATION_OUTBOX.c.headers)
        + func.octet_length(APPLICATION_OUTBOX.c.payload)
    )
    statement = select(
        func.count(APPLICATION_OUTBOX.c.event_id),
        func.coalesce(func.sum(byte_count), 0),
    ).where(
        APPLICATION_OUTBOX.c.producer == identity.producer,
        or_(
            APPLICATION_OUTBOX.c.state == _UNCONFIRMED_STATES[0],
            APPLICATION_OUTBOX.c.state == _UNCONFIRMED_STATES[1],
        ),
    )
    return cast("FleetSelection", statement)


def critical_size(event: StagedApplicationEvent) -> int:
    """Return the exact topic, header, and body octets one critical row consumes."""
    return len(event.topic.encode("utf-8")) + len(event.headers) + len(event.payload)


def _validate_critical(
    identity: DroneStreamIdentity,
    event: StagedApplicationEvent,
) -> int:
    """Validate fleet ownership and family before any database I/O."""
    if event.family not in _CRITICAL_FAMILIES:
        raise FleetStoreError(FleetStoreRefusal.NONCRITICAL_FAMILY, event.family)
    if event.producer != identity.producer:
        raise FleetStoreError(FleetStoreRefusal.IDENTITY_CONFLICT, identity.drone_id)
    size = critical_size(event)
    if size > CRITICAL_OUTBOX_MAX_BYTES:
        raise FleetStoreError(FleetStoreRefusal.BYTE_CAPACITY, event.event_id)
    return size


async def _critical_usage(
    session: FleetSession,
    identity: DroneStreamIdentity,
) -> tuple[int, int]:
    """Return a strictly typed nonnegative count-and-bytes pair."""
    selected = await session.execute(critical_usage_statement(identity))
    row = selected.one_or_none()
    if row is None or len(row) != _USAGE_MEMBER_COUNT:
        raise FleetStoreError(FleetStoreRefusal.UNREADABLE_USAGE, identity.drone_id)
    records, used_bytes = row
    valid = type(records) is int and type(used_bytes) is int and records >= 0 and used_bytes >= 0
    if not valid:
        raise FleetStoreError(FleetStoreRefusal.UNREADABLE_USAGE, identity.drone_id)
    return cast("tuple[int, int]", (records, used_bytes))


async def stage_critical(
    session: FleetSession,
    identity: DroneStreamIdentity,
    event: StagedApplicationEvent,
) -> None:
    """Stage one critical row while holding its drone's dual-capacity lock."""
    size = _validate_critical(identity, event)
    await _locked_high_water(session, identity)
    records, used_bytes = await _critical_usage(session, identity)
    if records >= CRITICAL_OUTBOX_MAX_RECORDS:
        raise FleetStoreError(FleetStoreRefusal.RECORD_CAPACITY, identity.drone_id)
    if used_bytes + size > CRITICAL_OUTBOX_MAX_BYTES:
        raise FleetStoreError(FleetStoreRefusal.BYTE_CAPACITY, identity.drone_id)
    try:
        await stage(cast("ApplicationOutboxSession", session), event)
    except ApplicationOutboxError as error:
        raise FleetStoreError(FleetStoreRefusal.EVENT_CONFLICT, event.event_id) from error


def effect_statement(effect: DurableCommandEffect) -> Insert:
    """Insert one immutable effect without overwriting command identity."""
    proposed = postgresql_insert(DRONE_COMMAND_EFFECT).values(
        drone_id=effect.identity.drone_id,
        command_id=effect.identity.command_id,
        mission_id=effect.identity.mission_id,
        command_digest=effect.identity.command_digest,
        outcome=effect.outcome.value,
        effect_payload=effect.effect_payload,
        applied_sequence=effect.applied_sequence,
        applied_at=effect.applied_at,
    )
    inserted = proposed.on_conflict_do_nothing()
    return inserted.returning(DRONE_COMMAND_EFFECT.c.command_id)


def _valid_effect(effect: DurableCommandEffect) -> bool:
    """Return whether an effect can satisfy the migrated immutable shape."""
    outcome: object = effect.outcome
    payload: object = effect.effect_payload
    applied_sequence: object = effect.applied_sequence
    applied_at: object = effect.applied_at
    structurally_valid = (
        isinstance(outcome, CommandEffectOutcome)
        and isinstance(payload, bytes)
        and bool(payload)
        and type(applied_sequence) is int
        and applied_sequence >= 0
        and _DIGEST_PATTERN.fullmatch(effect.identity.command_digest) is not None
    )
    if not structurally_valid or not isinstance(applied_at, str):
        return False
    try:
        parse_instant(applied_at)
    except ValueError:
        return False
    return True


async def record_effect(
    session: FleetSession,
    effect: DurableCommandEffect,
) -> DurableCommandEffect:
    """Store one effect exactly once after its receipt has been claimed."""
    if not _valid_effect(effect):
        raise FleetStoreError(FleetStoreRefusal.INVALID_EFFECT, effect.identity.command_id)
    inserted = await session.scalar(effect_statement(effect))
    if inserted is None:
        raise FleetStoreError(FleetStoreRefusal.EFFECT_CONFLICT, effect.identity.command_id)
    return effect


class FleetTransaction:
    """Purpose-specific fleet operations over one caller-owned SQLAlchemy transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the one session that makes every exposed effect atomic."""
        self._session = session

    async def admit_sequence(
        self,
        identity: DroneStreamIdentity,
        sequence: int,
    ) -> SequenceVerdict:
        """Persist one producer sequence decision."""
        return await admit_sequence(cast("FleetSession", self._session), identity, sequence)

    async def claim_receipt(self, identity: CommandReceiptIdentity) -> ReceiptOutcome:
        """Claim new command identity or return its exact prior completion."""
        return await claim(cast("ReceiptSession", self._session), identity)

    async def persist_outcome(
        self,
        stream: DroneStreamIdentity,
        effect: DurableCommandEffect,
        publications: Sequence[StagedApplicationEvent],
        final_result: bytes,
    ) -> None:
        """Persist effect, critical publications, and completed receipt as one atomic set."""
        bound = (
            effect.identity.drone_id == stream.drone_id
            and bool(publications)
            and publications[-1].payload == final_result
        )
        if not bound:
            raise FleetStoreError(FleetStoreRefusal.RESULT_BINDING, effect.identity.command_id)
        session = cast("FleetSession", self._session)
        await record_effect(session, effect)
        for publication in publications:
            await stage_critical(session, stream, publication)
        await complete(
            cast("ReceiptSession", self._session),
            effect.identity,
            final_result,
            effect.applied_sequence,
            effect.applied_at,
        )


class FleetTransactions:
    """Construct fresh durable fleet transactions over an injected lazy session factory."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the factory without opening a connection."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[FleetTransaction]:
        """Return a transaction that commits every fleet effect or none of them."""
        return _open(self._factory)


@asynccontextmanager
async def _open(
    factory: Callable[[], AsyncSession],
) -> AsyncIterator[FleetTransaction]:
    """Adapt the shared commit-or-rollback boundary to fleet-purpose operations."""
    async with transaction(factory) as session:
        yield FleetTransaction(session)
