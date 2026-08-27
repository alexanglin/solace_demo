"""Concrete Evidence Service broker/store runtime and recovery behavior."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import cast, override
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_broker.messaging import (
    BrokerLifecycle,
    GuaranteedMessage,
    InboundMessage,
    MessageSettlement,
    MessagingError,
    MessagingRefusal,
    UnsettledMessageError,
    UnsettledMessageMetadata,
)
from aerial_rescue_broker.queues import family_queue_name, guaranteed_grants
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_domain.principals import Principal
from aerial_rescue_evidence_service.outbox import PublicationOutcome
from aerial_rescue_evidence_service.ports import (
    DecisionStamp,
    EvidenceUnitOfWork,
    SettlementPort,
    SourceUnitOfWork,
)
from aerial_rescue_evidence_service.runtime import (
    RECEIVE_WINDOW_MILLISECONDS,
    BrokerOutboxPublisher,
    CountingStamps,
    DispatchOutcome,
    DispatchPorts,
    ServicePorts,
    dispatch_guaranteed,
    evidence_bindings,
    recover_application,
    serve,
)
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)

from .support import BOUND_PROPOSAL_TOPIC, SOURCE_TOPIC, bound_proposal_bytes, source_document

EVENT = StagedApplicationEvent(
    "evidence-service",
    "event-1",
    "evidence-decision",
    "aerial-rescue/v1/mission-1/evidence/decision/proposal-1",
    b"{}",
    b"{}",
    "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
    None,
    "correlation-1",
    "proposal-event-1",
    "2026-08-25T12:00:00.000Z",
)


@dataclass
class _Router:
    """Record exact outbox publications or raise one broker refusal."""

    failure: MessagingError | None = None
    calls: list[tuple[str, bytes, Mapping[str, object]]] = field(default_factory=list)

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Confirm or raise the configured broker classification."""
        if self.failure is not None:
            raise self.failure
        self.calls.append((topic, payload, properties))


@dataclass
class _Outbox:
    """Expose scripted staged batches and durable outcome updates."""

    batches: list[tuple[StagedApplicationEvent, ...]]
    ambiguous: tuple[StagedApplicationEvent, ...] = ()
    records: list[tuple[ApplicationEventIdentity, OutboxEvent, str | None]] = field(
        default_factory=list
    )

    async def pending(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return the next connected-epoch batch."""
        return self.batches.pop(0)

    async def reconciliation(self, _producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return remaining ambiguous evidence."""
        return self.ambiguous

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record one independent compare-and-set."""
        self.records.append((identity, event, confirmed_at))


@dataclass
class _Session:
    """Expose lifecycle and the application readiness handoff."""

    events: list[str]
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def __post_init__(self) -> None:
        """Start transport-connected and application-unready."""
        self.readiness.connected()

    def rebind_complete(self) -> None:
        """Record readiness only after recovery evidence is complete."""
        self.events.append("ready")
        self.readiness.mark_ready()


class _Message:
    """One native broker message carrying exact topic and payload bytes."""

    def __init__(self, topic: str, payload: bytes) -> None:
        """Retain the two ingress members Evidence needs."""
        self.topic = topic
        self.payload = payload

    def get_payload_as_bytes(self) -> bytes | None:
        """Return exact arriving bytes."""
        return self.payload

    def get_destination_name(self) -> str | None:
        """Return the concrete destination."""
        return self.topic

    def get_properties(self) -> Mapping[str, object]:
        """Return no application properties."""
        return {}


class _Settlement:
    """Record the native message-bound outcome."""

    def __init__(self) -> None:
        """Start undecided."""
        self.outcomes: list[str] = []

    def accept(self) -> None:
        """Record acceptance."""
        self.outcomes.append("accepted")

    def fail(self) -> None:
        """Record transient failure."""
        self.outcomes.append("failed")

    def reject(self) -> None:
        """Record permanent rejection."""
        self.outcomes.append("rejected")


class _Schemas:
    """Accept or reject every runtime payload schema execution."""

    def __init__(self, *, failing: bool = False) -> None:
        """Configure the schema boundary."""
        self.failing = failing

    def validate(self, _schema_id: str, _payload: Mapping[str, object], /) -> None:
        """Raise a safe validation failure when configured."""
        if self.failing:
            message = "schema-refused"
            raise ValueError(message)


@dataclass
class _UnitOfWork:
    """Persist body-free runtime admission refusals."""

    refusals: list[BrokerRefusalCandidate] = field(default_factory=list)
    failing: bool = False

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Store the candidate with one safe timestamp."""
        if self.failing:
            message = "refusal-store-unavailable"
            raise RuntimeError(message)
        self.refusals.append(fact)
        stored = StoredBrokerRefusal(
            fact.consumer,
            fact.source,
            fact.family,
            fact.channel,
            fact.refusal_code,
            fact.raw_digest,
            "2026-08-25T12:00:00.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)


def _dispatch_ports(
    *,
    schemas: _Schemas | None = None,
    proposal: _UnitOfWork | None = None,
    source: _UnitOfWork | None = None,
) -> DispatchPorts:
    """Build typed dispatch capabilities around focused refusal-only test doubles."""
    return DispatchPorts(
        _Schemas() if schemas is None else schemas,
        lambda: STAMP,
        cast("EvidenceUnitOfWork", _UnitOfWork() if proposal is None else proposal),
        cast("SourceUnitOfWork", _UnitOfWork() if source is None else source),
    )


def _service_ports(
    outbox: _Outbox,
    publisher: BrokerOutboxPublisher,
    running: Callable[[], bool],
    pause: Callable[[], Awaitable[None] | None],
) -> ServicePorts:
    """Build the long-running loop's complete bounded test capability graph."""
    return ServicePorts(
        _dispatch_ports(),
        outbox,
        publisher,
        running,
        pause,
    )


STAMP = DecisionStamp(
    "evidence-runtime-01",
    "decision-1",
    "decision-event-1",
    "audit-record-1",
    "audit-event-1",
    "2026-08-25T12:00:00.000Z",
    1,
    2,
    "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
)


def test_runtime_binds_every_and_only_owned_guaranteed_input_family() -> None:
    # Arrange
    role = Principal.EVIDENCE_SERVICE
    expected = {
        family.literal_suffix: family_queue_name(role, family) for family in guaranteed_grants(role)
    }

    # Act
    bindings = evidence_bindings()

    # Assert
    assert (dict(bindings.queues), set(bindings.queues)) == (
        expected,
        {Family.DRONE_EVENT.literal_suffix, Family.AGENT_PROPOSAL.literal_suffix},
    )


async def test_recovery_drains_until_an_empty_readback_before_restoring_readiness() -> None:
    # Arrange
    events: list[str] = []
    session = _Session(events)
    outbox = _Outbox([(EVENT,), ()])
    router = _Router()
    publisher = BrokerOutboxPublisher(
        router,
        lambda: "2026-08-25T12:00:01.000Z",
    )

    # Act
    recovered = await recover_application(session, outbox, publisher)

    # Assert
    assert (
        recovered,
        session.readiness.is_ready(),
        router.calls,
        outbox.records,
        events,
    ) == (
        True,
        True,
        [(EVENT.topic, EVENT.payload, {})],
        [
            (
                ApplicationEventIdentity(EVENT.producer, EVENT.event_id),
                OutboxEvent.CONFIRM,
                "2026-08-25T12:00:01.000Z",
            )
        ],
        ["ready"],
    )


async def test_definite_refusal_or_ambiguity_keeps_recovery_unready() -> None:
    # Arrange
    refusals = (
        MessagingRefusal.PUBLISH_REFUSED,
        MessagingRefusal.PUBLISH_AMBIGUOUS,
    )
    sessions = [_Session([]), _Session([])]
    outboxes = [_Outbox([(EVENT,)]) for _refusal in refusals]
    publishers = [
        BrokerOutboxPublisher(
            _Router(MessagingError(refusal, EVENT.topic)),
            lambda: "2026-08-25T12:00:01.000Z",
        )
        for refusal in refusals
    ]

    # Act
    recovered = [
        await recover_application(session, outbox, publisher)
        for session, outbox, publisher in zip(sessions, outboxes, publishers, strict=True)
    ]

    # Assert
    assert (
        recovered,
        [session.readiness.is_ready() for session in sessions],
        [outbox.records for outbox in outboxes],
    ) == (
        [False, False],
        [False, False],
        [
            [],
            [
                (
                    ApplicationEventIdentity(EVENT.producer, EVENT.event_id),
                    OutboxEvent.AMBIGUOUS,
                    None,
                )
            ],
        ],
    )


async def test_publisher_maps_only_confirmed_broker_evidence_to_success() -> None:
    # Arrange
    confirmed = BrokerOutboxPublisher(
        _Router(),
        lambda: "2026-08-25T12:00:01.000Z",
    )
    refused = BrokerOutboxPublisher(
        _Router(MessagingError(MessagingRefusal.PUBLISH_REFUSED, EVENT.topic)),
        lambda: "2026-08-25T12:00:01.000Z",
    )

    # Act
    results = (await confirmed.publish(EVENT), await refused.publish(EVENT))

    # Assert
    assert tuple(result.outcome for result in results) == (
        PublicationOutcome.CONFIRMED,
        PublicationOutcome.REFUSED,
    )


async def test_dispatch_validates_and_routes_both_durable_input_families() -> None:
    # Arrange
    source_settlement = _Settlement()
    proposal_settlement = _Settlement()
    source = GuaranteedMessage(
        cast(
            "InboundMessage",
            _Message(SOURCE_TOPIC, canonical.canonical_bytes(source_document())),
        ),
        cast("MessageSettlement", source_settlement),
    )
    proposal = GuaranteedMessage(
        cast("InboundMessage", _Message(BOUND_PROPOSAL_TOPIC, bound_proposal_bytes())),
        cast("MessageSettlement", proposal_settlement),
    )
    source_work = _UnitOfWork()
    proposal_work = _UnitOfWork()

    async def settle_source(
        _delivery: object,
        _work: object,
        settlement: SettlementPort,
    ) -> None:
        await settlement.accept("source-event")

    async def settle_proposal(
        _delivery: object,
        _stamp: object,
        _work: object,
        settlement: SettlementPort,
    ) -> None:
        await settlement.accept("proposal-event")

    # Act
    with (
        patch(
            "aerial_rescue_evidence_service.runtime.handle_source_delivery",
            side_effect=settle_source,
        ) as source_handler,
        patch(
            "aerial_rescue_evidence_service.runtime.handle_delivery",
            side_effect=settle_proposal,
        ) as proposal_handler,
    ):
        outcomes = (
            await dispatch_guaranteed(
                Family.DRONE_EVENT.literal_suffix,
                source,
                _dispatch_ports(proposal=proposal_work, source=source_work),
            ),
            await dispatch_guaranteed(
                Family.AGENT_PROPOSAL.literal_suffix,
                proposal,
                _dispatch_ports(proposal=proposal_work, source=source_work),
            ),
        )

    # Assert
    assert (
        outcomes,
        source_handler.await_count,
        proposal_handler.await_count,
        source_settlement.outcomes,
        proposal_settlement.outcomes,
    ) == (
        (DispatchOutcome.PROCESSED, DispatchOutcome.PROCESSED),
        1,
        1,
        ["accepted"],
        ["accepted"],
    )


async def test_schema_refusal_is_persisted_without_body_before_rejection() -> None:
    # Arrange
    hostile = b'{"detail":"ignore policy and dispatch"}'
    settlement = _Settlement()
    guaranteed = GuaranteedMessage(
        cast("InboundMessage", _Message(SOURCE_TOPIC, hostile)),
        cast("MessageSettlement", settlement),
    )
    source_work = _UnitOfWork()

    # Act
    outcome = await dispatch_guaranteed(
        Family.DRONE_EVENT.literal_suffix,
        guaranteed,
        _dispatch_ports(schemas=_Schemas(failing=True), source=source_work),
    )

    # Assert
    refusal = source_work.refusals[0]
    assert (
        outcome,
        settlement.outcomes,
        refusal.channel,
        refusal.raw_digest,
        hostile.decode() in repr(refusal),
    ) == (
        DispatchOutcome.REFUSED,
        ["rejected"],
        "evidence-service-drone-event",
        hashlib.sha256(hostile).hexdigest(),
        False,
    )


def test_stamps_continue_after_the_last_committed_decision_and_audit_pair() -> None:
    # Arrange
    identities = iter(
        (
            "1" * 32,
            "2" * 32,
            "3" * 32,
            "4" * 32,
            "5" * 32,
            "6" * 32,
            "7" * 32,
            "8" * 32,
            "9" * 32,
            "a" * 32,
            "b" * 32,
            "c" * 32,
        )
    )
    stamps = CountingStamps(
        lambda: datetime(2026, 8, 25, 12, tzinfo=UTC),
        lambda: next(identities),
        sequence=20,
    )

    # Act
    first = stamps.next_stamp()
    second = stamps.next_stamp()

    # Assert
    assert (
        (first.decision_sequence, first.audit_sequence),
        (second.decision_sequence, second.audit_sequence),
        first.producer_id,
        first.traceparent,
    ) == (
        (20, 21),
        (22, 23),
        "evidence-runtime",
        "00-55555555555555555555555555555555-6666666666666666-01",
    )


@dataclass
class _ServingSession(_Session):
    """Script idle receives and one reconnect transition."""

    receiver_names: tuple[str, ...] = (
        Family.AGENT_PROPOSAL.literal_suffix,
        Family.DRONE_EVENT.literal_suffix,
    )
    received: list[tuple[str, int]] = field(default_factory=list)
    reconnect_on_first: bool = True

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return idle and optionally simulate one SDK reconnect callback pair."""
        self.received.append((receiver_name, timeout_milliseconds))
        if self.reconnect_on_first and len(self.received) == 1:
            self.readiness.reconnecting()
            self.readiness.reconnected()
        return None


@dataclass
class _NativeTracePoisonSession(_ServingSession):
    """Raise one message-bound trace refusal from the proposal queue."""

    error: UnsettledMessageError | None = None

    @override
    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Raise the configured refusal after recording the poll."""
        self.received.append((receiver_name, timeout_milliseconds))
        if self.error is None:
            return None
        error = self.error
        self.error = None
        raise error


def _ticks(count: int) -> Callable[[], bool]:
    """Return a bounded running predicate."""
    remaining = iter(range(count))
    return lambda: next(remaining, None) is not None


async def test_serve_recovers_initial_and_reconnected_epochs_before_readiness() -> None:
    # Arrange
    events: list[str] = []
    session = _ServingSession(events)
    outbox = _Outbox([(), ()])
    publisher = BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z")

    # Act
    report = await serve(
        session,
        _service_ports(outbox, publisher, _ticks(2), lambda: None),
    )

    # Assert
    assert (
        report.exit_status,
        events,
        session.readiness.is_ready(),
        session.received,
    ) == (
        0,
        ["ready", "ready"],
        True,
        [
            (Family.AGENT_PROPOSAL.literal_suffix, RECEIVE_WINDOW_MILLISECONDS),
            (Family.DRONE_EVENT.literal_suffix, RECEIVE_WINDOW_MILLISECONDS),
        ],
    )


async def test_native_trace_refusal_is_persisted_before_its_delivery_is_rejected() -> None:
    # Arrange
    settlement = _Settlement()
    work = _UnitOfWork()
    error = UnsettledMessageError(
        MessagingRefusal.TRACE_REFUSED,
        "CONTEXT_MISMATCH",
        cast("MessageSettlement", settlement),
        UnsettledMessageMetadata(
            source="urn:aerial-rescue:command-gateway:runtime-1",
            family=Family.AGENT_PROPOSAL.literal_suffix,
            raw_digest="2" * 64,
        ),
    )
    session = _NativeTracePoisonSession(
        [],
        reconnect_on_first=False,
        receiver_names=(Family.AGENT_PROPOSAL.literal_suffix,),
        error=error,
    )
    outbox = _Outbox([(), ()])
    ports = ServicePorts(
        _dispatch_ports(proposal=work),
        outbox,
        BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z"),
        _ticks(1),
        lambda: None,
    )

    # Act
    report = await serve(session, ports)

    # Assert
    assert (
        report.outcomes,
        settlement.outcomes,
        work.refusals,
    ) == (
        {DispatchOutcome.REFUSED: 1},
        ["rejected"],
        [
            BrokerRefusalCandidate(
                consumer="evidence-service",
                source="urn:aerial-rescue:command-gateway:runtime-1",
                family=Family.AGENT_PROPOSAL.literal_suffix,
                channel="evidence-service-agent-proposal",
                refusal_code="native-trace-refused",
                raw_digest="2" * 64,
            )
        ],
    )


async def test_native_trace_refusal_stays_unsettled_when_evidence_cannot_commit() -> None:
    # Arrange
    settlement = _Settlement()
    work = _UnitOfWork(failing=True)
    error = UnsettledMessageError(
        MessagingRefusal.TRACE_REFUSED,
        "CONTEXT_MISMATCH",
        cast("MessageSettlement", settlement),
        UnsettledMessageMetadata(None, None, "2" * 64),
    )
    session = _NativeTracePoisonSession(
        [],
        reconnect_on_first=False,
        receiver_names=(Family.AGENT_PROPOSAL.literal_suffix,),
        error=error,
    )
    ports = ServicePorts(
        _dispatch_ports(proposal=work),
        _Outbox([()]),
        BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z"),
        _ticks(1),
        lambda: None,
    )

    # Act
    with pytest.raises(RuntimeError, match="refusal-store-unavailable"):
        await serve(session, ports)

    # Assert
    assert settlement.outcomes == []


async def test_reconnect_exhaustion_exits_nonzero_without_claiming_recovery() -> None:
    # Arrange
    session = _ServingSession([], reconnect_on_first=False)
    session.readiness.exhausted()
    outbox = _Outbox([])

    # Act
    report = await serve(
        session,
        _service_ports(
            outbox,
            BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z"),
            _ticks(1),
            lambda: None,
        ),
    )

    # Assert
    assert (report.exit_status, session.readiness.is_ready(), outbox.batches) == (1, False, [])


async def test_publisher_treats_nonconfirmation_and_malformed_headers_as_unsafe() -> None:
    # Arrange
    ambiguous = BrokerOutboxPublisher(
        _Router(MessagingError(MessagingRefusal.TRACE_REFUSED, "trace")),
        lambda: "2026-08-25T12:00:01.000Z",
    )
    malformed = BrokerOutboxPublisher(
        _Router(),
        lambda: "2026-08-25T12:00:01.000Z",
    )

    # Act
    results = (
        await ambiguous.publish(EVENT),
        await malformed.publish(replace(EVENT, headers=b"[]")),
    )

    # Assert
    assert tuple(result.outcome for result in results) == (
        PublicationOutcome.AMBIGUOUS,
        PublicationOutcome.REFUSED,
    )


async def test_ambiguous_readback_prevents_recovery_even_with_no_staged_rows() -> None:
    # Arrange
    session = _Session([])
    outbox = _Outbox([()], ambiguous=(EVENT,))

    # Act
    recovered = await recover_application(
        session,
        outbox,
        BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z"),
    )

    # Assert
    assert (recovered, session.readiness.is_ready()) == (False, False)


async def test_unknown_or_mismatched_receiver_family_is_durably_rejected() -> None:
    # Arrange
    proposal_work = _UnitOfWork()
    unknown_settlement = _Settlement()
    mismatched_settlement = _Settlement()
    proposal_message = cast(
        "InboundMessage",
        _Message(BOUND_PROPOSAL_TOPIC, bound_proposal_bytes()),
    )

    # Act
    outcomes = (
        await dispatch_guaranteed(
            "unknown",
            GuaranteedMessage(
                proposal_message,
                cast("MessageSettlement", unknown_settlement),
            ),
            _dispatch_ports(proposal=proposal_work),
        ),
        await dispatch_guaranteed(
            Family.DRONE_EVENT.literal_suffix,
            GuaranteedMessage(
                proposal_message,
                cast("MessageSettlement", mismatched_settlement),
            ),
            _dispatch_ports(),
        ),
    )

    # Assert
    assert (
        outcomes,
        unknown_settlement.outcomes,
        mismatched_settlement.outcomes,
        proposal_work.refusals[0].refusal_code,
    ) == (
        (DispatchOutcome.REFUSED, DispatchOutcome.REFUSED),
        ["rejected"],
        ["rejected"],
        "receiver-family",
    )


async def test_serve_awaits_recovery_states_and_empty_endpoint_sets() -> None:
    # Arrange
    recovering = _ServingSession([], reconnect_on_first=False)
    recovering.readiness.reconnecting()
    empty = _ServingSession([], receiver_names=(), reconnect_on_first=False)
    pauses: list[str] = []

    async def pause() -> None:
        pauses.append("pause")

    # Act
    reports = (
        await serve(
            recovering,
            _service_ports(
                _Outbox([]),
                BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z"),
                _ticks(1),
                pause,
            ),
        ),
        await serve(
            empty,
            _service_ports(
                _Outbox([()]),
                BrokerOutboxPublisher(_Router(), lambda: "2026-08-25T12:00:00.000Z"),
                _ticks(1),
                pause,
            ),
        ),
    )

    # Assert
    assert (
        tuple(report.exit_status for report in reports),
        pauses,
        empty.readiness.is_ready(),
    ) == ((0, 0), ["pause", "pause"], True)


@dataclass
class _OneMessageSession(_ServingSession):
    """Return one durable message after initial recovery."""

    guaranteed: GuaranteedMessage | None = None

    @override
    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return the configured message once."""
        self.received.append((receiver_name, timeout_milliseconds))
        message = self.guaranteed
        self.guaranteed = None
        return message


async def test_connected_processing_counts_outcome_and_removes_readiness_on_publish_refusal() -> (
    None
):
    # Arrange
    settlement = _Settlement()
    guaranteed = GuaranteedMessage(
        cast("InboundMessage", _Message(BOUND_PROPOSAL_TOPIC, bound_proposal_bytes())),
        cast("MessageSettlement", settlement),
    )
    session = _OneMessageSession([], reconnect_on_first=False, guaranteed=guaranteed)
    outbox = _Outbox([(), (EVENT,)])
    publisher = BrokerOutboxPublisher(
        _Router(MessagingError(MessagingRefusal.PUBLISH_REFUSED, EVENT.topic)),
        lambda: "2026-08-25T12:00:00.000Z",
    )

    # Act
    with patch(
        "aerial_rescue_evidence_service.runtime.dispatch_guaranteed",
        AsyncMock(return_value=DispatchOutcome.PROCESSED),
    ):
        report = await serve(
            session,
            _service_ports(
                outbox,
                publisher,
                _ticks(1),
                lambda: None,
            ),
        )

    # Assert
    assert (report.outcomes, session.readiness.is_ready()) == (
        {DispatchOutcome.PROCESSED: 1},
        False,
    )
