"""Whether one Event Mesh Tool request produces one non-actuating command-gateway response.

This is the egress half of the Phase 0 Event Mesh spike, and the other half of what
``test_event_mesh_gateway_live.py`` began. Everything before it is evidence about
configuration: the offline validator proves a file's shape and
``tools/quality_gate_tests/deploy/test_agent_mesh_tool_config.py`` proves the committed file
stays inside the grants ``docs/adr/0061`` and ``docs/adr/0070`` give its role. Neither is
evidence that a request is answered.

The request these publish is built by the contract rather than hand-written: it is a
``GatewayRequest``, emitted through ``gateway_request_document`` and serialised by the
canonical encoder, so "one *validated* request" is a claim rather than a description. The
reply is read back through ``decode_gateway_response``, so an answer that does not satisfy
the profile fails here rather than being reported as success.

Four assertions, deliberately of different kinds. The first is model-independent: a request
published directly becomes a reply and a record, which is a transformation and involves no
model at all, so the spike's central claim does not depend on ``qwen3:4b``. The second is
the safety claim on the wire -- the reply says it actuated nothing, and no drone command
appears while it is answered. The third is model-dependent and asserts only that the model
reached the tool, never what it concluded. The fourth is the ACL: the tool's identity is
refused a real mission's gateway responses, which is what makes the reply channel a
narrowing rather than an addition.

Markers keep these out of every blocking suite: they need Docker, the broker, and Ollama.
"""

from __future__ import annotations

import http.client
import json
import unittest
import uuid
from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_broker.deployment import read_credential
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    SolacePublisher,
    SolaceReceiver,
    build_service,
)
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.rpc import (
    GatewayRequest,
    Outcome,
    decode_gateway_response,
    gateway_request_document,
)
from aerial_rescue_contracts.topics import (
    RESERVED_REPLY_MISSION,
    Family,
    Topic,
    format_topic,
)
from aerial_rescue_domain.principals import Principal
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError
from solace.messaging.messaging_service import MessagingService

pytestmark = [pytest.mark.phase0, pytest.mark.docker, pytest.mark.broker, pytest.mark.ollama]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEPLOY: Final = REPOSITORY_ROOT / "deploy"
ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn="default", trust_store=str(DEPLOY / "certs")
)
NAMESPACE: Final = "aerial-rescue-mesh"

MISSION: Final = "m-2026-0001"
TARGET_AGENT: Final = "MissionCoordinator"
A2A_REQUEST_TOPIC: Final = f"{NAMESPACE}/a2a/v1/agent/request/{TARGET_AGENT}"

REPLY_TOPIC_KEY: Final = "__solace_ai_connector_broker_request_response_topic__"
REPLY_METADATA_KEY: Final = "__solace_ai_connector_broker_request_reply_metadata__"

# The command gateway answers without consulting a model, so a reply that has not appeared
# in this window is not slow, it is absent. The model window is the other kind: it covers a
# cold local model deciding to call a tool at all.
REPLY_WINDOW_SECONDS: Final = 30
MODEL_WINDOW_SECONDS: Final = 240
SILENCE_WINDOW_SECONDS: Final = 30
RECEIVE_POLL_MILLISECONDS: Final = 1000
WEB_UI_HOST: Final = "127.0.0.1"
WEB_UI_PORT: Final = 8000
WEB_UI_TIMEOUT: Final = 30


class _UnpublishableRequestError(RuntimeError):
    """The request this test intends to publish is not one the contract accepts."""


def _reply_topic() -> str:
    """Return a reply topic of the shape Solace AI Connector builds, on the reserved level."""
    return format_topic(
        Topic(Family.GATEWAY_RESPONSE, RESERVED_REPLY_MISSION, {"requestId": str(uuid.uuid4())})
    )


def _request(command_type: str = "escalate-rescue", operation: str = "command-authority") -> bytes:
    """Return the canonical bytes of one gateway request the contract accepts.

    Raises:
        _UnpublishableRequestError: If the contract refuses the request this publishes, so a
            broken fixture can never be mistaken for a broken command gateway.
    """
    request = GatewayRequest(mission_id=MISSION, operation=operation, command_type=command_type)
    document = gateway_request_document(request)
    try:
        canonical.canonical_bytes(document)
    except canonical.CanonicalizationError as refusal:
        message = f"the contract refused the request this test publishes: {refusal}"
        raise _UnpublishableRequestError(message) from refusal
    return canonical.canonical_bytes(document)


def _properties(reply_topic: str, request_id: str) -> dict[str, object]:
    """Return the two user properties Solace AI Connector sets on every request."""
    return {
        REPLY_TOPIC_KEY: reply_topic,
        REPLY_METADATA_KEY: json.dumps([{"request_id": request_id, "response_topic": reply_topic}]),
    }


def _connected(role: Principal) -> MessagingService:
    """Return a connected service on ``role``'s identity, validating the checkout's authority."""
    service = build_service(ENDPOINT, role, read_credential(DEPLOY, role))
    service.connect()
    return service


def _ask(request: bytes, topic: str, seconds: int = REPLY_WINDOW_SECONDS) -> list[bytes]:
    """Publish one request as the tool's identity and return the reply it receives.

    The receiver subscribes to this requestor's own reply topic and nothing wider, which is
    the whole of what ``event-mesh-tool`` may consume (``docs/adr/0070``); the family
    pattern the recorder uses would be denied to this role. It starts before the publish,
    because a direct message nobody is subscribed to yet is gone rather than delayed.
    """
    reply_topic = _reply_topic()
    request_id = str(uuid.uuid4())
    service = _connected(Principal.EVENT_MESH_TOOL)
    receiver = SolaceReceiver(service, (reply_topic,))
    publisher = SolacePublisher(service)
    seen: list[bytes] = []
    try:
        publisher.publish(topic, request, _properties(reply_topic, request_id))
        for _ in range(seconds):
            message = receiver.receive(RECEIVE_POLL_MILLISECONDS)
            if message is not None:
                payload = message.get_payload_as_bytes()
                seen.append(bytes(payload) if payload is not None else b"")
                break
    finally:
        publisher.close()
        receiver.close()
        service.disconnect()
    return seen


def _request_topic(operation: str = "command-authority") -> str:
    """Return the gateway-request topic for one operation."""
    return format_topic(Topic(Family.GATEWAY_REQUEST, MISSION, {"operation": operation}))


def _observe(role: Principal, subscription: str, seconds: int) -> list[str]:
    """Return the topics ``subscription`` carries within the window, on ``role``'s identity."""
    service = _connected(role)
    receiver = SolaceReceiver(service, (subscription,))
    seen: list[str] = []
    try:
        for _ in range(seconds):
            message = receiver.receive(RECEIVE_POLL_MILLISECONDS)
            if message is not None:
                seen.append(str(message.get_destination_name()))
                break
    finally:
        receiver.close()
        service.disconnect()
    return seen


def _send_to(agent: str, text: str) -> None:
    """Submit one A2A task to ``agent`` through the Web UI's JSON-RPC surface.

    ``http.client`` rather than ``urllib.request`` for the reason ``packages/broker``'s SEMP
    transport gives: the connection is bounded and the scheme cannot be anything but HTTP.
    """
    body = {
        "jsonrpc": "2.0",
        "id": f"phase0-egress-{agent}",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": f"phase0-egress-message-{agent}",
                "parts": [{"kind": "text", "text": text}],
                "metadata": {"agent_name": agent},
            }
        },
    }
    payload = json.dumps(body).encode("utf-8")
    connection = http.client.HTTPConnection(WEB_UI_HOST, WEB_UI_PORT, timeout=WEB_UI_TIMEOUT)
    try:
        connection.request(
            "POST",
            "/api/v1/message:send",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        connection.getresponse().read()
    finally:
        connection.close()


def _observe_while_asking(
    role: Principal, subscription: str, seconds: int, prompt: str
) -> list[str]:
    """Return what ``subscription`` carries after one task is put to the coordinator.

    The receiver starts before the task is submitted, for the same reason ``_ask`` does.
    """
    service = _connected(role)
    receiver = SolaceReceiver(service, (subscription,))
    seen: list[str] = []
    try:
        _send_to(TARGET_AGENT, prompt)
        for _ in range(seconds):
            message = receiver.receive(RECEIVE_POLL_MILLISECONDS)
            if message is not None:
                seen.append(str(message.get_destination_name()))
                break
    finally:
        receiver.close()
        service.disconnect()
    return seen


class CommandAuthorityAnswerTests(unittest.TestCase):
    def test_one_request_produces_one_validated_reply_on_the_reserved_channel(self) -> None:
        # Arrange
        request = _request()

        # Act
        replies = _ask(request, _request_topic())

        # Assert
        self.assertEqual(
            [(Outcome.ANSWERED, "operator-approval", "escalate-rescue")],
            [
                (answer.outcome, answer.authority, answer.command_type)
                for answer in (decode_gateway_response(reply) for reply in replies)
            ],
        )

    def test_the_answer_reports_no_actuation_and_no_command_is_published(self) -> None:
        # Arrange
        request = _request()

        # Act
        replies = _ask(request, _request_topic())
        commands = _observe(
            Principal.DASHBOARD_API,
            subscription_for(Family.DRONE_COMMAND),
            SILENCE_WINDOW_SECONDS,
        )

        # Assert
        self.assertEqual(
            ([False], []),
            ([decode_gateway_response(reply).actuated for reply in replies], commands),
        )

    def test_an_operation_outside_the_closed_set_is_refused_by_name(self) -> None:
        # Arrange
        request = _request(operation="propose-command")

        # Act
        replies = _ask(request, _request_topic("propose-command"))

        # Assert
        self.assertEqual(
            [(Outcome.REFUSED, "unknown-operation", False)],
            [
                (answer.outcome, answer.refusal, answer.actuated)
                for answer in (decode_gateway_response(reply) for reply in replies)
            ],
        )


class ModelReachesTheToolTests(unittest.TestCase):
    def test_a_task_to_the_coordinator_produces_a_gateway_request(self) -> None:
        # Arrange
        subscription = subscription_for(Family.GATEWAY_REQUEST)

        # Act
        observed = _observe_while_asking(
            Principal.COMMAND_GATEWAY,
            subscription,
            MODEL_WINDOW_SECONDS,
            f"For mission {MISSION}, use your tool to find out which authority the "
            f"escalate-rescue command type falls under, then tell me what it said.",
        )

        # Assert
        self.assertNotEqual(
            [],
            observed,
            "no request reached the gateway-request family, so the tool was never called",
        )


class ReplyChannelAuthorityTests(unittest.TestCase):
    def test_the_tool_identity_is_denied_a_real_mission_s_gateway_responses(self) -> None:
        # Arrange
        forbidden = format_topic(
            Topic(Family.GATEWAY_RESPONSE, MISSION, {"requestId": "r-2026-0001"})
        )
        service = _connected(Principal.EVENT_MESH_TOOL)

        # Act
        try:
            SolaceReceiver(service, (forbidden,))
        except PubSubPlusClientError as denial:
            outcome = type(denial).__name__
        else:
            outcome = "subscribed"
        finally:
            service.disconnect()

        # Assert
        self.assertNotEqual("subscribed", outcome)


if __name__ == "__main__":
    unittest.main()
