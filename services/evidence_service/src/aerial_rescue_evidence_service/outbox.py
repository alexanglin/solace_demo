"""Bounded recovery of exact evidence-service application outbox rows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    StagedApplicationEvent,
)

from aerial_rescue_evidence_service.publication import PRODUCER


class PublicationOutcome(Enum):
    """The three broker outcomes the outbox state machine distinguishes."""

    CONFIRMED = "confirmed"
    REFUSED = "refused"
    AMBIGUOUS = "ambiguous"


class OutboxRefusal(Enum):
    """Why a worker cannot safely interpret an outbox or broker result."""

    BATCH_BOUND = "outbox returned more than the bounded batch"
    CONFIRMATION_EVIDENCE = "confirmation requires an instant and other outcomes forbid one"


class OutboxError(ValueError):
    """A redacted outbox-drain refusal."""

    def __init__(self, refusal: OutboxRefusal) -> None:
        """Expose the closed recovery reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class PublicationResult:
    """One broker result with confirmation evidence only on success."""

    outcome: PublicationOutcome
    confirmed_at: str | None

    def __post_init__(self) -> None:
        """Reject results that would guess confirmation from missing evidence."""
        confirmed = self.outcome is PublicationOutcome.CONFIRMED
        if confirmed is not (self.confirmed_at is not None):
            raise OutboxError(OutboxRefusal.CONFIRMATION_EVIDENCE)
        if self.confirmed_at is not None:
            try:
                parse_instant(self.confirmed_at)
            except ValueError:
                raise OutboxError(OutboxRefusal.CONFIRMATION_EVIDENCE) from None


@dataclass(frozen=True)
class DrainResult:
    """Counts from one bounded connected-epoch iteration."""

    visited: int
    confirmed: int
    refused: int
    ambiguous: int


class OutboxPort(Protocol):
    """Read and independently update exact durable application publications."""

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return at most the package-owned batch cap in durable order."""

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Persist one confirmation or ambiguity compare-and-set."""


class PublisherPort(Protocol):
    """Publish one exact staged event through the typed broker router."""

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Return definite refusal, ambiguity, or broker confirmation."""


async def drain_once(store: OutboxPort, publisher: PublisherPort) -> DrainResult:
    """Publish one bounded ordered batch without holding a database transaction."""
    pending = await store.pending(PRODUCER)
    if len(pending) > APPLICATION_OUTBOX_BATCH_SIZE:
        raise OutboxError(OutboxRefusal.BATCH_BOUND)
    counts = {outcome: 0 for outcome in PublicationOutcome}
    for event in pending:
        result = await publisher.publish(event)
        counts[result.outcome] += 1
        await _record_if_needed(store, event, result)
    return DrainResult(
        visited=len(pending),
        confirmed=counts[PublicationOutcome.CONFIRMED],
        refused=counts[PublicationOutcome.REFUSED],
        ambiguous=counts[PublicationOutcome.AMBIGUOUS],
    )


async def _record_if_needed(
    store: OutboxPort,
    staged: StagedApplicationEvent,
    result: PublicationResult,
) -> None:
    """Leave refusal staged and record only broker evidence or ambiguity."""
    if result.outcome is PublicationOutcome.REFUSED:
        return
    event = (
        OutboxEvent.CONFIRM
        if result.outcome is PublicationOutcome.CONFIRMED
        else OutboxEvent.AMBIGUOUS
    )
    identity = ApplicationEventIdentity(staged.producer, staged.event_id)
    await store.record(identity, event, result.confirmed_at)
