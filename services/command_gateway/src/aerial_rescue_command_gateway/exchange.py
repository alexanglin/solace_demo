"""One request in, one reply and one record out.

Where the three pure modules meet a broker. Both ports are injected, so this module opens
no socket, reads no clock, and consumes no random source; the composition root supplies a
publisher and a stamp, and this decides what happens.

Two refusals publish nothing at all, and both are deliberate. A message whose reply target
cannot be resolved has nowhere safe to be answered, and answering it anywhere else is the
injection ``docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md``
exists to prevent. A message whose body is not a gateway request, or does not agree with the
topic it arrived on, cannot be answered either: an RPC reply echoes the mission, operation,
and command type it answers, and inventing them would put values on the wire that no
requestor sent. Both leave the requestor to time out, which is the right outcome for a
producer that is broken or lying.

The reply is published before the record, because
``docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md`` makes the record the
weaker of the two: losing it costs an audit line, never an answer or a command. A record
that cannot be published is therefore reported rather than retried, because a retry could
double-answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_broker.messaging import (
    InboundMessage,
    MessagePublisher,
    MessagingError,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.rpc import (
    GatewayRequest,
    GatewayResponse,
    RpcError,
    decode_gateway_request,
    gateway_response_document,
)
from aerial_rescue_contracts.topics import Family, TopicError, parse_topic

from aerial_rescue_command_gateway import CommandGatewayError
from aerial_rescue_command_gateway.policy import answer
from aerial_rescue_command_gateway.record import RecordStamp, response_record
from aerial_rescue_command_gateway.reply import (
    REPLY_METADATA_KEY,
    ReplyError,
    ReplyTarget,
    reply_target,
)

_OPERATION_PARAMETER: Final = "operation"
_NO_PROPERTIES: Final[dict[str, object]] = {}


class ExchangeOutcome(Enum):
    """What became of one inbound request."""

    REPLIED = "reply and record published"
    RECORD_FAILED = "reply published, record refused"
    UNDELIVERABLE = "no reply target, so nothing was published"
    UNREADABLE = "body is not a request for the topic it arrived on"


@dataclass(frozen=True)
class Exchange:
    """What one inbound request produced, for the caller to log and count."""

    outcome: ExchangeOutcome
    topic: str | None = None
    request_id: str | None = None
    detail: str | None = None


def _agrees(topic_text: object, request: GatewayRequest) -> bool:
    """Report whether a request belongs on the topic it arrived on.

    The same discipline ``envelope.check_topic_binding`` applies to an event: the body must
    repeat what the topic already says, or one of the two is lying.
    """
    try:
        topic = parse_topic(topic_text)
    except TopicError:
        return False
    return (
        topic.family is Family.GATEWAY_REQUEST
        and topic.mission_id == request.mission_id
        and topic.parameters[_OPERATION_PARAMETER] == request.operation
    )


def _request_of(message: InboundMessage) -> GatewayRequest | None:
    """Return the request a message carries, or ``None`` when it carries none."""
    payload = message.get_payload_as_bytes()
    if payload is None:
        return None
    try:
        request = decode_gateway_request(bytes(payload))
    except canonical.CanonicalizationError, RpcError:
        return None
    if not _agrees(message.get_destination_name(), request):
        return None
    return request


def _record(
    publisher: MessagePublisher, response: GatewayResponse, stamp: RecordStamp
) -> str | None:
    """Publish the record for one answer; return why it did not land, or ``None``.

    A record that cannot be built and one the broker refuses are the same outcome to the
    caller: the answer has already gone, so this is reported rather than raised. Raising
    would invite a retry, and a retry would double-answer.
    """
    try:
        topic, document = response_record(response, stamp)
    except CommandGatewayError as refusal:
        return str(refusal)
    try:
        publisher.publish(topic, canonical.canonical_bytes(document), _NO_PROPERTIES)
    except MessagingError as refusal:
        return str(refusal)
    return None


def _reply(publisher: MessagePublisher, target: ReplyTarget, response: GatewayResponse) -> None:
    """Publish one answer to the requestor, echoing its correlation metadata unchanged."""
    publisher.publish(
        target.topic,
        canonical.canonical_bytes(gateway_response_document(response)),
        {REPLY_METADATA_KEY: target.metadata},
    )


def handle_message(
    message: InboundMessage, publisher: MessagePublisher, stamp: RecordStamp
) -> Exchange:
    """Answer one inbound request, and record the answer.

    Args:
        message: The message as it arrived, including the user properties its producer set.
        publisher: Where the reply and the record are sent.
        stamp: The identifier, instant, sequence, and trace parent for the record.

    Returns:
        What became of the message. A publication failure on the reply propagates, because
        an answer that could not be sent is not an outcome this can report; a failure to
        build the record is reported instead, because the answer has already gone.
    """
    try:
        target = reply_target(message.get_properties())
    except ReplyError as refusal:
        return Exchange(ExchangeOutcome.UNDELIVERABLE, detail=str(refusal))
    request = _request_of(message)
    if request is None:
        return Exchange(ExchangeOutcome.UNREADABLE, target.topic, target.request_id)
    response = answer(request, target.request_id)
    _reply(publisher, target, response)
    detail = _record(publisher, response, stamp)
    outcome = ExchangeOutcome.REPLIED if detail is None else ExchangeOutcome.RECORD_FAILED
    return Exchange(outcome, target.topic, target.request_id, detail)
