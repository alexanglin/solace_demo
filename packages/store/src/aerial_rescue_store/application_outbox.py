"""Exact application publications and bounded deterministic outbox draining.

The repository stages canonical topic, headers, and payload bytes transactionally with the
domain effect that produced them.  A worker reads at most fifty oldest staged rows, performs
broker I/O outside a database transaction, and records each confirmed or ambiguous outcome with
an independent compare-and-set.  A definite refusal deliberately performs no state write.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_domain.outbox import INITIAL_STATE, OutboxEvent, OutboxState, transition
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import APPLICATION_OUTBOX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

APPLICATION_OUTBOX_BATCH_SIZE: Final = 50
"""Maximum rows one connected worker reads in one iteration (ADR-0146)."""

STORED_MEMBER_COUNT: Final = 13

type PendingSelection = Select[tuple[object, ...]]
type StagedSelection = Select[tuple[str]]


class ApplicationOutboxRefusal(Enum):
    """Why staging, reading, or recording one publication cannot proceed."""

    ALREADY_STAGED = "the producer already staged that event identity"
    UNREADABLE_ROW = "a staged outbox row does not match its migrated typed shape"
    CONFIRMATION_EVIDENCE = "confirmation requires an instant and ambiguity forbids one"
    NOT_IN_EXPECTED_STATE = "the outbox row was no longer in the compared state"


class ApplicationOutboxError(StoreError):
    """An application outbox operation this repository refuses."""


@dataclass(frozen=True)
class StagedApplicationEvent:
    """One exact application publication, before any broker I/O occurs."""

    producer: str
    event_id: str
    family: str
    topic: str
    headers: bytes
    payload: bytes
    traceparent: str
    tracestate: str | None
    correlation_id: str
    causation_id: str | None
    staged_at: str


@dataclass(frozen=True)
class ApplicationEventIdentity:
    """The producer-scoped event key used by publication compare-and-set."""

    producer: str
    event_id: str


def stage_statement(event: StagedApplicationEvent) -> Insert:
    """Return an insert that never overwrites an existing event identity."""
    proposed = postgresql_insert(APPLICATION_OUTBOX).values(
        producer=event.producer,
        event_id=event.event_id,
        family=event.family,
        topic=event.topic,
        headers=event.headers,
        payload=event.payload,
        state=INITIAL_STATE.value,
        traceparent=event.traceparent,
        tracestate=event.tracestate,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        staged_at=event.staged_at,
        confirmed_at=None,
    )
    staged = proposed.on_conflict_do_nothing(
        index_elements=[APPLICATION_OUTBOX.c.producer, APPLICATION_OUTBOX.c.event_id]
    )
    return staged.returning(APPLICATION_OUTBOX.c.event_id)


def staged_statement(identity: ApplicationEventIdentity) -> StagedSelection:
    """Return an exact primary-key read asking whether one identity is already staged."""
    return select(APPLICATION_OUTBOX.c.event_id).where(
        APPLICATION_OUTBOX.c.producer == identity.producer,
        APPLICATION_OUTBOX.c.event_id == identity.event_id,
    )


def _selection_statement(producer: str, state: OutboxState) -> PendingSelection:
    """Return at most fifty oldest rows in one exact publication state."""
    statement = (
        select(*APPLICATION_OUTBOX.c)
        .where(
            APPLICATION_OUTBOX.c.producer == producer,
            APPLICATION_OUTBOX.c.state == state.value,
        )
        .order_by(APPLICATION_OUTBOX.c.staged_at, APPLICATION_OUTBOX.c.event_id)
        .limit(APPLICATION_OUTBOX_BATCH_SIZE)
    )
    return cast("PendingSelection", statement)


def pending_statement(producer: str) -> PendingSelection:
    """Return at most fifty oldest staged rows for one producer."""
    return _selection_statement(producer, OutboxState.STAGED)


def reconciliation_statement(producer: str) -> PendingSelection:
    """Return at most fifty oldest ambiguous rows without making them publishable."""
    return _selection_statement(producer, OutboxState.RECONCILIATION_NEEDED)


def publication_statement(
    producer: str,
    event_id: str,
    was: OutboxState,
    became: OutboxState,
    confirmed_at: str | None,
) -> Update:
    """Return a per-row compare-and-set for one broker publication outcome."""
    return (
        update(APPLICATION_OUTBOX)
        .where(
            APPLICATION_OUTBOX.c.producer == producer,
            APPLICATION_OUTBOX.c.event_id == event_id,
            APPLICATION_OUTBOX.c.state == was.value,
        )
        .values(state=became.value, confirmed_at=confirmed_at)
        .returning(APPLICATION_OUTBOX.c.event_id)
    )


class PendingRows(Protocol):
    """The ordered staged rows returned by one bounded read."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return every selected row in database order."""


class ApplicationOutboxSession(Protocol):
    """The injected SQLAlchemy session operations this repository requires."""

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Return one affected event identity, or ``None``."""

    async def execute(self, statement: PendingSelection | StagedSelection, /) -> PendingRows:
        """Return one bounded ordered batch, or one existence answer."""


async def stage(session: ApplicationOutboxSession, event: StagedApplicationEvent) -> None:
    """Stage exact bytes once, refusing an existing producer/event identity."""
    staged = await session.scalar(stage_statement(event))
    if staged is None:
        raise ApplicationOutboxError(ApplicationOutboxRefusal.ALREADY_STAGED, event.event_id)


async def is_staged(session: ApplicationOutboxSession, identity: ApplicationEventIdentity) -> bool:
    """Report whether one producer already holds that event identity in the outbox.

    ``stage`` refuses an existing identity rather than ignoring it, so a producer whose
    event identity is derived rather than generated must ask before it stages. Rows are
    never deleted, so a true answer is permanent and survives a process restart.
    """
    selected = await session.execute(staged_statement(identity))
    return len(selected.all()) > 0


async def pending(
    session: ApplicationOutboxSession, producer: str
) -> tuple[StagedApplicationEvent, ...]:
    """Return one bounded ordered batch of strictly validated staged rows."""
    selected = await session.execute(pending_statement(producer))
    return tuple(_stored(row, OutboxState.STAGED) for row in selected.all())


async def reconciliation(
    session: ApplicationOutboxSession, producer: str
) -> tuple[StagedApplicationEvent, ...]:
    """Return ambiguous rows for evidence-only reconciliation, never blind retry."""
    selected = await session.execute(reconciliation_statement(producer))
    return tuple(_stored(row, OutboxState.RECONCILIATION_NEEDED) for row in selected.all())


def _stored(row: Sequence[object], expected_state: OutboxState) -> StagedApplicationEvent:
    """Map one database row without coercing malformed persisted values."""
    if len(row) != STORED_MEMBER_COUNT:
        raise ApplicationOutboxError(ApplicationOutboxRefusal.UNREADABLE_ROW, "shape")
    (
        producer,
        event_id,
        family,
        topic,
        headers,
        payload,
        state,
        traceparent,
        tracestate,
        correlation_id,
        causation_id,
        staged_at,
        confirmed_at,
    ) = row
    required_text = (
        producer,
        event_id,
        family,
        topic,
        traceparent,
        correlation_id,
        staged_at,
    )
    optional_text = (tracestate, causation_id)
    valid = (
        all(isinstance(value, str) for value in required_text)
        and all(value is None or isinstance(value, str) for value in optional_text)
        and isinstance(headers, bytes)
        and isinstance(payload, bytes)
        and state == expected_state.value
        and confirmed_at is None
    )
    if not valid:
        raise ApplicationOutboxError(ApplicationOutboxRefusal.UNREADABLE_ROW, event_id)
    return StagedApplicationEvent(
        producer=cast("str", producer),
        event_id=cast("str", event_id),
        family=cast("str", family),
        topic=cast("str", topic),
        headers=cast("bytes", headers),
        payload=cast("bytes", payload),
        traceparent=cast("str", traceparent),
        tracestate=cast("str | None", tracestate),
        correlation_id=cast("str", correlation_id),
        causation_id=cast("str | None", causation_id),
        staged_at=cast("str", staged_at),
    )


async def record_publication(
    session: ApplicationOutboxSession,
    identity: ApplicationEventIdentity,
    was: OutboxState,
    event: OutboxEvent,
    confirmed_at: str | None,
) -> OutboxState:
    """Record confirmation or ambiguity without guessing a refused publication succeeded."""
    became = transition(was, event)
    has_confirmation = confirmed_at is not None
    if (became is OutboxState.CONFIRMED) is not has_confirmation:
        raise ApplicationOutboxError(
            ApplicationOutboxRefusal.CONFIRMATION_EVIDENCE, identity.event_id
        )
    changed = await session.scalar(
        publication_statement(identity.producer, identity.event_id, was, became, confirmed_at)
    )
    if changed is None:
        raise ApplicationOutboxError(
            ApplicationOutboxRefusal.NOT_IN_EXPECTED_STATE, identity.event_id
        )
    return became
