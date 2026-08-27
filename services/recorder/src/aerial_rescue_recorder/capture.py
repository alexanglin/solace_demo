"""Durable recorder orchestration with settlement strictly after transaction commit.

The database implementation is injected as one unit of work because the broker inbox claim,
complete source event, append-only audit row, and inbox completion must commit atomically. The
service owns their order; ``packages/store`` owns SQLAlchemy and the physical transaction adapter.
No broker publisher appears in this module or in any of its ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Protocol

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import Envelope, check_topic_binding, envelope_document
from aerial_rescue_contracts.topics import Delivery, Family, Topic, delivery_for, format_topic


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
    """Every value the recorder's atomic store adapter must persist together."""

    inbox: InboxFact
    source_event: SourceEventFact
    audit: AuditFact


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
        notification.observed_at,
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
    return RecordingFact(inbox, source, audit)


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
    await transaction.complete_inbox(fact.inbox, ordinal, fact.source_event.observed_at)
    return CaptureOutcome(CaptureDecision.RECORDED, ordinal)


def _positive_ordinal(value: int | None, refusal: CaptureRefusal) -> int:
    """Return a positive non-Boolean audit ordinal or refuse malformed adapter output."""
    if type(value) is not int or value <= 0:
        raise CaptureError(refusal, "redacted-ordinal")
    return value
