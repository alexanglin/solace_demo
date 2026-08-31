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

import pytest
from aerial_rescue_domain.principals import Principal
from solace.messaging.resources.topic_subscription import TopicSubscription

from tests.broker_live_support import native_role_service

pytestmark = [
    pytest.mark.phase0,
    pytest.mark.docker,
    pytest.mark.broker,
    pytest.mark.ollama,
]

NAMESPACE = "aerial-rescue-mesh"
WEB_UI_HOST = "127.0.0.1"
WEB_UI_PORT = 8000
WEB_UI_TIMEOUT_SECONDS = 60

CONFIGURED_AGENTS = frozenset(
    {
        "Orchestrator",
        "MissionCoordinator",
        "MissionResponse",
        "SectorPlanner",
        "EvidenceFusion",
    }
)
"""The five cards agent-mesh/configs/ declares; the workflow publishes one of its own."""
DISCOVERY_TOPIC = f"{NAMESPACE}/a2a/v1/discovery/agentcards"
FIRST_NODE_TOPIC = f"{NAMESPACE}/a2a/v1/agent/request/SectorPlanner"
SECOND_NODE_TOPIC = f"{NAMESPACE}/a2a/v1/agent/request/EvidenceFusion"
COORDINATOR_TOPIC = f"{NAMESPACE}/a2a/v1/agent/request/MissionCoordinator"
DISCOVERY_WINDOW_SECONDS = 45
"""Longer than the 10-second agent_card_publishing interval, so several rounds land."""
RECEIVE_POLL_MILLISECONDS = 1000
DELEGATION_WINDOW_SECONDS = 300
"""Two local-model turns run in series before the second node is reached at all.

Measured 2026-08-31 on the reference stack: the first node was invoked 17 s after
submission and the second 48 s after that, with the workflow completing at 89 s.
"""


def _observe(
    subscription: str, seconds: int, trigger: Callable[[], None] | None = None
) -> list[tuple[str, bytes]]:
    """Subscribe, optionally run ``trigger``, and return everything seen inside the window.

    The subscription is established before the trigger runs, because a direct message the
    probe was not yet listening for is gone rather than delayed. Messages are pulled with the
    blocking receive rather than a callback, so nothing here subclasses the untyped upstream
    handler (``docs/adr/0028-untyped-solace-client-boundary.md``).
    """
    service = native_role_service(Principal.AGENT_MESH_AGENT)
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


REPORT = (
    "A hiker is missing near Eagle Ridge, last known position at the trailhead, "
    "search area about four square kilometres."
)


class DelegationTests(unittest.TestCase):
    def test_invoking_the_workflow_delegates_to_its_first_node_agent(self) -> None:
        # Arrange
        subscription = FIRST_NODE_TOPIC

        # Act
        received = _observe(
            subscription,
            DELEGATION_WINDOW_SECONDS,
            trigger=lambda: _send_to("MissionResponse", REPORT),
        )

        # Assert
        self.assertTrue(
            received,
            "the workflow produced no request on the SectorPlanner topic, so either the "
            "card was absent from the registry or the mesh did not carry the delegation",
        )
        self.assertEqual({FIRST_NODE_TOPIC}, {topic for topic, _ in received})

    def test_the_second_node_runs_which_proves_the_first_produced_its_artifact(
        self,
    ) -> None:
        """The only executable proof that the workflow's output defect is fixed.

        ``fuse_evidence`` depends on ``assess_sectors``, and a node completes only when
        its agent saved an output artifact the workflow could load. A request on the
        second node's topic therefore proves the first node produced a real artifact,
        which is exactly what the configuration failed to do before.
        """
        # Arrange
        subscription = SECOND_NODE_TOPIC

        # Act
        received = _observe(
            subscription,
            DELEGATION_WINDOW_SECONDS,
            trigger=lambda: _send_to("MissionResponse", REPORT),
        )

        # Assert
        self.assertTrue(
            received,
            "the workflow never reached its second node, so the first node's agent did "
            "not save the output artifact the mapping needs",
        )
        self.assertEqual({SECOND_NODE_TOPIC}, {topic for topic, _ in received})

    def test_the_workflow_path_never_reaches_the_gateway_s_coordinator(self) -> None:
        """Finite silence: the workflow and salient-event paths are separate.

        This is a bounded observation, not a proof of never. It states only that no
        request reached the coordinator inside this window while the workflow ran.
        """
        # Arrange
        subscription = COORDINATOR_TOPIC

        # Act
        received = _observe(
            subscription,
            DELEGATION_WINDOW_SECONDS,
            trigger=lambda: _send_to("MissionResponse", REPORT),
        )

        # Assert
        self.assertEqual([], list(received))


if __name__ == "__main__":
    unittest.main()
