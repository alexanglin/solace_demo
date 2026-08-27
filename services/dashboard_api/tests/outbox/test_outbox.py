"""Bounded dashboard application-outbox publication tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

import pytest
from aerial_rescue_dashboard_api.messaging.outbox import (
    DashboardOutboxPublisher,
    PublicationOutcome,
    PublicationResult,
    drain_once,
)
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)

TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"
EVENT: Final = StagedApplicationEvent(
    "dashboard-api",
    "event-1",
    "operator-command",
    "aerial-rescue/v1/mission-1/operator/command/assign-sector",
    b"{}",
    b'{"event":"canonical"}',
    TRACEPARENT,
    None,
    "command-1",
    None,
    "2026-08-26T12:00:00.000Z",
)


@dataclass
class _Store:
    pending_rows: tuple[StagedApplicationEvent, ...] = (EVENT,)
    writes: list[tuple[ApplicationEventIdentity, OutboxEvent, str | None]] = field(
        default_factory=list
    )

    async def pending(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        return self.pending_rows

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        self.writes.append((identity, event, confirmed_at))


@dataclass
class _Publisher:
    result: PublicationResult
    events: list[StagedApplicationEvent] = field(default_factory=list)

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        self.events.append(event)
        return self.result


@dataclass
class _Router:
    calls: list[tuple[str, bytes, Mapping[str, object]]] = field(default_factory=list)

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        self.calls.append((topic, payload, properties))


@pytest.mark.asyncio
async def test_confirmed_row_is_published_exactly_and_advanced_with_evidence() -> None:
    # Arrange
    store = _Store()
    publisher = _Publisher(
        PublicationResult(PublicationOutcome.CONFIRMED, "2026-08-26T12:00:01.000Z")
    )

    # Act
    result = await drain_once(store, publisher)

    # Assert
    assert result.confirmed == 1
    assert publisher.events == [EVENT]
    assert store.writes == [
        (
            ApplicationEventIdentity("dashboard-api", "event-1"),
            OutboxEvent.CONFIRM,
            "2026-08-26T12:00:01.000Z",
        )
    ]


@pytest.mark.asyncio
async def test_definite_refusal_leaves_the_row_staged_for_explicit_recovery() -> None:
    # Arrange
    store = _Store()
    publisher = _Publisher(PublicationResult(PublicationOutcome.REFUSED, None))

    # Act
    result = await drain_once(store, publisher)

    # Assert
    assert result.refused == 1
    assert store.writes == []


@pytest.mark.asyncio
async def test_broker_publisher_rehydrates_only_the_closed_properties_object() -> None:
    # Arrange
    router = _Router()
    publisher = DashboardOutboxPublisher(
        router,
        confirmed_at=lambda: "2026-08-26T12:00:01.000Z",
    )

    # Act
    result = await publisher.publish(EVENT)

    # Assert
    assert result.outcome is PublicationOutcome.CONFIRMED
    assert router.calls == [(EVENT.topic, EVENT.payload, {})]
