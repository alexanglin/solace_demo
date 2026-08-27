"""The command gateway's closed broker-ingress surface.

Guaranteed application messages are canonical CloudEvents whose closed payload is validated
before it can reach a transaction.  The two non-notification representations retain their
contract-owned parsers: gateway request/reply and the direct Agent Response integration body.
ADR-0146 explicitly removes command-gateway subscription authority for canonical proposals,
so an arriving proposal is denied from its topic alone, before hostile payload bytes are read.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest
from aerial_rescue_command_gateway.ingress import (
    AgentResponseIngress,
    AssignSectorAction,
    CommandResultIngress,
    EscalateRescueAction,
    GatewayRequestIngress,
    IngressError,
    IngressRefusal,
    OperatorApprovalIngress,
    OperatorCommandIngress,
    accept_ingress,
)

ROOT = Path(__file__).parents[3]


def _fixture(relative: str) -> bytes:
    """Return exact committed fixture bytes."""
    return (ROOT / "fixtures" / "golden" / "v1" / relative).read_bytes()


class NotificationIngressTests(unittest.TestCase):
    def test_an_assign_sector_operator_command_is_strictly_typed(self) -> None:
        # Arrange
        payload = _fixture("event/operator-command/baseline.json")
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/command/assign-sector"

        # Act
        accepted = accept_ingress(payload, topic)

        # Assert
        assert isinstance(accepted, OperatorCommandIngress)
        assert isinstance(accepted.payload.action, AssignSectorAction)
        self.assertEqual(
            (OperatorCommandIngress, "command-synthetic-0001", "sector-synthetic-01"),
            (
                type(accepted),
                accepted.payload.command_id,
                accepted.payload.action.sector_id,
            ),
        )

    def test_an_escalation_command_retains_every_approval_binding(self) -> None:
        # Arrange
        payload = _fixture("event/operator-command/escalate-rescue.json")
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/command/escalate-rescue"

        # Act
        accepted = accept_ingress(payload, topic)

        # Assert
        assert isinstance(accepted, OperatorCommandIngress)
        assert isinstance(accepted.payload.action, EscalateRescueAction)
        self.assertEqual(
            (
                OperatorCommandIngress,
                "proposal-synthetic-0001",
                "decision-synthetic-0001",
                45123456,
            ),
            (
                type(accepted),
                accepted.payload.action.proposal_id,
                accepted.payload.action.evidence_decision_id,
                accepted.payload.action.latitude_microdegrees,
            ),
        )

    def test_an_operator_approval_retains_the_exact_action_and_expiry(self) -> None:
        # Arrange
        payload = _fixture("event/operator-approval/baseline.json")
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve"

        # Act
        accepted = accept_ingress(payload, topic)

        # Assert
        assert isinstance(accepted, OperatorApprovalIngress)
        self.assertEqual(
            (
                OperatorApprovalIngress,
                "approval-synthetic-0001",
                "2026-08-25T12:06:00.000Z",
                "escalate-rescue",
            ),
            (
                type(accepted),
                accepted.payload.approval_id,
                accepted.payload.expires_at,
                accepted.payload.action.command_type,
            ),
        )

    def test_a_command_result_is_strictly_typed_and_topic_bound(self) -> None:
        # Arrange
        payload = _fixture("event/drone-command-result/baseline.json")
        topic = "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/command-result/cmd-2026-0001"

        # Act
        accepted = accept_ingress(payload, topic)

        # Assert
        assert isinstance(accepted, CommandResultIngress)
        self.assertEqual(
            (CommandResultIngress, "cmd-2026-0001", "acknowledged"),
            (type(accepted), accepted.payload.command_id, accepted.payload.outcome),
        )


class IntegrationIngressTests(unittest.TestCase):
    def test_a_structured_agent_response_uses_the_contract_owned_parser(self) -> None:
        # Arrange
        payload = _fixture("integration/agent-response/baseline.json")
        topic = "aerial-rescue/v1/mission-synthetic-0001/agent/response/VisionAgent"

        # Act
        accepted = accept_ingress(payload, topic)

        # Assert
        assert isinstance(accepted, AgentResponseIngress)
        self.assertEqual(
            (AgentResponseIngress, "invocation-synthetic-0001", "candidate"),
            (type(accepted), accepted.response.invocation_id, accepted.response.outcome.value),
        )

    def test_a_gateway_request_uses_the_schema_bound_rpc_parser(self) -> None:
        # Arrange
        payload = _fixture("rpc/gateway-request/baseline.json")
        document = json.loads(payload)
        topic = f"aerial-rescue/v1/{document['missionId']}/gateway/request/{document['operation']}"

        # Act
        accepted = accept_ingress(payload, topic)

        # Assert
        assert isinstance(accepted, GatewayRequestIngress)
        self.assertEqual(
            (GatewayRequestIngress, document["commandType"]),
            (type(accepted), accepted.request.command_type),
        )


class FailClosedIngressTests(unittest.TestCase):
    def test_a_broker_delivered_canonical_proposal_is_denied_before_payload_decoding(self) -> None:
        # Arrange
        payload = b"these bytes are deliberately not JSON"
        topic = (
            "aerial-rescue/v1/mission-synthetic-0001/agent/proposal/VisionAgent/candidate-location"
        )

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(payload, topic)

        # Assert
        self.assertEqual(IngressRefusal.UNAUTHORIZED_FAMILY, captured.value.refusal)

    def test_a_payload_with_an_unknown_member_never_becomes_an_operator_command(self) -> None:
        # Arrange
        document = json.loads(_fixture("event/operator-command/baseline.json"))
        document["data"]["untrusted"] = True
        payload = json.dumps(document).encode()
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/command/assign-sector"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(payload, topic)

        # Assert
        self.assertEqual(IngressRefusal.PAYLOAD, captured.value.refusal)

    def test_a_topic_body_identity_mismatch_is_redacted(self) -> None:
        # Arrange
        payload = _fixture("integration/agent-response/baseline.json")
        topic = "aerial-rescue/v1/another-mission/agent/response/VisionAgent"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(payload, topic)

        # Assert
        self.assertEqual(
            (IngressRefusal.BINDING, IngressRefusal.BINDING.value),
            (captured.value.refusal, str(captured.value)),
        )

    def test_a_non_application_topic_is_refused_without_echoing_it(self) -> None:
        # Arrange
        topic = "hostile/topic"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(b"not-json", topic)

        # Assert
        self.assertEqual(
            (IngressRefusal.TOPIC, IngressRefusal.TOPIC.value),
            (captured.value.refusal, str(captured.value)),
        )

    def test_a_malformed_notification_is_refused_before_payload_validation(self) -> None:
        # Arrange
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/command/assign-sector"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(b"not-json", topic)

        # Assert
        self.assertEqual(IngressRefusal.ENVELOPE, captured.value.refusal)

    def test_a_boolean_version_is_not_the_integer_one(self) -> None:
        # Arrange
        document = json.loads(_fixture("event/operator-command/baseline.json"))
        document["data"]["operatorCommandVersion"] = True
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/command/assign-sector"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(json.dumps(document).encode(), topic)

        # Assert
        self.assertEqual(IngressRefusal.PAYLOAD, captured.value.refusal)

    def test_an_approval_branch_with_the_wrong_expiry_shape_is_refused(self) -> None:
        # Arrange
        document = json.loads(_fixture("event/operator-approval/baseline.json"))
        document["data"].pop("expiresAt")
        topic = "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(json.dumps(document).encode(), topic)

        # Assert
        self.assertEqual(IngressRefusal.PAYLOAD, captured.value.refusal)

    def test_malformed_gateway_rpc_is_refused_as_a_payload(self) -> None:
        # Arrange
        topic = "aerial-rescue/v1/m-2026-0001/gateway/request/command-authority"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(b"not-json", topic)

        # Assert
        self.assertEqual(IngressRefusal.PAYLOAD, captured.value.refusal)

    def test_a_gateway_rpc_mission_mismatch_is_refused(self) -> None:
        # Arrange
        payload = _fixture("rpc/gateway-request/baseline.json")
        document = json.loads(payload)
        topic = f"aerial-rescue/v1/another-mission/gateway/request/{document['operation']}"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(payload, topic)

        # Assert
        self.assertEqual(IngressRefusal.BINDING, captured.value.refusal)

    def test_malformed_agent_response_is_refused_as_a_payload(self) -> None:
        # Arrange
        topic = "aerial-rescue/v1/mission-synthetic-0001/agent/response/VisionAgent"

        # Act
        with pytest.raises(IngressError) as captured:
            accept_ingress(b"not-json", topic)

        # Assert
        self.assertEqual(IngressRefusal.PAYLOAD, captured.value.refusal)


if __name__ == "__main__":
    unittest.main()
