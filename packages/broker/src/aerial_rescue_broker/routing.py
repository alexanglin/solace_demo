"""Authorize, validate, and derive publication delivery from an application topic.

Callers provide only a topic, canonical payload, and message properties.  They cannot
select Direct, Guaranteed, or request/reply delivery: the closed contract table selects
the port after topic parsing, role authorization, and payload/topic binding all succeed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_contracts.envelope import check_topic_binding, decode_envelope
from aerial_rescue_contracts.integration import (
    check_agent_response_topic,
    decode_agent_response,
)
from aerial_rescue_contracts.rpc import decode_gateway_request, decode_gateway_response
from aerial_rescue_contracts.topics import Delivery, Family, Topic, delivery_for, parse_topic
from aerial_rescue_domain.principals import Access, Principal, authorize, grants

from aerial_rescue_broker.messaging import (
    DirectPublisher,
    InboundMessage,
    MessagePublisher,
    RequestReplyRequester,
)

__all__ = [
    "DeliveryRouter",
    "GuaranteedReplyResponder",
    "PublicationPorts",
    "RequestReplyRequester",
    "RequestReplyResponder",
    "RoutingError",
    "RoutingRefusal",
    "required_deliveries",
]


class RequestReplyResponder(Protocol):
    """A reply-only capability preserving an inbound request's correlation metadata."""

    def publish_reply(
        self, topic: str, payload: bytes, properties: Mapping[str, object], /
    ) -> None:
        """Publish one correlated response without constructing a requester."""


class GuaranteedReplyResponder:
    """Preserve connector correlation metadata on a confirmed reply publication."""

    def __init__(self, publisher: MessagePublisher) -> None:
        """Retain the guaranteed publisher owned by the mixed responder session."""
        self._publisher = publisher

    def publish_reply(
        self, topic: str, payload: bytes, properties: Mapping[str, object], /
    ) -> None:
        """Forward the exact response and opaque inbound correlation properties."""
        self._publisher.publish(topic, payload, properties)


@dataclass(frozen=True)
class PublicationPorts:
    """The exact publication capabilities constructed for one broker principal."""

    direct: DirectPublisher | None = None
    guaranteed: MessagePublisher | None = None
    requester: RequestReplyRequester | None = None
    responder: RequestReplyResponder | None = None


class RoutingRefusal(Enum):
    """Why publication was refused before the selected broker operation."""

    CAPABILITY_MISMATCH = "constructed capabilities do not equal the role's grants"
    INVALID_TOPIC = "destination is not an application topic"
    NOT_AUTHORIZED = "role is not authorized to publish this topic family"
    INVALID_PAYLOAD = "payload does not validate and bind to its topic"
    INVALID_TIMEOUT = "request/reply timeout must be a positive integer number of milliseconds"
    OPERATION_MISMATCH = "request and reply operations cannot be interchanged"


class RoutingError(ValueError):
    """A publication the typed delivery router refuses."""

    def __init__(self, refusal: RoutingRefusal, value: object) -> None:
        """Retain a structured refusal without retaining untrusted payload bytes."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class _PayloadBindingError(ValueError):
    """An otherwise valid integration body that disagrees with its topic."""


def required_deliveries(role: Principal) -> frozenset[Delivery]:
    """Return the exact delivery capabilities implied by one role's publish grants."""
    return frozenset(delivery_for(family) for family in grants(role, Access.PUBLISH))


def _present_deliveries(ports: PublicationPorts) -> frozenset[Delivery]:
    """Return the capabilities actually supplied to one router."""
    present: set[Delivery] = set()
    if ports.direct is not None:
        present.add(Delivery.DIRECT)
    if ports.guaranteed is not None:
        present.add(Delivery.GUARANTEED)
    if ports.requester is not None or ports.responder is not None:
        present.add(Delivery.REQUEST_REPLY)
    return frozenset(present)


def _request_reply_direction_matches(role: Principal, ports: PublicationPorts) -> bool:
    """Return whether requester/responder capabilities exactly match the role's grants."""
    published = grants(role, Access.PUBLISH)
    expects_requester = Family.GATEWAY_REQUEST in published
    expects_responder = Family.GATEWAY_RESPONSE in published
    return (ports.requester is not None) is expects_requester and (
        ports.responder is not None
    ) is expects_responder


def _validate_gateway_request(topic: Topic, payload: bytes) -> None:
    """Validate an RPC request and bind its mission and operation to the destination."""
    request = decode_gateway_request(payload)
    if request.mission_id != topic.mission_id or request.operation != topic.parameters["operation"]:
        raise _PayloadBindingError


def _validate_payload(topic: Topic, payload: bytes) -> None:
    """Validate the representation owned by ``topic.family`` and its topic binding."""
    if topic.family is Family.GATEWAY_REQUEST:
        _validate_gateway_request(topic, payload)
        return
    if topic.family is Family.GATEWAY_RESPONSE:
        decode_gateway_response(payload)
        return
    if topic.family is Family.AGENT_RESPONSE:
        response = decode_agent_response(payload)
        check_agent_response_topic(response, topic)
        return
    envelope = decode_envelope(payload)
    check_topic_binding(envelope, topic)


class DeliveryRouter:
    """Publish through only the delivery capability required by the validated family."""

    def __init__(self, role: Principal, ports: PublicationPorts) -> None:
        """Refuse both missing authority and capabilities wider than the role's grants."""
        required = required_deliveries(role)
        present = _present_deliveries(ports)
        if present != required or not _request_reply_direction_matches(role, ports):
            value = {
                "role": role.value,
                "required": tuple(sorted(item.value for item in required)),
                "present": tuple(sorted(item.value for item in present)),
            }
            raise RoutingError(RoutingRefusal.CAPABILITY_MISMATCH, value)
        self._role = role
        self._ports = ports

    def publish(self, topic_text: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Validate and authorize one publication, then derive its broker operation."""
        topic = self._prepare(topic_text, payload)
        if topic.family is Family.GATEWAY_REQUEST:
            raise RoutingError(RoutingRefusal.OPERATION_MISMATCH, topic.family.name)
        self._dispatch(topic, topic_text, payload, properties)

    def request(
        self,
        topic_text: str,
        payload: bytes,
        properties: Mapping[str, object],
        timeout_milliseconds: int,
        /,
    ) -> InboundMessage:
        """Validate one request family and return the port's correlated response."""
        if type(timeout_milliseconds) is not int or timeout_milliseconds <= 0:
            raise RoutingError(RoutingRefusal.INVALID_TIMEOUT, timeout_milliseconds)
        topic = self._prepare(topic_text, payload)
        requester = self._ports.requester
        if topic.family is not Family.GATEWAY_REQUEST or requester is None:
            raise RoutingError(RoutingRefusal.OPERATION_MISMATCH, topic.family.name)
        return requester.request(topic_text, payload, properties, timeout_milliseconds)

    def _prepare(self, topic_text: str, payload: bytes) -> Topic:
        """Parse, authorize, and bind one payload before any broker operation."""
        try:
            topic = parse_topic(topic_text)
        except ValueError as error:
            raise RoutingError(RoutingRefusal.INVALID_TOPIC, "redacted-topic") from error
        try:
            authorize(self._role, Access.PUBLISH, topic.family)
        except ValueError as error:
            raise RoutingError(
                RoutingRefusal.NOT_AUTHORIZED,
                {"role": self._role.value, "family": topic.family.name},
            ) from error
        try:
            _validate_payload(topic, payload)
        except (TypeError, ValueError) as error:
            raise RoutingError(RoutingRefusal.INVALID_PAYLOAD, topic.family.name) from error
        return topic

    def _dispatch(
        self,
        parsed: Topic,
        topic_text: str,
        payload: bytes,
        properties: Mapping[str, object],
    ) -> None:
        """Call the one typed port selected by the total delivery table."""
        delivery = delivery_for(parsed.family)
        if delivery is Delivery.DIRECT and self._ports.direct is not None:
            self._ports.direct.publish_unacknowledged(topic_text, payload, properties)
            return
        if delivery is Delivery.GUARANTEED and self._ports.guaranteed is not None:
            self._ports.guaranteed.publish(topic_text, payload, properties)
            return
        if delivery is Delivery.REQUEST_REPLY and self._ports.responder is not None:
            self._ports.responder.publish_reply(topic_text, payload, properties)
            return
        raise RoutingError(RoutingRefusal.CAPABILITY_MISMATCH, self._role.value)
