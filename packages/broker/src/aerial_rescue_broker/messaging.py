"""The typed façade over the pinned Solace client, and the only place it is called.

``docs/adr/0028-untyped-solace-client-boundary.md`` accepts that `solace-pubsubplus` 1.11.0
ships no type information, so everything it returns is ``Any`` and static analysis of every
call into it is lost. The compensating control it names is this module: a typed surface that
the rest of the tree talks to, so the untyped calls are confined to one file with tests
rather than spread across every service.

The three ports below are what owned code depends on. ``InboundMessage`` is named for the
methods the upstream message object already has, so a real message satisfies it without a
wrapper; the publisher and receiver are owned classes, because their upstream shapes are
builder chains rather than the operations a caller wants.

Nothing here decides anything. Which topic an answer may go to, and what an answer says,
belong to the command gateway (``docs/adr/0005-deterministic-command-gateway.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_domain.principals import Principal
from solace.messaging.config.solace_properties import (
    authentication_properties as authentication,
)
from solace.messaging.config.solace_properties import (
    service_properties,
)
from solace.messaging.config.solace_properties import (
    transport_layer_properties as transport,
)
from solace.messaging.config.transport_security_strategy import TLS
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.topic import Topic as SolaceTopic
from solace.messaging.resources.topic_subscription import TopicSubscription

PUBLISH_TIMEOUT_MILLISECONDS: Final = 10_000
"""Bound on one guaranteed publication; see docs/operating-parameters.md."""

CONNECTION_RETRIES: Final = 0
RECONNECTION_ATTEMPTS: Final = 0
"""Both zero so a broker that is absent fails the caller rather than retrying forever.

The first ``mesh`` run found the other behaviour: a client refused for a bad credential
retried without ever logging an error, and the failure was visible only in the broker's own
event log (``release-evidence/phase-0/mesh-first-run.md``).
"""


class MessagingRefusal(Enum):
    """Why a broker endpoint cannot be used."""

    INSECURE_TRANSPORT = "broker URL does not use a validated TLS transport"
    PUBLISH_REFUSED = "the broker did not acknowledge the publication"


class MessagingError(ValueError):
    """An endpoint this module refuses, carrying the refusal as structured data."""

    def __init__(self, refusal: MessagingRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class BrokerEndpoint:
    """Where the broker is, and what signs its certificate."""

    url: str
    vpn: str
    trust_store: str


class InboundMessage(Protocol):
    """One message as it arrived, named for the methods the upstream object already has."""

    def get_payload_as_bytes(self) -> bytes | None:
        """Return the payload, or ``None`` when the message carries none."""

    def get_destination_name(self) -> str | None:
        """Return the topic the message arrived on."""

    def get_properties(self) -> Mapping[str, object]:
        """Return the user properties the producer set."""


class MessagePublisher(Protocol):
    """Somewhere to send one message, with the user properties it must carry."""

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Publish one message and wait for the broker to acknowledge it."""


class MessageReceiver(Protocol):
    """Somewhere one message arrives from."""

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""


def connection_properties(
    endpoint: BrokerEndpoint, role: Principal, credential: str
) -> dict[str, object]:
    """Return the connection properties binding one authorization role to a broker.

    Args:
        endpoint: The broker's URL, message VPN, and trust store directory.
        role: The broker authorization role to authenticate as; its own name is the client
            username, so the identity the broker reports is the role that was granted.
        credential: The role's password, which this module never logs.

    Returns:
        The property mapping, with retries disabled in both directions.

    Raises:
        MessagingError: With ``INSECURE_TRANSPORT`` for a URL that is not ``tcps``.
    """
    if not endpoint.url.startswith("tcps://"):
        raise MessagingError(MessagingRefusal.INSECURE_TRANSPORT, endpoint.url)
    return {
        transport.HOST: endpoint.url,
        service_properties.VPN_NAME: endpoint.vpn,
        authentication.SCHEME_BASIC_USER_NAME: role.value,
        authentication.SCHEME_BASIC_PASSWORD: credential,
        transport.CONNECTION_RETRIES: CONNECTION_RETRIES,
        transport.RECONNECTION_ATTEMPTS: RECONNECTION_ATTEMPTS,
    }


def build_service(endpoint: BrokerEndpoint, role: Principal, credential: str) -> MessagingService:
    """Return an unconnected messaging service for one role, with TLS validation left on.

    Certificate validation and hostname checking are never relaxed here; the trust store is
    the per-checkout authority ``docs/adr/0046`` generates. The service is returned
    unconnected, the way ``semp.connect`` returns an unopened connection, so building it can
    be tested without a broker.
    """
    return (
        MessagingService.builder()
        .from_properties(connection_properties(endpoint, role, credential))
        .with_transport_security_strategy(
            TLS.create().with_certificate_validation(
                True, validate_server_name=True, trust_store_file_path=endpoint.trust_store
            )
        )
        .build()
    )


class SolacePublisher:
    """A :class:`MessagePublisher` backed by a guaranteed Solace publisher."""

    def __init__(self, service: MessagingService) -> None:
        """Start a persistent publisher and a message builder on a connected service."""
        self._messages = service.message_builder()
        self._publisher = service.create_persistent_message_publisher_builder().build()
        self._publisher.start()

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Publish one message and wait for the broker to acknowledge it.

        The builder takes a ``bytearray`` or a ``str`` and never ``bytes``, which is what
        the canonical encoder emits, so the conversion is here rather than at every caller.

        Raises:
            MessagingError: With ``PUBLISH_REFUSED`` when the client reports a failure, so
                a caller catches one owned type rather than an untyped upstream one.
        """
        message = self._messages.build(
            bytearray(payload), additional_message_properties=dict(properties)
        )
        try:
            self._publisher.publish_await_acknowledgement(
                message, SolaceTopic.of(topic), PUBLISH_TIMEOUT_MILLISECONDS
            )
        except PubSubPlusClientError as error:
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic) from error

    def close(self) -> None:
        """Terminate the publisher, so shutdown is explicit rather than collected."""
        self._publisher.terminate()


class SolaceReceiver:
    """A :class:`MessageReceiver` backed by a direct Solace receiver.

    Direct rather than guaranteed because no durable queue exists yet
    (``TECH_DEBT.md`` section 6). Receiving is blocking rather than by callback so that
    nothing here subclasses the untyped upstream handler (``docs/adr/0028``).
    """

    def __init__(self, service: MessagingService, subscriptions: Sequence[str]) -> None:
        """Start a direct receiver subscribed to each pattern, on a connected service."""
        self._receiver = (
            service.create_direct_message_receiver_builder()
            .with_subscriptions([TopicSubscription.of(pattern) for pattern in subscriptions])
            .build()
        )
        self._receiver.start()

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""
        received: InboundMessage | None = self._receiver.receive_message(
            timeout=timeout_milliseconds
        )
        return received

    def close(self) -> None:
        """Terminate the receiver, so shutdown is explicit rather than collected."""
        self._receiver.terminate()
