"""Bounded recovery of exact dashboard application-outbox publications."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from aerial_rescue_broker.messaging import MessagingError, MessagingRefusal
from aerial_rescue_broker.routing import RoutingError
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    StagedApplicationEvent,
)

PRODUCER = "dashboard-api"


class PublicationOutcome(Enum):
    """The three broker outcomes the durable state machine distinguishes."""

    CONFIRMED = "confirmed"
    REFUSED = "refused"
    AMBIGUOUS = "ambiguous"


class OutboxRefusal(Enum):
    """Why an outbox row or broker result cannot be interpreted safely."""

    BATCH_BOUND = "outbox returned more than the bounded batch"
    CONFIRMATION_EVIDENCE = (
        "confirmation requires a valid instant and no other outcome may carry one"
    )


class OutboxError(ValueError):
    """A typed, body-free dashboard outbox refusal."""

    def __init__(self, refusal: OutboxRefusal) -> None:
        """Retain only the closed refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class PublicationResult:
    """One broker outcome with confirmation evidence only on success."""

    outcome: PublicationOutcome
    confirmed_at: str | None

    def __post_init__(self) -> None:
        """Refuse missing, misplaced, or malformed confirmation evidence."""
        if (self.outcome is PublicationOutcome.CONFIRMED) is not (self.confirmed_at is not None):
            raise OutboxError(OutboxRefusal.CONFIRMATION_EVIDENCE)
        if self.confirmed_at is not None:
            try:
                parse_instant(self.confirmed_at)
            except ValueError:
                raise OutboxError(OutboxRefusal.CONFIRMATION_EVIDENCE) from None


@dataclass(frozen=True)
class DrainResult:
    """Counts from one bounded connected-epoch drain."""

    visited: int
    confirmed: int
    refused: int
    ambiguous: int


class OutboxPort(Protocol):
    """Read and independently advance exact dashboard publications."""

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return one bounded durable oldest-first batch."""

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Persist explicit confirmation or ambiguity by compare-and-set."""


class PublisherPort(Protocol):
    """Publish one exact row through a least-privilege Guaranteed route."""

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Return the broker's classified outcome."""


class PublicationRouter(Protocol):
    """The role-authorized publisher capability used after durable staging."""

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Publish one exact staged event."""


class DashboardOutboxPublisher:
    """Publish staged dashboard rows through the typed Guaranteed router."""

    def __init__(self, router: PublicationRouter, confirmed_at: Callable[[], str]) -> None:
        """Retain explicit router and confirmation clock dependencies."""
        self._router = router
        self._confirmed_at = confirmed_at

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Map only explicit broker confirmation to terminal success."""
        try:
            self._router.publish(
                event.topic,
                event.payload,
                _properties(event.headers),
            )
        except MessagingError as error:
            outcome = (
                PublicationOutcome.REFUSED
                if error.refusal is MessagingRefusal.PUBLISH_REFUSED
                else PublicationOutcome.AMBIGUOUS
            )
            return PublicationResult(outcome, None)
        except RoutingError, TypeError, ValueError:
            return PublicationResult(PublicationOutcome.REFUSED, None)
        return PublicationResult(PublicationOutcome.CONFIRMED, self._confirmed_at())


def _properties(headers: bytes) -> Mapping[str, object]:
    """Decode the exact closed properties object without coercion."""
    properties = canonical.decode(headers)
    if not isinstance(properties, Mapping):
        raise TypeError
    return cast("Mapping[str, object]", properties)


async def drain_once(store: OutboxPort, publisher: PublisherPort) -> DrainResult:
    """Publish one finite oldest-first batch outside all database transactions."""
    rows = await store.pending(PRODUCER)
    if len(rows) > APPLICATION_OUTBOX_BATCH_SIZE:
        raise OutboxError(OutboxRefusal.BATCH_BOUND)
    counts = {outcome: 0 for outcome in PublicationOutcome}
    for row in rows:
        result = await publisher.publish(row)
        counts[result.outcome] += 1
        await _record(store, row, result)
    return DrainResult(
        visited=len(rows),
        confirmed=counts[PublicationOutcome.CONFIRMED],
        refused=counts[PublicationOutcome.REFUSED],
        ambiguous=counts[PublicationOutcome.AMBIGUOUS],
    )


async def _record(
    store: OutboxPort,
    row: StagedApplicationEvent,
    result: PublicationResult,
) -> None:
    """Leave definite refusals staged and persist all other broker evidence."""
    if result.outcome is PublicationOutcome.REFUSED:
        return
    event = (
        OutboxEvent.CONFIRM
        if result.outcome is PublicationOutcome.CONFIRMED
        else OutboxEvent.AMBIGUOUS
    )
    await store.record(
        ApplicationEventIdentity(row.producer, row.event_id),
        event,
        result.confirmed_at,
    )
