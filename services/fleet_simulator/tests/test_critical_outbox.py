"""Per-drone critical outbox recovery and readiness gating."""

from __future__ import annotations

import unittest
from typing import cast, override

import pytest
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_fleet_simulator.critical_outbox import (
    CriticalOutboxError,
    CriticalOutboxRefusal,
    PublicationOutcome,
    PublicationResult,
    drain_recovery,
    require_critical,
)
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)

pytestmark = [pytest.mark.unit]

DRONE = "drone-vision-01"


def _event(
    event_id: str = "event-1",
    family: str = "drone-command-result",
) -> StagedApplicationEvent:
    """Return one exact staged event."""
    return StagedApplicationEvent(
        producer=f"urn:aerial-rescue:drone:{DRONE}",
        event_id=event_id,
        family=family,
        topic=f"aerial-rescue/v1/m-1/drone/{DRONE}/command-result/cmd-1",
        headers=b"{}",
        payload=b"{}",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
        tracestate=None,
        correlation_id="corr-1",
        causation_id="cause-1",
        staged_at="2026-08-25T12:00:00.000Z",
    )


class FakeStore:
    def __init__(self, pending: dict[str, list[StagedApplicationEvent]]) -> None:
        """Retain mutable pending rows and publication records."""
        self.rows = pending
        self.recorded: list[tuple[ApplicationEventIdentity, OutboxEvent, str | None]] = []

    async def pending(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Return the current durable rows for one drone."""
        return tuple(self.rows.get(drone_id, ()))

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record confirmation or ambiguity and remove it from the staged view."""
        self.recorded.append((identity, event, confirmed_at))
        for rows in self.rows.values():
            rows[:] = [row for row in rows if row.event_id != identity.event_id]


class StickyStore(FakeStore):
    """Records broker evidence while simulating an independently added staged row."""

    @override
    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record without emptying the staged readback."""
        self.recorded.append((identity, event, confirmed_at))


class FakePublisher:
    def __init__(self, outcomes: list[PublicationResult]) -> None:
        """Return scripted broker outcomes and record exact events."""
        self.outcomes = outcomes
        self.published: list[StagedApplicationEvent] = []

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Publish one exact row and return its scripted outcome."""
        self.published.append(event)
        return self.outcomes.pop(0)


class FakeReadiness:
    def __init__(self, connected: bool) -> None:
        """Begin in one transport state and record application readiness."""
        self.connected = connected
        self.ready = False
        self.recovery_calls = 0

    def is_connected(self) -> bool:
        """Return the current transport state."""
        return self.connected

    def recovery_required(self) -> None:
        """Remove application readiness."""
        self.recovery_calls += 1
        self.ready = False

    def mark_ready(self) -> None:
        """Restore application readiness after recovery."""
        self.ready = True


class CriticalOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_drains_every_exact_row_before_restoring_readiness(self) -> None:
        # Arrange
        events = [_event("event-1"), _event("event-2")]
        store = FakeStore({DRONE: events.copy()})
        confirmed = PublicationResult(
            PublicationOutcome.CONFIRMED,
            "2026-08-25T12:00:01.000Z",
        )
        publisher = FakePublisher([confirmed, confirmed])
        readiness = FakeReadiness(connected=True)

        # Act
        report = await drain_recovery((DRONE,), store, publisher, readiness)

        # Assert
        self.assertEqual((report.visited, report.confirmed, report.ready), (2, 2, True))
        self.assertEqual(publisher.published, events)
        self.assertEqual(len(store.recorded), 2)
        self.assertTrue(readiness.ready)

    async def test_disconnected_transport_performs_no_broker_or_store_mutation(self) -> None:
        # Arrange
        store = FakeStore({DRONE: [_event()]})
        publisher = FakePublisher([])
        readiness = FakeReadiness(connected=False)

        # Act
        report = await drain_recovery((DRONE,), store, publisher, readiness)

        # Assert
        self.assertEqual((report.visited, report.ready), (0, False))
        self.assertEqual((publisher.published, store.recorded), ([], []))
        self.assertFalse(readiness.ready)

    async def test_refusal_stays_staged_and_ambiguity_is_recorded_without_readiness(self) -> None:
        # Arrange
        first = _event("event-refused")
        second = _event("event-ambiguous")
        store = FakeStore({DRONE: [first, second]})
        publisher = FakePublisher(
            [
                PublicationResult(PublicationOutcome.REFUSED, None),
                PublicationResult(PublicationOutcome.AMBIGUOUS, None),
            ]
        )
        readiness = FakeReadiness(connected=True)

        # Act
        report = await drain_recovery((DRONE,), store, publisher, readiness)

        # Assert
        self.assertEqual(
            (report.refused, report.ambiguous, report.ready),
            (1, 1, False),
        )
        self.assertEqual(store.rows[DRONE], [first])
        self.assertEqual(store.recorded[0][1], OutboxEvent.AMBIGUOUS)
        self.assertFalse(readiness.ready)

    async def test_telemetry_can_never_enter_the_critical_outbox(self) -> None:
        # Arrange
        telemetry = _event(family="drone-telemetry")

        # Act
        with pytest.raises(CriticalOutboxError) as raised:
            require_critical(telemetry)

        # Assert
        self.assertEqual(raised.value.refusal, CriticalOutboxRefusal.NONCRITICAL_FAMILY)

    async def test_confirmation_evidence_and_batch_size_fail_closed(self) -> None:
        # Arrange
        malformed_results = (
            (PublicationOutcome.CONFIRMED, None),
            (PublicationOutcome.REFUSED, "2026-08-25T12:00:01.000Z"),
            (PublicationOutcome.CONFIRMED, "not-an-instant"),
        )
        failures: list[CriticalOutboxRefusal] = []
        overfull = FakeStore({DRONE: [_event(f"event-{index}") for index in range(51)]})

        # Act
        for outcome, instant in malformed_results:
            with pytest.raises(CriticalOutboxError) as raised:
                PublicationResult(outcome, instant)
            failures.append(cast("CriticalOutboxRefusal", raised.value.refusal))
        with pytest.raises(CriticalOutboxError) as raised_batch:
            await drain_recovery(
                (DRONE,),
                overfull,
                FakePublisher([]),
                FakeReadiness(connected=True),
            )

        # Assert
        self.assertEqual(
            failures,
            [CriticalOutboxRefusal.CONFIRMATION_EVIDENCE] * 3,
        )
        self.assertEqual(raised_batch.value.refusal, CriticalOutboxRefusal.BATCH_BOUND)

    async def test_newly_staged_work_keeps_recovery_unready_after_confirmation(self) -> None:
        # Arrange
        store = StickyStore({DRONE: [_event()]})
        publisher = FakePublisher(
            [
                PublicationResult(
                    PublicationOutcome.CONFIRMED,
                    "2026-08-25T12:00:01.000Z",
                )
            ]
        )
        readiness = FakeReadiness(connected=True)

        # Act
        report = await drain_recovery((DRONE,), store, publisher, readiness)

        # Assert
        self.assertEqual((report.confirmed, report.ready), (1, False))
        self.assertFalse(readiness.ready)


if __name__ == "__main__":
    unittest.main()
