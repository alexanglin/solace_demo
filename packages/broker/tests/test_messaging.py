"""The typed façade over the pinned Solace client.

ADR-0028 accepts that every call into this client is invisible to static analysis, and
names this adapter and its tests as the only compensating control. So the tests below are
the type check: they assert what is passed, in what order, and with what conversion, using
fakes in place of the client's builder chain. Nothing here opens a socket.

The one exception is `build_service`, which builds a real messaging service without
connecting it, the way `semp.connect` returns an unopened connection. The client requires
the trust-store path to exist when it builds the session, so that test supplies a temporary
directory rather than `deploy/certs`: the per-checkout authority of ADR-0046 is generated
and untracked, and a test in the blocking suite cannot depend on it. An empty directory is
enough, because the certificates are read when the session connects and this one never does.
"""

from __future__ import annotations

import hashlib
import unittest
from collections.abc import Collection, Mapping, Sequence
from tempfile import TemporaryDirectory
from typing import Final, cast, override
from unittest.mock import patch

import pytest
from aerial_rescue_broker.messaging import (
    APPLICATION_DESCRIPTION,
    CONNECTION_ATTEMPTS_TIMEOUT_MILLISECONDS,
    CONNECTION_RETRIES,
    CONNECTION_RETRIES_PER_HOST,
    DIRECT_BUFFER_CAPACITY,
    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    DIRECT_TELEMETRY_RECEIVER_CAPACITY,
    KEEP_ALIVE_INTERVAL_MILLISECONDS,
    KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT,
    PERSISTENT_BUFFER_CAPACITY,
    PUBLISH_TIMEOUT_MILLISECONDS,
    RECONNECTION_ATTEMPTS,
    RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS,
    REQUIRED_OUTCOMES,
    SHUTDOWN_GRACE_PERIOD_MILLISECONDS,
    BrokerEndpoint,
    BrokerLifecycle,
    BrokerLifecycleState,
    BrokerSession,
    CommandGatewayBindings,
    CommandGatewaySession,
    ConsumingSession,
    DashboardBindings,
    DashboardSession,
    GuaranteedMessage,
    InvalidDirectMessageError,
    MessageSettlement,
    MessagingError,
    MessagingRefusal,
    PublishingSession,
    ReceiverOnlyBindings,
    ReceiverOnlySession,
    RequestingSession,
    SolaceDirectPublisher,
    SolacePersistentReceiver,
    SolacePublisher,
    SolaceReceiver,
    SolaceRequestReplyRequester,
    UnsettledMessageError,
    build_service,
    command_gateway_session,
    connection_properties,
    dashboard_session,
    fleet_session,
    install_lifecycle_listeners,
    open_command_gateway_session,
    open_consuming_session,
    open_dashboard_session,
    open_fleet_session,
    open_publishing_session,
    open_receiver_only_session,
    open_requesting_session,
    open_session,
    receiver_only_session,
    transport_security_strategy,
)
from aerial_rescue_broker.tracing import NativeTraceError, NativeTraceRefusal
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome
from solace.messaging.config.receiver_activation_passivation_configuration import (
    ReceiverState,
    ReceiverStateChangeListener,
)
from solace.messaging.config.solace_properties import (
    authentication_properties as authentication,
)
from solace.messaging.config.solace_properties import (
    client_properties,
    message_properties,
)
from solace.messaging.config.solace_properties import (
    transport_layer_properties as transport,
)
from solace.messaging.config.solace_properties.transport_layer_security_properties import (
    CERT_REJECT_EXPIRED,
    CERT_VALIDATE_SERVERNAME,
    MINIMUM_PROTOCOL,
    TRUST_STORE_PATH,
)
from solace.messaging.errors.pubsubplus_client_error import (
    MessageRejectedByBrokerError,
    PublisherOverflowError,
    PubSubPlusClientError,
    PubSubPlusClientIOError,
    PubSubTimeoutError,
)
from solace.messaging.messaging_service import (
    ReconnectionAttemptListener,
    ReconnectionListener,
    ServiceInterruptionListener,
)
from solace.messaging.publisher.publisher_health_check import PublisherReadinessListener
from solace.messaging.resources.queue import Queue as SolaceQueue
from solace.messaging.resources.topic import Topic as SolaceTopic
from solace.messaging.resources.topic_subscription import TopicSubscription
from solace.messaging.utils.life_cycle_control import (
    TerminationEvent,
    TerminationNotificationListener,
)
from solace.messaging.utils.manageable import Metric

ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn="default", trust_store="deploy/certs"
)
CREDENTIAL: Final = "not-a-real-credential"
CLIENT_FAILURE: Final = "the broker refused the publication"
QUEUE: Final = "aerial-rescue/v1/recorder/audit"
EARLIER_QUEUE: Final = "aerial-rescue/v1/drone/drone-thermal-02/command"
LATER_QUEUE: Final = "aerial-rescue/v1/drone/drone-vision-01/command"
FLEET_QUEUES: Final = {
    "drone-vision-01": LATER_QUEUE,
    "drone-thermal-02": EARLIER_QUEUE,
}
"""Two drones, deliberately not in ascending insertion order, so key order is asserted."""

DIRECT_RECEIVER_CAPACITY: Final = 7
"""An injected test value; production compositions own their direct-receiver bound."""

NativeContext = tuple[bytearray | None, bytearray | None, bool | None, str | None]


class FakeMessages:
    """The client's outbound message builder, recording what it was asked to build."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.built: list[tuple[object, Mapping[str, object]]] = []

    def build(self, payload: object, additional_message_properties: Mapping[str, object]) -> object:
        """Record one build and return a stand-in for the message."""
        self.built.append((payload, additional_message_properties))
        return ("message", payload)


class FakeTraceContext:
    """Record native context operations and optionally refuse one direction."""

    def __init__(
        self,
        *,
        outbound_refusal: NativeTraceRefusal | None = None,
        inbound_refusal: NativeTraceRefusal | None = None,
    ) -> None:
        """Retain the scripted safe refusal without any raw header value."""
        self.outbound: list[tuple[object, bytes]] = []
        self.inbound: list[tuple[object, bytes]] = []
        self._outbound_refusal = outbound_refusal
        self._inbound_refusal = inbound_refusal

    def inject_outbound(self, message: object, payload: bytes) -> None:
        """Record injection before optionally refusing the native context."""
        self.outbound.append((message, payload))
        if self._outbound_refusal is not None:
            raise NativeTraceError(self._outbound_refusal)

    def validate_inbound(self, message: object, payload: bytes) -> None:
        """Record validation before optionally refusing the native context."""
        self.inbound.append((message, payload))
        if self._inbound_refusal is not None:
            raise NativeTraceError(self._inbound_refusal)


class _FakePublisherLifecycle:
    """Lifecycle recorder shared by the three pinned publisher shapes."""

    def __init__(
        self,
        *,
        notify_failing: bool,
        terminate_failing: bool,
        order: list[str] | None = None,
    ) -> None:
        """Record the common lifecycle script and optional shutdown-order log."""
        self.started = 0
        self.terminated: list[int] = []
        self.readiness_listener: object | None = None
        self.termination_listener: object | None = None
        self.readiness_notifications = 0
        self._notify_failing = notify_failing
        self._terminate_failing = terminate_failing
        self._order = order

    @property
    def ready_notifications(self) -> int:
        """Return the persistent-publisher spelling of the shared counter."""
        return self.readiness_notifications

    def start(self) -> None:
        """Record that the publisher was started."""
        self.started += 1

    def set_publisher_readiness_listener(self, listener: object) -> None:
        """Record the listener for publisher capacity recovery."""
        self.readiness_listener = listener

    def set_termination_notification_listener(self, listener: object) -> None:
        """Record the listener for independently terminated publication."""
        self.termination_listener = listener

    def is_ready(self) -> bool:
        """Report the started fake as ready."""
        return self.started > 0

    def notify_when_ready(self) -> None:
        """Record one requested callback after capacity becomes available."""
        self.readiness_notifications += 1
        if self._notify_failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)

    def refuse_ready_notification(self) -> None:
        """Make the next readiness-notification request fail."""
        self._notify_failing = True

    def terminate(self, grace_period: int) -> None:
        """Record bounded termination and its optional shared shutdown order."""
        self.terminated.append(grace_period)
        if self._order is not None:
            self._order.append("terminate")
        if self._terminate_failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)


class FakePublisher(_FakePublisherLifecycle):
    """The client's persistent publisher, recording its lifecycle and publications."""

    def __init__(
        self,
        failure: type[PubSubPlusClientError] | None = None,
        *,
        notify_failing: bool = False,
        terminate_failing: bool = False,
    ) -> None:
        """Record the client failure type this publisher reports, if any."""
        super().__init__(
            notify_failing=notify_failing,
            terminate_failing=terminate_failing,
        )
        self.published: list[tuple[object, object, int]] = []
        self._failure = failure

    def publish_await_acknowledgement(
        self, message: object, destination: object, time_out: int
    ) -> None:
        """Record one publication, or raise the way the client does."""
        if self._failure is not None:
            raise self._failure(CLIENT_FAILURE)
        self.published.append((message, destination, time_out))


class FakePersistentPublisherBuilder:
    """The persistent publisher builder, recording its bounded rejection policy."""

    def __init__(self, publisher: FakePublisher) -> None:
        """Record which publisher this builder yields."""
        self.capacities: list[int] = []
        self._publisher = publisher

    def on_back_pressure_reject(self, buffer_capacity: int) -> FakePersistentPublisherBuilder:
        """Record the capacity and return self, the way the client's builder does."""
        self.capacities.append(buffer_capacity)
        return self

    def build(self) -> FakePublisher:
        """Return the publisher."""
        return self._publisher


class _FakeReceiverLifecycle:
    """Receive, termination-listener, and shutdown recording shared by receiver fakes."""

    def __init__(
        self,
        scripted: Sequence[object],
        *,
        terminate_failing: bool,
        order: list[str] | None = None,
    ) -> None:
        """Record the delivery script and optional shared shutdown-order log."""
        self.terminated: list[int] = []
        self.timeouts: list[int] = []
        self.termination_listener: object | None = None
        self._scripted = list(scripted)
        self._terminate_failing = terminate_failing
        self._order = order

    def receive_message(self, timeout: int) -> object:
        """Return the next scripted message, or ``None`` when the script is exhausted."""
        self.timeouts.append(timeout)
        return self._scripted.pop(0) if self._scripted else None

    def set_termination_notification_listener(self, listener: object) -> None:
        """Record the listener for an independently terminated receiver flow."""
        self.termination_listener = listener

    def terminate(self, grace_period: int) -> None:
        """Record bounded termination and its optional shared shutdown order."""
        self.terminated.append(grace_period)
        if self._order is not None:
            self._order.append("receiver-terminate")
        if self._terminate_failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)


class FakeReceiver(_FakeReceiverLifecycle):
    """The client's direct receiver, answering from a scripted list."""

    def __init__(
        self,
        scripted: Sequence[object],
        *,
        start_failing: bool = False,
        terminate_failing: bool = False,
    ) -> None:
        """Record what this receiver will yield, in order."""
        super().__init__(scripted, terminate_failing=terminate_failing)
        self.started = 0
        self._start_failing = start_failing

    def start(self) -> None:
        """Record that the receiver was started."""
        self.started += 1
        if self._start_failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)


class FakeReceiverBuilder:
    """The client's receiver builder, recording the subscriptions it was given."""

    def __init__(self, receiver: FakeReceiver) -> None:
        """Record which receiver this builder yields."""
        self.subscriptions: list[object] = []
        self.capacities: list[int] = []
        self._receiver = receiver

    def on_back_pressure_drop_oldest(self, buffer_capacity: int) -> FakeReceiverBuilder:
        """Record the bounded freshest-value policy and return self."""
        self.capacities.append(buffer_capacity)
        return self

    def with_subscriptions(self, subscriptions: Sequence[object]) -> FakeReceiverBuilder:
        """Record the subscriptions and return self, the way the client's builder does."""
        self.subscriptions.extend(subscriptions)
        return self

    def build(self) -> FakeReceiver:
        """Return the receiver."""
        return self._receiver


class FakeDirectPublisher(_FakePublisherLifecycle):
    """The client's direct publisher, recording its lifecycle and publications."""

    def __init__(
        self,
        order: list[str],
        failure: type[PubSubPlusClientError] | None = None,
        *,
        notify_failing: bool = False,
        terminate_failing: bool = False,
    ) -> None:
        """Record the shared order log and whether this publisher reports a failure."""
        super().__init__(
            notify_failing=notify_failing,
            terminate_failing=terminate_failing,
            order=order,
        )
        self.published: list[tuple[object, SolaceTopic, Mapping[str, object] | None]] = []
        self._failure = failure

    def publish(
        self,
        message: object,
        destination: SolaceTopic,
        additional_message_properties: Mapping[str, object] | None = None,
    ) -> None:
        """Record one publication, or raise the way the client does."""
        if self._failure is not None:
            raise self._failure(CLIENT_FAILURE)
        self.published.append((message, destination, additional_message_properties))


class FakeDirectPublisherBuilder:
    """The client's direct publisher builder, recording the back-pressure it was given."""

    def __init__(self, publisher: FakeDirectPublisher) -> None:
        """Record which publisher this builder yields."""
        self.capacities: list[int] = []
        self._publisher = publisher

    def on_back_pressure_reject(self, buffer_capacity: int) -> FakeDirectPublisherBuilder:
        """Record the capacity and return self, the way the client's builder does."""
        self.capacities.append(buffer_capacity)
        return self

    def build(self) -> FakeDirectPublisher:
        """Return the publisher."""
        return self._publisher


class FakeRequestReplyPublisher(_FakePublisherLifecycle):
    """The official request/reply publisher, returning one correlated response."""

    def __init__(
        self,
        response: object,
        failure: type[PubSubPlusClientError] | None = None,
        *,
        notify_failing: bool = False,
        terminate_failing: bool = False,
    ) -> None:
        """Record the response or typed client failure this publisher yields."""
        super().__init__(
            notify_failing=notify_failing,
            terminate_failing=terminate_failing,
        )
        self.requests: list[tuple[object, SolaceTopic, int]] = []
        self._response = response
        self._failure = failure

    def publish_await_response(
        self, message: object, destination: SolaceTopic, reply_timeout: int
    ) -> object:
        """Return the correlated response or raise the scripted failure."""
        if self._failure is not None:
            raise self._failure(CLIENT_FAILURE)
        self.requests.append((message, destination, reply_timeout))
        return self._response


class FakeRequestReplyPublisherBuilder:
    """The official request/reply publisher builder."""

    def __init__(self, publisher: FakeRequestReplyPublisher) -> None:
        """Record which publisher is built."""
        self._publisher = publisher

    def build(self) -> FakeRequestReplyPublisher:
        """Return the request/reply publisher."""
        return self._publisher


class FakeRequestReplyService:
    """The request/reply view of one messaging service."""

    def __init__(self, publisher: FakeRequestReplyPublisher) -> None:
        """Build around one scripted publisher."""
        self.publisher_builder = FakeRequestReplyPublisherBuilder(publisher)

    def create_request_reply_message_publisher_builder(
        self,
    ) -> FakeRequestReplyPublisherBuilder:
        """Return the official request/reply publisher builder."""
        return self.publisher_builder


class FakeMessage:
    """One inbound message, carrying the members the ``InboundMessage`` port names."""

    def __init__(self, payload: bytes = b"{}") -> None:
        """Record the payload this message reports."""
        self._payload = payload

    def get_payload_as_bytes(self) -> bytes | None:
        """Return the payload."""
        return self._payload

    def get_destination_name(self) -> str | None:
        """Return the topic the message arrived on."""
        return "aerial-rescue/v1/m-0001/audit/approval"

    def get_properties(self) -> Mapping[str, object]:
        """Return the user properties the producer set."""
        return {}

    def get_creation_trace_context(self) -> NativeContext:
        """Report that this ordinary non-envelope fake has no creation context."""
        return (None, None, None, None)

    def get_transport_trace_context(self) -> NativeContext:
        """Report that this ordinary non-envelope fake has no transport context."""
        return (None, None, None, None)


class _PayloadlessMessage(FakeMessage):
    """One malformed inbound message whose body is absent at the trust boundary."""

    @override
    def get_payload_as_bytes(self) -> None:
        """Return the absent body exactly as the SDK permits."""


class FakePersistentReceiver(_FakeReceiverLifecycle):
    """The client's persistent receiver, recording every settlement it was asked for."""

    def __init__(
        self,
        scripted: Sequence[object],
        failing: bool = False,
        unbindable: bool = False,
        terminate_failing: bool = False,
        order: list[str] | None = None,
    ) -> None:
        """Record what this receiver yields, and whether binding or settling refuses."""
        super().__init__(
            scripted,
            terminate_failing=terminate_failing,
            order=order,
        )
        self.started = 0
        self.settled: list[tuple[object, Outcome]] = []
        self.state_change_listener: object | None = None
        self._failing = failing
        self._unbindable = unbindable

    def start(self) -> None:
        """Record the start, or raise the way the client does for a refused binding."""
        if self._unbindable:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        self.started += 1
        if self.state_change_listener is not None:
            cast(ReceiverStateChangeListener, self.state_change_listener).on_change(
                ReceiverState.PASSIVE,
                ReceiverState.ACTIVE,
                0.0,
            )

    def settle(self, message: object, outcome: Outcome) -> None:
        """Record one settlement, or raise the way the client does when it cannot send one."""
        if self._failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        self.settled.append((message, outcome))


class FakePersistentReceiverBuilder:
    """The client's persistent receiver builder, recording what it was configured with."""

    def __init__(
        self,
        receiver: FakePersistentReceiver,
        order: list[str] | None = None,
        unbindable: Collection[str] = (),
    ) -> None:
        """Record which receiver this builder yields, and which queue names refuse."""
        self.outcomes: tuple[Outcome, ...] = ()
        self.client_acknowledgement = 0
        self.endpoints: list[SolaceQueue] = []
        self.built: list[FakePersistentReceiver] = []
        self.activation_listeners: list[object] = []
        self._receiver = receiver
        self._order = order
        self._unbindable = frozenset(unbindable)
        self._activation_listener: object | None = None

    def with_activation_passivation_support(
        self, listener: object
    ) -> FakePersistentReceiverBuilder:
        """Record the receiver-flow state listener passed to the pinned SDK."""
        self._activation_listener = listener
        return self

    def with_required_message_outcome_support(
        self, *outcomes: Outcome
    ) -> FakePersistentReceiverBuilder:
        """Record the negative outcomes the receiver must be able to send."""
        self.outcomes = outcomes
        return self

    def with_message_client_acknowledgement(self) -> FakePersistentReceiverBuilder:
        """Record that the caller settles each message rather than the client doing it."""
        self.client_acknowledgement += 1
        return self

    def build(self, endpoint_to_consume_from: SolaceQueue) -> FakePersistentReceiver:
        """Record the queue and return its receiver, minting one per queue for a fleet.

        A builder given no order and no refusing queue yields the one receiver it was
        constructed with, which is what a single-queue session needs.
        """
        self.endpoints.append(endpoint_to_consume_from)
        name = endpoint_to_consume_from.get_name()
        if self._order is None and not self._unbindable:
            self._receiver.state_change_listener = self._activation_listener
            if self._activation_listener is not None:
                self.activation_listeners.append(self._activation_listener)
            self.built.append(self._receiver)
            return self._receiver
        minted = FakePersistentReceiver((), unbindable=name in self._unbindable, order=self._order)
        minted.state_change_listener = self._activation_listener
        if self._activation_listener is not None:
            self.activation_listeners.append(self._activation_listener)
        self.built.append(minted)
        return minted


class FakeMetrics:
    """Return scripted aggregate SDK metric values."""

    def __init__(self, discarded: Sequence[int], *, failing: bool = False) -> None:
        """Retain at least one backpressure-discard counter value."""
        self._discarded = list(discarded)
        self._last = self._discarded[0]
        self._failing = failing
        self.read: list[Metric] = []

    def get_value(self, metric: Metric) -> int:
        """Return the next counter value, then retain the last value."""
        self.read.append(metric)
        if self._failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        if self._discarded:
            self._last = self._discarded.pop(0)
        return self._last


class FakeService:
    """Enough of the client's messaging service for the adapter to be exercised."""

    def __init__(
        self,
        publisher: FakePublisher | None = None,
        receiver: FakeReceiver | None = None,
        direct_failure: type[PubSubPlusClientError] | None = None,
        direct_terminate_failing: bool = False,
        request_reply_publisher: FakeRequestReplyPublisher | None = None,
    ) -> None:
        """Record the publisher and receiver this service hands out."""
        self.messages = FakeMessages()
        self.order: list[str] = []
        self.connected = 0
        self.publisher = publisher or FakePublisher()
        self.publisher_builder = FakePersistentPublisherBuilder(self.publisher)
        self.receiver = receiver or FakeReceiver(())
        self.receiver_builder = FakeReceiverBuilder(self.receiver)
        self.direct_publisher = FakeDirectPublisher(
            self.order,
            failure=direct_failure,
            terminate_failing=direct_terminate_failing,
        )
        self.direct_publisher_builder = FakeDirectPublisherBuilder(self.direct_publisher)
        self.persistent_receiver = FakePersistentReceiver(())
        self.persistent_receiver_builder = FakePersistentReceiverBuilder(self.persistent_receiver)
        self.request_reply_publisher = request_reply_publisher or FakeRequestReplyPublisher(
            FakeMessage()
        )
        self.request_reply_service = FakeRequestReplyService(self.request_reply_publisher)
        self.reconnection_attempt_listeners: list[object] = []
        self.reconnection_listeners: list[object] = []
        self.interruption_listeners: list[object] = []
        self.api_metrics = FakeMetrics((0,))

    def message_builder(self) -> FakeMessages:
        """Return the outbound message builder."""
        return self.messages

    def create_persistent_message_publisher_builder(self) -> FakePersistentPublisherBuilder:
        """Return a builder for the persistent publisher."""
        return self.publisher_builder

    def create_direct_message_publisher_builder(self) -> FakeDirectPublisherBuilder:
        """Return a builder for the direct publisher."""
        return self.direct_publisher_builder

    def create_direct_message_receiver_builder(self) -> FakeReceiverBuilder:
        """Return a builder for the direct receiver."""
        return self.receiver_builder

    def create_persistent_message_receiver_builder(self) -> FakePersistentReceiverBuilder:
        """Return a builder for the persistent receiver."""
        return self.persistent_receiver_builder

    def request_reply(self) -> FakeRequestReplyService:
        """Return the request/reply builder view of this service."""
        return self.request_reply_service

    def metrics(self) -> FakeMetrics:
        """Return the scripted SDK aggregate metrics view."""
        return self.api_metrics

    def disconnect(self) -> None:
        """Record that the service was disconnected, and when."""
        self.order.append("disconnect")

    def connect(self) -> None:
        """Record an initial connection for composition tests."""
        self.connected += 1

    def add_reconnection_attempt_listener(self, listener: object) -> FakeService:
        """Record the listener for loss of an active transport."""
        self.reconnection_attempt_listeners.append(listener)
        return self

    def add_reconnection_listener(self, listener: object) -> FakeService:
        """Record the listener for a re-established transport."""
        self.reconnection_listeners.append(listener)
        return self

    def add_service_interruption_listener(self, listener: object) -> None:
        """Record the listener for exhausted recovery."""
        self.interruption_listeners.append(listener)


class FakeServiceEvent:
    """A lifecycle event whose timestamp is usable and whose vendor prose stays ignored."""

    def __init__(self, time_stamp: float = 0.0) -> None:
        """Retain the deterministic SDK event instant in epoch seconds."""
        self._time_stamp = time_stamp

    def get_time_stamp(self) -> float:
        """Return a deterministic instant."""
        return self._time_stamp

    def get_message(self) -> str:
        """Return a value that the lifecycle adapter deliberately ignores."""
        return "not for diagnostics"

    def get_cause(self) -> PubSubPlusClientError:
        """Return a typed cause that the lifecycle adapter deliberately ignores."""
        return PubSubPlusClientError("redacted")

    def get_broker_uri(self) -> str:
        """Return a non-secret local URI."""
        return ENDPOINT.url


def _active_persistent_lifecycle() -> tuple[
    FakeService,
    BrokerLifecycle,
    ReceiverStateChangeListener,
    ReconnectionAttemptListener,
    ReconnectionListener,
]:
    """Return one ready durable flow and its deterministic SDK callbacks."""
    service = FakeService()
    lifecycle = BrokerLifecycle()
    install_lifecycle_listeners(service, lifecycle)
    lifecycle.connected()
    SolacePersistentReceiver(service, QUEUE, lifecycle=lifecycle)
    lifecycle.mark_ready()
    state = cast(
        ReceiverStateChangeListener,
        service.persistent_receiver_builder.activation_listeners[0],
    )
    reconnecting = cast(ReconnectionAttemptListener, service.reconnection_attempt_listeners[0])
    reconnected = cast(ReconnectionListener, service.reconnection_listeners[0])
    return service, lifecycle, state, reconnecting, reconnected


class ConnectionPropertyTests(unittest.TestCase):
    def test_the_role_name_is_the_client_username_the_broker_authenticates(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        properties = connection_properties(ENDPOINT, role, CREDENTIAL)

        # Assert
        self.assertEqual(
            ("command-gateway", CREDENTIAL, "tcps://localhost:55443"),
            (
                properties[authentication.SCHEME_BASIC_USER_NAME],
                properties[authentication.SCHEME_BASIC_PASSWORD],
                properties[transport.HOST],
            ),
        )

    def test_connection_and_reconnection_budgets_match_the_recovery_decision(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        properties = connection_properties(ENDPOINT, role, CREDENTIAL)

        # Assert
        self.assertEqual(
            (
                CONNECTION_ATTEMPTS_TIMEOUT_MILLISECONDS,
                CONNECTION_RETRIES,
                CONNECTION_RETRIES_PER_HOST,
                RECONNECTION_ATTEMPTS,
                RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS,
            ),
            (
                properties[transport.CONNECTION_ATTEMPTS_TIMEOUT],
                properties[transport.CONNECTION_RETRIES],
                properties[transport.CONNECTION_RETRIES_PER_HOST],
                properties[transport.RECONNECTION_ATTEMPTS],
                properties[transport.RECONNECTION_ATTEMPTS_WAIT_INTERVAL],
            ),
        )

    def test_client_identity_description_and_keepalives_are_explicit_and_stable(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        first = connection_properties(ENDPOINT, role, CREDENTIAL)
        second = connection_properties(ENDPOINT, role, CREDENTIAL)

        # Assert
        expected = (
            "aerial-rescue-command-gateway",
            APPLICATION_DESCRIPTION,
            KEEP_ALIVE_INTERVAL_MILLISECONDS,
            KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT,
        )
        self.assertEqual(
            (expected, expected),
            (
                (
                    first[client_properties.NAME],
                    first[client_properties.APPLICATION_DESCRIPTION],
                    first[transport.KEEP_ALIVE_INTERVAL],
                    first[transport.KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT],
                ),
                (
                    second[client_properties.NAME],
                    second[client_properties.APPLICATION_DESCRIPTION],
                    second[transport.KEEP_ALIVE_INTERVAL],
                    second[transport.KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT],
                ),
            ),
        )

    def test_a_transport_that_is_not_validated_tls_is_refused(self) -> None:
        # Arrange
        urls = ("tcp://localhost:55555", "ws://localhost:8008", "localhost:55443", "")

        # Act
        refusals = tuple(
            _endpoint_refusal(BrokerEndpoint(url=url, vpn="default", trust_store="x"))
            for url in urls
        )

        # Assert
        self.assertEqual(
            tuple((MessagingRefusal.INSECURE_TRANSPORT, url) for url in urls), refusals
        )


def _endpoint_refusal(endpoint: BrokerEndpoint) -> tuple[MessagingRefusal, object]:
    """Return the refusal building properties raises, failing the test if accepted."""
    try:
        connection_properties(endpoint, Principal.COMMAND_GATEWAY, CREDENTIAL)
    except MessagingError as error:
        return (error.refusal, error.value)
    message = f"accepted: {endpoint!r}"
    raise AssertionError(message)


class BuildServiceTests(unittest.TestCase):
    def test_tls_rejects_expired_or_wrong_host_certificates_and_requires_tls_1_3(self) -> None:
        # Arrange
        endpoint = BrokerEndpoint(
            url=ENDPOINT.url, vpn=ENDPOINT.vpn, trust_store="/trusted/project-authority"
        )

        # Act
        strategy = transport_security_strategy(endpoint)
        configuration = strategy.security_configuration

        # Assert
        self.assertEqual(
            (True, True, "TLSv1.3", endpoint.trust_store),
            (
                configuration[CERT_REJECT_EXPIRED],
                configuration[CERT_VALIDATE_SERVERNAME],
                configuration[MINIMUM_PROTOCOL],
                configuration[TRUST_STORE_PATH],
            ),
        )

    def test_a_service_is_built_without_connecting_to_anything(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY
        endpoint = BrokerEndpoint(
            url=ENDPOINT.url, vpn=ENDPOINT.vpn, trust_store=self.enterContext(TemporaryDirectory())
        )

        # Act
        service = build_service(endpoint, role, CREDENTIAL)

        # Assert
        self.assertTrue(hasattr(service, "connect"))

    def test_an_insecure_endpoint_is_refused_before_a_service_exists(self) -> None:
        # Arrange
        endpoint = BrokerEndpoint(url="tcp://localhost:55555", vpn="default", trust_store="x")

        # Act
        with pytest.raises(MessagingError) as captured:
            build_service(endpoint, Principal.COMMAND_GATEWAY, CREDENTIAL)

        # Assert
        self.assertEqual(MessagingRefusal.INSECURE_TRANSPORT, captured.value.refusal)


class BrokerLifecycleTests(unittest.TestCase):
    def test_disconnect_reconnect_and_exhaustion_remain_unready_until_recovery_finishes(
        self,
    ) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        install_lifecycle_listeners(service, lifecycle)
        lifecycle.connected()
        lifecycle.mark_ready()
        event = FakeServiceEvent()
        reconnecting = cast(ReconnectionAttemptListener, service.reconnection_attempt_listeners[0])
        reconnected = cast(ReconnectionListener, service.reconnection_listeners[0])
        interrupted = cast(ServiceInterruptionListener, service.interruption_listeners[0])

        # Act
        ready_before = lifecycle.is_ready()
        reconnecting.on_reconnecting(event)
        recovering = (lifecycle.state, lifecycle.is_ready())
        reconnected.on_reconnected(event)
        reconnected_before_drain = (lifecycle.state, lifecycle.is_ready())
        lifecycle.mark_ready()
        recovered = (lifecycle.state, lifecycle.is_ready())
        interrupted.on_service_interrupted(event)
        lifecycle.mark_ready()
        lifecycle.connected()
        lifecycle.publisher_blocked()
        lifecycle.publisher_available()
        lifecycle.reconnecting()
        lifecycle.reconnected()

        # Assert
        self.assertEqual(
            (
                True,
                (BrokerLifecycleState.RECOVERING, False),
                (BrokerLifecycleState.RECOVERY_PENDING, False),
                (BrokerLifecycleState.CONNECTED, True),
                (BrokerLifecycleState.EXHAUSTED, False, True),
            ),
            (
                ready_before,
                recovering,
                reconnected_before_drain,
                recovered,
                (lifecycle.state, lifecycle.is_ready(), lifecycle.is_terminal()),
            ),
        )

    def test_reconnect_waits_for_every_registered_durable_flow_in_either_callback_order(
        self,
    ) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.receiver_registered("commands")
        lifecycle.receiver_registered("approvals")
        lifecycle.receiver_active("commands")
        lifecycle.receiver_active("approvals")
        lifecycle.mark_ready()

        # Act
        initially_ready = lifecycle.is_ready()
        lifecycle.reconnecting()
        lifecycle.reconnected()
        lifecycle.mark_ready()
        lifecycle.receiver_active("commands")
        one_flow_active = (lifecycle.state, lifecycle.is_ready())
        lifecycle.receiver_active("approvals")
        application_first = (lifecycle.state, lifecycle.is_ready())
        lifecycle.reconnecting()
        lifecycle.reconnected()
        lifecycle.receiver_active("commands")
        lifecycle.receiver_active("approvals")
        flows_first = (lifecycle.state, lifecycle.is_ready())
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (
                True,
                (BrokerLifecycleState.RECOVERY_PENDING, False),
                (BrokerLifecycleState.CONNECTED, True),
                (BrokerLifecycleState.RECOVERY_PENDING, False),
                (BrokerLifecycleState.CONNECTED, True),
            ),
            (
                initially_ready,
                one_flow_active,
                application_first,
                flows_first,
                (lifecycle.state, lifecycle.is_ready()),
            ),
        )

    def test_fresh_flow_activation_survives_delayed_transport_callbacks(self) -> None:
        # Arrange
        _service, lifecycle, state_listener, reconnecting, reconnected = (
            _active_persistent_lifecycle()
        )

        # Act
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 1_500.0)
        reconnecting.on_reconnecting(FakeServiceEvent(1.0))
        reconnected.on_reconnected(FakeServiceEvent(1.25))
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (BrokerLifecycleState.CONNECTED, True),
            (lifecycle.state, lifecycle.is_ready()),
        )

    def test_service_and_flow_callbacks_share_integer_millisecond_precision(self) -> None:
        # Arrange
        _service, lifecycle, state_listener, reconnecting, reconnected = (
            _active_persistent_lifecycle()
        )

        # Act
        reconnecting.on_reconnecting(FakeServiceEvent(2.0009))
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 2_000.0)
        reconnected.on_reconnected(FakeServiceEvent(2.001))
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (BrokerLifecycleState.CONNECTED, True),
            (lifecycle.state, lifecycle.is_ready()),
        )

    def test_stale_flow_activation_cannot_satisfy_a_new_transport_epoch(self) -> None:
        # Arrange
        _service, lifecycle, state_listener, reconnecting, reconnected = (
            _active_persistent_lifecycle()
        )

        # Act
        reconnecting.on_reconnecting(FakeServiceEvent(2.0))
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 1_500.0)
        reconnected.on_reconnected(FakeServiceEvent(2.25))
        lifecycle.mark_ready()
        stale = (lifecycle.state, lifecycle.is_ready())
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 2_500.0)

        # Assert
        self.assertEqual(
            (
                (BrokerLifecycleState.RECOVERY_PENDING, False),
                (BrokerLifecycleState.CONNECTED, True),
            ),
            (stale, (lifecycle.state, lifecycle.is_ready())),
        )

    def test_stale_passivation_cannot_erase_a_newer_flow_activation(self) -> None:
        # Arrange
        _service, lifecycle, state_listener, reconnecting, reconnected = (
            _active_persistent_lifecycle()
        )

        # Act
        reconnecting.on_reconnecting(FakeServiceEvent(2.0))
        reconnected.on_reconnected(FakeServiceEvent(2.25))
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 2_500.0)
        state_listener.on_change(ReceiverState.ACTIVE, ReceiverState.PASSIVE, 1_500.0)
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (BrokerLifecycleState.CONNECTED, True),
            (lifecycle.state, lifecycle.is_ready()),
        )

    def test_stale_reconnected_callback_cannot_end_a_newer_recovery_epoch(self) -> None:
        # Arrange
        _service, lifecycle, state_listener, reconnecting, reconnected = (
            _active_persistent_lifecycle()
        )

        # Act
        reconnecting.on_reconnecting(FakeServiceEvent(1.0))
        reconnected.on_reconnected(FakeServiceEvent(1.25))
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 1_500.0)
        lifecycle.mark_ready()
        first_epoch = (lifecycle.state, lifecycle.is_ready())
        reconnecting.on_reconnecting(FakeServiceEvent(3.0))
        reconnected.on_reconnected(FakeServiceEvent(2.0))
        stale = (lifecycle.state, lifecycle.is_ready())
        reconnected.on_reconnected(FakeServiceEvent(3.25))
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 3_500.0)
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (
                (BrokerLifecycleState.CONNECTED, True),
                (BrokerLifecycleState.RECOVERING, False),
                (BrokerLifecycleState.CONNECTED, True),
            ),
            (first_epoch, stale, (lifecycle.state, lifecycle.is_ready())),
        )

    def test_reconnect_exhaustion_is_terminal_during_partial_durable_flow_recovery(
        self,
    ) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        install_lifecycle_listeners(service, lifecycle)
        lifecycle.connected()
        lifecycle.receiver_registered("commands")
        lifecycle.receiver_registered("approvals")
        lifecycle.receiver_active("commands")
        lifecycle.receiver_active("approvals")
        lifecycle.mark_ready()
        interrupted = cast(ServiceInterruptionListener, service.interruption_listeners[0])

        # Act
        lifecycle.reconnecting()
        lifecycle.reconnected()
        lifecycle.receiver_active("commands")
        interrupted.on_service_interrupted(FakeServiceEvent())
        lifecycle.receiver_active("approvals")
        lifecycle.mark_ready()
        lifecycle.reconnected()

        # Assert
        self.assertEqual(
            (BrokerLifecycleState.EXHAUSTED, False, True),
            (lifecycle.state, lifecycle.is_ready(), lifecycle.is_terminal()),
        )

    def test_mixed_publishers_require_every_blocked_endpoint_to_recover(self) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        persistent_service = FakeService(publisher=FakePublisher(failure=PublisherOverflowError))
        direct_service = FakeService(direct_failure=PublisherOverflowError)
        persistent = SolacePublisher(persistent_service, lifecycle=lifecycle)
        direct = SolaceDirectPublisher(direct_service, lifecycle=lifecycle)
        lifecycle.mark_ready()

        # Act
        with pytest.raises(MessagingError):
            persistent.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", {})
        with pytest.raises(MessagingError):
            direct.publish_unacknowledged("aerial-rescue/v1/m-1/drone/d-1/telemetry", b"{}", {})
        persistent_listener = cast(
            PublisherReadinessListener, persistent_service.publisher.readiness_listener
        )
        persistent_listener.ready()
        lifecycle.mark_ready()
        ready_after_one = lifecycle.is_ready()
        direct_listener = cast(
            PublisherReadinessListener, direct_service.direct_publisher.readiness_listener
        )
        direct_listener.ready()
        lifecycle.mark_ready()

        # Assert
        self.assertEqual((False, True), (ready_after_one, lifecycle.is_ready()))

    def test_nonrecoverable_publisher_terminations_exhaust_their_shared_lifecycles(self) -> None:
        # Arrange
        services = (FakeService(), FakeService(), FakeService())
        lifecycles = (BrokerLifecycle(), BrokerLifecycle(), BrokerLifecycle())
        for lifecycle in lifecycles:
            lifecycle.connected()
            lifecycle.mark_ready()

        # Act
        SolacePublisher(services[0], lifecycle=lifecycles[0])
        SolaceDirectPublisher(services[1], lifecycle=lifecycles[1])
        SolaceRequestReplyRequester(services[2], lifecycle=lifecycles[2])
        listeners = (
            services[0].publisher.termination_listener,
            services[1].direct_publisher.termination_listener,
            services[2].request_reply_publisher.termination_listener,
        )
        for listener in listeners:
            cast(TerminationNotificationListener, listener).on_termination(
                cast(TerminationEvent, object())
            )

        # Assert
        self.assertEqual(
            ((BrokerLifecycleState.EXHAUSTED, False),) * 3,
            tuple((lifecycle.state, lifecycle.is_ready()) for lifecycle in lifecycles),
        )


class SolacePublisherTests(unittest.TestCase):
    def test_the_publisher_rejects_at_the_owned_bound_and_has_a_readiness_listener(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        SolacePublisher(service)

        # Assert
        self.assertEqual(
            ([PERSISTENT_BUFFER_CAPACITY], True),
            (
                service.publisher_builder.capacities,
                service.publisher.readiness_listener is not None,
            ),
        )

    def test_the_publisher_is_started_when_it_is_built(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        SolacePublisher(service)

        # Assert
        self.assertEqual(1, service.publisher.started)

    def test_bytes_are_converted_to_a_bytearray_the_builder_accepts(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolacePublisher(service)

        # Act
        publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", {"k": "v"})

        # Assert
        self.assertEqual(
            [
                (
                    bytearray(b"{}"),
                    {
                        "k": "v",
                        message_properties.PERSISTENT_ACK_IMMEDIATELY: True,
                        message_properties.PERSISTENT_DMQ_ELIGIBLE: True,
                    },
                )
            ],
            [(payload, dict(properties)) for payload, properties in service.messages.built],
        )

    def test_confirmed_publications_require_an_immediate_broker_ack(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolacePublisher(service)
        supplied = {
            "k": "v",
            message_properties.PERSISTENT_ACK_IMMEDIATELY: False,
        }

        # Act
        publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", supplied)
        built = dict(service.messages.built[0][1])

        # Assert
        self.assertEqual(
            ("v", True),
            (built["k"], built[message_properties.PERSISTENT_ACK_IMMEDIATELY]),
        )

    def test_confirmed_publications_are_always_dead_message_queue_eligible(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolacePublisher(service)
        supplied = {
            "k": "v",
            message_properties.PERSISTENT_DMQ_ELIGIBLE: False,
        }

        # Act
        publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", supplied)
        built = dict(service.messages.built[0][1])

        # Assert
        self.assertEqual(
            ("v", True),
            (built["k"], built[message_properties.PERSISTENT_DMQ_ELIGIBLE]),
        )

    def test_every_publication_waits_for_the_broker_within_the_bound(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolacePublisher(service)

        # Act
        publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", {})

        # Assert
        self.assertEqual(
            (1, PUBLISH_TIMEOUT_MILLISECONDS),
            (len(service.publisher.published), service.publisher.published[0][2]),
        )

    def test_native_trace_refusal_occurs_before_guaranteed_broker_io(self) -> None:
        # Arrange
        service = FakeService()
        tracing = FakeTraceContext(outbound_refusal=NativeTraceRefusal.NATIVE_CONTEXT_ABSENT)
        publisher = SolacePublisher(service, tracing=tracing)

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.TRACE_REFUSED, 1, []),
            (captured.value.refusal, len(tracing.outbound), service.publisher.published),
        )

    def test_a_broker_rejection_becomes_one_owned_definite_refusal(self) -> None:
        # Arrange
        service = FakeService(publisher=FakePublisher(failure=MessageRejectedByBrokerError))
        publisher = SolacePublisher(service)
        topic = "aerial-rescue/v1/m-1/audit/decision"

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish(topic, b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, topic),
            (captured.value.refusal, captured.value.value),
        )

    def test_capacity_refusal_requests_a_ready_callback_but_requires_outbox_recovery(self) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.mark_ready()
        service = FakeService(publisher=FakePublisher(failure=PublisherOverflowError))
        publisher = SolacePublisher(service, lifecycle=lifecycle)

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", {})
        listener = cast(PublisherReadinessListener, service.publisher.readiness_listener)
        listener.ready()

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, 1, False),
            (captured.value.refusal, service.publisher.ready_notifications, lifecycle.is_ready()),
        )

    def test_timeout_or_disconnect_after_send_is_an_ambiguous_publication(self) -> None:
        # Arrange
        failures = (PubSubTimeoutError, PubSubPlusClientIOError, PubSubPlusClientError)
        topic = "aerial-rescue/v1/m-1/audit/decision"

        # Act
        refusals = []
        for failure in failures:
            publisher = SolacePublisher(FakeService(publisher=FakePublisher(failure=failure)))
            with pytest.raises(MessagingError) as captured:
                publisher.publish(topic, b"{}", {})
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([MessagingRefusal.PUBLISH_AMBIGUOUS] * 3, refusals)

    def test_a_failed_ready_notification_still_returns_the_owned_publication_refusal(self) -> None:
        # Arrange
        service = FakeService(
            publisher=FakePublisher(failure=PublisherOverflowError, notify_failing=True)
        )
        publisher = SolacePublisher(service)

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish("aerial-rescue/v1/m-1/audit/decision", b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, PubSubPlusClientError),
            (captured.value.refusal, type(captured.value.__cause__)),
        )

    def test_closing_terminates_the_publisher_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolacePublisher(service)

        # Act
        publisher.close()

        # Assert
        self.assertEqual([SHUTDOWN_GRACE_PERIOD_MILLISECONDS], service.publisher.terminated)


class SolaceRequestReplyRequesterTests(unittest.TestCase):
    def test_request_returns_the_correlated_response_with_payload_properties_topic_and_timeout(
        self,
    ) -> None:
        # Arrange
        response = FakeMessage(b'{"accepted":true}')
        service = FakeService(request_reply_publisher=FakeRequestReplyPublisher(response))
        requester = SolaceRequestReplyRequester(service)
        topic = "aerial-rescue/v1/m-1/gateway/request/coordinate"

        # Act
        received = requester.request(topic, b"{}", {"traceparent": "00-test"}, 1_250)

        # Assert
        published = service.request_reply_publisher.requests[0]
        self.assertEqual(
            (
                response,
                [(bytearray(b"{}"), {"traceparent": "00-test"})],
                topic,
                1_250,
            ),
            (
                received,
                service.messages.built,
                published[1].get_name(),
                published[2],
            ),
        )

    def test_request_timeout_or_disconnect_is_ambiguous_but_broker_rejection_is_definite(
        self,
    ) -> None:
        # Arrange
        failures = (
            (PubSubTimeoutError, MessagingRefusal.PUBLISH_AMBIGUOUS),
            (PubSubPlusClientIOError, MessagingRefusal.PUBLISH_AMBIGUOUS),
            (MessageRejectedByBrokerError, MessagingRefusal.PUBLISH_REFUSED),
        )
        topic = "aerial-rescue/v1/m-1/gateway/request/coordinate"

        # Act
        actual = []
        for failure, expected in failures:
            service = FakeService(
                request_reply_publisher=FakeRequestReplyPublisher(FakeMessage(), failure=failure)
            )
            requester = SolaceRequestReplyRequester(service)
            with pytest.raises(MessagingError) as captured:
                requester.request(topic, b"{}", {}, 1_250)
            actual.append((captured.value.refusal, expected))

        # Assert
        self.assertEqual([(expected, expected) for _, expected in failures], actual)

    def test_native_trace_refusal_occurs_before_request_reply_broker_io(self) -> None:
        # Arrange
        service = FakeService()
        tracing = FakeTraceContext(outbound_refusal=NativeTraceRefusal.CONTEXT_MISMATCH)
        requester = SolaceRequestReplyRequester(service, tracing=tracing)

        # Act
        with pytest.raises(MessagingError) as captured:
            requester.request("aerial-rescue/v1/m-1/gateway/request/coordinate", b"{}", {}, 1_250)

        # Assert
        self.assertEqual(
            (MessagingRefusal.TRACE_REFUSED, 1, []),
            (
                captured.value.refusal,
                len(tracing.outbound),
                service.request_reply_publisher.requests,
            ),
        )

    def test_invalid_native_context_on_the_correlated_reply_is_refused(self) -> None:
        # Arrange
        response = FakeMessage()
        service = FakeService(request_reply_publisher=FakeRequestReplyPublisher(response))
        tracing = FakeTraceContext(inbound_refusal=NativeTraceRefusal.CONTEXT_MISMATCH)
        requester = SolaceRequestReplyRequester(service, tracing=tracing)

        # Act
        with pytest.raises(MessagingError) as captured:
            requester.request("aerial-rescue/v1/m-1/gateway/request/coordinate", b"{}", {}, 1_250)

        # Assert
        self.assertEqual(
            (MessagingRefusal.TRACE_REFUSED, [(response, b"{}")], 1),
            (
                captured.value.refusal,
                tracing.inbound,
                len(service.request_reply_publisher.requests),
            ),
        )

    def test_request_capacity_refusal_is_definite_and_requests_one_ready_notification(
        self,
    ) -> None:
        # Arrange
        publisher = FakeRequestReplyPublisher(FakeMessage(), failure=PublisherOverflowError)
        service = FakeService(request_reply_publisher=publisher)
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        requester = SolaceRequestReplyRequester(service, lifecycle=lifecycle)
        lifecycle.mark_ready()
        topic = "aerial-rescue/v1/m-1/gateway/request/coordinate"

        # Act
        with pytest.raises(MessagingError) as captured:
            requester.request(topic, b"{}", {}, 1_250)

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, False, 1),
            (
                captured.value.refusal,
                lifecycle.is_ready(),
                publisher.readiness_notifications,
            ),
        )

    def test_closing_request_reply_uses_the_same_bounded_sdk_grace(self) -> None:
        # Arrange
        service = FakeService()
        requester = SolaceRequestReplyRequester(service)

        # Act
        requester.close()

        # Assert
        self.assertEqual(
            [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
            service.request_reply_publisher.terminated,
        )


class SolaceReceiverTests(unittest.TestCase):
    def test_telemetry_keeps_one_newest_message_and_other_direct_ingress_is_bounded(self) -> None:
        # Arrange
        expected = (1, 50)

        # Act
        capacities = (
            DIRECT_TELEMETRY_RECEIVER_CAPACITY,
            DIRECT_INTEGRATION_RECEIVER_CAPACITY,
        )

        # Assert
        self.assertEqual(expected, capacities)

    def test_every_subscription_reaches_the_builder_and_the_receiver_starts(self) -> None:
        # Arrange
        service = FakeService()
        patterns = ("aerial-rescue/v1/*/gateway/request/*", "aerial-rescue/v1/*/audit/*")

        # Act
        SolaceReceiver(service, patterns, buffer_capacity=DIRECT_RECEIVER_CAPACITY)

        # Assert
        self.assertEqual(
            (2, [DIRECT_RECEIVER_CAPACITY], 1),
            (
                len(service.receiver_builder.subscriptions),
                service.receiver_builder.capacities,
                service.receiver.started,
            ),
        )

    def test_nonrecoverable_direct_receiver_termination_exhausts_readiness(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.mark_ready()

        # Act
        SolaceReceiver(
            service,
            (),
            buffer_capacity=DIRECT_RECEIVER_CAPACITY,
            lifecycle=lifecycle,
        )
        listener = cast(TerminationNotificationListener, service.receiver.termination_listener)
        listener.on_termination(cast(TerminationEvent, object()))

        # Assert
        self.assertEqual(
            (BrokerLifecycleState.EXHAUSTED, False),
            (lifecycle.state, lifecycle.is_ready()),
        )

    def test_receiving_passes_the_window_through_and_yields_the_message(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService(receiver=FakeReceiver((message,)))
        receiver = SolaceReceiver(service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY)

        # Act
        received = receiver.receive(1000)

        # Assert
        self.assertEqual((message, [1000]), (received, service.receiver.timeouts))

    def test_invalid_native_context_is_dropped_before_direct_delivery(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService(receiver=FakeReceiver((message,)))
        tracing = FakeTraceContext(inbound_refusal=NativeTraceRefusal.CONTEXT_MISMATCH)
        receiver = SolaceReceiver(
            service,
            (),
            buffer_capacity=DIRECT_RECEIVER_CAPACITY,
            tracing=tracing,
        )

        # Act
        with pytest.raises(MessagingError) as captured:
            receiver.receive(1_000)

        # Assert
        self.assertEqual(
            (MessagingRefusal.TRACE_REFUSED, [(message, b"{}")]),
            (captured.value.refusal, tracing.inbound),
        )

    def test_invalid_direct_context_retains_only_body_free_refusal_metadata(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService(receiver=FakeReceiver((message,)))
        tracing = FakeTraceContext(inbound_refusal=NativeTraceRefusal.CONTEXT_MISMATCH)
        receiver = SolaceReceiver(
            service,
            (),
            buffer_capacity=DIRECT_RECEIVER_CAPACITY,
            tracing=tracing,
        )

        # Act
        with pytest.raises(InvalidDirectMessageError) as captured:
            receiver.receive(1_000)

        # Assert
        self.assertEqual(
            (None, "audit", hashlib.sha256(b"{}").hexdigest(), False),
            (
                captured.value.metadata.source,
                captured.value.metadata.family,
                captured.value.metadata.raw_digest,
                "{}" in repr(captured.value.metadata),
            ),
        )

    def test_backpressure_discards_are_counted_and_remove_readiness_until_resynchronised(
        self,
    ) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService(receiver=FakeReceiver((message,)))
        service.api_metrics = FakeMetrics((4, 6))
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.mark_ready()
        receiver = SolaceReceiver(
            service,
            (),
            buffer_capacity=DIRECT_TELEMETRY_RECEIVER_CAPACITY,
            lifecycle=lifecycle,
        )

        # Act
        received = receiver.receive(1_000)
        discarded = receiver.discarded_messages()

        # Assert
        self.assertEqual(
            (
                message,
                2,
                False,
                [
                    Metric.RECEIVED_MESSAGES_BACKPRESSURE_DISCARDED,
                    Metric.RECEIVED_MESSAGES_BACKPRESSURE_DISCARDED,
                    Metric.RECEIVED_MESSAGES_BACKPRESSURE_DISCARDED,
                ],
            ),
            (received, discarded, lifecycle.is_ready(), service.api_metrics.read),
        )

    def test_an_unreadable_discard_metric_refuses_the_receiver_before_it_starts(self) -> None:
        # Arrange
        service = FakeService()
        service.api_metrics = FakeMetrics((0,), failing=True)

        # Act
        with pytest.raises(MessagingError) as captured:
            SolaceReceiver(
                service,
                (),
                buffer_capacity=DIRECT_INTEGRATION_RECEIVER_CAPACITY,
            )

        # Assert
        self.assertEqual(
            (MessagingRefusal.METRICS_REFUSED, 0),
            (captured.value.refusal, service.receiver.started),
        )

    def test_an_empty_window_yields_none_rather_than_blocking(self) -> None:
        # Arrange
        service = FakeService(receiver=FakeReceiver(()))
        receiver = SolaceReceiver(service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY)

        # Act
        received = receiver.receive(1000)

        # Assert
        self.assertIsNone(received)

    def test_closing_terminates_the_receiver_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        receiver = SolaceReceiver(service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY)

        # Act
        receiver.close()

        # Assert
        self.assertEqual([SHUTDOWN_GRACE_PERIOD_MILLISECONDS], service.receiver.terminated)


class SolaceDirectPublisherTests(unittest.TestCase):
    def test_the_publisher_rejects_rather_than_buffering_and_is_started(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        SolaceDirectPublisher(service)

        # Assert
        self.assertEqual(
            ([DIRECT_BUFFER_CAPACITY], 1),
            (service.direct_publisher_builder.capacities, service.direct_publisher.started),
        )

    def test_the_capacity_is_zero_so_a_full_transport_refuses_rather_than_queues(self) -> None:
        # Arrange
        expected = 0

        # Act
        capacity = DIRECT_BUFFER_CAPACITY

        # Assert
        self.assertEqual(expected, capacity)

    def test_the_payload_reaches_the_client_as_a_bytearray_with_its_topic(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolaceDirectPublisher(service)
        topic = "aerial-rescue/v1/m-1/drone/d-1/telemetry"

        # Act
        publisher.publish_unacknowledged(topic, b"{}", {"k": "v"})

        # Assert
        self.assertEqual(
            [(("message", bytearray(b"{}")), topic, {})],
            [
                (message, destination.get_name(), dict(properties or {}))
                for message, destination, properties in service.direct_publisher.published
            ],
        )
        self.assertEqual([(bytearray(b"{}"), {"k": "v"})], service.messages.built)

    def test_direct_publication_injects_native_context_into_the_built_message(self) -> None:
        # Arrange
        service = FakeService()
        tracing = FakeTraceContext()
        publisher = SolaceDirectPublisher(service, tracing=tracing)

        # Act
        publisher.publish_unacknowledged("aerial-rescue/v1/m-1/drone/d-1/telemetry", b"{}", {})

        # Assert
        self.assertEqual(
            [(("message", bytearray(b"{}")), b"{}")],
            tracing.outbound,
        )

    def test_native_trace_refusal_occurs_before_direct_broker_io(self) -> None:
        # Arrange
        service = FakeService()
        tracing = FakeTraceContext(outbound_refusal=NativeTraceRefusal.NATIVE_CONTEXT_ABSENT)
        publisher = SolaceDirectPublisher(service, tracing=tracing)

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish_unacknowledged("aerial-rescue/v1/m-1/drone/d-1/telemetry", b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.TRACE_REFUSED, 1, []),
            (
                captured.value.refusal,
                len(tracing.outbound),
                service.direct_publisher.published,
            ),
        )

    def test_an_io_failure_is_ambiguous_and_removes_readiness(self) -> None:
        # Arrange
        service = FakeService(direct_failure=PubSubPlusClientIOError)
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        publisher = SolaceDirectPublisher(service, lifecycle=lifecycle)
        lifecycle.mark_ready()
        topic = "aerial-rescue/v1/m-1/drone/d-1/telemetry"

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish_unacknowledged(topic, b"{}", {})
        ready_before_reconciliation = lifecycle.is_ready()
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_AMBIGUOUS, topic, False, True, 0),
            (
                captured.value.refusal,
                captured.value.value,
                ready_before_reconciliation,
                lifecycle.is_ready(),
                service.direct_publisher.readiness_notifications,
            ),
        )

    def test_capacity_refusal_is_definite_and_requests_one_readiness_notification(self) -> None:
        # Arrange
        service = FakeService(direct_failure=PublisherOverflowError)
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        publisher = SolaceDirectPublisher(service, lifecycle=lifecycle)
        lifecycle.mark_ready()
        topic = "aerial-rescue/v1/m-1/drone/d-1/telemetry"

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish_unacknowledged(topic, b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, topic, False, 1, True),
            (
                captured.value.refusal,
                captured.value.value,
                lifecycle.is_ready(),
                service.direct_publisher.readiness_notifications,
                service.direct_publisher.readiness_listener is not None,
            ),
        )

    def test_closing_terminates_the_publisher_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolaceDirectPublisher(service)

        # Act
        publisher.close()

        # Assert
        self.assertEqual([SHUTDOWN_GRACE_PERIOD_MILLISECONDS], service.direct_publisher.terminated)


class PublishingSessionTests(unittest.TestCase):
    def test_closing_terminates_the_publisher_before_disconnecting(self) -> None:
        # Arrange
        service = FakeService()
        session = PublishingSession(SolaceDirectPublisher(service), service)

        # Act
        session.close()

        # Assert
        self.assertEqual(["terminate", "disconnect"], service.order)


class RequestingSessionTests(unittest.TestCase):
    def test_opening_reuses_one_connection_and_exposes_only_the_requester(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        with patch("aerial_rescue_broker.messaging.build_service", return_value=service) as build:
            session = open_requesting_session(ENDPOINT, Principal.EVENT_MESH_TOOL, CREDENTIAL)

        # Assert
        self.assertEqual(
            (1, True, False, Principal.EVENT_MESH_TOOL),
            (
                service.connected,
                isinstance(session.requester, SolaceRequestReplyRequester),
                session.readiness.is_ready(),
                build.call_args.args[1],
            ),
        )

    def test_closing_terminates_the_requester_before_disconnect_and_closes_readiness(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        requester = SolaceRequestReplyRequester(service, lifecycle=lifecycle)
        session = RequestingSession(requester, service, lifecycle)

        # Act
        session.close()

        # Assert
        self.assertEqual(
            (
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                ["disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (
                service.request_reply_publisher.terminated,
                service.order,
                lifecycle.state,
            ),
        )

    def test_close_continues_after_requester_refusal_and_returns_one_owned_error(self) -> None:
        # Arrange
        publisher = FakeRequestReplyPublisher(FakeMessage(), terminate_failing=True)
        service = FakeService(request_reply_publisher=publisher)
        lifecycle = BrokerLifecycle()
        requester = SolaceRequestReplyRequester(service, lifecycle=lifecycle)
        session = RequestingSession(requester, service, lifecycle)

        # Act
        with pytest.raises(MessagingError) as captured:
            session.close()

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.SHUTDOWN_REFUSED,
                ["disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (captured.value.refusal, service.order, lifecycle.state),
        )


class OpenSessionCompositionTests(unittest.TestCase):
    def test_each_composition_connects_once_and_exposes_only_its_typed_capabilities(self) -> None:
        # Arrange
        services = tuple(FakeService() for _ in range(4))

        # Act
        with patch("aerial_rescue_broker.messaging.build_service", side_effect=services):
            publishing = open_publishing_session(ENDPOINT, Principal.FLEET_SIMULATOR, CREDENTIAL)
            mixed = open_session(
                ENDPOINT,
                Principal.COMMAND_GATEWAY,
                CREDENTIAL,
                (),
                direct_receiver_capacity=DIRECT_RECEIVER_CAPACITY,
            )
            consuming = open_consuming_session(ENDPOINT, Principal.RECORDER, CREDENTIAL, QUEUE)
            fleet = open_fleet_session(
                ENDPOINT, Principal.FLEET_SIMULATOR, CREDENTIAL, FLEET_QUEUES
            )

        # Assert
        self.assertEqual(
            (
                [1, 1, 1, 1],
                SolaceDirectPublisher,
                (SolaceDirectPublisher, SolacePublisher, SolaceReceiver),
                SolacePersistentReceiver,
                (SolaceDirectPublisher, SolacePublisher, 2),
            ),
            (
                [service.connected for service in services],
                type(publishing.publisher),
                (
                    type(mixed.direct_publisher),
                    type(mixed.publisher),
                    type(mixed.receiver),
                ),
                type(consuming.receiver),
                (type(fleet.telemetry), type(fleet.results), len(fleet.receivers)),
            ),
        )

    def test_mixed_composition_releases_prior_endpoints_when_receiver_start_is_refused(
        self,
    ) -> None:
        # Arrange
        service = FakeService(receiver=FakeReceiver((), start_failing=True))

        # Act
        with (
            patch("aerial_rescue_broker.messaging.build_service", return_value=service),
            pytest.raises(MessagingError) as captured,
        ):
            open_session(
                ENDPOINT,
                Principal.COMMAND_GATEWAY,
                CREDENTIAL,
                (),
                direct_receiver_capacity=DIRECT_INTEGRATION_RECEIVER_CAPACITY,
            )

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                ["terminate", "disconnect"],
            ),
            (
                captured.value.refusal,
                service.receiver.terminated,
                service.direct_publisher.terminated,
                service.publisher.terminated,
                service.order,
            ),
        )


class EndpointShutdownTests(unittest.TestCase):
    def test_every_endpoint_contains_sdk_termination_failure_as_one_owned_refusal(self) -> None:
        # Arrange
        direct_service = FakeService(direct_terminate_failing=True)
        receiver_service = FakeService(receiver=FakeReceiver((), terminate_failing=True))
        persistent_service = FakeService()
        persistent_service.persistent_receiver = FakePersistentReceiver((), terminate_failing=True)
        persistent_service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            persistent_service.persistent_receiver
        )
        endpoints = (
            SolaceDirectPublisher(direct_service),
            SolaceReceiver(receiver_service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY),
            SolacePersistentReceiver(persistent_service, QUEUE),
        )

        # Act
        refusals = []
        for endpoint in endpoints:
            with pytest.raises(MessagingError) as captured:
                endpoint.close()
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([MessagingRefusal.SHUTDOWN_REFUSED] * 3, refusals)


class BrokerSessionTests(unittest.TestCase):
    def test_shutdown_continues_after_one_endpoint_refuses_and_uses_the_grace_bound(self) -> None:
        # Arrange
        service = FakeService(publisher=FakePublisher(terminate_failing=True))
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.mark_ready()
        session = BrokerSession(
            direct_publisher=SolaceDirectPublisher(service, lifecycle=lifecycle),
            publisher=SolacePublisher(service, lifecycle=lifecycle),
            receiver=SolaceReceiver(service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY),
            _service=service,
            readiness=lifecycle,
        )

        # Act
        with pytest.raises(MessagingError) as captured:
            session.close()

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.SHUTDOWN_REFUSED,
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                ["terminate", "disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (
                captured.value.refusal,
                service.direct_publisher.terminated,
                service.publisher.terminated,
                service.receiver.terminated,
                service.order,
                lifecycle.state,
            ),
        )


class FleetSessionTests(unittest.TestCase):
    """One connection carrying two publishers and one receiver per drone (ADR-0080)."""

    def test_one_receiver_is_bound_per_named_queue_in_key_order(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )

        # Act
        session = fleet_session(service, FLEET_QUEUES)

        # Assert
        builder = service.persistent_receiver_builder
        self.assertEqual(
            ([EARLIER_QUEUE, LATER_QUEUE], sorted(FLEET_QUEUES)),
            ([endpoint.get_name() for endpoint in builder.endpoints], sorted(session.receivers)),
        )

    def test_one_connection_carries_every_receiver_and_both_publishers(self) -> None:
        """`MAX_BIND_COUNT` bounds flows per queue, not services per process."""
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )

        # Act
        session = fleet_session(service, FLEET_QUEUES)

        # Assert
        self.assertEqual(
            (2, True, True),
            (
                len(session.receivers),
                isinstance(session.telemetry, SolaceDirectPublisher),
                isinstance(session.results, SolacePublisher),
            ),
        )

    def test_closing_releases_every_receiver_before_it_disconnects(self) -> None:
        """Disconnecting first would strand messages a receiver had taken and not settled."""
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        session = fleet_session(service, FLEET_QUEUES)

        # Act
        session.close()

        # Assert
        self.assertEqual(
            ["receiver-terminate", "receiver-terminate", "terminate", "disconnect"],
            service.order,
        )

    def test_close_releases_the_complete_fleet_graph_in_exact_reverse_construction_order(
        self,
    ) -> None:
        # Arrange
        service = FakeService()
        order = service.order
        publisher = _PublisherEndpointFactory("results", order)
        telemetry = _PublisherEndpointFactory("telemetry", order)
        receiver = _ReceiverEndpointFactory(order)

        # Act
        with (
            patch("aerial_rescue_broker.messaging.SolaceDirectPublisher", side_effect=telemetry),
            patch("aerial_rescue_broker.messaging.SolacePublisher", side_effect=publisher),
            patch("aerial_rescue_broker.messaging.SolacePersistentReceiver", side_effect=receiver),
        ):
            session = fleet_session(service, FLEET_QUEUES)
            session.close()

        # Assert
        self.assertEqual(
            [LATER_QUEUE, EARLIER_QUEUE, "results", "telemetry", "disconnect"],
            order,
        )

    def test_close_continues_reverse_cleanup_after_the_first_endpoint_refusal(self) -> None:
        # Arrange
        service = FakeService()
        order = service.order
        publisher = _PublisherEndpointFactory("results", order, failing=True)
        telemetry = _PublisherEndpointFactory("telemetry", order)
        receiver = _ReceiverEndpointFactory(order, failing=(LATER_QUEUE,))

        with (
            patch("aerial_rescue_broker.messaging.SolaceDirectPublisher", side_effect=telemetry),
            patch("aerial_rescue_broker.messaging.SolacePublisher", side_effect=publisher),
            patch("aerial_rescue_broker.messaging.SolacePersistentReceiver", side_effect=receiver),
        ):
            session = fleet_session(service, FLEET_QUEUES)

        # Act
        with pytest.raises(MessagingError) as raised:
            session.close()

        # Assert
        self.assertEqual(MessagingRefusal.SHUTDOWN_REFUSED, raised.value.refusal)
        self.assertEqual(
            [LATER_QUEUE, EARLIER_QUEUE, "results", "telemetry", "disconnect"],
            order,
        )

    def test_a_refused_binding_names_the_queue_the_broker_would_not_give(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order, unbindable=(LATER_QUEUE,)
        )

        # Act
        with pytest.raises(MessagingError) as captured:
            fleet_session(service, FLEET_QUEUES)

        # Assert
        self.assertEqual(
            (MessagingRefusal.BIND_REFUSED, LATER_QUEUE),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_refused_binding_releases_everything_already_opened(self) -> None:
        """A partly built fleet must not leave a connection and a bound queue behind."""
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order, unbindable=(LATER_QUEUE,)
        )

        # Act
        with pytest.raises(MessagingError):
            fleet_session(service, FLEET_QUEUES)

        # Assert
        self.assertEqual(
            (
                [
                    "receiver-terminate",
                    "receiver-terminate",
                    "terminate",
                    "disconnect",
                ],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
            ),
            (service.order, service.publisher.terminated),
        )

    def test_a_refused_binding_releases_the_partial_graph_in_reverse_order(self) -> None:
        # Arrange
        service = FakeService()
        order = service.order
        publisher = _PublisherEndpointFactory("results", order)
        telemetry = _PublisherEndpointFactory("telemetry", order)
        receiver = _ReceiverEndpointFactory(order, refuse=LATER_QUEUE)

        # Act
        with (
            patch("aerial_rescue_broker.messaging.SolaceDirectPublisher", side_effect=telemetry),
            patch("aerial_rescue_broker.messaging.SolacePublisher", side_effect=publisher),
            patch("aerial_rescue_broker.messaging.SolacePersistentReceiver", side_effect=receiver),
            pytest.raises(MessagingError) as raised,
        ):
            fleet_session(service, FLEET_QUEUES)

        # Assert
        self.assertEqual(
            (MessagingRefusal.BIND_REFUSED, LATER_QUEUE),
            (raised.value.refusal, raised.value.value),
        )
        self.assertEqual(
            [EARLIER_QUEUE, "results", "telemetry", "disconnect"],
            order,
        )


if __name__ == "__main__":
    unittest.main()


class SolacePersistentReceiverTests(unittest.TestCase):
    def test_the_receiver_binds_the_named_durable_exclusive_queue_and_starts(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        SolacePersistentReceiver(service, QUEUE)

        # Assert
        builder = service.persistent_receiver_builder
        self.assertEqual(
            (QUEUE, True, True, 1),
            (
                builder.endpoints[0].get_name(),
                builder.endpoints[0].is_durable(),
                builder.endpoints[0].is_exclusively_accessible(),
                service.persistent_receiver.started,
            ),
        )

    def test_activation_listener_removes_readiness_until_the_durable_flow_is_active(
        self,
    ) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        SolacePersistentReceiver(service, QUEUE, lifecycle=lifecycle)
        lifecycle.mark_ready()
        listener = cast(
            ReceiverStateChangeListener,
            service.persistent_receiver_builder.activation_listeners[0],
        )

        # Act
        initially_ready = lifecycle.is_ready()
        listener.on_change(ReceiverState.ACTIVE, ReceiverState.PASSIVE, 1.0)
        passive = (lifecycle.state, lifecycle.is_ready())
        listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 2.0)
        active_before_application = (lifecycle.state, lifecycle.is_ready())
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (
                True,
                (BrokerLifecycleState.RECOVERY_PENDING, False),
                (BrokerLifecycleState.RECOVERY_PENDING, False),
                (BrokerLifecycleState.CONNECTED, True),
            ),
            (
                initially_ready,
                passive,
                active_before_application,
                (lifecycle.state, lifecycle.is_ready()),
            ),
        )

    def test_nonrecoverable_durable_flow_termination_exhausts_readiness(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        SolacePersistentReceiver(service, QUEUE, lifecycle=lifecycle)
        lifecycle.mark_ready()

        # Act
        ready_before_termination = lifecycle.is_ready()
        listener = cast(
            TerminationNotificationListener,
            service.persistent_receiver.termination_listener,
        )
        listener.on_termination(cast(TerminationEvent, object()))
        state_listener = cast(
            ReceiverStateChangeListener,
            service.persistent_receiver_builder.activation_listeners[0],
        )
        state_listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 3.0)
        lifecycle.mark_ready()

        # Assert
        self.assertEqual(
            (True, BrokerLifecycleState.EXHAUSTED, False),
            (ready_before_termination, lifecycle.state, lifecycle.is_ready()),
        )

    def test_the_caller_settles_each_message_rather_than_the_client(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        SolacePersistentReceiver(service, QUEUE)

        # Assert
        self.assertEqual(1, service.persistent_receiver_builder.client_acknowledgement)

    def test_both_negative_outcomes_are_requested_so_a_refusal_can_be_sent(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        SolacePersistentReceiver(service, QUEUE)

        # Assert
        self.assertEqual(
            (REQUIRED_OUTCOMES, (Outcome.FAILED, Outcome.REJECTED)),
            (service.persistent_receiver_builder.outcomes, REQUIRED_OUTCOMES),
        )

    def test_receiving_passes_the_window_through_and_yields_the_message(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((message,))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        received = receiver.receive(250)

        # Assert
        self.assertEqual((message, [250]), (received, service.persistent_receiver.timeouts))

    def test_invalid_native_context_stays_unsettled_until_its_refusal_is_durable(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((message,))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )
        tracing = FakeTraceContext(inbound_refusal=NativeTraceRefusal.CONTEXT_MISMATCH)
        receiver = SolacePersistentReceiver(service, QUEUE, tracing=tracing)

        # Act
        with pytest.raises(UnsettledMessageError) as captured:
            receiver.receive(250)
        before_durable_refusal = list(service.persistent_receiver.settled)
        captured.value.settlement.reject()

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.TRACE_REFUSED,
                [(message, b"{}")],
                [],
                [(message, Outcome.REJECTED)],
            ),
            (
                captured.value.refusal,
                tracing.inbound,
                before_durable_refusal,
                service.persistent_receiver.settled,
            ),
        )

    def test_invalid_native_context_retains_only_bounded_refusal_metadata(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((message,))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )
        tracing = FakeTraceContext(inbound_refusal=NativeTraceRefusal.CONTEXT_MISMATCH)
        receiver = SolacePersistentReceiver(service, QUEUE, tracing=tracing)

        # Act
        with pytest.raises(UnsettledMessageError) as captured:
            receiver.receive(250)

        # Assert
        self.assertEqual(
            (
                None,
                "audit",
                hashlib.sha256(b"{}").hexdigest(),
                False,
            ),
            (
                captured.value.metadata.source,
                captured.value.metadata.family,
                captured.value.metadata.raw_digest,
                "{}" in repr(captured.value.metadata),
            ),
        )

    def test_an_empty_window_yields_none_rather_than_blocking(self) -> None:
        # Arrange
        service = FakeService()
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        received = receiver.receive(10)

        # Assert
        self.assertIsNone(received)

    def test_accepting_a_message_removes_it_from_the_queue(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService()
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        receiver.settle(message, Outcome.ACCEPTED)

        # Assert
        self.assertEqual([(message, Outcome.ACCEPTED)], service.persistent_receiver.settled)

    def test_rejecting_a_message_sends_it_to_the_dead_message_queue(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService()
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        receiver.settle(message, Outcome.REJECTED)

        # Assert
        self.assertEqual([(message, Outcome.REJECTED)], service.persistent_receiver.settled)

    def test_a_client_failure_settling_becomes_one_owned_refusal(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((), failing=True)
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        with pytest.raises(MessagingError) as raised:
            receiver.settle(FakeMessage(), Outcome.ACCEPTED)

        # Assert
        self.assertEqual(MessagingRefusal.SETTLE_REFUSED, raised.value.refusal)

    def test_a_refused_binding_names_the_queue_as_one_owned_refusal(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((), unbindable=True)
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )

        # Act
        with pytest.raises(MessagingError) as raised:
            SolacePersistentReceiver(service, QUEUE)

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                QUEUE,
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
            ),
            (
                raised.value.refusal,
                raised.value.value,
                service.persistent_receiver.terminated,
            ),
        )

    def test_closing_terminates_the_receiver_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        receiver.close()

        # Assert
        self.assertEqual(
            [SHUTDOWN_GRACE_PERIOD_MILLISECONDS], service.persistent_receiver.terminated
        )


class ConsumingSessionTests(unittest.TestCase):
    def test_closing_terminates_the_receiver_before_disconnecting(self) -> None:
        # Arrange
        service = FakeService()
        session = ConsumingSession(
            receiver=SolacePersistentReceiver(service, QUEUE), _service=service
        )

        # Act
        session.close()

        # Assert
        self.assertEqual(
            ([SHUTDOWN_GRACE_PERIOD_MILLISECONDS], ["disconnect"]),
            (service.persistent_receiver.terminated, service.order),
        )


class MessageSettlementTests(unittest.TestCase):
    def test_each_terminal_outcome_is_bound_to_its_original_message(self) -> None:
        # Arrange
        messages = tuple(FakeMessage() for _ in range(3))
        services = tuple(FakeService() for _ in range(3))
        settlements = tuple(
            MessageSettlement(SolacePersistentReceiver(service, QUEUE), message)
            for service, message in zip(services, messages, strict=True)
        )

        # Act
        settlements[0].accept()
        settlements[1].fail()
        settlements[2].reject()

        # Assert
        self.assertEqual(
            [
                [(messages[0], Outcome.ACCEPTED)],
                [(messages[1], Outcome.FAILED)],
                [(messages[2], Outcome.REJECTED)],
            ],
            [service.persistent_receiver.settled for service in services],
        )

    def test_a_settlement_capability_can_be_used_exactly_once(self) -> None:
        # Arrange
        message = FakeMessage()
        service = FakeService()
        settlement = MessageSettlement(SolacePersistentReceiver(service, QUEUE), message)

        # Act
        settlement.accept()
        with pytest.raises(MessagingError) as captured:
            settlement.reject()

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.SETTLEMENT_ALREADY_DECIDED,
                [(message, Outcome.ACCEPTED)],
            ),
            (captured.value.refusal, service.persistent_receiver.settled),
        )

    def test_a_failed_settlement_attempt_is_spent_instead_of_being_sent_twice(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((), failing=True)
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )
        settlement = MessageSettlement(SolacePersistentReceiver(service, QUEUE), FakeMessage())

        # Act
        with pytest.raises(MessagingError) as first:
            settlement.fail()
        with pytest.raises(MessagingError) as second:
            settlement.accept()

        # Assert
        self.assertEqual(
            (MessagingRefusal.SETTLE_REFUSED, MessagingRefusal.SETTLEMENT_ALREADY_DECIDED),
            (first.value.refusal, second.value.refusal),
        )


class _ClosingReceiver:
    """A patched endpoint that records close order and can refuse shutdown."""

    def __init__(self, name: str, order: list[str], *, failing: bool = False) -> None:
        """Retain one safe endpoint name and its shared call log."""
        self._name = name
        self._order = order
        self._failing = failing

    def close(self) -> None:
        """Record shutdown before optionally returning one owned refusal."""
        self._order.append(self._name)
        if self._failing:
            raise MessagingError(MessagingRefusal.SHUTDOWN_REFUSED, self._name)


class _PublisherEndpointFactory:
    """Build patched publishers with one named close effect."""

    def __init__(self, name: str, order: list[str], *, failing: bool = False) -> None:
        """Retain the endpoint effect independently from the SDK constructor shape."""
        self._name = name
        self._order = order
        self._failing = failing

    def __call__(
        self,
        service: object,
        *,
        lifecycle: BrokerLifecycle,
        tracing: object | None = None,
    ) -> _ClosingReceiver:
        """Return the configured patched endpoint."""
        del service, lifecycle, tracing
        return _ClosingReceiver(self._name, self._order, failing=self._failing)


class _ReceiverEndpointFactory:
    """Build patched durable receivers with controlled construction and close refusals."""

    def __init__(
        self,
        order: list[str],
        *,
        refuse: str | None = None,
        failing: Collection[str] = (),
    ) -> None:
        """Retain only the exact queue identities that exercise a refusal path."""
        self._order = order
        self._refuse = refuse
        self._failing = frozenset(failing)

    def __call__(
        self,
        service: object,
        queue: str,
        *,
        lifecycle: BrokerLifecycle,
        tracing: object | None = None,
    ) -> _ClosingReceiver:
        """Build one receiver or raise its injected binding refusal."""
        del service, lifecycle, tracing
        if queue == self._refuse:
            raise MessagingError(MessagingRefusal.BIND_REFUSED, queue)
        return _ClosingReceiver(queue, self._order, failing=queue in self._failing)


class ReceiverOnlySessionTests(unittest.TestCase):
    def test_one_service_binds_named_guaranteed_queues_and_one_bounded_direct_receiver(
        self,
    ) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        subscriptions = (
            "aerial-rescue/v1/*/drone/*/telemetry",
            "aerial-rescue/v1/*/agent/response/*/*",
        )

        # Act
        session = receiver_only_session(
            service,
            ReceiverOnlyBindings(
                FLEET_QUEUES,
                subscriptions,
                DIRECT_RECEIVER_CAPACITY,
            ),
        )

        # Assert
        persistent = service.persistent_receiver_builder
        self.assertEqual(
            (
                [EARLIER_QUEUE, LATER_QUEUE],
                list(subscriptions),
                [DIRECT_RECEIVER_CAPACITY],
                tuple(sorted(FLEET_QUEUES)),
                False,
                False,
                False,
            ),
            (
                [endpoint.get_name() for endpoint in persistent.endpoints],
                [
                    cast(TopicSubscription, subscription).get_name()
                    for subscription in service.receiver_builder.subscriptions
                ],
                service.receiver_builder.capacities,
                session.receiver_names,
                hasattr(session, "publisher"),
                hasattr(session, "requester"),
                "FakeService" in repr(session),
            ),
        )

    def test_receive_returns_native_validated_direct_and_message_bound_guaranteed_inputs(
        self,
    ) -> None:
        # Arrange
        direct_message = FakeMessage(b'{"kind":"direct"}')
        guaranteed_message = FakeMessage(b'{"kind":"guaranteed"}')
        service = FakeService(receiver=FakeReceiver((direct_message,)))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        tracing = FakeTraceContext()
        session = receiver_only_session(
            service,
            ReceiverOnlyBindings(
                {"audit": QUEUE},
                ("aerial-rescue/v1/*/drone/*/telemetry",),
                DIRECT_RECEIVER_CAPACITY,
            ),
            tracing=tracing,
        )
        service.persistent_receiver_builder.built[0]._scripted.append(guaranteed_message)

        # Act
        guaranteed = cast(GuaranteedMessage, session.receive_guaranteed("audit", 125))
        direct = session.receive_direct(250)
        guaranteed.settlement.accept()

        # Assert
        self.assertEqual(
            (
                GuaranteedMessage,
                guaranteed_message,
                direct_message,
                [
                    (guaranteed_message, b'{"kind":"guaranteed"}'),
                    (direct_message, b'{"kind":"direct"}'),
                ],
                [(guaranteed_message, Outcome.ACCEPTED)],
                [125],
                [250],
            ),
            (
                type(guaranteed),
                guaranteed.message,
                direct,
                tracing.inbound,
                service.persistent_receiver_builder.built[0].settled,
                service.persistent_receiver_builder.built[0].timeouts,
                service.receiver.timeouts,
            ),
        )

    def test_empty_guaranteed_window_and_unknown_receiver_are_distinct(self) -> None:
        # Arrange
        service = FakeService()
        session = receiver_only_session(
            service,
            ReceiverOnlyBindings({"audit": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
        )

        # Act
        idle = session.receive_guaranteed("audit", 5)
        with pytest.raises(MessagingError) as captured:
            session.receive_guaranteed("missing", 5)

        # Assert
        self.assertEqual(
            (None, MessagingRefusal.RECEIVER_NOT_FOUND, "missing"),
            (idle, captured.value.refusal, captured.value.value),
        )

    def test_readiness_recovers_only_after_application_and_durable_flow_rebind(self) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        service = FakeService()
        session = receiver_only_session(
            service,
            ReceiverOnlyBindings({"audit": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
            lifecycle=lifecycle,
        )

        # Act
        initial = lifecycle.is_ready()
        session.rebind_complete()
        ready = lifecycle.is_ready()
        lifecycle.reconnecting()
        disconnected = lifecycle.is_ready()
        lifecycle.reconnected()
        pending = lifecycle.is_ready()
        session.rebind_complete()
        application_only = lifecycle.is_ready()
        listener = cast(
            ReceiverStateChangeListener,
            service.persistent_receiver_builder.activation_listeners[0],
        )
        listener.on_change(ReceiverState.PASSIVE, ReceiverState.ACTIVE, 1.0)

        # Assert
        self.assertEqual(
            (False, True, False, False, False, True, BrokerLifecycleState.CONNECTED),
            (
                initial,
                ready,
                disconnected,
                pending,
                application_only,
                lifecycle.is_ready(),
                lifecycle.state,
            ),
        )

    def test_close_is_reverse_order_bounded_and_continues_after_the_first_refusal(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        persistent = _ReceiverEndpointFactory(service.order, failing=(LATER_QUEUE,))

        direct = _ClosingReceiver("direct", service.order, failing=True)
        with (
            patch(
                "aerial_rescue_broker.messaging.SolacePersistentReceiver", side_effect=persistent
            ),
            patch("aerial_rescue_broker.messaging.SolaceReceiver", return_value=direct),
        ):
            session = receiver_only_session(
                service,
                ReceiverOnlyBindings(FLEET_QUEUES, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
                tracing=FakeTraceContext(),
            )

        # Act
        with pytest.raises(MessagingError) as captured:
            session.close()

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.SHUTDOWN_REFUSED,
                ["direct", LATER_QUEUE, EARLIER_QUEUE, "disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (captured.value.refusal, service.order, lifecycle.state),
        )

    def test_partial_binding_failure_releases_prior_receivers_and_the_service(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        persistent = _ReceiverEndpointFactory(service.order, refuse=LATER_QUEUE)

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.SolacePersistentReceiver", side_effect=persistent
            ),
            pytest.raises(MessagingError) as captured,
        ):
            receiver_only_session(
                service,
                ReceiverOnlyBindings(FLEET_QUEUES, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
                tracing=FakeTraceContext(),
            )

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [EARLIER_QUEUE, "disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (captured.value.refusal, service.order, lifecycle.state),
        )

    def test_open_connects_once_and_returns_only_receiver_capabilities(self) -> None:
        # Arrange
        service = FakeService()

        # Act
        with patch("aerial_rescue_broker.messaging.build_service", return_value=service):
            session = open_receiver_only_session(
                ENDPOINT,
                Principal.RECORDER,
                CREDENTIAL,
                ReceiverOnlyBindings({"audit": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
            )

        # Assert
        self.assertEqual(
            (1, ReceiverOnlySession, ("audit",), False),
            (
                service.connected,
                type(session),
                session.receiver_names,
                hasattr(session, "publisher"),
            ),
        )


class CommandGatewaySessionTests(unittest.TestCase):
    def test_one_service_exposes_only_the_gateway_publish_and_receive_capabilities(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        bindings = CommandGatewayBindings(
            {"approval": QUEUE},
            ("aerial-rescue/v1/*/agent/response/*",),
            DIRECT_RECEIVER_CAPACITY,
        )

        # Act
        session = command_gateway_session(service, bindings)

        # Assert
        self.assertEqual(
            (
                ("approval",),
                [QUEUE],
                [DIRECT_RECEIVER_CAPACITY],
                True,
                True,
                False,
            ),
            (
                session.receiver_names,
                [endpoint.get_name() for endpoint in service.persistent_receiver_builder.endpoints],
                service.receiver_builder.capacities,
                hasattr(session, "direct_publisher"),
                hasattr(session, "publisher"),
                hasattr(session, "requester"),
            ),
        )

    def test_open_connects_once_and_stays_unready_until_recovery_completes(self) -> None:
        # Arrange
        service = FakeService()
        bindings = CommandGatewayBindings({"approval": QUEUE}, (), DIRECT_RECEIVER_CAPACITY)

        # Act
        with patch("aerial_rescue_broker.messaging.build_service", return_value=service):
            session = open_command_gateway_session(
                ENDPOINT,
                Principal.COMMAND_GATEWAY,
                CREDENTIAL,
                bindings,
            )
        before_recovery = session.readiness.is_ready()
        session.rebind_complete()

        # Assert
        self.assertEqual(
            (1, CommandGatewaySession, False, True),
            (service.connected, type(session), before_recovery, session.readiness.is_ready()),
        )


class DashboardSessionTests(unittest.TestCase):
    def test_one_service_exposes_only_confirmed_publish_and_dashboard_ingress(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        bindings = DashboardBindings(
            {"events": QUEUE},
            ("aerial-rescue/v1/*/drone/*/telemetry",),
            DIRECT_RECEIVER_CAPACITY,
        )

        # Act
        session = dashboard_session(service, bindings)

        # Assert
        self.assertEqual(
            (
                ("events",),
                [QUEUE],
                [DIRECT_RECEIVER_CAPACITY],
                True,
                False,
                False,
            ),
            (
                session.receiver_names,
                [endpoint.get_name() for endpoint in service.persistent_receiver_builder.endpoints],
                service.receiver_builder.capacities,
                hasattr(session, "publisher"),
                hasattr(session, "direct_publisher"),
                hasattr(session, "requester"),
            ),
        )

    def test_dashboard_inputs_are_bounded_and_guaranteed_settlement_is_message_bound(
        self,
    ) -> None:
        # Arrange
        direct_message = FakeMessage(b'{"kind":"telemetry"}')
        guaranteed_message = FakeMessage(b'{"kind":"event"}')
        service = FakeService(receiver=FakeReceiver((direct_message,)))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        session = dashboard_session(
            service,
            DashboardBindings(
                {"events": QUEUE},
                ("aerial-rescue/v1/*/drone/*/telemetry",),
                DIRECT_RECEIVER_CAPACITY,
            ),
        )
        service.persistent_receiver_builder.built[0]._scripted.append(guaranteed_message)

        # Act
        guaranteed = cast(GuaranteedMessage, session.receive_guaranteed("events", 125))
        direct = session.receive_direct(250)
        guaranteed.settlement.accept()

        # Assert
        self.assertEqual(
            (
                guaranteed_message,
                direct_message,
                [(guaranteed_message, Outcome.ACCEPTED)],
                [125],
                [250],
            ),
            (
                guaranteed.message,
                direct,
                service.persistent_receiver_builder.built[0].settled,
                service.persistent_receiver_builder.built[0].timeouts,
                service.receiver.timeouts,
            ),
        )

    def test_idle_and_unknown_guaranteed_inputs_have_distinct_outcomes(self) -> None:
        # Arrange
        session = dashboard_session(
            FakeService(),
            DashboardBindings({"events": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
        )

        # Act
        idle = session.receive_guaranteed("events", 5)
        with pytest.raises(MessagingError) as captured:
            session.receive_guaranteed("missing", 5)

        # Assert
        self.assertEqual(
            (None, MessagingRefusal.RECEIVER_NOT_FOUND, "missing"),
            (idle, captured.value.refusal, captured.value.value),
        )

    def test_close_terminates_every_endpoint_before_closing_readiness(self) -> None:
        # Arrange
        service = FakeService()
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        session = dashboard_session(
            service,
            DashboardBindings({"events": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
        )

        # Act
        session.close()

        # Assert
        self.assertEqual(
            (
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                ["receiver-terminate", "disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (
                service.receiver.terminated,
                service.persistent_receiver_builder.built[0].terminated,
                service.publisher.terminated,
                service.order,
                session.readiness.state,
            ),
        )

    def test_direct_binding_failure_releases_publisher_receivers_and_service(self) -> None:
        # Arrange
        service = FakeService(receiver=FakeReceiver((), start_failing=True))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver, order=service.order
        )
        lifecycle = BrokerLifecycle()

        # Act
        with pytest.raises(MessagingError) as captured:
            dashboard_session(
                service,
                DashboardBindings({"events": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
            )

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                ["receiver-terminate", "disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (
                captured.value.refusal,
                service.persistent_receiver_builder.built[0].terminated,
                service.publisher.terminated,
                service.order,
                lifecycle.state,
            ),
        )

    def test_publisher_construction_failure_disconnects_without_partial_endpoints(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        construction_refusal = MessagingError(MessagingRefusal.PUBLISH_REFUSED, "publisher")

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.SolacePublisher",
                side_effect=construction_refusal,
            ),
            pytest.raises(MessagingError) as captured,
        ):
            dashboard_session(
                service,
                DashboardBindings({"events": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
                tracing=FakeTraceContext(),
            )

        # Assert
        self.assertEqual(
            (
                construction_refusal,
                ["disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (captured.value, service.order, lifecycle.state),
        )

    def test_cleanup_failure_preserves_the_dashboard_binding_refusal(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        binding_refusal = MessagingError(MessagingRefusal.BIND_REFUSED, "direct")
        persistent = _ReceiverEndpointFactory(service.order, failing=(QUEUE,))

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.SolacePersistentReceiver",
                side_effect=persistent,
            ),
            patch("aerial_rescue_broker.messaging.SolaceReceiver", side_effect=binding_refusal),
            pytest.raises(MessagingError) as captured,
        ):
            dashboard_session(
                service,
                DashboardBindings({"events": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
                tracing=FakeTraceContext(),
            )

        # Assert
        self.assertEqual(
            (
                binding_refusal,
                [QUEUE, "disconnect"],
                MessagingError,
                BrokerLifecycleState.CLOSED,
            ),
            (
                captured.value,
                service.order,
                type(captured.value.__cause__),
                lifecycle.state,
            ),
        )

    def test_open_connects_once_and_readiness_waits_for_dashboard_recovery(self) -> None:
        # Arrange
        service = FakeService()
        bindings = DashboardBindings({"events": QUEUE}, (), DIRECT_RECEIVER_CAPACITY)

        # Act
        with patch("aerial_rescue_broker.messaging.build_service", return_value=service):
            session = open_dashboard_session(
                ENDPOINT,
                Principal.DASHBOARD_API,
                CREDENTIAL,
                bindings,
            )
        before_recovery = session.readiness.is_ready()
        session.rebind_complete()

        # Assert
        self.assertEqual(
            (1, DashboardSession, False, True),
            (service.connected, type(session), before_recovery, session.readiness.is_ready()),
        )


class MessagingFailureBranchTests(unittest.TestCase):
    """Failure-injection evidence for branches hidden behind the untyped SDK."""

    def test_terminal_lifecycle_ignores_late_recovery_requests(self) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.mark_ready()
        lifecycle.closed()

        # Act
        lifecycle.recovery_required()

        # Assert
        self.assertEqual(
            (BrokerLifecycleState.CLOSED, False), (lifecycle.state, lifecycle.is_ready())
        )

    def test_receiver_only_trace_construction_failure_releases_the_owned_service(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        trace_refusal = MessagingError(MessagingRefusal.TRACE_REFUSED, "CONTEXT_FORM")

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.default_solace_trace_context",
                side_effect=trace_refusal,
            ),
            pytest.raises(MessagingError) as captured,
        ):
            receiver_only_session(
                service,
                ReceiverOnlyBindings({"audit": QUEUE}, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
            )

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.TRACE_REFUSED,
                ["disconnect"],
                BrokerLifecycleState.CLOSED,
            ),
            (captured.value.refusal, service.order, lifecycle.state),
        )

    def test_an_absent_guaranteed_payload_remains_unsettled_for_a_durable_refusal(self) -> None:
        # Arrange
        message = _PayloadlessMessage()
        service = FakeService()
        service.persistent_receiver = FakePersistentReceiver((message,))
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(
            service.persistent_receiver
        )
        tracing = FakeTraceContext()
        receiver = SolacePersistentReceiver(service, QUEUE, tracing=tracing)

        # Act
        with pytest.raises(UnsettledMessageError) as captured:
            receiver.receive(10)
        before_durable_refusal = list(service.persistent_receiver.settled)
        captured.value.settlement.fail()

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.TRACE_REFUSED,
                "PAYLOAD_ABSENT",
                [],
                [],
                [(message, Outcome.FAILED)],
            ),
            (
                captured.value.refusal,
                captured.value.value,
                tracing.inbound,
                before_durable_refusal,
                service.persistent_receiver.settled,
            ),
        )

    def test_endpoint_construction_does_not_claim_sdk_readiness_that_is_absent(self) -> None:
        # Arrange
        persistent_service = FakeService()
        direct_service = FakeService()
        request_service = FakeService()
        lifecycles = tuple(BrokerLifecycle() for _ in range(3))
        for lifecycle in lifecycles:
            lifecycle.connected()

        # Act
        with (
            patch.object(persistent_service.publisher, "is_ready", return_value=False),
            patch.object(direct_service.direct_publisher, "is_ready", return_value=False),
            patch.object(request_service.request_reply_publisher, "is_ready", return_value=False),
        ):
            SolacePublisher(persistent_service, lifecycle=lifecycles[0])
            SolaceDirectPublisher(direct_service, lifecycle=lifecycles[1])
            SolaceRequestReplyRequester(request_service, lifecycle=lifecycles[2])

        # Assert
        self.assertEqual(
            ([False, False, False], [1, 1, 1]),
            (
                [lifecycle.is_ready() for lifecycle in lifecycles],
                [
                    persistent_service.publisher.started,
                    direct_service.direct_publisher.started,
                    request_service.request_reply_publisher.started,
                ],
            ),
        )

    def test_request_ready_notification_failure_keeps_the_original_refusal(self) -> None:
        # Arrange
        publisher = FakeRequestReplyPublisher(
            FakeMessage(),
            failure=PublisherOverflowError,
            notify_failing=True,
        )
        requester = SolaceRequestReplyRequester(FakeService(request_reply_publisher=publisher))

        # Act
        with pytest.raises(MessagingError) as captured:
            requester.request("aerial-rescue/v1/m-1/gateway/request/coordinate", b"{}", {}, 25)

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, PubSubPlusClientError, 1),
            (
                captured.value.refusal,
                type(captured.value.__cause__),
                publisher.readiness_notifications,
            ),
        )

    def test_direct_ready_notification_failure_keeps_the_original_refusal(self) -> None:
        # Arrange
        service = FakeService(
            direct_failure=PublisherOverflowError,
        )
        service.direct_publisher.refuse_ready_notification()
        publisher = SolaceDirectPublisher(service)

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish_unacknowledged("aerial-rescue/v1/m-1/drone/d-1/telemetry", b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, PubSubPlusClientError, 1),
            (
                captured.value.refusal,
                type(captured.value.__cause__),
                service.direct_publisher.readiness_notifications,
            ),
        )

    def test_direct_bind_cleanup_failure_is_still_one_owned_refusal(self) -> None:
        # Arrange
        receiver = FakeReceiver((), start_failing=True, terminate_failing=True)
        service = FakeService(receiver=receiver)

        # Act
        with pytest.raises(MessagingError) as captured:
            SolaceReceiver(service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY)

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                PubSubPlusClientError,
            ),
            (captured.value.refusal, receiver.terminated, type(captured.value.__cause__)),
        )

    def test_persistent_bind_cleanup_failure_is_still_one_owned_refusal(self) -> None:
        # Arrange
        receiver = FakePersistentReceiver((), unbindable=True, terminate_failing=True)
        service = FakeService()
        service.persistent_receiver = receiver
        service.persistent_receiver_builder = FakePersistentReceiverBuilder(receiver)

        # Act
        with pytest.raises(MessagingError) as captured:
            SolacePersistentReceiver(service, QUEUE)

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [SHUTDOWN_GRACE_PERIOD_MILLISECONDS],
                PubSubPlusClientError,
            ),
            (captured.value.refusal, receiver.terminated, type(captured.value.__cause__)),
        )

    def test_invalid_direct_metric_types_and_values_fail_closed(self) -> None:
        # Arrange
        services = (FakeService(), FakeService())
        services[0].api_metrics = FakeMetrics((True,))
        services[1].api_metrics = FakeMetrics((-1,))

        # Act
        refusals = []
        for service in services:
            with pytest.raises(MessagingError) as captured:
                SolaceReceiver(service, (), buffer_capacity=DIRECT_RECEIVER_CAPACITY)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([MessagingRefusal.METRICS_REFUSED] * 2, refusals)

    def test_mixed_session_cleans_up_when_each_early_constructor_refuses(self) -> None:
        # Arrange
        direct_service = FakeService()
        persistent_service = FakeService()
        direct = _ClosingReceiver("direct", persistent_service.order)
        construction_refusal = MessagingError(MessagingRefusal.BIND_REFUSED, "endpoint")

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.build_service",
                side_effect=(direct_service, persistent_service),
            ),
            patch(
                "aerial_rescue_broker.messaging.SolaceDirectPublisher",
                side_effect=(construction_refusal, direct),
            ),
            patch(
                "aerial_rescue_broker.messaging.SolacePublisher",
                side_effect=construction_refusal,
            ),
        ):
            captured: list[MessagingError] = []
            for _ in range(2):
                with pytest.raises(MessagingError) as raised:
                    open_session(
                        ENDPOINT,
                        Principal.COMMAND_GATEWAY,
                        CREDENTIAL,
                        (),
                        direct_receiver_capacity=DIRECT_RECEIVER_CAPACITY,
                    )
                captured.append(raised.value)

        # Assert
        self.assertEqual(
            (
                [MessagingRefusal.BIND_REFUSED] * 2,
                ["disconnect"],
                ["direct", "disconnect"],
            ),
            (
                [error.refusal for error in captured],
                direct_service.order,
                persistent_service.order,
            ),
        )

    def test_mixed_session_cleanup_failure_does_not_skip_disconnect(self) -> None:
        # Arrange
        service = FakeService(
            receiver=FakeReceiver((), start_failing=True),
            direct_terminate_failing=True,
        )

        # Act
        with (
            patch("aerial_rescue_broker.messaging.build_service", return_value=service),
            pytest.raises(MessagingError) as captured,
        ):
            open_session(
                ENDPOINT,
                Principal.COMMAND_GATEWAY,
                CREDENTIAL,
                (),
                direct_receiver_capacity=DIRECT_RECEIVER_CAPACITY,
            )

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                ["terminate", "disconnect"],
                MessagingError,
            ),
            (captured.value.refusal, service.order, type(captured.value.__cause__)),
        )

    def test_receiver_only_cleanup_failure_does_not_hide_the_binding_refusal(self) -> None:
        # Arrange
        service = FakeService()
        lifecycle = BrokerLifecycle()
        persistent = _ReceiverEndpointFactory(
            service.order,
            refuse=LATER_QUEUE,
            failing=(EARLIER_QUEUE,),
        )

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.SolacePersistentReceiver",
                side_effect=persistent,
            ),
            pytest.raises(MessagingError) as captured,
        ):
            receiver_only_session(
                service,
                ReceiverOnlyBindings(FLEET_QUEUES, (), DIRECT_RECEIVER_CAPACITY),
                lifecycle=lifecycle,
                tracing=FakeTraceContext(),
            )

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [EARLIER_QUEUE, "disconnect"],
                MessagingError,
                BrokerLifecycleState.CLOSED,
            ),
            (
                captured.value.refusal,
                service.order,
                type(captured.value.__cause__),
                lifecycle.state,
            ),
        )

    def test_fleet_cleanup_failure_does_not_hide_the_binding_refusal(self) -> None:
        # Arrange
        service = FakeService()
        persistent = _ReceiverEndpointFactory(
            service.order,
            refuse=LATER_QUEUE,
            failing=(EARLIER_QUEUE,),
        )

        # Act
        with (
            patch(
                "aerial_rescue_broker.messaging.SolacePersistentReceiver",
                side_effect=persistent,
            ),
            pytest.raises(MessagingError) as captured,
        ):
            fleet_session(service, FLEET_QUEUES)

        # Assert
        self.assertEqual(
            (
                MessagingRefusal.BIND_REFUSED,
                [EARLIER_QUEUE, "terminate", "disconnect"],
                MessagingError,
            ),
            (captured.value.refusal, service.order, type(captured.value.__cause__)),
        )
