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

import unittest
from collections.abc import Collection, Mapping, Sequence
from tempfile import TemporaryDirectory
from typing import Final

import pytest
from aerial_rescue_broker.messaging import (
    DIRECT_BUFFER_CAPACITY,
    PUBLISH_TIMEOUT_MILLISECONDS,
    REQUIRED_OUTCOMES,
    BrokerEndpoint,
    ConsumingSession,
    MessagingError,
    MessagingRefusal,
    PublishingSession,
    SolaceDirectPublisher,
    SolacePersistentReceiver,
    SolacePublisher,
    SolaceReceiver,
    build_service,
    connection_properties,
    fleet_session,
)
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome
from solace.messaging.config.solace_properties import (
    authentication_properties as authentication,
)
from solace.messaging.config.solace_properties import (
    transport_layer_properties as transport,
)
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError
from solace.messaging.resources.queue import Queue as SolaceQueue
from solace.messaging.resources.topic import Topic as SolaceTopic

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


class FakeMessages:
    """The client's outbound message builder, recording what it was asked to build."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.built: list[tuple[object, Mapping[str, object]]] = []

    def build(self, payload: object, additional_message_properties: Mapping[str, object]) -> object:
        """Record one build and return a stand-in for the message."""
        self.built.append((payload, additional_message_properties))
        return ("message", payload)


class FakePublisher:
    """The client's persistent publisher, recording its lifecycle and publications."""

    def __init__(self, failing: bool = False) -> None:
        """Record whether this publisher reports a client failure."""
        self.started = 0
        self.terminated = 0
        self.published: list[tuple[object, object, int]] = []
        self._failing = failing

    def start(self) -> None:
        """Record that the publisher was started."""
        self.started += 1

    def publish_await_acknowledgement(
        self, message: object, destination: object, time_out: int
    ) -> None:
        """Record one publication, or raise the way the client does."""
        if self._failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        self.published.append((message, destination, time_out))

    def terminate(self) -> None:
        """Record that the publisher was terminated."""
        self.terminated += 1


class FakeReceiver:
    """The client's direct receiver, answering from a scripted list."""

    def __init__(self, scripted: Sequence[object]) -> None:
        """Record what this receiver will yield, in order."""
        self.started = 0
        self.terminated = 0
        self.timeouts: list[int] = []
        self._scripted = list(scripted)

    def start(self) -> None:
        """Record that the receiver was started."""
        self.started += 1

    def receive_message(self, timeout: int) -> object:
        """Return the next scripted message, or ``None`` when the script is exhausted."""
        self.timeouts.append(timeout)
        return self._scripted.pop(0) if self._scripted else None

    def terminate(self) -> None:
        """Record that the receiver was terminated."""
        self.terminated += 1


class FakeReceiverBuilder:
    """The client's receiver builder, recording the subscriptions it was given."""

    def __init__(self, receiver: FakeReceiver) -> None:
        """Record which receiver this builder yields."""
        self.subscriptions: list[object] = []
        self._receiver = receiver

    def with_subscriptions(self, subscriptions: Sequence[object]) -> FakeReceiverBuilder:
        """Record the subscriptions and return self, the way the client's builder does."""
        self.subscriptions.extend(subscriptions)
        return self

    def build(self) -> FakeReceiver:
        """Return the receiver."""
        return self._receiver


class FakeDirectPublisher:
    """The client's direct publisher, recording its lifecycle and publications."""

    def __init__(self, order: list[str], failing: bool = False) -> None:
        """Record the shared order log and whether this publisher reports a failure."""
        self.started = 0
        self.terminated = 0
        self.published: list[tuple[object, SolaceTopic, Mapping[str, object] | None]] = []
        self._order = order
        self._failing = failing

    def start(self) -> None:
        """Record that the publisher was started."""
        self.started += 1

    def publish(
        self,
        message: object,
        destination: SolaceTopic,
        additional_message_properties: Mapping[str, object] | None = None,
    ) -> None:
        """Record one publication, or raise the way the client does."""
        if self._failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        self.published.append((message, destination, additional_message_properties))

    def terminate(self) -> None:
        """Record that the publisher was terminated, and when."""
        self.terminated += 1
        self._order.append("terminate")


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


class FakePersistentReceiver:
    """The client's persistent receiver, recording every settlement it was asked for."""

    def __init__(
        self,
        scripted: Sequence[object],
        failing: bool = False,
        unbindable: bool = False,
        order: list[str] | None = None,
    ) -> None:
        """Record what this receiver yields, and whether binding or settling refuses."""
        self.started = 0
        self.terminated = 0
        self.timeouts: list[int] = []
        self.settled: list[tuple[object, Outcome]] = []
        self._scripted = list(scripted)
        self._failing = failing
        self._unbindable = unbindable
        self._order = order

    def start(self) -> None:
        """Record the start, or raise the way the client does for a refused binding."""
        if self._unbindable:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        self.started += 1

    def receive_message(self, timeout: int) -> object:
        """Return the next scripted message, or ``None`` when the script is exhausted."""
        self.timeouts.append(timeout)
        return self._scripted.pop(0) if self._scripted else None

    def settle(self, message: object, outcome: Outcome) -> None:
        """Record one settlement, or raise the way the client does when it cannot send one."""
        if self._failing:
            raise PubSubPlusClientError(CLIENT_FAILURE)
        self.settled.append((message, outcome))

    def terminate(self) -> None:
        """Record that the receiver was terminated, and when if a shared order was given."""
        self.terminated += 1
        if self._order is not None:
            self._order.append("receiver-terminate")


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
        self._receiver = receiver
        self._order = order
        self._unbindable = frozenset(unbindable)

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
            self.built.append(self._receiver)
            return self._receiver
        minted = FakePersistentReceiver((), unbindable=name in self._unbindable, order=self._order)
        self.built.append(minted)
        return minted


class FakeBuilder:
    """A builder whose ``build`` returns one fixed object."""

    def __init__(self, built: object) -> None:
        """Record what this builder yields."""
        self._built = built

    def build(self) -> object:
        """Return the object."""
        return self._built


class FakeService:
    """Enough of the client's messaging service for the adapter to be exercised."""

    def __init__(
        self,
        publisher: FakePublisher | None = None,
        receiver: FakeReceiver | None = None,
        direct_failing: bool = False,
    ) -> None:
        """Record the publisher and receiver this service hands out."""
        self.messages = FakeMessages()
        self.order: list[str] = []
        self.publisher = publisher or FakePublisher()
        self.receiver = receiver or FakeReceiver(())
        self.receiver_builder = FakeReceiverBuilder(self.receiver)
        self.direct_publisher = FakeDirectPublisher(self.order, failing=direct_failing)
        self.direct_publisher_builder = FakeDirectPublisherBuilder(self.direct_publisher)
        self.persistent_receiver = FakePersistentReceiver(())
        self.persistent_receiver_builder = FakePersistentReceiverBuilder(self.persistent_receiver)

    def message_builder(self) -> FakeMessages:
        """Return the outbound message builder."""
        return self.messages

    def create_persistent_message_publisher_builder(self) -> FakeBuilder:
        """Return a builder for the persistent publisher."""
        return FakeBuilder(self.publisher)

    def create_direct_message_publisher_builder(self) -> FakeDirectPublisherBuilder:
        """Return a builder for the direct publisher."""
        return self.direct_publisher_builder

    def create_direct_message_receiver_builder(self) -> FakeReceiverBuilder:
        """Return a builder for the direct receiver."""
        return self.receiver_builder

    def create_persistent_message_receiver_builder(self) -> FakePersistentReceiverBuilder:
        """Return a builder for the persistent receiver."""
        return self.persistent_receiver_builder

    def disconnect(self) -> None:
        """Record that the service was disconnected, and when."""
        self.order.append("disconnect")


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

    def test_neither_retry_count_lets_a_refused_client_loop_forever(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        properties = connection_properties(ENDPOINT, role, CREDENTIAL)

        # Assert
        self.assertEqual(
            (0, 0),
            (
                properties[transport.CONNECTION_RETRIES],
                properties[transport.RECONNECTION_ATTEMPTS],
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


class SolacePublisherTests(unittest.TestCase):
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
            [(bytearray(b"{}"), {"k": "v"})],
            [(payload, dict(properties)) for payload, properties in service.messages.built],
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

    def test_a_client_failure_becomes_one_owned_refusal(self) -> None:
        # Arrange
        service = FakeService(publisher=FakePublisher(failing=True))
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

    def test_closing_terminates_the_publisher_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolacePublisher(service)

        # Act
        publisher.close()

        # Assert
        self.assertEqual(1, service.publisher.terminated)


class SolaceReceiverTests(unittest.TestCase):
    def test_every_subscription_reaches_the_builder_and_the_receiver_starts(self) -> None:
        # Arrange
        service = FakeService()
        patterns = ("aerial-rescue/v1/*/gateway/request/*", "aerial-rescue/v1/*/audit/*")

        # Act
        SolaceReceiver(service, patterns)

        # Assert
        self.assertEqual(
            (2, 1), (len(service.receiver_builder.subscriptions), service.receiver.started)
        )

    def test_receiving_passes_the_window_through_and_yields_the_message(self) -> None:
        # Arrange
        service = FakeService(receiver=FakeReceiver(("first",)))
        receiver = SolaceReceiver(service, ())

        # Act
        received = receiver.receive(1000)

        # Assert
        self.assertEqual(("first", [1000]), (received, service.receiver.timeouts))

    def test_an_empty_window_yields_none_rather_than_blocking(self) -> None:
        # Arrange
        service = FakeService(receiver=FakeReceiver(()))
        receiver = SolaceReceiver(service, ())

        # Act
        received = receiver.receive(1000)

        # Assert
        self.assertIsNone(received)

    def test_closing_terminates_the_receiver_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        receiver = SolaceReceiver(service, ())

        # Act
        receiver.close()

        # Assert
        self.assertEqual(1, service.receiver.terminated)


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
            [(bytearray(b"{}"), topic, {"k": "v"})],
            [
                (message, destination.get_name(), dict(properties or {}))
                for message, destination, properties in service.direct_publisher.published
            ],
        )

    def test_nothing_is_built_through_the_outbound_message_builder(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolaceDirectPublisher(service)

        # Act
        publisher.publish_unacknowledged("aerial-rescue/v1/m-1/drone/d-1/telemetry", b"{}", {})

        # Assert
        self.assertEqual([], service.messages.built)

    def test_a_client_failure_becomes_one_owned_refusal(self) -> None:
        # Arrange
        service = FakeService(direct_failing=True)
        publisher = SolaceDirectPublisher(service)
        topic = "aerial-rescue/v1/m-1/drone/d-1/telemetry"

        # Act
        with pytest.raises(MessagingError) as captured:
            publisher.publish_unacknowledged(topic, b"{}", {})

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, topic),
            (captured.value.refusal, captured.value.value),
        )

    def test_closing_terminates_the_publisher_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        publisher = SolaceDirectPublisher(service)

        # Act
        publisher.close()

        # Assert
        self.assertEqual(1, service.direct_publisher.terminated)


class PublishingSessionTests(unittest.TestCase):
    def test_closing_terminates_the_publisher_before_disconnecting(self) -> None:
        # Arrange
        service = FakeService()
        session = PublishingSession(SolaceDirectPublisher(service), service)

        # Act
        session.close()

        # Assert
        self.assertEqual(["terminate", "disconnect"], service.order)


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
            (["receiver-terminate", "terminate", "disconnect"], 1),
            (service.order, service.publisher.terminated),
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
            (MessagingRefusal.BIND_REFUSED, QUEUE),
            (raised.value.refusal, raised.value.value),
        )

    def test_closing_terminates_the_receiver_rather_than_leaving_it_collected(self) -> None:
        # Arrange
        service = FakeService()
        receiver = SolacePersistentReceiver(service, QUEUE)

        # Act
        receiver.close()

        # Assert
        self.assertEqual(1, service.persistent_receiver.terminated)


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
            (1, ["disconnect"]), (service.persistent_receiver.terminated, service.order)
        )
