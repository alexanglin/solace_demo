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
from collections.abc import Mapping, Sequence
from tempfile import TemporaryDirectory
from typing import Final

import pytest
from aerial_rescue_broker.messaging import (
    PUBLISH_TIMEOUT_MILLISECONDS,
    BrokerEndpoint,
    MessagingError,
    MessagingRefusal,
    SolacePublisher,
    SolaceReceiver,
    build_service,
    connection_properties,
)
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.solace_properties import (
    authentication_properties as authentication,
)
from solace.messaging.config.solace_properties import (
    transport_layer_properties as transport,
)
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError

ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn="default", trust_store="deploy/certs"
)
CREDENTIAL: Final = "not-a-real-credential"
CLIENT_FAILURE: Final = "the broker refused the publication"


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
        self, publisher: FakePublisher | None = None, receiver: FakeReceiver | None = None
    ) -> None:
        """Record the publisher and receiver this service hands out."""
        self.messages = FakeMessages()
        self.publisher = publisher or FakePublisher()
        self.receiver = receiver or FakeReceiver(())
        self.receiver_builder = FakeReceiverBuilder(self.receiver)

    def message_builder(self) -> FakeMessages:
        """Return the outbound message builder."""
        return self.messages

    def create_persistent_message_publisher_builder(self) -> FakeBuilder:
        """Return a builder for the persistent publisher."""
        return FakeBuilder(self.publisher)

    def create_direct_message_receiver_builder(self) -> FakeReceiverBuilder:
        """Return a builder for the direct receiver."""
        return self.receiver_builder


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


if __name__ == "__main__":
    unittest.main()
