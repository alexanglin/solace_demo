"""Family-derived, authorization-checked publication through typed broker capabilities."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Final, override

import pytest
from aerial_rescue_broker.routing import (
    DeliveryRouter,
    GuaranteedReplyResponder,
    PublicationPorts,
    RequestReplyRequester,
    RequestReplyResponder,
    RoutingError,
    RoutingRefusal,
    required_deliveries,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.topics import Delivery
from aerial_rescue_domain.principals import Principal

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
FIXTURES: Final = REPOSITORY_ROOT / "fixtures" / "golden" / "v1"
CONTRACT_BASELINES: Final = REPOSITORY_ROOT / "packages" / "contracts" / "tests" / "baselines"


class _GuaranteedSpy:
    """Record acknowledged publications without broker I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Record one Guaranteed call."""
        self.calls.append((topic, payload, properties))


class _DirectSpy:
    """Record Direct publications without broker I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object], /
    ) -> None:
        """Record one Direct call."""
        self.calls.append((topic, payload, properties))


class _ReplyMessage:
    """A correlated response returned by the request/reply requester fake."""

    def get_payload_as_bytes(self) -> bytes | None:
        """Return a distinctive response body."""
        return b"response"

    def get_destination_name(self) -> str | None:
        """Return the private reply destination."""
        return "aerial-rescue/v1/reply/gateway/response/requestor-1"

    def get_properties(self) -> Mapping[str, object]:
        """Return no user properties."""
        return {}


class _RequestSpy(RequestReplyRequester):
    """Record response-bearing request/reply calls without broker I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, Mapping[str, object]]] = []

    @override
    def request(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        timeout_milliseconds: int,
        /,
    ) -> _ReplyMessage:
        """Record one request and return its correlated response."""
        self.calls.append((topic, payload, {**properties, "timeout": timeout_milliseconds}))
        return _ReplyMessage()


class _ReplySpy(RequestReplyResponder):
    """Record correlated replies without broker I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, Mapping[str, object]]] = []

    @override
    def publish_reply(
        self, topic: str, payload: bytes, properties: Mapping[str, object], /
    ) -> None:
        """Record one reply publication."""
        self.calls.append((topic, payload, properties))


def _wire(path: Path) -> bytes:
    """Return one fixture as canonical bytes."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return canonical.canonical_bytes(document)


def _ports_for(
    role: Principal,
) -> tuple[
    PublicationPorts,
    _DirectSpy,
    _GuaranteedSpy,
    _RequestSpy,
    _ReplySpy,
]:
    """Return exactly the publication capabilities one role is granted."""
    direct = _DirectSpy()
    guaranteed = _GuaranteedSpy()
    requester = _RequestSpy()
    responder = _ReplySpy()
    required = required_deliveries(role)
    ports = PublicationPorts(
        direct=direct if Delivery.DIRECT in required else None,
        guaranteed=guaranteed if Delivery.GUARANTEED in required else None,
        requester=requester if role is Principal.EVENT_MESH_TOOL else None,
        responder=responder if role is Principal.COMMAND_GATEWAY else None,
    )
    return ports, direct, guaranteed, requester, responder


class CapabilityConstructionTests(unittest.TestCase):
    def test_a_reply_responder_preserves_payload_and_correlation_properties(self) -> None:
        # Arrange
        publisher = _GuaranteedSpy()
        responder = GuaranteedReplyResponder(publisher)
        topic = "aerial-rescue/v1/reply/gateway/response/requestor-1"
        payload = _wire(CONTRACT_BASELINES / "rpc_response_baseline.json")
        properties = {"correlation": "opaque"}

        # Act
        responder.publish_reply(topic, payload, properties)

        # Assert
        self.assertEqual([(topic, payload, properties)], publisher.calls)

    def test_required_delivery_capabilities_are_total_over_every_principal(self) -> None:
        # Arrange
        expected = {
            Principal.FLEET_SIMULATOR: frozenset({Delivery.DIRECT, Delivery.GUARANTEED}),
            Principal.COMMAND_GATEWAY: frozenset(
                {Delivery.DIRECT, Delivery.GUARANTEED, Delivery.REQUEST_REPLY}
            ),
            Principal.DASHBOARD_API: frozenset({Delivery.GUARANTEED}),
            Principal.EVIDENCE_SERVICE: frozenset({Delivery.GUARANTEED}),
            Principal.RECORDER: frozenset(),
            Principal.EVENT_MESH_GATEWAY: frozenset({Delivery.DIRECT}),
            Principal.EVENT_MESH_TOOL: frozenset({Delivery.REQUEST_REPLY}),
            Principal.AGENT_MESH_AGENT: frozenset(),
            Principal.DISCOVERY: frozenset(),
        }

        # Act
        actual = {role: required_deliveries(role) for role in Principal}

        # Assert
        self.assertEqual(expected, actual)

    def test_a_router_refuses_missing_or_widened_capabilities_at_construction(self) -> None:
        # Arrange
        direct = _DirectSpy()
        cases = (
            (Principal.DASHBOARD_API, PublicationPorts()),
            (Principal.DASHBOARD_API, PublicationPorts(direct=direct)),
            (Principal.EVENT_MESH_TOOL, PublicationPorts(responder=_ReplySpy())),
        )

        # Act
        refusals = []
        for role, ports in cases:
            with self.subTest(role=role, ports=ports):
                with pytest.raises(RoutingError) as captured:
                    DeliveryRouter(role, ports)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                RoutingRefusal.CAPABILITY_MISMATCH,
                RoutingRefusal.CAPABILITY_MISMATCH,
                RoutingRefusal.CAPABILITY_MISMATCH,
            ],
            refusals,
        )


class FamilyDerivedDispatchTests(unittest.TestCase):
    def test_a_valid_telemetry_event_uses_only_the_direct_port(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.FLEET_SIMULATOR)
        router = DeliveryRouter(Principal.FLEET_SIMULATOR, ports)
        topic = "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry"
        payload = _wire(FIXTURES / "event" / "drone-telemetry" / "baseline.json")

        # Act
        router.publish(topic, payload, {"trace": "telemetry"})

        # Assert
        self.assertEqual([(topic, payload, {"trace": "telemetry"})], direct.calls)
        self.assertEqual(([], [], []), (guaranteed.calls, requester.calls, responder.calls))

    def test_a_valid_mission_event_uses_only_the_guaranteed_port(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.DASHBOARD_API)
        router = DeliveryRouter(Principal.DASHBOARD_API, ports)
        topic = "aerial-rescue/v1/mission-01/mission/event/lifecycle"
        payload = _wire(FIXTURES / "event" / "mission-event-lifecycle" / "baseline.json")

        # Act
        router.publish(topic, payload, {})

        # Assert
        self.assertEqual([(topic, payload, {})], guaranteed.calls)
        self.assertEqual(([], [], []), (direct.calls, requester.calls, responder.calls))

    def test_a_valid_gateway_request_uses_only_the_request_reply_port(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.EVENT_MESH_TOOL)
        router = DeliveryRouter(Principal.EVENT_MESH_TOOL, ports)
        topic = "aerial-rescue/v1/m-2026-0001/gateway/request/command-authority"
        payload = _wire(CONTRACT_BASELINES / "rpc_request_baseline.json")

        # Act
        response = router.request(topic, payload, {"reply": "private"}, 1_000)

        # Assert
        self.assertEqual(
            (b"response", [(topic, payload, {"reply": "private", "timeout": 1_000})]),
            (response.get_payload_as_bytes(), requester.calls),
        )
        self.assertEqual(([], [], []), (direct.calls, guaranteed.calls, responder.calls))

    def test_a_valid_gateway_response_uses_only_the_reply_port(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.COMMAND_GATEWAY)
        router = DeliveryRouter(Principal.COMMAND_GATEWAY, ports)
        topic = "aerial-rescue/v1/reply/gateway/response/requestor-1"
        payload = _wire(CONTRACT_BASELINES / "rpc_response_baseline.json")

        # Act
        router.publish(topic, payload, {"correlation": "opaque"})

        # Assert
        self.assertEqual([(topic, payload, {"correlation": "opaque"})], responder.calls)
        self.assertEqual(([], [], []), (direct.calls, guaranteed.calls, requester.calls))

    def test_request_and_reply_operations_cannot_be_interchanged(self) -> None:
        # Arrange
        request_ports, _direct, _guaranteed, requester, _responder = _ports_for(
            Principal.EVENT_MESH_TOOL
        )
        reply_ports, _direct, _guaranteed, _requester, responder = _ports_for(
            Principal.COMMAND_GATEWAY
        )
        request_router = DeliveryRouter(Principal.EVENT_MESH_TOOL, request_ports)
        reply_router = DeliveryRouter(Principal.COMMAND_GATEWAY, reply_ports)
        request_topic = "aerial-rescue/v1/m-2026-0001/gateway/request/command-authority"
        request_payload = _wire(CONTRACT_BASELINES / "rpc_request_baseline.json")
        reply_topic = "aerial-rescue/v1/reply/gateway/response/requestor-1"
        reply_payload = _wire(CONTRACT_BASELINES / "rpc_response_baseline.json")

        # Act
        with pytest.raises(RoutingError) as published_request:
            request_router.publish(request_topic, request_payload, {})
        with pytest.raises(RoutingError) as requested_reply:
            reply_router.request(reply_topic, reply_payload, {}, 1_000)

        # Assert
        self.assertEqual(
            (RoutingRefusal.OPERATION_MISMATCH, RoutingRefusal.OPERATION_MISMATCH, [], []),
            (
                published_request.value.refusal,
                requested_reply.value.refusal,
                requester.calls,
                responder.calls,
            ),
        )

    def test_a_valid_agent_response_uses_only_the_direct_port(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.EVENT_MESH_GATEWAY)
        router = DeliveryRouter(Principal.EVENT_MESH_GATEWAY, ports)
        topic = "aerial-rescue/v1/mission-synthetic-0001/agent/response/VisionAgent"
        payload = _wire(FIXTURES / "integration" / "agent-response" / "baseline.json")

        # Act
        router.publish(topic, payload, {})

        # Assert
        self.assertEqual([(topic, payload, {})], direct.calls)
        self.assertEqual(([], [], []), (guaranteed.calls, requester.calls, responder.calls))


class FailBeforeIoTests(unittest.TestCase):
    def test_a_lost_runtime_capability_fails_closed_before_publication(self) -> None:
        # Arrange
        ports, _direct, _guaranteed, _requester, responder = _ports_for(Principal.COMMAND_GATEWAY)
        router = DeliveryRouter(Principal.COMMAND_GATEWAY, ports)
        object.__setattr__(router, "_ports", PublicationPorts())
        topic = "aerial-rescue/v1/reply/gateway/response/requestor-1"
        payload = _wire(CONTRACT_BASELINES / "rpc_response_baseline.json")

        # Act
        with pytest.raises(RoutingError) as captured:
            router.publish(topic, payload, {})

        # Assert
        self.assertEqual(RoutingRefusal.CAPABILITY_MISMATCH, captured.value.refusal)
        self.assertEqual([], responder.calls)

    def test_an_invalid_topic_is_redacted_and_refused_before_any_port_call(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.FLEET_SIMULATOR)
        router = DeliveryRouter(Principal.FLEET_SIMULATOR, ports)
        payload = _wire(FIXTURES / "event" / "drone-telemetry" / "baseline.json")

        # Act
        with pytest.raises(RoutingError) as captured:
            router.publish("credential-value/not-an-application-topic", payload, {})

        # Assert
        self.assertEqual(RoutingRefusal.INVALID_TOPIC, captured.value.refusal)
        self.assertNotIn("credential-value", str(captured.value))
        self.assertEqual(
            ([], [], [], []),
            (direct.calls, guaranteed.calls, requester.calls, responder.calls),
        )

    def test_a_nonpositive_request_timeout_is_refused_before_requester_io(self) -> None:
        # Arrange
        ports, _direct, _guaranteed, requester, _responder = _ports_for(Principal.EVENT_MESH_TOOL)
        router = DeliveryRouter(Principal.EVENT_MESH_TOOL, ports)
        topic = "aerial-rescue/v1/m-2026-0001/gateway/request/command-authority"
        payload = _wire(CONTRACT_BASELINES / "rpc_request_baseline.json")

        # Act
        with pytest.raises(RoutingError) as captured:
            router.request(topic, payload, {}, 0)

        # Assert
        self.assertEqual(
            (RoutingRefusal.INVALID_TIMEOUT, []),
            (captured.value.refusal, requester.calls),
        )

    def test_a_role_without_the_family_grant_is_refused_before_any_port_call(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.DASHBOARD_API)
        router = DeliveryRouter(Principal.DASHBOARD_API, ports)
        topic = "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry"
        payload = _wire(FIXTURES / "event" / "drone-telemetry" / "baseline.json")

        # Act
        with pytest.raises(RoutingError) as captured:
            router.publish(topic, payload, {})

        # Assert
        self.assertEqual(RoutingRefusal.NOT_AUTHORIZED, captured.value.refusal)
        self.assertEqual(
            ([], [], [], []),
            (direct.calls, guaranteed.calls, requester.calls, responder.calls),
        )

    def test_a_topic_payload_mismatch_is_refused_before_any_port_call(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.FLEET_SIMULATOR)
        router = DeliveryRouter(Principal.FLEET_SIMULATOR, ports)
        wrong_topic = "aerial-rescue/v1/m-2026-0001/drone/other-drone/telemetry"
        payload = _wire(FIXTURES / "event" / "drone-telemetry" / "baseline.json")

        # Act
        with pytest.raises(RoutingError) as captured:
            router.publish(wrong_topic, payload, {})

        # Assert
        self.assertEqual(RoutingRefusal.INVALID_PAYLOAD, captured.value.refusal)
        self.assertEqual(
            ([], [], [], []),
            (direct.calls, guaranteed.calls, requester.calls, responder.calls),
        )

    def test_a_gateway_request_body_must_bind_its_mission_and_operation_to_the_topic(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.EVENT_MESH_TOOL)
        router = DeliveryRouter(Principal.EVENT_MESH_TOOL, ports)
        wrong_topic = "aerial-rescue/v1/other-mission/gateway/request/propose-command"
        payload = _wire(CONTRACT_BASELINES / "rpc_request_baseline.json")

        # Act
        with pytest.raises(RoutingError) as captured:
            router.publish(wrong_topic, payload, {})

        # Assert
        self.assertEqual(RoutingRefusal.INVALID_PAYLOAD, captured.value.refusal)
        self.assertEqual(
            ([], [], [], []),
            (direct.calls, guaranteed.calls, requester.calls, responder.calls),
        )

    def test_malformed_bytes_are_refused_without_disclosing_them_or_calling_a_port(self) -> None:
        # Arrange
        ports, direct, guaranteed, requester, responder = _ports_for(Principal.DASHBOARD_API)
        router = DeliveryRouter(Principal.DASHBOARD_API, ports)
        topic = "aerial-rescue/v1/mission-01/mission/event/lifecycle"
        payload = b'{"authorization":"sensitive",'

        # Act
        with pytest.raises(RoutingError) as captured:
            router.publish(topic, payload, {})

        # Assert
        self.assertEqual(RoutingRefusal.INVALID_PAYLOAD, captured.value.refusal)
        self.assertNotIn("sensitive", str(captured.value))
        self.assertEqual(
            ([], [], [], []),
            (direct.calls, guaranteed.calls, requester.calls, responder.calls),
        )
