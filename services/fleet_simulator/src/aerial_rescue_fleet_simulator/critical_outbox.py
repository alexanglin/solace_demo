"""Bounded recovery for each simulated drone's exact critical publications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    StagedApplicationEvent,
)

from aerial_rescue_fleet_simulator import FleetSimulatorError

_CRITICAL_FAMILIES: Final = frozenset({"drone-event", "drone-command-result", "sector-event"})


class CriticalOutboxRefusal(Enum):
    """Why a staged row or recovery result cannot be used safely."""

    NONCRITICAL_FAMILY = "only critical fleet families may enter the durable edge outbox"
    BATCH_BOUND = "the durable store returned more than one bounded recovery batch"
    CONFIRMATION_EVIDENCE = "only confirmation carries a valid confirmation instant"


class CriticalOutboxError(FleetSimulatorError):
    """A fleet critical-outbox operation refused with a closed reason."""


class PublicationOutcome(Enum):
    """The three publisher outcomes that durable recovery distinguishes."""

    CONFIRMED = "confirmed"
    REFUSED = "refused"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class PublicationResult:
    """One broker result with confirmation evidence only on success."""

    outcome: PublicationOutcome
    confirmed_at: str | None

    def __post_init__(self) -> None:
        """Refuse missing, invented, or malformed confirmation evidence."""
        confirmed = self.outcome is PublicationOutcome.CONFIRMED
        if confirmed is not (self.confirmed_at is not None):
            raise CriticalOutboxError(CriticalOutboxRefusal.CONFIRMATION_EVIDENCE, self.outcome)
        if self.confirmed_at is not None:
            try:
                parse_instant(self.confirmed_at)
            except ValueError as error:
                raise CriticalOutboxError(
                    CriticalOutboxRefusal.CONFIRMATION_EVIDENCE,
                    self.outcome,
                ) from error


class CriticalOutboxPort(Protocol):
    """Read and independently update one drone's exact durable publications."""

    async def pending(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Return at most one oldest-first batch for one drone."""

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Persist one confirmation or ambiguity compare-and-set."""


class CriticalPublisherPort(Protocol):
    """Publish an exact staged row through the typed broker router."""

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Return definite refusal, ambiguity, or broker confirmation."""


class RecoveryReadiness(Protocol):
    """Connection and application-recovery readiness for one broker epoch."""

    def is_connected(self) -> bool:
        """Return whether broker I/O is currently permitted."""

    def recovery_required(self) -> None:
        """Remove readiness before inspecting durable recovery work."""

    def mark_ready(self) -> None:
        """Restore readiness only after bindings and outboxes are reconciled."""


@dataclass(frozen=True)
class RecoveryReport:
    """Counts from one bounded connected-epoch recovery attempt."""

    visited: int
    confirmed: int
    refused: int
    ambiguous: int
    ready: bool


def require_critical(event: StagedApplicationEvent) -> StagedApplicationEvent:
    """Return a critical row and refuse telemetry or another noncritical family."""
    if event.family not in _CRITICAL_FAMILIES:
        raise CriticalOutboxError(CriticalOutboxRefusal.NONCRITICAL_FAMILY, event.family)
    return event


async def _record(
    store: CriticalOutboxPort,
    event: StagedApplicationEvent,
    result: PublicationResult,
) -> None:
    """Leave refusal staged and record only broker evidence or ambiguity."""
    if result.outcome is PublicationOutcome.REFUSED:
        return
    transition = (
        OutboxEvent.CONFIRM
        if result.outcome is PublicationOutcome.CONFIRMED
        else OutboxEvent.AMBIGUOUS
    )
    await store.record(
        ApplicationEventIdentity(event.producer, event.event_id),
        transition,
        result.confirmed_at,
    )


async def _batch(store: CriticalOutboxPort, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
    """Read and validate one bounded critical batch."""
    pending = await store.pending(drone_id)
    if len(pending) > APPLICATION_OUTBOX_BATCH_SIZE:
        raise CriticalOutboxError(CriticalOutboxRefusal.BATCH_BOUND, drone_id)
    return tuple(require_critical(event) for event in pending)


async def _has_pending(store: CriticalOutboxPort, drone_ids: tuple[str, ...]) -> bool:
    """Return whether any staged work remains after the attempted batch."""
    for drone_id in drone_ids:
        if await _batch(store, drone_id):
            return True
    return False


async def drain_recovery(
    drone_ids: tuple[str, ...],
    store: CriticalOutboxPort,
    publisher: CriticalPublisherPort,
    readiness: RecoveryReadiness,
) -> RecoveryReport:
    """Drain one bounded connected epoch and restore readiness only at an empty boundary."""
    readiness.recovery_required()
    if not readiness.is_connected():
        return RecoveryReport(0, 0, 0, 0, False)
    counts = {outcome: 0 for outcome in PublicationOutcome}
    visited = 0
    ordered = tuple(sorted(drone_ids))
    for drone_id in ordered:
        for event in await _batch(store, drone_id):
            result = await publisher.publish(event)
            visited += 1
            counts[result.outcome] += 1
            await _record(store, event, result)
    unresolved = (
        counts[PublicationOutcome.REFUSED] > 0
        or counts[PublicationOutcome.AMBIGUOUS] > 0
        or await _has_pending(store, ordered)
    )
    if not unresolved:
        readiness.mark_ready()
    return RecoveryReport(
        visited,
        counts[PublicationOutcome.CONFIRMED],
        counts[PublicationOutcome.REFUSED],
        counts[PublicationOutcome.AMBIGUOUS],
        not unresolved,
    )
