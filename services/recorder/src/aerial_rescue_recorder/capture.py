"""Durable recorder orchestration with settlement strictly after transaction commit.

The database implementation is injected as one unit of work because the broker inbox claim,
complete source event, append-only audit row, and inbox completion must commit atomically. The
service owns their order; ``packages/store`` owns SQLAlchemy and the physical transaction adapter.
No broker publisher appears in this module or in any of its ports.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Protocol

from aerial_rescue_broker.messaging import (
    AcknowledgingReceiver,
    InboundMessage,
    Outcome,
    inbound_payload,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import (
    Envelope,
    EnvelopeError,
    check_topic_binding,
    decode_envelope,
    envelope_document,
)
from aerial_rescue_contracts.topics import (
    Delivery,
    Family,
    Topic,
    TopicError,
    delivery_for,
    format_topic,
    parse_topic,
)
from aerial_rescue_contracts.view import DashboardEvent, ViewError, project
from aerial_rescue_domain.mission import MissionError
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard.events import (
    BrokerEvent,
    DashboardEventError,
    DashboardEventRefusal,
)


class RecordingPolicy(Enum):
    """Whether a family is an authoritative application notification to record."""

    RECORD = "record the accepted application notification"
    EXCLUDED = "exclude transport-only or non-authoritative integration input"


_EXCLUDED_FAMILIES = frozenset(
    {Family.AGENT_RESPONSE, Family.GATEWAY_REQUEST, Family.GATEWAY_RESPONSE}
)


def recording_policy(family: Family) -> RecordingPolicy:
    """Classify every topic family without inspecting or retaining an excluded raw body."""
    if family in _EXCLUDED_FAMILIES:
        return RecordingPolicy.EXCLUDED
    return RecordingPolicy.RECORD


class CaptureRefusal(Enum):
    """Why an already typed ingress value cannot enter the durable recorder transaction."""

    SETTLEMENT_MISMATCH = "settlement capability does not match the family's delivery"
    EXCLUDED_FAMILY = "transport-only or integration input cannot become an audit event"
    TOPIC_BINDING = "accepted envelope and arriving topic disagree"
    INBOX_OUTCOME = "durable inbox outcome is incomplete or malformed"
    AUDIT_ORDINAL = "durable audit append returned no positive ordinal"


class CaptureError(ValueError):
    """A capture refused before settlement, carrying only redacted structured context."""

    def __init__(self, refusal: CaptureRefusal, value: object) -> None:
        """Retain a structured refusal without retaining untrusted payload bytes."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class CaptureDecision(Enum):
    """Whether this delivery appended a fact or replayed its durable prior result."""

    RECORDED = "recorded"
    DUPLICATE = "exact duplicate"


class InboxDecision(Enum):
    """The typed result of the recorder's durable inbox claim."""

    CLAIMED = "claimed"
    DUPLICATE = "exact duplicate"


@dataclass(frozen=True)
class InboxOutcome:
    """A new claim or the exact audit ordinal returned by a committed duplicate."""

    decision: InboxDecision
    audit_ordinal: int | None


@dataclass(frozen=True)
class ReceivedNotification:
    """One notification already decoded and validated at the broker trust boundary."""

    topic: Topic
    envelope: Envelope
    observed_at: str


@dataclass(frozen=True)
class InboxFact:
    """The complete durable identity claimed before applying any recorder effect."""

    consumer: str
    source: str
    event_id: str
    mission_id: str
    canonical_digest: str


@dataclass(frozen=True)
class SourceEventFact:
    """The immutable complete source event independently retained for provenance."""

    source: str
    event_id: str
    mission_id: str
    topic: str
    canonical_digest: str
    canonical_event: bytes
    observed_at: str


@dataclass(frozen=True)
class AuditFact:
    """One accepted application notification ready for append-only audit ordering."""

    mission_id: str
    kind: str
    occurred_at: str
    canonical_event: bytes
    correlation_id: str
    causation_id: str | None
    traceparent: str


@dataclass(frozen=True)
class RecordingFact:
    """Every value the recorder's atomic store adapter must persist together.

    ``observed_at`` is the recorder's receive instant and completes the inbox; the source
    event carries the event's own time, because the shared immutable ``source_event`` row is
    derived from the event alone and the evidence service stores the same row.
    """

    inbox: InboxFact
    source_event: SourceEventFact
    audit: AuditFact
    broker_event: BrokerEvent
    observed_at: str


@dataclass(frozen=True)
class CaptureOutcome:
    """The durable result safe to settle after the unit of work exits successfully."""

    decision: CaptureDecision
    audit_ordinal: int


class RecordingTransaction(Protocol):
    """Operations one recorder transaction must expose, in service-owned order."""

    async def claim_inbox(self, fact: InboxFact, /) -> InboxOutcome:
        """Claim new work or return one exact committed prior ordinal."""

    async def record_source_event(self, fact: SourceEventFact, /) -> None:
        """Persist the complete immutable source event idempotently."""

    async def append_audit(self, fact: AuditFact, /) -> int:
        """Append the accepted fact and return its authoritative mission ordinal."""

    async def link_broker_event(self, event: BrokerEvent, mission_id: str, ordinal: int, /) -> None:
        """Link the appended ordinal to its broker identity, so the watermark can see it."""

    async def complete_inbox(self, fact: InboxFact, ordinal: int, processed_at: str, /) -> None:
        """Store the transaction's exact prior result for a future redelivery."""


class RecordingUnitOfWork(Protocol):
    """An async context that commits on success and rolls back on every failure."""

    async def __aenter__(self) -> RecordingTransaction:
        """Open one transaction."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit a clean exit or roll back an exceptional one."""


class RecordingTransactions(Protocol):
    """Factory for one package-store-owned recording transaction."""

    def open(self) -> RecordingUnitOfWork:
        """Return a fresh transaction context without opening it early."""


class AcceptedSettlement(Protocol):
    """The one settlement capability a Guaranteed delivery may carry."""

    def accept(self) -> None:
        """Settle the already committed delivery as accepted."""


class Recorder:
    """Coordinate exact recorder effects without owning transport or SQLAlchemy."""

    def __init__(self, consumer: str, transactions: RecordingTransactions) -> None:
        """Retain the durable consumer identity and injected unit-of-work factory."""
        self._consumer = consumer
        self._transactions = transactions

    async def capture(
        self,
        notification: ReceivedNotification,
        settlement: AcceptedSettlement | None,
    ) -> CaptureOutcome:
        """Persist one notification atomically, then settle only a Guaranteed delivery."""
        family = notification.topic.family
        if recording_policy(family) is RecordingPolicy.EXCLUDED:
            raise CaptureError(CaptureRefusal.EXCLUDED_FAMILY, family.name)
        delivery = delivery_for(family)
        _require_settlement(delivery, settlement)
        fact = _recording_fact(self._consumer, notification)
        async with self._transactions.open() as transaction:
            outcome = await _persist(transaction, fact)
        if settlement is not None:
            settlement.accept()
        return outcome


def _require_settlement(delivery: Delivery, settlement: AcceptedSettlement | None) -> None:
    """Require exactly one settlement capability for Guaranteed delivery and no other."""
    matched = (delivery is Delivery.GUARANTEED) is (settlement is not None)
    if not matched:
        raise CaptureError(CaptureRefusal.SETTLEMENT_MISMATCH, delivery.value)


def _recording_fact(consumer: str, notification: ReceivedNotification) -> RecordingFact:
    """Recompute complete canonical provenance and build the atomic durable fact."""
    try:
        check_topic_binding(notification.envelope, notification.topic)
    except ValueError as error:
        raise CaptureError(CaptureRefusal.TOPIC_BINDING, notification.topic.family.name) from error
    envelope = notification.envelope
    canonical_event = canonical.canonical_bytes(envelope_document(envelope))
    event_digest = source_event_digest(envelope)
    inbox = InboxFact(consumer, envelope.source, envelope.id, envelope.subject, event_digest)
    source = SourceEventFact(
        envelope.source,
        envelope.id,
        envelope.subject,
        format_topic(notification.topic),
        event_digest,
        canonical_event,
        envelope.time,
    )
    audit = AuditFact(
        envelope.subject,
        envelope.type,
        envelope.time,
        canonical_event,
        envelope.correlation_id,
        envelope.causation_id,
        envelope.traceparent,
    )
    broker_event = BrokerEvent(
        source=envelope.source,
        event_id=envelope.id,
        source_sequence=int(envelope.sequence),
        payload_digest=hashlib.sha256(canonical_event).hexdigest(),
    )
    return RecordingFact(inbox, source, audit, broker_event, notification.observed_at)


async def _persist(transaction: RecordingTransaction, fact: RecordingFact) -> CaptureOutcome:
    """Apply new work once or return a committed duplicate without a second effect."""
    claimed = await transaction.claim_inbox(fact.inbox)
    if claimed.decision is InboxDecision.DUPLICATE:
        ordinal = _positive_ordinal(claimed.audit_ordinal, CaptureRefusal.INBOX_OUTCOME)
        return CaptureOutcome(CaptureDecision.DUPLICATE, ordinal)
    if claimed.audit_ordinal is not None:
        raise CaptureError(CaptureRefusal.INBOX_OUTCOME, fact.inbox.event_id)
    await transaction.record_source_event(fact.source_event)
    ordinal = _positive_ordinal(
        await transaction.append_audit(fact.audit), CaptureRefusal.AUDIT_ORDINAL
    )
    await transaction.link_broker_event(fact.broker_event, fact.audit.mission_id, ordinal)
    await transaction.complete_inbox(fact.inbox, ordinal, fact.observed_at)
    return CaptureOutcome(CaptureDecision.RECORDED, ordinal)


def _positive_ordinal(value: int | None, refusal: CaptureRefusal) -> int:
    """Return a positive non-Boolean audit ordinal or refuse malformed adapter output."""
    if type(value) is not int or value <= 0:
        raise CaptureError(refusal, "redacted-ordinal")
    return value


class _BoundaryError(ValueError):
    """A missing transport value that cannot reach a contract validator."""


class EventAppender(Protocol):
    """The transaction owner that returns only after broker identity and audit commit."""

    async def append(self, event: BrokerEvent, record: AuditRecord) -> None:
        """Commit one deduplicated broker identity and normalized audit record."""


@dataclass(frozen=True)
class _CaptureMaterial:
    event: BrokerEvent
    record: AuditRecord


_PERMANENT_STORE_REFUSALS = frozenset(
    {
        DashboardEventRefusal.DIVERGENT_DUPLICATE,
        DashboardEventRefusal.SEQUENCE_REUSED,
        DashboardEventRefusal.STALE_SEQUENCE,
        DashboardEventRefusal.UNREADABLE_SOURCE,
        DashboardEventRefusal.UNREADABLE_EVENT,
    }
)
_BOUNDARY_ERRORS = (
    _BoundaryError,
    canonical.CanonicalizationError,
    TopicError,
    EnvelopeError,
    ViewError,
    MissionError,
)


def _normalized_document(event: DashboardEvent) -> dict[str, object]:
    return {
        "kind": event.kind,
        "eventClass": event.event_class.name,
        "mission": event.mission,
        "time": event.time,
        "data": dict(event.data),
    }


def _capture_material(message: InboundMessage) -> _CaptureMaterial:
    destination = message.get_destination_name()
    payload = inbound_payload(message)
    if payload is None:
        raise _BoundaryError
    topic = parse_topic(destination)
    envelope = decode_envelope(payload)
    check_topic_binding(envelope, topic)
    normalized = project(envelope)
    envelope_bytes = canonical.canonical_bytes(envelope_document(envelope))
    broker_event = BrokerEvent(
        source=envelope.source,
        event_id=envelope.id,
        source_sequence=int(envelope.sequence),
        payload_digest=hashlib.sha256(envelope_bytes).hexdigest(),
    )
    record = AuditRecord(
        mission_id=normalized.mission,
        kind=normalized.kind,
        occurred_at=normalized.time,
        payload=canonical.canonical_bytes(_normalized_document(normalized)),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        traceparent=envelope.traceparent,
    )
    return _CaptureMaterial(broker_event, record)


class CaptureProcessor:
    """Validate source binding and settle guaranteed messages after durable commit."""

    def __init__(self, appender: EventAppender) -> None:
        """Use the injected transaction owner; construct no broker publisher."""
        self._appender = appender

    async def _append(self, message: InboundMessage) -> None:
        material = _capture_material(message)
        await self._appender.append(material.event, material.record)

    async def process_guaranteed(
        self,
        receiver: AcknowledgingReceiver,
        message: InboundMessage,
    ) -> None:
        """Commit then accept, reject permanent input, or leave transient work recoverable."""
        try:
            await self._append(message)
        except _BOUNDARY_ERRORS:
            receiver.settle(message, Outcome.REJECTED)
            return
        except DashboardEventError as error:
            if error.refusal in _PERMANENT_STORE_REFUSALS:
                receiver.settle(message, Outcome.REJECTED)
                return
            receiver.settle(message, Outcome.FAILED)
            return
        except Exception:
            receiver.settle(message, Outcome.FAILED)
            raise
        receiver.settle(message, Outcome.ACCEPTED)

    async def process_best_effort(self, message: InboundMessage) -> None:
        """Persist a direct message when received without making acknowledgement claims."""
        try:
            await self._append(message)
        except _BOUNDARY_ERRORS:
            return
        except DashboardEventError:
            return
