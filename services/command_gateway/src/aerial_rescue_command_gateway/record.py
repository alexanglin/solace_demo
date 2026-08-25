"""The CloudEvent record the command gateway publishes for every answer it sends.

``docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md`` requires each answer
to be published twice: to the requestor's reply topic as the RPC reply, and here as an
event on the mission's own gateway-record topic, so the recorder, the dashboard, and the
audit timeline observe it without knowing anything about the Event Mesh Tool or about
Solace request/reply.

The four members the Event Mesh Tool could not have produced -- the identifier, the
instant, the producer sequence, and the trace parent -- are supplied by the composition
root as a :class:`RecordStamp`, so this module reads no clock and consumes no random
source. The record is read back through the envelope profile before it is returned, so a
defect fails here rather than reaching the broker.

This module is pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.rpc import GatewayResponse, gateway_response_document
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic

from aerial_rescue_command_gateway import CommandGatewayError, event_source


class RecordRefusal(Enum):
    """Why a record cannot be built."""

    SEQUENCE_RANGE = "producer sequence outside the representable range"
    UNPUBLISHABLE = "record does not satisfy the envelope profile it was built for"


class RecordError(CommandGatewayError):
    """A record the profile refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class RecordStamp:
    """What the composition root supplies that this module may not read for itself."""

    event_id: str
    occurred_at: str
    sequence: int
    traceparent: str


def _sequence_text(value: int) -> str:
    """Return the zero-padded producer sequence, refusing one the form cannot carry."""
    rendered = sequence_text(value)
    if rendered is None:
        raise RecordError(RecordRefusal.SEQUENCE_RANGE, value)
    return rendered


def response_record(
    response: GatewayResponse,
    stamp: RecordStamp,
) -> tuple[str, dict[str, object]]:
    """Return the topic and envelope document recording one answer.

    Args:
        response: The answer that was sent to the requestor.
        stamp: The identifier, instant, sequence, and trace parent to record it under.

    Returns:
        The topic text and the envelope document, in that order.

    Raises:
        RecordError: With ``SEQUENCE_RANGE`` for a sequence the form cannot carry, or
            ``UNPUBLISHABLE`` if the built record does not satisfy the profile it claims.
    """
    topic = Topic(Family.GATEWAY_RECORD, response.mission_id, {"requestId": response.request_id})
    declared = event_type(topic)
    envelope = Envelope(
        id=stamp.event_id,
        source=event_source(),
        type=declared,
        subject=response.mission_id,
        time=stamp.occurred_at,
        dataschema=binding_for(declared).dataschema,
        sequence=_sequence_text(stamp.sequence),
        correlation_id=response.request_id,
        traceparent=stamp.traceparent,
        data=gateway_response_document(response),
    )
    document = envelope_document(envelope)
    try:
        check_topic_binding(parse_envelope(document), topic)
    except ValueError as refusal:
        raise RecordError(RecordRefusal.UNPUBLISHABLE, str(refusal)) from refusal
    return format_topic(topic), document
