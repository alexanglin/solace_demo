"""Whether the running Agent Mesh container actually does what Phase 0 requires of it.

``agent-mesh/tests/test_config_validator.py`` proves the configuration is well formed. That is
evidence about a document. These probes are the other kind: they read the running container's
own HTTP surface and subscribe to the broker in ``deploy/compose.yaml``, so what is asserted is
what the mesh did rather than what it was told to do
(``docs/IMPLEMENTATION_PLAN.md`` Phase 0, ``docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md``).

Three claims are made here, and each is the observation of a side effect rather than of a
return value: the agent cards a running mesh publishes, the A2A discovery traffic those cards
travel on, and one delegation from a workflow node to a peer agent. The delegation probe is the
one that answers the plan's kill criterion, because a model that cannot make a tool call cannot
produce a request on another agent's topic no matter how the mesh is configured.

They carry the ``phase0``, ``docker``, ``broker``, and ``ollama`` markers, so no blocking suite
runs them (``docs/TESTING.md``); the pushed stages stay runnable with no daemon, no broker, and
no model. The delegation probe drives a local model and takes tens of seconds.
"""

from __future__ import annotations

import http.client
import json
import time
import unittest
from collections.abc import Callable
from pathlib import Path

import pytest
from aerial_rescue_broker.deployment import credential_path
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
from solace.messaging.messaging_service import MessagingService
from solace.messaging.resources.topic_subscription import TopicSubscription

pytestmark = [
    pytest.mark.phase0,
    pytest.mark.docker,
    pytest.mark.broker,
    pytest.mark.ollama,
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPOSITORY_ROOT / "deploy"
TRUST_STORE = DEPLOY / "certs"
BROKER_URL = "tcps://localhost:55443"
VPN = "default"
NAMESPACE = "aerial-rescue-mesh"
WEB_UI_HOST = "127.0.0.1"
WEB_UI_PORT = 8000
WEB_UI_TIMEOUT_SECONDS = 60

CONFIGURED_AGENTS = frozenset({"Orchestrator", "MissionCoordinator", "MissionResponse"})
"""The three cards agent-mesh/configs/ declares; the workflow publishes one of its own."""
DISCOVERY_TOPIC = f"{NAMESPACE}/a2a/v1/discovery/agentcards"
DELEGATION_TOPIC = f"{NAMESPACE}/a2a/v1/agent/request/MissionCoordinator"
DISCOVERY_WINDOW_SECONDS = 45
"""Longer than the 30-second agent_card_publishing interval, so one round always lands."""
RECEIVE_POLL_MILLISECONDS = 1000
DELEGATION_WINDOW_SECONDS = 180
"""A local model has to run before a delegation can happen at all."""


def _service() -> MessagingService:
    """Return a service bound to the container on the Agent Mesh role's own identity."""
    credential = credential_path(DEPLOY, Principal.AGENT_MESH_AGENT).read_text(encoding="utf-8")
    properties = {
        transport.HOST: BROKER_URL,
        service_property.VPN_NAME: VPN,
        auth.SCHEME_BASIC_USER_NAME: Principal.AGENT_MESH_AGENT.value,
        auth.SCHEME_BASIC_PASSWORD: credential.strip(),
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


def _observe(
    subscription: str, seconds: int, trigger: Callable[[], None] | None = None
) -> list[tuple[str, bytes]]:
    """Subscribe, optionally run ``trigger``, and return everything seen inside the window.

    The subscription is established before the trigger runs, because a direct message the
    probe was not yet listening for is gone rather than delayed. Messages are pulled with the
    blocking receive rather than a callback, so nothing here subclasses the untyped upstream
    handler (``docs/adr/0028-untyped-solace-client-boundary.md``).
    """
    service = _service()
    service.connect()
    received: list[tuple[str, bytes]] = []
    receiver = (
        service.create_direct_message_receiver_builder()
        .with_subscriptions([TopicSubscription.of(subscription)])
        .build()
    )
    try:
        receiver.start()
        if trigger is not None:
            trigger()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not received:
            message = receiver.receive_message(timeout=RECEIVE_POLL_MILLISECONDS)
            if message is None:
                continue
            topic = str(message.get_destination_name())
            received.append((topic, bytes(message.get_payload_as_bytes() or b"")))
        return received
    finally:
        receiver.terminate()
        service.disconnect()


class _WebUiShapeError(TypeError):
    """The Web UI answered with something other than a list of agent card objects."""

    def __init__(self) -> None:
        super().__init__("the Web UI did not return a list of agent card objects")


def _decoded(method: str, path: str, body: object | None = None) -> object:
    """Return one decoded Web UI response over a bounded loopback connection.

    ``http.client`` rather than ``urllib.request`` for the reason ``packages/broker``'s SEMP
    transport gives: the connection is bounded and the scheme cannot be anything but HTTP.
    """
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    connection = http.client.HTTPConnection(
        WEB_UI_HOST, WEB_UI_PORT, timeout=WEB_UI_TIMEOUT_SECONDS
    )
    try:
        connection.request(method, path, body=payload, headers=headers)
        return json.loads(connection.getresponse().read())
    finally:
        connection.close()


def _agent_card_names() -> frozenset[str]:
    """Return the agent names the running mesh reports, refusing a shape it should not send."""
    cards = _decoded("GET", "/api/v1/agentCards")
    if not isinstance(cards, list):
        raise _WebUiShapeError
    names: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            raise _WebUiShapeError
        names.add(str(card.get("name")))
    return frozenset(names)


def _send_to(agent: str, text: str) -> None:
    """Submit one A2A task to ``agent`` through the Web UI's JSON-RPC surface."""
    body = {
        "jsonrpc": "2.0",
        "id": f"phase0-{agent}",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": f"phase0-message-{agent}",
                "parts": [{"kind": "text", "text": text}],
                "metadata": {"agent_name": agent},
            }
        },
    }
    _decoded("POST", "/api/v1/message:send", body)


class AgentCardDiscoveryTests(unittest.TestCase):
    def test_the_running_mesh_reports_every_configured_agent_card(self) -> None:
        # Arrange
        expected = CONFIGURED_AGENTS

        # Act
        reported = _agent_card_names()

        # Assert
        self.assertEqual(expected, reported)

    def test_agent_cards_reach_the_a2a_discovery_topic(self) -> None:
        # Arrange
        subscription = DISCOVERY_TOPIC

        # Act
        received = _observe(subscription, DISCOVERY_WINDOW_SECONDS)

        # Assert
        names = {
            json.loads(payload).get("result", json.loads(payload)).get("name")
            for _, payload in received
        }
        self.assertTrue(received, "no agent card was published inside the window")
        self.assertTrue(names <= CONFIGURED_AGENTS, names)


class DelegationTests(unittest.TestCase):
    def test_invoking_the_workflow_delegates_to_its_named_peer_agent(self) -> None:
        # Arrange
        report = (
            "A hiker is missing near Eagle Ridge, last known position at the trailhead, "
            "search area about four square kilometres."
        )

        # Act
        received = _observe(
            DELEGATION_TOPIC,
            DELEGATION_WINDOW_SECONDS,
            trigger=lambda: _send_to("MissionResponse", report),
        )

        # Assert
        self.assertTrue(
            received,
            "the workflow produced no request on the MissionCoordinator topic, so either the "
            "model made no tool call or the mesh did not carry the delegation",
        )
        self.assertEqual({DELEGATION_TOPIC}, {topic for topic, _ in received})


if __name__ == "__main__":
    unittest.main()
