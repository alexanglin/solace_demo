"""Failure and ambiguity coverage for dashboard outbox publication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final

import pytest
from aerial_rescue_broker.messaging import MessagingError, MessagingRefusal
from aerial_rescue_broker.routing import RoutingError, RoutingRefusal
from aerial_rescue_dashboard_api.outbox import (
    DashboardOutboxPublisher,
    OutboxError,
    PublicationOutcome,
    PublicationResult,
    drain_once,
)
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    StagedApplicationEvent,
)

_TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"
_CONFIRMED_AT: Final = "2026-08-26T12:00:01.000Z"
_EVENT: Final = StagedApplicationEvent(
    "dashboard-api",
    "event-1",
    "operator-command",
    "aerial-rescue/v1/mission-1/operator/command/assign-sector",
    b"{}",
    b'{"event":"canonical"}',
    _TRACEPARENT,
    None,
    "command-1",
    None,
    "2026-08-26T12:00:00.000Z",
)


@dataclass
class _Store:
    rows: tuple[StagedApplicationEvent, ...]
    writes: list[tuple[ApplicationEventIdentity, OutboxEvent, str | None]] = field(
        default_factory=list
    )

    async def pending(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        return self.rows

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

    async def publish(self, _event: StagedApplicationEvent) -> PublicationResult:
        return self.result


@dataclass
class _FailingRouter:
    failure: BaseException | None = None

    def publish(
        self,
        _topic: str,
        _payload: bytes,
        _properties: Mapping[str, object],
        /,
    ) -> None:
        if self.failure is not None:
            raise self.failure


@pytest.mark.parametrize(
    ("outcome", "evidence"),
    [
        (PublicationOutcome.CONFIRMED, None),
        (PublicationOutcome.REFUSED, _CONFIRMED_AT),
        (PublicationOutcome.AMBIGUOUS, _CONFIRMED_AT),
        (PublicationOutcome.CONFIRMED, "invalid-instant"),
    ],
)
def test_publication_result_refuses_missing_misplaced_or_malformed_evidence(
    outcome: PublicationOutcome,
    evidence: str | None,
) -> None:
    # Arrange
    values = (outcome, evidence)

    # Act
    with pytest.raises(OutboxError) as captured:
        PublicationResult(*values)

    # Assert
    assert "confirmation requires a valid instant" in str(captured.value)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            MessagingError(MessagingRefusal.PUBLISH_REFUSED, "redacted"),
            PublicationOutcome.REFUSED,
        ),
        (
            MessagingError(MessagingRefusal.PUBLISH_AMBIGUOUS, "redacted"),
            PublicationOutcome.AMBIGUOUS,
        ),
        (
            RoutingError(RoutingRefusal.INVALID_TOPIC, "redacted"),
            PublicationOutcome.REFUSED,
        ),
        (ValueError("redacted"), PublicationOutcome.REFUSED),
    ],
)
@pytest.mark.asyncio
async def test_broker_publisher_classifies_only_explicit_ambiguity(
    failure: BaseException,
    expected: PublicationOutcome,
) -> None:
    # Arrange
    publisher = DashboardOutboxPublisher(_FailingRouter(failure), lambda: _CONFIRMED_AT)

    # Act
    result = await publisher.publish(_EVENT)

    # Assert
    assert result == PublicationResult(expected, None)


@pytest.mark.asyncio
async def test_broker_publisher_refuses_nonobject_properties_before_broker_io() -> None:
    # Arrange
    router = _FailingRouter()
    publisher = DashboardOutboxPublisher(router, lambda: _CONFIRMED_AT)
    malformed = replace(_EVENT, headers=b"[]")

    # Act
    result = await publisher.publish(malformed)

    # Assert
    assert result == PublicationResult(PublicationOutcome.REFUSED, None)


@pytest.mark.asyncio
async def test_ambiguous_drain_records_reconciliation_evidence_without_confirmation() -> None:
    # Arrange
    store = _Store((_EVENT,))
    publisher = _Publisher(PublicationResult(PublicationOutcome.AMBIGUOUS, None))

    # Act
    result = await drain_once(store, publisher)

    # Assert
    assert result.ambiguous == 1
    assert store.writes == [
        (ApplicationEventIdentity("dashboard-api", "event-1"), OutboxEvent.AMBIGUOUS, None)
    ]


@pytest.mark.asyncio
async def test_drain_refuses_a_store_batch_above_the_global_bound() -> None:
    # Arrange
    rows = tuple(
        replace(_EVENT, event_id=f"event-{index}")
        for index in range(APPLICATION_OUTBOX_BATCH_SIZE + 1)
    )
    store = _Store(rows)
    publisher = _Publisher(PublicationResult(PublicationOutcome.CONFIRMED, _CONFIRMED_AT))

    # Act
    with pytest.raises(OutboxError) as captured:
        await drain_once(store, publisher)

    # Assert
    assert str(captured.value) == "outbox returned more than the bounded batch"
    assert store.writes == []
