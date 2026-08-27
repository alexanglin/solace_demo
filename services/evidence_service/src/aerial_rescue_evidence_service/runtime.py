"""Long-running Evidence Service broker/store composition and recovery."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, cast

from aerial_rescue_broker.ingress import (
    IngressError as BrokerIngressError,
)
from aerial_rescue_broker.ingress import (
    PayloadSchemaExecutor,
    validate_notification,
)
from aerial_rescue_broker.messaging import (
    BrokerLifecycle,
    BrokerLifecycleState,
    GuaranteedMessage,
    GuaranteedProcessingBindings,
    MessageSettlement,
    MessagingError,
    MessagingRefusal,
    UnsettledMessageError,
)
from aerial_rescue_broker.queues import family_queue_name, guaranteed_grants
from aerial_rescue_broker.routing import RoutingError
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_contracts.topics import Family, parse_topic
from aerial_rescue_domain.principals import Principal
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome

from aerial_rescue_evidence_service.outbox import (
    OutboxPort,
    PublicationOutcome,
    PublicationResult,
    PublisherPort,
    drain_once,
)
from aerial_rescue_evidence_service.ports import (
    DecisionStamp,
    EvidenceUnitOfWork,
    InboundDelivery,
    SourceUnitOfWork,
)
from aerial_rescue_evidence_service.processing import (
    ProcessingError,
    handle_delivery,
)
from aerial_rescue_evidence_service.publication import PRODUCER
from aerial_rescue_evidence_service.source_processing import (
    SourceProcessingError,
    handle_source_delivery,
)
from aerial_rescue_evidence_service.wire import IngressError as ProposalIngressError

RECEIVE_WINDOW_MILLISECONDS = 1_000
"""Bound one fair durable-channel poll so shutdown and recovery remain observable."""

TRACE_VERSION = "00"
TRACE_SAMPLED = "01"
TRACE_PARENT_DIGITS = 16


class RecoverySession(Protocol):
    """The broker lifecycle handoff required after application recovery."""

    @property
    def readiness(self) -> BrokerLifecycle:
        """Return shared transport and application readiness."""

    def rebind_complete(self) -> None:
        """Mark bindings and durable recovery complete."""


class ProcessingSession(RecoverySession, Protocol):
    """The named durable receive capability used by the long-running loop."""

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return stable durable receiver names."""

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return one bounded durable delivery or an idle result."""


class RecoveryOutbox(OutboxPort, Protocol):
    """Staged publication and ambiguity evidence owned by recovery."""

    async def reconciliation(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return ambiguous rows that prevent blind recovery."""


class PublicationRouter(Protocol):
    """The role-authorized publication capability used by the outbox worker."""

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Publish one schema-validated event with exact stored properties."""


class BrokerOutboxPublisher:
    """Publish exact staged rows through the role-authorized Guaranteed router."""

    def __init__(self, router: PublicationRouter, confirmed_at: Callable[[], str]) -> None:
        """Retain the typed router and trusted confirmation clock."""
        self._router = router
        self._confirmed_at = confirmed_at

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Map only broker confirmation to success and preserve ambiguity."""
        try:
            properties = _publication_properties(event.headers)
            self._router.publish(event.topic, event.payload, properties)
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


def _publication_properties(headers: bytes) -> Mapping[str, object]:
    """Decode the exact stored headers and reject every non-object value."""
    properties = canonical.decode(headers)
    if not isinstance(properties, Mapping):
        raise TypeError
    return cast("Mapping[str, object]", properties)


class DispatchOutcome(Enum):
    """Whether one durable delivery processed or followed its refusal path."""

    PROCESSED = "processed"
    REFUSED = "refused"


@dataclass(frozen=True)
class ServeReport:
    """Durable dispatch counts and the supervisor-facing exit status."""

    outcomes: Mapping[DispatchOutcome, int]
    exit_status: int


@dataclass
class CountingStamps:
    """Producer stamps continuing after the last committed decision/audit pair."""

    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    sequence: int = field(default=0)
    producer_id: str = field(default="evidence-runtime")

    def next_stamp(self) -> DecisionStamp:
        """Mint two consecutive producer sequences and trusted event identities."""
        decision_sequence = self.sequence
        audit_sequence = decision_sequence + 1
        self.sequence = audit_sequence + 1
        return DecisionStamp(
            producer_id=self.producer_id,
            decision_id=self.identifiers(),
            decision_event_id=self.identifiers(),
            audit_record_id=self.identifiers(),
            audit_event_id=self.identifiers(),
            decided_at=format_instant(self.clock()),
            decision_sequence=decision_sequence,
            audit_sequence=audit_sequence,
            traceparent="-".join(
                (
                    TRACE_VERSION,
                    self.identifiers(),
                    self.identifiers()[:TRACE_PARENT_DIGITS],
                    TRACE_SAMPLED,
                )
            ),
        )


class RuntimeRefusalPort(Protocol):
    """The independent body-free refusal transaction shared by both input paths."""

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Persist one bounded refusal before broker rejection."""


@dataclass(frozen=True, slots=True)
class DispatchPorts:
    """Validated ingress and durable transaction capabilities for one delivery."""

    schemas: PayloadSchemaExecutor
    next_stamp: Callable[[], DecisionStamp]
    proposal_work: EvidenceUnitOfWork
    source_work: SourceUnitOfWork


@dataclass(frozen=True, slots=True)
class ServicePorts:
    """The bounded application capabilities owned by the long-running loop."""

    dispatch: DispatchPorts
    outbox: RecoveryOutbox
    publisher: PublisherPort
    running: Callable[[], bool]
    pause: Callable[[], Awaitable[None] | None]


class _BoundSettlement:
    """Adapt the broker's one-shot synchronous capability to processing ports."""

    def __init__(self, settlement: MessageSettlement) -> None:
        """Retain only the capability bound to the received message."""
        self._settlement = settlement

    async def accept(self, _event_id: str) -> None:
        """Accept after the processing transaction has committed."""
        self._settlement.accept()

    async def reject(self) -> None:
        """Reject after the body-free refusal transaction has committed."""
        self._settlement.reject()


async def dispatch_guaranteed(
    receiver_name: str,
    guaranteed: GuaranteedMessage,
    ports: DispatchPorts,
) -> DispatchOutcome:
    """Execute schema admission and route one durable family to its transaction."""
    message = guaranteed.message
    topic = message.get_destination_name() or ""
    payload = message.get_payload_as_bytes() or b""
    delivery = InboundDelivery(topic, payload, hashlib.sha256(payload).hexdigest())
    settlement = _BoundSettlement(guaranteed.settlement)
    source_channel = receiver_name == Family.DRONE_EVENT.literal_suffix
    proposal_channel = receiver_name == Family.AGENT_PROPOSAL.literal_suffix
    if not source_channel and not proposal_channel:
        await _runtime_refusal(
            delivery,
            "receiver-family",
            ports.proposal_work,
            settlement,
            receiver_name,
        )
        return DispatchOutcome.REFUSED
    work: RuntimeRefusalPort = ports.source_work if source_channel else ports.proposal_work
    try:
        _validate_channel_family(topic, payload, ports.schemas, source_channel)
    except BrokerIngressError, TypeError, ValueError:
        await _runtime_refusal(
            delivery,
            "schema-invalid",
            work,
            settlement,
            receiver_name,
        )
        return DispatchOutcome.REFUSED
    try:
        if source_channel:
            await handle_source_delivery(delivery, ports.source_work, settlement)
        else:
            await handle_delivery(
                delivery,
                ports.next_stamp(),
                ports.proposal_work,
                settlement,
            )
    except ProcessingError, ProposalIngressError, SourceProcessingError:
        return DispatchOutcome.REFUSED
    return DispatchOutcome.PROCESSED


def _validate_channel_family(
    topic: str,
    payload: bytes,
    schemas: PayloadSchemaExecutor,
    source_channel: bool,
) -> None:
    """Execute the registered schema and bind it to the selected durable channel."""
    validated = validate_notification(topic, payload, schemas)
    expected = Family.DRONE_EVENT if source_channel else Family.AGENT_PROPOSAL
    if validated.topic.family is not expected:
        raise ValueError


async def _runtime_refusal(
    delivery: InboundDelivery,
    refusal_code: str,
    work: RuntimeRefusalPort,
    settlement: _BoundSettlement,
    channel: str,
) -> None:
    """Persist only bounded metadata and a one-way body digest before rejection."""
    family: str | None = None
    source: str | None = None
    with suppress(ValueError):
        family = parse_topic(delivery.topic).family.literal_suffix
    with suppress(ValueError):
        source = decode_envelope(delivery.payload).source
    fact = BrokerRefusalCandidate(
        consumer="evidence-service",
        source=source,
        family=family,
        channel=f"evidence-service-{channel.replace('.', '-')}",
        refusal_code=refusal_code,
        raw_digest=hashlib.sha256(delivery.payload).hexdigest(),
    )
    await work.refuse(fact)
    await settlement.reject()


async def _native_trace_refusal(
    error: UnsettledMessageError,
    work: RuntimeRefusalPort,
    channel: str,
) -> None:
    """Commit body-free native-trace evidence before rejecting its exact delivery."""
    fact = BrokerRefusalCandidate(
        consumer="evidence-service",
        source=error.metadata.source,
        family=error.metadata.family,
        channel=f"evidence-service-{channel.replace('.', '-')}",
        refusal_code="native-trace-refused",
        raw_digest=error.metadata.raw_digest,
    )
    await work.refuse(fact)
    error.settlement.reject()


async def recover_application(
    session: RecoverySession,
    outbox: RecoveryOutbox,
    publisher: PublisherPort,
) -> bool:
    """Drain every staged batch and read back ambiguity before restoring readiness."""
    while True:
        result = await drain_once(outbox, publisher)
        if result.refused or result.ambiguous:
            session.readiness.recovery_required()
            return False
        if result.visited == 0:
            break
    if await outbox.reconciliation(PRODUCER):
        session.readiness.recovery_required()
        return False
    session.rebind_complete()
    return True


async def _receive_outcome(
    session: ProcessingSession,
    ports: ServicePorts,
    channel: str,
) -> DispatchOutcome | None:
    """Receive and dispatch one delivery, including native validation refusals."""
    try:
        guaranteed = session.receive_guaranteed(channel, RECEIVE_WINDOW_MILLISECONDS)
    except UnsettledMessageError as error:
        work: RuntimeRefusalPort = (
            ports.dispatch.source_work
            if channel == Family.DRONE_EVENT.literal_suffix
            else ports.dispatch.proposal_work
        )
        await _native_trace_refusal(error, work, channel)
        return DispatchOutcome.REFUSED
    if guaranteed is None:
        return None
    return await dispatch_guaranteed(channel, guaranteed, ports.dispatch)


async def serve(
    session: ProcessingSession,
    ports: ServicePorts,
) -> ServeReport:
    """Process one bounded channel at a time and recover before restored readiness."""
    counted: dict[DispatchOutcome, int] = {}
    channel_index = 0
    while ports.running():
        state = session.readiness.state
        if state in {BrokerLifecycleState.EXHAUSTED, BrokerLifecycleState.CLOSED}:
            break
        if not await _ready_for_receive(session, ports):
            continue
        channels = session.receiver_names
        if not channels:
            await _pause(ports.pause)
            continue
        channel = channels[channel_index % len(channels)]
        channel_index += 1
        outcome = await _receive_outcome(session, ports, channel)
        if outcome is None:
            continue
        counted[outcome] = counted.get(outcome, 0) + 1
        publication = await drain_once(ports.outbox, ports.publisher)
        if publication.refused or publication.ambiguous:
            session.readiness.recovery_required()
    return ServeReport(
        counted,
        int(session.readiness.state is BrokerLifecycleState.EXHAUSTED),
    )


async def _ready_for_receive(session: ProcessingSession, ports: ServicePorts) -> bool:
    """Wait through reconnects and complete durable recovery before consumption."""
    if session.readiness.state not in {
        BrokerLifecycleState.CONNECTED,
        BrokerLifecycleState.RECOVERY_PENDING,
    }:
        await _pause(ports.pause)
        return False
    if session.readiness.is_ready():
        return True
    if await recover_application(session, ports.outbox, ports.publisher):
        return True
    await _pause(ports.pause)
    return False


async def _pause(pause: Callable[[], Awaitable[None] | None]) -> None:
    """Permit deterministic tests while awaiting the production recovery cadence."""
    result = pause()
    if inspect.isawaitable(result):
        await result


def evidence_bindings() -> GuaranteedProcessingBindings:
    """Derive the Evidence Service's complete durable input set from its grants."""
    role = Principal.EVIDENCE_SERVICE
    return GuaranteedProcessingBindings(
        {
            family.literal_suffix: family_queue_name(role, family)
            for family in guaranteed_grants(role)
        }
    )
