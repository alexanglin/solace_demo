"""Receiver-only broker capture into durable audit-ordered dashboard events."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from aerial_rescue_broker.messaging import AcknowledgingReceiver, InboundMessage, Outcome
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    EnvelopeError,
    check_topic_binding,
    decode_envelope,
    envelope_document,
)
from aerial_rescue_contracts.topics import TopicError, parse_topic
from aerial_rescue_contracts.view import DashboardEvent, ViewError, project
from aerial_rescue_domain.mission import MissionError
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard_events import (
    BrokerEvent,
    DashboardEventError,
    DashboardEventRefusal,
)


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
    payload = message.get_payload_as_bytes()
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
