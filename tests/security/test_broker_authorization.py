"""Whether the broker itself refuses what the authorization matrix says it must.

The tables in ``packages/domain`` and their projection in ``packages/broker`` are proven
offline against a fake. That is evidence about a plan, not about a broker. These probes are
the other kind: they open real connections to the container in ``deploy/compose.yaml`` and
try to publish, so what is asserted is the broker's answer rather than the project's
intention (``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``).

Catalogue cases B17, B18, and B19 live here. Every one of them is a denial, and a denial
proves nothing on its own: a broker that refused everybody would pass all three. The
positive control is therefore part of the same evidence -- the command gateway publishing
the very topic every other role is refused -- and so is the case that the factory identity
can no longer connect at all, because while it could, every denial below was one connection
away from being bypassed.

They carry the ``security``, ``docker``, and ``broker`` markers, so no blocking suite runs
them (``docs/TESTING.md``). The pushed stages stay runnable with no daemon and no broker.
"""

from __future__ import annotations

import unittest
from enum import Enum
from pathlib import Path

import pytest
from aerial_rescue_broker.deployment import credential_path
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.solace_properties import (
    authentication_properties as auth,
)
from solace.messaging.config.solace_properties import (
    service_properties as service_property,
)
from solace.messaging.config.solace_properties import (
    transport_layer_properties as transport,
)
from solace.messaging.config.transport_security_strategy import TLS
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.topic import Topic as SolaceTopic

pytestmark = [pytest.mark.security, pytest.mark.docker, pytest.mark.broker]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPOSITORY_ROOT / "deploy"
TRUST_STORE = DEPLOY / "certs"
BROKER_URL = "tcps://localhost:55443"
VPN = "default"
ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS = 5000

MISSION = "m-1"
DRONE_COMMAND = format_topic(
    Topic(Family.DRONE_COMMAND, MISSION, {"droneId": "d-1", "commandType": "escalate-rescue"})
)
GATEWAY_REQUEST = format_topic(
    Topic(Family.GATEWAY_REQUEST, MISSION, {"operation": "propose-escalation"})
)
DRONE_TELEMETRY = format_topic(Topic(Family.DRONE_TELEMETRY, MISSION, {"droneId": "d-1"}))


class Outcome(Enum):
    """What the broker did with one connect-and-publish attempt."""

    PUBLISHED = "the identity connected and the broker accepted the publish"
    PUBLISH_DENIED = "the identity connected and the broker refused the publish"
    CONNECT_DENIED = "the broker refused the connection"


def _service(username: str, credential: str) -> MessagingService:
    """Return a service bound to the container, validating the per-checkout authority."""
    properties = {
        transport.HOST: BROKER_URL,
        service_property.VPN_NAME: VPN,
        auth.SCHEME_BASIC_USER_NAME: username,
        auth.SCHEME_BASIC_PASSWORD: credential,
        transport.CONNECTION_RETRIES: 0,
        transport.RECONNECTION_ATTEMPTS: 0,
    }
    return (
        MessagingService.builder()
        .from_properties(properties)
        .with_transport_security_strategy(
            TLS.create().with_certificate_validation(
                True, validate_server_name=True, trust_store_file_path=str(TRUST_STORE)
            )
        )
        .build()
    )


def _attempt(username: str, credential: str, topic: str) -> Outcome:
    """Connect as ``username`` and try one guaranteed publish to ``topic``.

    Guaranteed rather than direct delivery, because a direct publish the broker discards
    looks the same to the client as one it delivered; only an acknowledged publish makes
    the broker's answer observable.

    Only ``PubSubPlusClientError`` is caught. Catching every exception would make a defect
    in this helper indistinguishable from a denial, and every test below asserts a denial,
    so a broad catch would turn a broken probe into ten passing security tests.
    """
    service = _service(username, credential)
    try:
        service.connect()
    except PubSubPlusClientError:
        return Outcome.CONNECT_DENIED
    try:
        publisher = service.create_persistent_message_publisher_builder().build()
        publisher.start()
        message = service.message_builder().build("authorization probe")
        publisher.publish_await_acknowledgement(
            message, SolaceTopic.of(topic), ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS
        )
    except PubSubPlusClientError:
        return Outcome.PUBLISH_DENIED
    else:
        return Outcome.PUBLISHED
    finally:
        service.disconnect()


def _credential(role: Principal) -> str:
    """Return the credential the generator wrote for ``role``."""
    return credential_path(DEPLOY, role).read_text(encoding="utf-8").strip()


def _publish_as(role: Principal, topic: str) -> Outcome:
    """Return what the broker does when ``role`` publishes ``topic``."""
    return _attempt(role.value, _credential(role), topic)


class PositiveControlTests(unittest.TestCase):
    def test_the_command_gateway_may_publish_an_executable_drone_command(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        outcome = _publish_as(role, DRONE_COMMAND)

        # Assert
        self.assertIs(Outcome.PUBLISHED, outcome)

    def test_the_fleet_simulator_may_publish_its_own_telemetry(self) -> None:
        # Arrange
        role = Principal.FLEET_SIMULATOR

        # Act
        outcome = _publish_as(role, DRONE_TELEMETRY)

        # Assert
        self.assertIs(Outcome.PUBLISHED, outcome)

    def test_the_event_mesh_tool_may_publish_a_gateway_request(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_TOOL

        # Act
        outcome = _publish_as(role, GATEWAY_REQUEST)

        # Assert
        self.assertIs(Outcome.PUBLISHED, outcome)


class DenialTests(unittest.TestCase):
    def test_b17_an_edge_agent_identity_is_denied_an_executable_command_topic(self) -> None:
        # Arrange
        role = Principal.AGENT_MESH_AGENT

        # Act
        outcome = _publish_as(role, DRONE_COMMAND)

        # Assert
        self.assertIs(Outcome.PUBLISH_DENIED, outcome)

    def test_b18_the_event_mesh_tool_identity_is_denied_a_command_topic(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_TOOL

        # Act
        outcome = _publish_as(role, DRONE_COMMAND)

        # Assert
        self.assertIs(Outcome.PUBLISH_DENIED, outcome)

    def test_b19_the_recorder_and_dashboard_identities_are_denied_a_command_topic(self) -> None:
        # Arrange
        roles = (Principal.RECORDER, Principal.DASHBOARD_API)

        # Act
        outcomes = tuple(_publish_as(role, DRONE_COMMAND) for role in roles)

        # Assert
        self.assertEqual((Outcome.PUBLISH_DENIED, Outcome.PUBLISH_DENIED), outcomes)

    def test_the_recorder_may_publish_nothing_at_all(self) -> None:
        # Arrange
        topics = (DRONE_TELEMETRY, GATEWAY_REQUEST, DRONE_COMMAND)

        # Act
        outcomes = tuple(_publish_as(Principal.RECORDER, topic) for topic in topics)

        # Assert
        self.assertEqual(tuple(Outcome.PUBLISH_DENIED for _ in topics), outcomes)

    def test_every_role_but_the_command_gateway_is_denied_the_drone_command_family(self) -> None:
        # Arrange
        roles = tuple(role for role in Principal if role is not Principal.COMMAND_GATEWAY)

        # Act
        outcomes = tuple(_publish_as(role, DRONE_COMMAND) for role in roles)

        # Assert
        self.assertEqual(tuple(Outcome.PUBLISH_DENIED for _ in roles), outcomes)


class FactoryIdentityTests(unittest.TestCase):
    def test_the_factory_client_username_can_no_longer_connect(self) -> None:
        # Arrange
        username = "default"

        # Act
        outcome = _attempt(username, "", DRONE_COMMAND)

        # Assert
        self.assertIs(Outcome.CONNECT_DENIED, outcome)

    def test_an_identity_outside_the_matrix_can_no_longer_connect(self) -> None:
        # Arrange
        username = "not-a-real-role"

        # Act
        outcome = _attempt(username, "fixture-not-a-real-credential", DRONE_COMMAND)

        # Assert
        self.assertIs(Outcome.CONNECT_DENIED, outcome)


if __name__ == "__main__":
    unittest.main()
