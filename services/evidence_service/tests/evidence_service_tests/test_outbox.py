from __future__ import annotations

import unittest

from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_evidence_service.outbox import (
    OutboxError,
    OutboxRefusal,
    PublicationOutcome,
    PublicationResult,
    drain_once,
)
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)


def _event(event_id: str, family: str) -> StagedApplicationEvent:
    """Return one exact staged evidence-service publication."""
    return StagedApplicationEvent(
        producer="evidence-service",
        event_id=event_id,
        family=family,
        topic=f"aerial-rescue/v1/m-2026-0001/{family}/test",
        headers=b"{}",
        payload=f'{{"id":"{event_id}"}}'.encode(),
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4739-b7ad6b7169203335-01",
        tracestate=None,
        correlation_id="correlation-bound-0001",
        causation_id="proposal-bound-0001",
        staged_at="2026-08-25T12:04:00.000Z",
    )


class FakeOutbox:
    """Return a restart-visible batch and record per-row outcomes."""

    def __init__(self, events: tuple[StagedApplicationEvent, ...]) -> None:
        """Configure the durable rows visible after restart."""
        self.events = events
        self.recorded: list[tuple[ApplicationEventIdentity, OutboxEvent, str | None]] = []

    async def pending(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return the pending rows in durable order."""
        return self.events

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record one independent publication outcome."""
        self.recorded.append((identity, event, confirmed_at))


class FakePublisher:
    """Confirm each exact event the worker republishes after restart."""

    def __init__(self) -> None:
        """Start with no publications."""
        self.published: list[str] = []

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Return broker confirmation for this exact event."""
        self.published.append(event.event_id)
        return PublicationResult(
            PublicationOutcome.CONFIRMED,
            "2026-08-25T12:05:00.000Z",
        )


class SequencedPublisher:
    """Return a configured result for each ordered event."""

    def __init__(self, results: tuple[PublicationResult, ...]) -> None:
        """Configure one broker result per publication."""
        self.results = iter(results)
        self.published: list[str] = []

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Record the event and return its configured result."""
        self.published.append(event.event_id)
        return next(self.results)


def _publication_refusal(outcome: PublicationOutcome, confirmed_at: str | None) -> OutboxRefusal:
    """Return an invalid publication result's refusal."""
    try:
        PublicationResult(outcome, confirmed_at)
    except OutboxError as error:
        return error.refusal
    message = "publication result unexpectedly accepted"
    raise AssertionError(message)


async def _drain_refusal(store: FakeOutbox, publisher: FakePublisher) -> OutboxRefusal:
    """Return a refused drain's structured reason."""
    try:
        await drain_once(store, publisher)
    except OutboxError as error:
        return error.refusal
    message = "outbox drain unexpectedly succeeded"
    raise AssertionError(message)


class RestartRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_restart_drains_the_exact_pending_decision_and_audit_in_order(self) -> None:
        # Arrange
        events = (
            _event("event-evidence-bound-0001", "evidence-decision"),
            _event("event-audit-bound-0001", "audit"),
        )
        store = FakeOutbox(events)
        publisher = FakePublisher()

        # Act
        result = await drain_once(store, publisher)

        # Assert
        self.assertEqual(
            (
                2,
                [event.event_id for event in events],
                [OutboxEvent.CONFIRM, OutboxEvent.CONFIRM],
            ),
            (
                result.confirmed,
                publisher.published,
                [event for _identity, event, _instant in store.recorded],
            ),
        )

    async def test_refusal_stays_staged_ambiguity_reconciles_and_later_rows_continue(self) -> None:
        # Arrange
        events = tuple(_event(f"event-evidence-bound-000{index}", "audit") for index in (1, 2, 3))
        results = (
            PublicationResult(PublicationOutcome.REFUSED, None),
            PublicationResult(PublicationOutcome.AMBIGUOUS, None),
            PublicationResult(
                PublicationOutcome.CONFIRMED,
                "2026-08-25T12:05:00.000Z",
            ),
        )
        store = FakeOutbox(events)
        publisher = SequencedPublisher(results)

        # Act
        result = await drain_once(store, publisher)

        # Assert
        self.assertEqual(
            (
                (3, 1, 1, 1),
                [event.event_id for event in events],
                [OutboxEvent.AMBIGUOUS, OutboxEvent.CONFIRM],
                [None, "2026-08-25T12:05:00.000Z"],
            ),
            (
                (result.visited, result.confirmed, result.refused, result.ambiguous),
                publisher.published,
                [event for _identity, event, _instant in store.recorded],
                [instant for _identity, _event, instant in store.recorded],
            ),
        )

    async def test_a_port_returning_more_than_fifty_rows_is_refused_before_broker_io(self) -> None:
        # Arrange
        events = tuple(_event(f"event-overbound-{index:04d}", "audit") for index in range(51))
        store = FakeOutbox(events)
        publisher = FakePublisher()

        # Act
        refusal = await _drain_refusal(store, publisher)

        # Assert
        self.assertEqual((OutboxRefusal.BATCH_BOUND, []), (refusal, publisher.published))


class PublicationEvidenceTests(unittest.TestCase):
    def test_a_confirmation_with_a_noncanonical_instant_is_refused(self) -> None:
        # Arrange
        expected = OutboxRefusal.CONFIRMATION_EVIDENCE

        # Act
        refusal = _publication_refusal(PublicationOutcome.CONFIRMED, "tomorrow")

        # Assert
        self.assertEqual(expected, refusal)

    def test_refusal_and_ambiguity_cannot_carry_a_confirmation_instant(self) -> None:
        # Arrange
        instant = "2026-08-25T12:05:00.000Z"
        outcomes = (PublicationOutcome.REFUSED, PublicationOutcome.AMBIGUOUS)

        # Act
        refusals = tuple(_publication_refusal(outcome, instant) for outcome in outcomes)

        # Assert
        self.assertEqual((OutboxRefusal.CONFIRMATION_EVIDENCE,) * 2, refusals)
