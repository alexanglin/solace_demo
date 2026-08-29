"""Broker recovery, receive-loop, and malformed-delivery edge coverage."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final, cast

import pytest
from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_broker.messaging import BrokerLifecycle, InboundMessage
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import BINDINGS, Envelope, envelope_document
from aerial_rescue_dashboard_api.messaging import broker_runtime as broker_module
from aerial_rescue_dashboard_api.messaging.outbox import PublicationOutcome, PublicationResult
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import StoredAuditRecord
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome

_MISSION: Final = "mission-synthetic-0001"
_TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"
_EXPECTED_AUDIT_PAGE_CALLS: Final = 2
_STAGED: Final = StagedApplicationEvent(
    "dashboard-api",
    "event-outbox-0001",
    "operator-command",
    f"aerial-rescue/v1/{_MISSION}/operator/command/assign-sector",
    b"{}",
    b'{"event":"canonical"}',
    _TRACEPARENT,
    None,
    "command-synthetic-0001",
    None,
    "2026-08-26T12:00:00.000Z",
)


def _envelope(*, mission_id: str = _MISSION) -> Envelope:
    event_type = "aerial-rescue.v1.mission.event.lifecycle"
    return Envelope(
        id="event-0001",
        source="urn:aerial-rescue:mission-lifecycle:run-synthetic-0001",
        type=event_type,
        subject=mission_id,
        time="2026-08-26T12:00:00.000Z",
        dataschema=BINDINGS[event_type].dataschema,
        sequence="000000000000001",
        correlation_id="run-synthetic-0001",
        traceparent=_TRACEPARENT,
        data={"missionId": mission_id, "lifecycle": "SEARCHING"},
    )


def _payload(*, mission_id: str = _MISSION) -> bytes:
    return canonical.canonical_bytes(envelope_document(_envelope(mission_id=mission_id)))


def _record(ordinal: int = 1) -> StoredAuditRecord:
    envelope = _envelope()
    return StoredAuditRecord(
        _MISSION,
        ordinal,
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
    applied: list[int] = field(default_factory=list)

    @property
    def latest_audit_ordinal(self) -> int:
        return self.ordinal

    async def apply_audit(self, record: StoredAuditRecord, _schemas: object) -> object:
        self.ordinal = record.ordinal
        self.applied.append(record.ordinal)
        return object()


@dataclass
class _Audit:
    records: tuple[StoredAuditRecord, ...] = ()
    calls: int = 0

    async def read_after(
        self,
        _mission_id: str,
        after_ordinal: int,
        limit: int,
    ) -> tuple[StoredAuditRecord, ...]:
        self.calls += 1
        return tuple(record for record in self.records if record.ordinal > after_ordinal)[:limit]


@dataclass
class _InboxTransaction:
    completed: dict[InboxIdentity, bytes]
    duplicate: bool = False
    completions: int = 0

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        prior = self.completed.get(identity)
        if self.duplicate or prior is not None:
            return InboxOutcome(InboxDecision.DUPLICATE, prior or b"prior")
        return InboxOutcome(InboxDecision.CLAIMED, None)

    async def complete(self, identity: InboxIdentity, result: bytes, _processed_at: str) -> None:
        self.completed[identity] = result
        self.completions += 1


@dataclass
class _Inboxes:
    duplicate: bool = False
    completed: dict[InboxIdentity, bytes] = field(default_factory=dict)
    transactions: list[_InboxTransaction] = field(default_factory=list)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_InboxTransaction]:
        transaction = _InboxTransaction(self.completed, self.duplicate)
        self.transactions.append(transaction)
        yield transaction


@dataclass
class _Outbox:
    batches: list[tuple[StagedApplicationEvent, ...]] = field(default_factory=list)
    reconciliation_rows: tuple[StagedApplicationEvent, ...] = ()
    writes: int = 0

    async def pending(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        if self.batches:
            return self.batches.pop(0)
        return ()

    async def reconciliation(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        return self.reconciliation_rows

    async def record(self, _identity: object, _event: object, _instant: object) -> None:
        self.writes += 1


@dataclass
class _Publisher:
    result: PublicationResult = field(
        default_factory=lambda: PublicationResult(
            PublicationOutcome.CONFIRMED,
            "2026-08-26T12:00:01.000Z",
        )
    )

    async def publish(self, _event: StagedApplicationEvent) -> PublicationResult:
        return self.result


@dataclass
class _Schemas:
    def validate(self, _schema_id: str, _payload: object, /) -> None:
        return None


@dataclass
class _Refusals:
    records: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def record(self, candidate: BrokerRefusalCandidate) -> object:
        self.records.append(candidate)
        return object()


@dataclass
class _Session:
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)
    receiver_names: tuple[str, ...] = ()
    direct: InboundMessage | None = None
    guaranteed: broker_module.GuaranteedDelivery | None = None
    rebound: int = 0
    direct_calls: int = 0
    guaranteed_calls: int = 0

    def __post_init__(self) -> None:
        self.readiness.connected()

    def rebind_complete(self) -> None:
        self.rebound += 1
        self.readiness.mark_ready()

    def receive_direct(self, _timeout_milliseconds: int, /) -> InboundMessage | None:
        self.direct_calls += 1
        result = self.direct
        self.direct = None
        return result

    def receive_guaranteed(
        self,
        _receiver_name: str,
        _timeout_milliseconds: int,
        /,
    ) -> broker_module.GuaranteedDelivery | None:
        self.guaranteed_calls += 1
        result = self.guaranteed
        self.guaranteed = None
        return result


@dataclass
class _Message:
    topic: object
    payload: object

    def get_destination_name(self) -> object:
        return self.topic

    def get_payload_as_bytes(self) -> object:
        return self.payload


@dataclass
class _Settlement:
    outcome: str | None = None

    def accept(self) -> None:
        self.outcome = "accepted"

    def fail(self) -> None:
        self.outcome = "failed"

    def reject(self) -> None:
        self.outcome = "rejected"


@dataclass
class _Guaranteed:
    message: InboundMessage
    settlement: _Settlement


def _plane(
    *,
    records: tuple[StoredAuditRecord, ...] = (),
    page_size: int = 50,
    outbox: _Outbox | None = None,
    publisher: _Publisher | None = None,
    inboxes: _Inboxes | None = None,
) -> tuple[broker_module.DashboardDataPlane, _Session, _Hub, _Audit, _Refusals]:
    session = _Session()
    hub = _Hub()
    audit = _Audit(records)
    refusals = _Refusals()
    plane = broker_module.DashboardDataPlane(
        session=cast("broker_module.RecoverySession", session),
        ports=broker_module.DataPlanePorts(
            hub,
            audit,
            inboxes or _Inboxes(),
            outbox or _Outbox(),
            publisher or _Publisher(),
            cast("PayloadSchemaExecutor", _Schemas()),
            refusals,
            lambda: "2026-08-26T12:00:01.000Z",
        ),
        audit_page_size=page_size,
    )
    plane.activate_mission(_MISSION)
    return plane, session, hub, audit, refusals


@pytest.mark.parametrize("page_size", [0, -1, True])
def test_data_plane_refuses_nonpositive_or_boolean_audit_page_sizes(page_size: int) -> None:
    # Arrange
    session = _Session()

    # Act
    with pytest.raises(broker_module.DataPlaneError) as captured:
        broker_module.DashboardDataPlane(
            session=cast("broker_module.RecoverySession", session),
            ports=cast("broker_module.DataPlanePorts", object()),
            audit_page_size=page_size,
        )

    # Assert
    assert captured.value.refusal is broker_module.DataPlaneRefusal.CONFIGURATION


def test_data_plane_refuses_an_empty_mission_identity() -> None:
    # Arrange
    plane, _session, _hub, _audit, _refusals = _plane()

    # Act
    with pytest.raises(broker_module.DataPlaneError) as captured:
        plane.activate_mission("")

    # Assert
    assert captured.value.refusal is broker_module.DataPlaneRefusal.CONFIGURATION


@pytest.mark.parametrize(
    ("outcome", "reconciliation"),
    [
        (PublicationOutcome.REFUSED, False),
        (PublicationOutcome.AMBIGUOUS, False),
        (PublicationOutcome.CONFIRMED, True),
    ],
)
@pytest.mark.asyncio
async def test_recovery_stays_unready_for_refusal_ambiguity_or_reconciliation(
    outcome: PublicationOutcome,
    reconciliation: bool,
) -> None:
    # Arrange
    evidence = "2026-08-26T12:00:01.000Z" if outcome is PublicationOutcome.CONFIRMED else None
    outbox = _Outbox(
        batches=[(_STAGED,)] if not reconciliation else [],
        reconciliation_rows=(_STAGED,) if reconciliation else (),
    )
    plane, session, _hub, _audit, _refusals = _plane(
        outbox=outbox,
        publisher=_Publisher(PublicationResult(outcome, evidence)),
    )

    # Act
    recovered = await plane.recover()

    # Assert
    assert recovered is False
    assert session.rebound == 0
    assert session.readiness.is_ready() is False


@pytest.mark.asyncio
async def test_full_audit_page_continues_and_duplicate_inbox_is_not_completed_again() -> None:
    # Arrange
    inboxes = _Inboxes(duplicate=True)
    plane, session, hub, audit, _refusals = _plane(
        records=(_record(),),
        page_size=1,
        inboxes=inboxes,
    )

    # Act
    recovered = await plane.recover()

    # Assert
    assert recovered is True
    assert audit.calls == _EXPECTED_AUDIT_PAGE_CALLS
    assert hub.applied == [1]
    assert sum(transaction.completions for transaction in inboxes.transactions) == 0
    assert session.rebound == 1


@pytest.mark.asyncio
async def test_malformed_guaranteed_shape_records_empty_body_free_evidence() -> None:
    # Arrange
    plane, _session, _hub, _audit, refusals = _plane()
    settlement = _Settlement()
    delivery = _Guaranteed(cast("InboundMessage", _Message(7, "not-bytes")), settlement)

    # Act
    decision = await plane.handle_guaranteed(
        "drone.event",
        cast("broker_module.GuaranteedDelivery", delivery),
    )

    # Assert
    assert decision is broker_module.DeliveryDecision.REJECTED
    assert settlement.outcome == "rejected"
    assert refusals.records[0].source is None
    assert refusals.records[0].family is None


@pytest.mark.asyncio
async def test_wrong_receiver_records_decoded_source_and_parsed_family_before_rejection() -> None:
    # Arrange
    plane, _session, _hub, _audit, refusals = _plane()
    settlement = _Settlement()
    message = _Message(
        f"aerial-rescue/v1/{_MISSION}/mission/event/lifecycle",
        _payload(),
    )
    delivery = _Guaranteed(cast("InboundMessage", message), settlement)

    # Act
    decision = await plane.handle_guaranteed(
        "drone.event",
        cast("broker_module.GuaranteedDelivery", delivery),
    )

    # Assert
    assert decision is broker_module.DeliveryDecision.REJECTED
    assert refusals.records[0].source == _envelope().source
    assert refusals.records[0].family == "mission.event"
    assert refusals.records[0].channel == "dashboard-api-drone-event"


@pytest.mark.asyncio
async def test_valid_guaranteed_delivery_is_deferred_when_recovery_cannot_drain() -> None:
    # Arrange
    outbox = _Outbox(batches=[(_STAGED,)])
    plane, _session, _hub, _audit, _refusals = _plane(
        outbox=outbox,
        publisher=_Publisher(PublicationResult(PublicationOutcome.REFUSED, None)),
    )
    settlement = _Settlement()
    delivery = _Guaranteed(
        cast(
            "InboundMessage",
            _Message(
                f"aerial-rescue/v1/{_MISSION}/mission/event/lifecycle",
                _payload(),
            ),
        ),
        settlement,
    )

    # Act
    decision = await plane.handle_guaranteed(
        "mission.event",
        cast("broker_module.GuaranteedDelivery", delivery),
    )

    # Assert
    assert decision is broker_module.DeliveryDecision.DEFERRED
    assert settlement.outcome == "failed"


@pytest.mark.asyncio
async def test_direct_delivery_refuses_shape_schema_and_wrong_mission() -> None:
    # Arrange
    plane, _session, _hub, _audit, _refusals = _plane()
    messages = (
        cast("InboundMessage", _Message(7, b"payload")),
        cast("InboundMessage", _Message("not/a/topic", b"hostile")),
        cast(
            "InboundMessage",
            _Message(
                "aerial-rescue/v1/mission-other/mission/event/lifecycle",
                _payload(mission_id="mission-other"),
            ),
        ),
    )

    # Act
    decisions = tuple([await plane.handle_direct(message) for message in messages])

    # Assert
    assert decisions == (broker_module.DeliveryDecision.REJECTED,) * len(messages)


@pytest.mark.asyncio
async def test_direct_delivery_distinguishes_recovery_failure_from_success() -> None:
    # Arrange
    topic = f"aerial-rescue/v1/{_MISSION}/mission/event/lifecycle"
    message = cast("InboundMessage", _Message(topic, _payload()))
    failed, _session, _hub, _audit, _refusals = _plane(
        outbox=_Outbox(batches=[(_STAGED,)]),
        publisher=_Publisher(PublicationResult(PublicationOutcome.REFUSED, None)),
    )
    recovered, _session, _hub, _audit, _refusals = _plane()

    # Act
    deferred = await failed.handle_direct(message)
    accepted = await recovered.handle_direct(message)

    # Assert
    assert deferred is broker_module.DeliveryDecision.DEFERRED
    assert accepted is broker_module.DeliveryDecision.ACCEPTED


@pytest.mark.parametrize("timeout", [-1, True])
@pytest.mark.asyncio
async def test_receive_loop_refuses_negative_or_boolean_windows(timeout: int) -> None:
    # Arrange
    plane, session, _hub, _audit, _refusals = _plane()

    # Act
    with pytest.raises(broker_module.DataPlaneError) as captured:
        await broker_module.serve(
            cast("broker_module.DashboardServingSession", session),
            plane,
            broker_module.ServePorts(lambda: False, lambda _ready: None, lambda: None, timeout),
        )

    # Assert
    assert captured.value.refusal is broker_module.DataPlaneRefusal.CONFIGURATION


@dataclass
class _ServingPlane:
    recovered: bool = True
    direct_calls: int = 0
    guaranteed_calls: int = 0

    async def recover(self) -> bool:
        return self.recovered

    async def handle_direct(self, _message: InboundMessage) -> broker_module.DeliveryDecision:
        self.direct_calls += 1
        return broker_module.DeliveryDecision.ACCEPTED

    async def handle_guaranteed(
        self,
        _receiver: str,
        _delivery: broker_module.GuaranteedDelivery,
    ) -> broker_module.DeliveryDecision:
        self.guaranteed_calls += 1
        return broker_module.DeliveryDecision.ACCEPTED


def _running_for(cycles: int) -> Callable[[], bool]:
    remaining = cycles

    def running() -> bool:
        nonlocal remaining
        result = remaining > 0
        remaining -= 1
        return result

    return running


@pytest.mark.asyncio
async def test_receive_loop_fairly_dispatches_direct_and_guaranteed_channels() -> None:
    # Arrange
    settlement = _Settlement()
    message = cast("InboundMessage", _Message("unused", b"unused"))
    session = _Session(
        receiver_names=("drone.event",),
        direct=message,
        guaranteed=cast(
            "broker_module.GuaranteedDelivery",
            _Guaranteed(message, settlement),
        ),
    )
    session.readiness.mark_ready()
    plane = _ServingPlane()
    signals: list[bool] = []

    # Act
    report = await broker_module.serve(
        cast("broker_module.DashboardServingSession", session),
        cast("broker_module.DashboardDataPlane", plane),
        broker_module.ServePorts(_running_for(2), signals.append, lambda: None, 0),
    )

    # Assert
    assert report.exit_status == 0
    assert plane.direct_calls == 1
    assert plane.guaranteed_calls == 1
    assert session.direct_calls == 1
    assert session.guaranteed_calls == 1
    assert signals[-1] is True


@pytest.mark.asyncio
async def test_receive_loop_awaits_pause_while_transport_or_recovery_is_unavailable() -> None:
    # Arrange
    session = _Session()
    session.readiness.recovery_required()
    plane = _ServingPlane(recovered=False)
    pauses = 0

    async def pause() -> None:
        nonlocal pauses
        pauses += 1

    # Act
    report = await broker_module.serve(
        cast("broker_module.DashboardServingSession", session),
        cast("broker_module.DashboardDataPlane", plane),
        broker_module.ServePorts(_running_for(1), lambda _ready: None, pause, 0),
    )

    # Assert
    assert report.exit_status == 0
    assert pauses == 1


@pytest.mark.asyncio
async def test_starting_transport_pauses_without_attempting_application_recovery() -> None:
    # Arrange
    session = _Session()
    session.readiness = BrokerLifecycle()
    plane = _ServingPlane()
    pauses: list[str] = []

    def pause() -> Awaitable[None] | None:
        pauses.append("paused")
        return None

    # Act
    report = await broker_module.serve(
        cast("broker_module.DashboardServingSession", session),
        cast("broker_module.DashboardDataPlane", plane),
        broker_module.ServePorts(_running_for(1), lambda _ready: None, pause, 0),
    )

    # Assert
    assert report.exit_status == 0
    assert pauses == ["paused"]
