"""Whether the broker itself refuses what the authorization matrix says it must.

The tables in ``packages/domain`` and their projection in ``packages/broker`` are proven
offline against a fake. That is evidence about a plan, not about a broker. These probes are
the other kind: they open real connections to the container in ``deploy/compose.yaml`` and
try to publish or subscribe, so what is asserted is the broker's answer rather than the
project's intention
(``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``).

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

import pytest
from aerial_rescue_broker.monitor_console import MONITOR_CREDENTIAL
from aerial_rescue_broker.monitoring import MONITOR_USERNAME, ReadOnlySempMonitor
from aerial_rescue_broker.provisioning import (
    Method,
    Request,
    queue_monitor_collection_path,
    queue_tx_flow_monitor_path,
)
from aerial_rescue_broker.semp import SempEndpoint, SempError, SempFailure, SempSession, connect
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_domain.principals import Principal
from solace.messaging.errors.pubsubplus_client_error import (
    MessageDestinationDoesNotExistError,
    PubSubPlusClientError,
)
from solace.messaging.resources.topic import Topic as SolaceTopic
from solace.messaging.resources.topic_subscription import TopicSubscription

from tests.broker_live_support import DEPLOY_ROOT as DEPLOY
from tests.broker_live_support import LOCAL_BROKER_ENDPOINT, role_credential
from tests.broker_live_support import native_service as _service

pytestmark = [pytest.mark.security, pytest.mark.docker, pytest.mark.broker]

TRUST_STORE = DEPLOY / "certs"
VPN = LOCAL_BROKER_ENDPOINT.vpn
ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS = 5000
SEMP_PORT = 1943

MISSION = "m-1"
DRONE_COMMAND = format_topic(
    Topic(Family.DRONE_COMMAND, MISSION, {"droneId": "d-1", "commandType": "escalate-rescue"})
)
GATEWAY_REQUEST = format_topic(
    Topic(Family.GATEWAY_REQUEST, MISSION, {"operation": "propose-escalation"})
)
DRONE_TELEMETRY = format_topic(Topic(Family.DRONE_TELEMETRY, MISSION, {"droneId": "d-1"}))
A2A_REQUEST = "aerial-rescue-mesh/a2a/v1/agent/request/mission-coordinator"


def _lifecycle_topic(family_name: str, parameters: dict[str, str]) -> str:
    """Return one lifecycle topic, or a red-phase sentinel while its family is absent."""
    family = Family.__members__.get(family_name)
    if family is None:
        return f"missing-family/{family_name}"
    return format_topic(Topic(family, MISSION, parameters))


CONNECTIVITY_LIFECYCLE = format_topic(
    Topic(
        Family.DRONE_EVENT,
        MISSION,
        {"droneId": "d-1", "eventType": "connectivity-changed"},
    )
)
SALIENT_DRONE_EVENT = format_topic(
    Topic(
        Family.DRONE_EVENT,
        MISSION,
        {"droneId": "d-1", "eventType": "salient"},
    )
)
MISSION_LIFECYCLE = _lifecycle_topic("MISSION_EVENT", {"eventType": "lifecycle"})
SECTOR_LIFECYCLE = _lifecycle_topic(
    "SECTOR_EVENT", {"sectorId": "sector-01", "eventType": "lifecycle"}
)


class Outcome(Enum):
    """What the broker did with one connect-and-publish attempt."""

    PUBLISHED = "the identity connected and the broker accepted the publish"
    PUBLISH_DENIED = "the identity connected and the broker refused the publish"
    PUBLISH_UNMATCHED = (
        "the identity connected, the broker authorized the publish, and no Guaranteed subscription"
        " matched"
    )
    SUBSCRIBED = "the identity connected and the broker accepted the subscription"
    SUBSCRIBE_DENIED = "the identity connected and the broker refused the subscription"
    CONNECT_DENIED = "the broker refused the connection"


def _attempt(username: str, credential: str, topic: str) -> Outcome:
    """Connect as ``username`` and try one guaranteed publish to ``topic``.

    Guaranteed rather than direct delivery, because a direct publish the broker discards
    looks the same to the client as one it delivered; only an acknowledged publish makes
    the broker's answer observable.

    A project application profile rejects a Guaranteed send that matches no Guaranteed
    subscription (ADR-0153), and the broker checks the ACL before it matches: the SDK raises
    ``MessageRejectedByBrokerError`` for an ACL denial and
    ``MessageDestinationDoesNotExistError`` for a no-match rejection. The second therefore
    proves authorization for a Direct family no queue subscribes, and is reported as
    :attr:`Outcome.PUBLISH_UNMATCHED` rather than as a denial.

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
    except MessageDestinationDoesNotExistError:
        return Outcome.PUBLISH_UNMATCHED
    except PubSubPlusClientError:
        return Outcome.PUBLISH_DENIED
    else:
        return Outcome.PUBLISHED
    finally:
        service.disconnect()


def _publish_as(role: Principal, topic: str) -> Outcome:
    """Return what the broker does when ``role`` publishes ``topic``."""
    return _attempt(role.value, role_credential(role), topic)


def _subscribe_as(role: Principal, topic: str) -> Outcome:
    """Connect as ``role`` and return the broker's answer to one direct subscription."""
    service = _service(role.value, role_credential(role))
    try:
        service.connect()
    except PubSubPlusClientError:
        return Outcome.CONNECT_DENIED
    receiver = None
    started = False
    try:
        receiver = (
            service.create_direct_message_receiver_builder()
            .with_subscriptions([TopicSubscription.of(topic)])
            .build()
        )
        receiver.start()
        started = True
    except PubSubPlusClientError:
        return Outcome.SUBSCRIBE_DENIED
    else:
        return Outcome.SUBSCRIBED
    finally:
        if started and receiver is not None:
            receiver.terminate()
        service.disconnect()


def _monitor_endpoint() -> SempEndpoint:
    """Return the dedicated VPN-scoped SEMP identity from generated material."""
    credential = (DEPLOY / MONITOR_CREDENTIAL).read_text(encoding="utf-8").strip()
    return SempEndpoint(
        "localhost",
        SEMP_PORT,
        MONITOR_USERNAME,
        credential,
        str(TRUST_STORE / "ca.pem"),
    )


class PositiveControlTests(unittest.TestCase):
    def test_the_command_gateway_may_publish_an_executable_drone_command(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        outcome = _publish_as(role, DRONE_COMMAND)

        # Assert
        self.assertIs(Outcome.PUBLISHED, outcome)

    def test_the_fleet_simulator_may_publish_its_own_telemetry_though_no_queue_subscribes_it(
        self,
    ) -> None:
        # Arrange
        role = Principal.FLEET_SIMULATOR

        # Act
        outcome = _publish_as(role, DRONE_TELEMETRY)

        # Assert
        self.assertIs(Outcome.PUBLISH_UNMATCHED, outcome)

    def test_the_event_mesh_tool_may_publish_a_gateway_request(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_TOOL

        # Act
        outcome = _publish_as(role, GATEWAY_REQUEST)

        # Assert
        self.assertIs(Outcome.PUBLISHED, outcome)

    def test_the_dashboard_api_may_publish_authoritative_mission_lifecycle(self) -> None:
        # Arrange
        role = Principal.DASHBOARD_API

        # Act
        outcome = _publish_as(role, MISSION_LIFECYCLE)

        # Assert
        self.assertIs(Outcome.PUBLISHED, outcome)

    def test_the_fleet_simulator_may_publish_connectivity_and_sector_lifecycle(self) -> None:
        # Arrange
        topics = (CONNECTIVITY_LIFECYCLE, SECTOR_LIFECYCLE)

        # Act
        outcomes = tuple(_publish_as(Principal.FLEET_SIMULATOR, topic) for topic in topics)

        # Assert
        self.assertEqual(tuple(Outcome.PUBLISHED for _ in topics), outcomes)


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
        roles = tuple(
            role
            for role in Principal
            if role not in {Principal.COMMAND_GATEWAY, Principal.DISCOVERY}
        )

        # Act
        outcomes = tuple(_publish_as(role, DRONE_COMMAND) for role in roles)

        # Assert
        self.assertEqual(tuple(Outcome.PUBLISH_DENIED for _ in roles), outcomes)

    def test_the_dashboard_api_is_denied_sector_connectivity_command_and_a2a(self) -> None:
        # Arrange
        role = Principal.DASHBOARD_API
        topics = (
            SECTOR_LIFECYCLE,
            CONNECTIVITY_LIFECYCLE,
            DRONE_COMMAND,
            A2A_REQUEST,
        )

        # Act
        outcomes = tuple(_publish_as(role, topic) for topic in topics)

        # Assert
        self.assertEqual(tuple(Outcome.PUBLISH_DENIED for _ in topics), outcomes)

    def test_fleet_and_recorder_are_denied_mission_lifecycle_publication(self) -> None:
        # Arrange
        roles = (Principal.FLEET_SIMULATOR, Principal.RECORDER)

        # Act
        outcomes = tuple(_publish_as(role, MISSION_LIFECYCLE) for role in roles)

        # Assert
        self.assertEqual(tuple(Outcome.PUBLISH_DENIED for _ in roles), outcomes)


class SubscriptionAuthorizationTests(unittest.TestCase):
    def test_fleet_cannot_subscribe_to_mission_lifecycle_while_recorder_can(
        self,
    ) -> None:
        """The recorder positive control distinguishes an ACL denial from a shared outage."""
        # Arrange
        topic = MISSION_LIFECYCLE

        # Act
        denied = _subscribe_as(Principal.FLEET_SIMULATOR, topic)
        allowed = _subscribe_as(Principal.RECORDER, topic)

        # Assert
        self.assertEqual((Outcome.SUBSCRIBE_DENIED, Outcome.SUBSCRIBED), (denied, allowed))

    def test_recorder_subscription_covers_the_application_stream_but_not_rpc_or_a2a(self) -> None:
        # Arrange
        allowed_topics = (MISSION_LIFECYCLE, SALIENT_DRONE_EVENT, DRONE_COMMAND)
        denied_topics = (GATEWAY_REQUEST, A2A_REQUEST)

        # Act
        allowed = tuple(_subscribe_as(Principal.RECORDER, topic) for topic in allowed_topics)
        denied = tuple(_subscribe_as(Principal.RECORDER, topic) for topic in denied_topics)

        # Assert
        self.assertEqual(tuple(Outcome.SUBSCRIBED for _ in allowed_topics), allowed)
        self.assertEqual(tuple(Outcome.SUBSCRIBE_DENIED for _ in denied_topics), denied)


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


class SempMonitorAuthorizationTests(unittest.TestCase):
    def test_the_dedicated_monitor_can_read_parent_depth_and_active_flow_aggregates(self) -> None:
        # Arrange
        endpoint = _monitor_endpoint()
        connection = connect(endpoint)
        monitor = ReadOnlySempMonitor(connection, endpoint)

        # Act
        try:
            rows = monitor.read_monitor_rows(queue_monitor_collection_path(VPN))
            queue_name = next(
                name for row in rows if isinstance((name := row.data.get("queueName")), str)
            )
            active_flows = monitor.read_monitor_count(queue_tx_flow_monitor_path(VPN, queue_name))
        finally:
            connection.close()

        # Assert
        self.assertIsInstance(rows, tuple)
        self.assertGreater(len(rows), 0)
        self.assertGreaterEqual(active_flows, 0)
        self.assertFalse(hasattr(monitor, "send"))

    def test_the_dedicated_monitor_is_denied_a_same_value_configuration_write(self) -> None:
        # Arrange
        endpoint = _monitor_endpoint()
        connection = connect(endpoint)
        session = SempSession(connection, endpoint)
        path = f"msgVpns/{VPN}"

        # Act
        try:
            current = session.send(Request(Method.GET, path, {}))
            with pytest.raises(SempError) as captured:
                session.send(Request(Method.PATCH, path, {"enabled": current[0]["enabled"]}))
        finally:
            connection.close()

        # Assert
        self.assertIs(SempFailure.STATUS, captured.value.failure)


if __name__ == "__main__":
    unittest.main()
