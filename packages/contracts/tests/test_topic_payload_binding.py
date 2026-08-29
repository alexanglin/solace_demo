"""Repeated topic discriminators must agree with their closed payload carriers."""

from __future__ import annotations

import unittest
from collections.abc import Mapping

from aerial_rescue_contracts.envelope import (
    Envelope,
    EnvelopeError,
    EnvelopeRefusal,
    check_topic_binding,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type

MISSION = "mission-0001"


def _envelope(topic: Topic, data: Mapping[str, object]) -> Envelope:
    """Return an already validated envelope at the topic-binding boundary."""
    return Envelope(
        id="event-0001",
        source="urn:aerial-rescue:producer:run-0001",
        type=event_type(topic),
        subject=topic.mission_id,
        time="2026-08-25T12:00:00.000Z",
        dataschema="https://aerial-rescue.invalid/schemas/v1/payload/operator-command.schema.json",
        sequence="000000000000001",
        correlation_id="correlation-0001",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        data=data,
    )


def _refusal(topic: Topic, data: Mapping[str, object]) -> tuple[str, object]:
    """Return the mismatched payload member and value, failing if the topic binds."""
    try:
        check_topic_binding(_envelope(topic, data), topic)
    except EnvelopeError as error:
        if error.refusal is not EnvelopeRefusal.TOPIC_BINDING:
            raise AssertionError(error.refusal) from error
        return (error.attribute, error.value)
    message = f"bound: {topic!r} {data!r}"
    raise AssertionError(message)


class RepeatedDiscriminatorBindingTests(unittest.TestCase):
    def test_nested_operator_action_command_type_must_equal_the_topic(self) -> None:
        # Arrange
        topic = Topic(Family.OPERATOR_COMMAND, MISSION, {"commandType": "assign-sector"})
        data = {
            "missionId": MISSION,
            "action": {
                "commandType": "escalate-rescue",
                "droneId": "drone-vision-01",
            },
        }

        # Act
        refusal = _refusal(topic, data)

        # Assert
        self.assertEqual(("action.commandType", "escalate-rescue"), refusal)

    def test_approval_decision_must_equal_the_topic(self) -> None:
        # Arrange
        topic = Topic(Family.OPERATOR_APPROVAL, MISSION, {"decision": "approve"})
        data = {"missionId": MISSION, "decision": "reject"}

        # Act
        refusal = _refusal(topic, data)

        # Assert
        self.assertEqual(("decision", "reject"), refusal)

    def test_agent_name_and_proposal_type_each_must_equal_the_topic(self) -> None:
        # Arrange
        topic = Topic(
            Family.AGENT_PROPOSAL,
            MISSION,
            {"agentName": "MissionCoordinator", "proposalType": "candidate-location"},
        )
        documents = (
            {"missionId": MISSION, "agentName": "OtherAgent", "proposalType": "candidate-location"},
            {"missionId": MISSION, "agentName": "MissionCoordinator", "proposalType": "other"},
        )

        # Act
        refusals = tuple(_refusal(topic, document) for document in documents)

        # Assert
        self.assertEqual(
            (("agentName", "OtherAgent"), ("proposalType", "other")),
            refusals,
        )

    def test_evidence_proposal_and_escalation_drone_identifiers_must_equal_their_topics(
        self,
    ) -> None:
        # Arrange
        cases = (
            (
                Topic(Family.EVIDENCE_DECISION, MISSION, {"proposalId": "proposal-0001"}),
                {"missionId": MISSION, "proposalId": "proposal-0002"},
                ("proposalId", "proposal-0002"),
            ),
            (
                Topic(
                    Family.DRONE_COMMAND,
                    MISSION,
                    {"droneId": "drone-vision-01", "commandType": "escalate-rescue"},
                ),
                {"missionId": MISSION, "droneId": "drone-thermal-02"},
                ("droneId", "drone-thermal-02"),
            ),
        )

        # Act
        refusals = tuple(_refusal(topic, data) for topic, data, _ in cases)

        # Assert
        self.assertEqual(tuple(expected for _, _, expected in cases), refusals)

    def test_audit_record_type_must_equal_the_topic(self) -> None:
        # Arrange
        topic = Topic(Family.AUDIT, MISSION, {"recordType": "proposal-normalization"})
        data = {"missionId": MISSION, "recordType": "command-authorization"}

        # Act
        refusal = _refusal(topic, data)

        # Assert
        self.assertEqual(("recordType", "command-authorization"), refusal)


if __name__ == "__main__":
    unittest.main()
