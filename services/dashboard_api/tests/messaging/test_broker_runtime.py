"""Recorder-audit recovery and broker-settlement runtime tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final, cast

import pytest
from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_broker.messaging import BrokerLifecycle
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import BINDINGS, Envelope, envelope_document
from aerial_rescue_dashboard_api.messaging.broker_runtime import (
    DashboardDataPlane,
    DashboardServingSession,
    DataPlanePorts,
    DeliveryDecision,
    GuaranteedDelivery,
    ProjectionPort,
    RecoverySession,
    ServePorts,
    serve,
)
from aerial_rescue_dashboard_api.messaging.outbox import PublicationOutcome, PublicationResult
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import StoredAuditRecord
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome

MISSION: Final = "mission-synthetic-0001"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"


def _envelope() -> Envelope:
    event_type = "aerial-rescue.v1.mission.event.lifecycle"
    return Envelope(
        id="event-0001",
        source="urn:aerial-rescue:mission-lifecycle:run-synthetic-0001",
        type=event_type,
        subject=MISSION,
        time="2026-08-26T12:00:00.000Z",
        dataschema=BINDINGS[event_type].dataschema,
        sequence="000000000000001",
        correlation_id="run-synthetic-0001",
        traceparent=TRACEPARENT,
        data={"missionId": MISSION, "lifecycle": "SEARCHING"},
    )


def _record() -> StoredAuditRecord:
    envelope = _envelope()
    return StoredAuditRecord(
        MISSION,
        1,
        envelope.type,
        envelope.time,
        canonical.canonical_bytes(envelope_document(envelope)),
        envelope.correlation_id,
        envelope.causation_id,
        envelope.traceparent,
    )


@dataclass
class _Hub:
    ordinal: int = 0
    applied: list[StoredAuditRecord] = field(default_factory=list)

    @property
    def latest_audit_ordinal(self) -> int:
        return self.ordinal

    async def apply_audit(self, record: StoredAuditRecord, _schemas: object) -> object:
        self.ordinal = record.ordinal
        self.applied.append(record)
        return object()


@dataclass
class _Audit:
    records: tuple[StoredAuditRecord, ...]
    calls: list[tuple[str, int, int]] = field(default_factory=list)

    async def read_after(
        self, mission_id: str, after_ordinal: int, limit: int
    ) -> tuple[StoredAuditRecord, ...]:
        self.calls.append((mission_id, after_ordinal, limit))
        return tuple(record for record in self.records if record.ordinal > after_ordinal)[:limit]


@dataclass
class _InboxTransaction:
    completed: dict[InboxIdentity, bytes]

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        prior = self.completed.get(identity)
        if prior is None:
            return InboxOutcome(InboxDecision.CLAIMED, None)
        return InboxOutcome(InboxDecision.DUPLICATE, prior)

    async def complete(self, identity: InboxIdentity, result: bytes, _processed_at: str) -> None:
        self.completed[identity] = result


@dataclass
class _Inboxes:
    completed: dict[InboxIdentity, bytes] = field(default_factory=dict)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_InboxTransaction]:
        yield _InboxTransaction(self.completed)


@dataclass
class _Outbox:
    pending_rows: tuple[StagedApplicationEvent, ...] = ()
    ambiguous: tuple[StagedApplicationEvent, ...] = ()

    async def pending(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        rows = self.pending_rows
        self.pending_rows = ()
        return rows

    async def reconciliation(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        return self.ambiguous

    async def record(self, _identity: object, _event: object, _instant: object) -> None:
        return None


@dataclass
class _Publisher:
    async def publish(self, _event: StagedApplicationEvent) -> PublicationResult:
        return PublicationResult(PublicationOutcome.CONFIRMED, "2026-08-26T12:00:01.000Z")


@dataclass
class _Session:
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)
    rebound: int = 0
    receiver_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.readiness.connected()

    def rebind_complete(self) -> None:
        self.rebound += 1
        self.readiness.mark_ready()

    def receive_direct(self, _timeout_milliseconds: int, /) -> None:
        return None

    def receive_guaranteed(self, _name: str, _timeout_milliseconds: int, /) -> None:
        return None


@dataclass
class _Settlement:
    outcome: str | None = None

    def accept(self) -> None:
        self.outcome = "accept"

    def fail(self) -> None:
        self.outcome = "fail"

    def reject(self) -> None:
        self.outcome = "reject"


@dataclass
class _Message:
    topic: str
    payload: bytes

    def get_destination_name(self) -> str:
        return self.topic

    def get_payload_as_bytes(self) -> bytes:
        return self.payload


@dataclass
class _Guaranteed:
    message: _Message
    settlement: _Settlement


@dataclass
class _Schemas:
    def validate(self, _schema_id: str, _payload: object, /) -> None:
        return None


@dataclass
class _Refusals:
    recorded: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def record(self, candidate: BrokerRefusalCandidate) -> object:
        self.recorded.append(candidate)
        return object()


def _plane(
    records: tuple[StoredAuditRecord, ...], *, active: bool = True
) -> tuple[DashboardDataPlane, _Session, _Hub]:
    session = _Session()
    hub = _Hub()
    plane = DashboardDataPlane(
        session=cast("RecoverySession", session),
        ports=DataPlanePorts(
            hub=cast("ProjectionPort", hub),
            audit=_Audit(records),
            inboxes=_Inboxes(),
            outbox=_Outbox(),
            publisher=_Publisher(),
            schemas=cast("PayloadSchemaExecutor", _Schemas()),
            refusals=_Refusals(),
            observed_at=lambda: "2026-08-26T12:00:01.000Z",
        ),
        audit_page_size=50,
    )
    if active:
        plane.activate_mission(MISSION)
    return plane, session, hub


@pytest.mark.asyncio
async def test_recovery_folds_recorder_audit_commits_inbox_and_only_then_restores_readiness() -> (
    None
):
    # Arrange
    plane, session, hub = _plane((_record(),))

    # Act
    recovered = await plane.recover()

    # Assert
    assert recovered is True
    assert hub.applied == [_record()]
    assert session.rebound == 1
    assert session.readiness.is_ready()


@pytest.mark.asyncio
async def test_initial_no_mission_epoch_can_drain_outbox_and_become_ready_to_start() -> None:
    # Arrange
    plane, session, hub = _plane((), active=False)

    # Act
    recovered = await plane.recover()

    # Assert
    assert recovered is True
    assert hub.applied == []
    assert session.readiness.is_ready()


@pytest.mark.asyncio
async def test_guaranteed_delivery_accepts_only_after_its_recorder_fact_is_committed() -> None:
    # Arrange
    plane, _session, _hub = _plane((_record(),))
    settlement = _Settlement()
    delivered = _Guaranteed(
        _Message(
            "aerial-rescue/v1/mission-synthetic-0001/mission/event/lifecycle",
            _record().payload,
        ),
        settlement,
    )

    # Act
    decision = await plane.handle_guaranteed("mission.event", cast("GuaranteedDelivery", delivered))

    # Assert
    assert decision is DeliveryDecision.ACCEPTED
    assert settlement.outcome == "accept"


@pytest.mark.asyncio
async def test_guaranteed_delivery_missing_from_audit_is_failed_for_redelivery() -> None:
    # Arrange
    plane, _session, _hub = _plane(())
    settlement = _Settlement()
    delivered = _Guaranteed(
        _Message(
            "aerial-rescue/v1/mission-synthetic-0001/mission/event/lifecycle",
            _record().payload,
        ),
        settlement,
    )

    # Act
    decision = await plane.handle_guaranteed("mission.event", cast("GuaranteedDelivery", delivered))

    # Assert
    assert decision is DeliveryDecision.DEFERRED
    assert settlement.outcome == "fail"


@pytest.mark.asyncio
async def test_malformed_guaranteed_input_is_recorded_body_free_before_rejection() -> None:
    # Arrange
    plane, _session, _hub = _plane(())
    settlement = _Settlement()
    delivered = _Guaranteed(_Message("not/a/topic", b"hostile"), settlement)

    # Act
    decision = await plane.handle_guaranteed("drone.event", cast("GuaranteedDelivery", delivered))

    # Assert
    assert decision is DeliveryDecision.REJECTED
    assert settlement.outcome == "reject"
    assert plane.refusal_count == 1


@pytest.mark.asyncio
async def test_reconnected_session_recovers_before_any_receiver_is_polled() -> None:
    # Arrange
    plane, session, _hub = _plane((_record(),))
    session.readiness.reconnected()
    calls = 0

    def running() -> bool:
        nonlocal calls
        calls += 1
        return calls <= 1

    signals: list[bool] = []

    # Act
    report = await serve(
        cast("DashboardServingSession", session),
        plane,
        ServePorts(running, signals.append, lambda: None, 1),
    )

    # Assert
    assert report.exit_status == 0
    assert session.rebound == 1
    assert signals[-1] is True


@pytest.mark.asyncio
async def test_exhausted_session_exits_nonzero_without_claiming_recovery() -> None:
    # Arrange
    plane, session, _hub = _plane(())
    session.readiness.exhausted()
    signals: list[bool] = []

    # Act
    report = await serve(
        cast("DashboardServingSession", session),
        plane,
        ServePorts(lambda: True, signals.append, lambda: None, 1),
    )

    # Assert
    assert report.exit_status == 1
    assert session.rebound == 0
    assert signals == [False]
