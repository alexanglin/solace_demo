"""The typed façade over the pinned Solace client, and the only place it is called.

``docs/adr/0028-untyped-solace-client-boundary.md`` accepts that `solace-pubsubplus` 1.11.0
ships no type information, so everything it returns is ``Any`` and static analysis of every
call into it is lost. The compensating control it names is this module: a typed surface that
the rest of the tree talks to, so the untyped calls are confined to one file with tests
rather than spread across every service.

The four ports below are what owned code depends on. ``InboundMessage`` is named for the
methods the upstream message object already has, so a real message satisfies it without a
wrapper; the publishers and the receiver are owned classes, because their upstream shapes
are builder chains rather than the operations a caller wants. There are two publisher
ports because there are two delivery guarantees, and ``docs/CONTRACTS.md`` decides which
families get which.

Nothing here decides anything. Which topic an answer may go to, and what an answer says,
belong to the command gateway (``docs/adr/0005-deterministic-command-gateway.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome
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
from solace.messaging.resources.queue import Queue as SolaceQueue
from solace.messaging.resources.topic import Topic as SolaceTopic
from solace.messaging.resources.topic_subscription import TopicSubscription

PUBLISH_TIMEOUT_MILLISECONDS: Final = 10_000
"""Bound on one guaranteed publication; see docs/operating-parameters.md."""

DIRECT_BUFFER_CAPACITY: Final = 0
"""No internal buffer on the direct publisher; see docs/operating-parameters.md.

Zero is the absence of a queue rather than a tuned queue depth, so it needs no
measurement, and it is the same posture as the two retry counts below. Routine telemetry
is droppable under ``docs/CONTRACTS.md``, so refusing a publication when the transport is
full is the honest outcome; buffering it elastically would turn a congested broker into
unbounded process memory.
"""

CONNECTION_RETRIES: Final = 0
RECONNECTION_ATTEMPTS: Final = 0
"""Both zero so a broker that is absent fails the caller rather than retrying forever.

The first ``mesh`` run found the other behaviour: a client refused for a bad credential
retried without ever logging an error, and the failure was visible only in the broker's own
event log (``release-evidence/phase-0/mesh-first-run.md``).
"""


REQUIRED_OUTCOMES: Final = (Outcome.FAILED, Outcome.REJECTED)
"""The negative settlements a consumer must ask for before it may send one.

The client permits ``ACCEPTED`` unconditionally and refuses the other two unless the
receiver was built asking for them, so a consumer that had not asked would discover at the
first poison message that its only options were to accept it or to leave it redelivering.
Both are asked for here, and which one a handler chooses is the handler's decision:
``FAILED`` returns the message for redelivery, ``REJECTED`` sends it to the dead-message
queue at once (``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md``).
"""


class MessagingRefusal(Enum):
    """Why a broker endpoint cannot be used."""

    INSECURE_TRANSPORT = "broker URL does not use a validated TLS transport"
    PUBLISH_REFUSED = "the broker did not acknowledge the publication"
    SETTLE_REFUSED = "the broker did not accept the settlement"
    BIND_REFUSED = "the broker refused the queue binding"


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

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Publish one message and wait for the broker to acknowledge it.

        The parameters are positional-only so an implementation may name them as it likes.
        """


class DirectPublisher(Protocol):
    """Somewhere to send one message that the broker never acknowledges.

    The method is deliberately not named ``publish``. A protocol is satisfied
    structurally, so a direct publisher sharing that name would also satisfy
    :class:`MessagePublisher` and could be passed wherever an acknowledged publication is
    required -- silently downgrading an audit record to a droppable one. The name is the
    control, and it says at every call site which guarantee the caller is getting.
    """

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object], /
    ) -> None:
        """Publish one message without waiting for the broker to acknowledge it.

        The parameters are positional-only so an implementation may name them as it likes.
        """


class MessageReceiver(Protocol):
    """Somewhere one message arrives from."""

    def receive(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""


class AcknowledgingReceiver(MessageReceiver, Protocol):
    """Somewhere one message arrives from that the consumer must settle explicitly.

    A :class:`MessageReceiver` on its own says nothing about settlement, and a direct
    receiver satisfies it structurally, so a caller that must acknowledge what it consumed
    would accept one and silently lose every message it processed. Requiring
    :meth:`settle` is what makes the two impossible to confuse, the same control the two
    publisher ports use.
    """

    def settle(self, message: InboundMessage, outcome: Outcome, /) -> None:
        """Settle one message, so the broker learns the work either finished or did not."""


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


class SolaceDirectPublisher:
    """A :class:`DirectPublisher` backed by a direct Solace publisher.

    ``docs/CONTRACTS.md`` puts routine telemetry on direct delivery, because a current
    position supersedes a stale one. The client takes the payload itself rather than a
    built message, so there is no outbound message builder here and nothing to keep in
    step with the persistent path above.
    """

    def __init__(self, service: MessagingService) -> None:
        """Start a direct publisher that refuses rather than buffers, on a connected service."""
        self._publisher = (
            service.create_direct_message_publisher_builder()
            .on_back_pressure_reject(buffer_capacity=DIRECT_BUFFER_CAPACITY)
            .build()
        )
        self._publisher.start()

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object]
    ) -> None:
        """Publish one message without waiting for the broker to acknowledge it.

        The builder takes a ``bytearray`` or a ``str`` and never ``bytes``, which is what
        the canonical encoder emits, so the conversion is here rather than at every caller.

        Raises:
            MessagingError: With ``PUBLISH_REFUSED`` when the client reports a failure,
                which includes the overflow a full transport raises, so a caller catches
                one owned type rather than an untyped upstream one.
        """
        try:
            self._publisher.publish(bytearray(payload), SolaceTopic.of(topic), dict(properties))
        except PubSubPlusClientError as error:
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic) from error

    def close(self) -> None:
        """Terminate the publisher, so shutdown is explicit rather than collected."""
        self._publisher.terminate()


class SolaceReceiver:
    """A :class:`MessageReceiver` backed by a direct Solace receiver.

    Direct rather than guaranteed because the families it carries are the ones ADR-0079
    calls direct or request-reply; a guaranteed family is consumed from its own durable
    queue by :class:`SolacePersistentReceiver`. Receiving is blocking rather than by
    callback so that nothing here subclasses the untyped upstream handler
    (``docs/adr/0028``).
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


class SolacePersistentReceiver:
    """An :class:`AcknowledgingReceiver` bound to one durable queue.

    Durable and exclusive because that is how the queue was provisioned: a second binding
    is a configuration error rather than a scale-out, and one consumer flow is what keeps a
    producer's sequence order across the endpoint. Receiving is blocking rather than by
    callback so that nothing here subclasses the untyped upstream handler
    (``docs/adr/0028-untyped-solace-client-boundary.md``).

    Nothing is settled automatically. The client offers auto-acknowledgement, which would
    remove a message from the queue as soon as it was handed over and before the consumer
    had committed anything, so the guarantee would end at the socket rather than at the
    durable outcome. Client acknowledgement is asked for here and the caller settles.
    """

    def __init__(self, service: MessagingService, queue: str) -> None:
        """Bind a persistent receiver to ``queue`` on a connected service, and start it.

        Raises:
            MessagingError: With ``BIND_REFUSED`` naming the queue when the broker refuses
                the binding. The queue permits no access to anyone but its named owner, so
                this is the ordinary answer to a role that holds the topic grant and is
                still not the owner, and a caller should see an owned type rather than an
                untyped upstream one.
        """
        self._receiver = (
            service.create_persistent_message_receiver_builder()
            .with_required_message_outcome_support(*REQUIRED_OUTCOMES)
            .with_message_client_acknowledgement()
            .build(SolaceQueue.durable_exclusive_queue(queue))
        )
        try:
            self._receiver.start()
        except PubSubPlusClientError as error:
            raise MessagingError(MessagingRefusal.BIND_REFUSED, queue) from error

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""
        received: InboundMessage | None = self._receiver.receive_message(
            timeout=timeout_milliseconds
        )
        return received

    def settle(self, message: InboundMessage, outcome: Outcome) -> None:
        """Settle one message, so the broker learns the work either finished or did not.

        Raises:
            MessagingError: With ``SETTLE_REFUSED`` when the client reports a failure, so a
                caller catches one owned type rather than an untyped upstream one. An
                unsettled message is redelivered, so the failure is recoverable and must
                not be mistaken for the work being done.
        """
        try:
            self._receiver.settle(message, outcome)
        except PubSubPlusClientError as error:
            raise MessagingError(MessagingRefusal.SETTLE_REFUSED, outcome.name) from error

    def close(self) -> None:
        """Terminate the receiver, so shutdown is explicit rather than collected."""
        self._receiver.terminate()


@dataclass(frozen=True)
class BrokerSession:
    """A connected publisher and receiver, and the one call that shuts both down."""

    publisher: SolacePublisher
    receiver: SolaceReceiver
    _service: MessagingService

    def close(self) -> None:
        """Terminate both endpoints and disconnect, in that order."""
        self.publisher.close()
        self.receiver.close()
        self._service.disconnect()


@dataclass(frozen=True)
class PublishingSession:
    """A connected direct publisher, and the one call that shuts it down.

    Publish-only because a role that consumes nothing should not hold a receiver: an
    unused receiver would be authority the process cannot justify holding. A role that does
    consume its own queues takes :class:`FleetSession` instead.
    """

    publisher: SolaceDirectPublisher
    _service: MessagingService

    def close(self) -> None:
        """Terminate the publisher and disconnect, in that order."""
        self.publisher.close()
        self._service.disconnect()


def open_publishing_session(
    endpoint: BrokerEndpoint, role: Principal, credential: str
) -> PublishingSession:
    """Connect on one role and return a direct publisher that consumes nothing.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as.
        credential: That role's password, which is never logged.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service = build_service(endpoint, role, credential)
    service.connect()
    return PublishingSession(publisher=SolaceDirectPublisher(service), _service=service)


def open_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    subscriptions: Sequence[str],
) -> BrokerSession:
    """Connect on one role and return its publisher and receiver.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as.
        credential: That role's password, which is never logged.
        subscriptions: The patterns the receiver binds, built by
            :mod:`aerial_rescue_broker.subscriptions` and never by hand.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service = build_service(endpoint, role, credential)
    service.connect()
    return BrokerSession(
        publisher=SolacePublisher(service),
        receiver=SolaceReceiver(service, subscriptions),
        _service=service,
    )


@dataclass(frozen=True)
class ConsumingSession:
    """A connected persistent receiver, and the one call that shuts it down.

    Consume-only because a role that binds a queue is not thereby a publisher: the
    recorder holds no publish grant at all, and giving it a publisher would be authority
    the process cannot justify holding
    (``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``).
    """

    receiver: SolacePersistentReceiver
    _service: MessagingService

    def close(self) -> None:
        """Terminate the receiver and disconnect, in that order."""
        self.receiver.close()
        self._service.disconnect()


def open_consuming_session(
    endpoint: BrokerEndpoint, role: Principal, credential: str, queue: str
) -> ConsumingSession:
    """Connect on one role and return a receiver bound to one durable queue.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as, which must be the queue's owner:
            the queue permits no access to anyone else.
        credential: That role's password, which is never logged.
        queue: The queue name, built by :mod:`aerial_rescue_broker.queues` and never by
            hand.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service = build_service(endpoint, role, credential)
    service.connect()
    return ConsumingSession(receiver=SolacePersistentReceiver(service, queue), _service=service)


@dataclass(frozen=True)
class FleetSession:
    """Two publishers and one queue-bound receiver per drone, on one connection.

    One connection rather than one per queue. ``MAX_BIND_COUNT`` and the exclusive access
    type of ``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md`` bound
    the flows on a queue, not the services in a process, so every receiver here can share a
    service and each queue still has exactly one flow. The reference fleet is 23 drones, and
    a session per drone would spend 25 connections against a message VPN that permits 100.

    The two publishers stay distinct types rather than one: routine telemetry is direct and
    supersedable while a command result is guaranteed, and a caller that held one port for
    both could downgrade a result to droppable delivery without the type system noticing.
    """

    telemetry: SolaceDirectPublisher
    results: SolacePublisher
    receivers: Mapping[str, SolacePersistentReceiver]
    _service: MessagingService

    def close(self) -> None:
        """Release every receiver, then both publishers, then disconnect, in that order.

        Receivers first because disconnecting under a receiver strands whatever it has
        taken and not yet settled; the broker redelivers it, but only after the flow times
        out rather than at once.
        """
        for key in sorted(self.receivers):
            self.receivers[key].close()
        self.telemetry.close()
        self.results.close()
        self._service.disconnect()


def fleet_session(service: MessagingService, queues: Mapping[str, str]) -> FleetSession:
    """Compose the publishers and one receiver per queue on an already connected service.

    Separated from :func:`open_fleet_session` so that the refusal path below is provable
    without a broker: a fleet is built one binding at a time, and a refusal partway through
    must not leave the earlier bindings and the connection behind.

    Args:
        service: A connected messaging service, which this function takes ownership of: on
            a refused binding it disconnects the service before re-raising.
        queues: Consumer key to queue name, the names built by
            :mod:`aerial_rescue_broker.queues` and never by hand. Bound in key order, so
            two runs of one fleet bind in the same order.

    Raises:
        MessagingError: With ``BIND_REFUSED`` naming the first queue the broker would not
            give, after everything already opened has been released.
    """
    telemetry = SolaceDirectPublisher(service)
    results = SolacePublisher(service)
    receivers: dict[str, SolacePersistentReceiver] = {}
    try:
        for key in sorted(queues):
            receivers[key] = SolacePersistentReceiver(service, queues[key])
    except MessagingError:
        for opened in receivers.values():
            opened.close()
        telemetry.close()
        results.close()
        service.disconnect()
        raise
    return FleetSession(telemetry=telemetry, results=results, receivers=receivers, _service=service)


def open_fleet_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    queues: Mapping[str, str],
) -> FleetSession:
    """Connect on one role and return its publishers and one receiver per named queue.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as, which must own every queue: each
            permits no access to anyone but its named owner.
        credential: That role's password, which is never logged.
        queues: Consumer key to queue name, built by :mod:`aerial_rescue_broker.queues`.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service = build_service(endpoint, role, credential)
    service.connect()
    return fleet_session(service, queues)
