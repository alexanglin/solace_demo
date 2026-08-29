"""The structured, non-CloudEvent Agent Mesh integration body at ingress."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from copy import deepcopy
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.integration import (
    AGENT_RESPONSE_VERSION,
    AgentCandidate,
    AgentOutcome,
    AgentResponse,
    AgentResponseReason,
    IntegrationError,
    IntegrationRefusal,
    agent_response_document,
    check_agent_response_topic,
    decode_agent_response,
    parse_agent_response,
)
from aerial_rescue_contracts.topics import Family, Topic

CANDIDATE: dict[str, object] = {
    "agentResponseVersion": 1,
    "missionId": "mission-0001",
    "agentName": "MissionCoordinator",
    "invocationId": "invocation-0001",
    "correlationId": "correlation-0001",
    "outcome": "candidate",
    "result": {
        "proposalType": "candidate-location",
        "sourceEventId": "event-0001",
        "sourceEventDigest": "1" * 64,
        "droneId": "drone-vision-01",
        "latitudeMicrodegrees": 44475000,
        "longitudeMicrodegrees": -79245000,
        "commandType": "escalate-rescue",
    },
}

ABSTAINED: dict[str, object] = {
    "agentResponseVersion": 1,
    "missionId": "mission-0001",
    "agentName": "MissionCoordinator",
    "invocationId": "invocation-0002",
    "correlationId": "correlation-0002",
    "outcome": "abstained",
    "reason": "timeout",
}


def _candidate(**changes: object) -> dict[str, object]:
    """Return a fresh candidate body with top-level members replaced or removed."""
    document = deepcopy(CANDIDATE)
    for name, value in changes.items():
        if value is ...:
            del document[name]
        else:
            document[name] = value
    return document


def _result(**changes: object) -> dict[str, object]:
    """Return a candidate body whose nested result has the requested changes."""
    document = _candidate()
    result = dict(cast("Mapping[str, object]", document["result"]))
    for name, value in changes.items():
        if value is ...:
            del result[name]
        else:
            result[name] = value
    document["result"] = result
    return document


def _refusal(document: object) -> tuple[IntegrationRefusal, str, object]:
    """Return the structured refusal for ``document``, failing if it is accepted."""
    try:
        parse_agent_response(document)
    except IntegrationError as error:
        return (error.refusal, error.member, error.value)
    message = f"accepted: {document!r}"
    raise AssertionError(message)


class AgentResponseParsingTests(unittest.TestCase):
    def test_candidate_and_abstention_bodies_parse_to_closed_domain_values(self) -> None:
        # Arrange
        documents = (CANDIDATE, ABSTAINED)

        # Act
        parsed = tuple(parse_agent_response(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                AgentResponse(
                    mission_id="mission-0001",
                    agent_name="MissionCoordinator",
                    invocation_id="invocation-0001",
                    correlation_id="correlation-0001",
                    outcome=AgentOutcome.CANDIDATE,
                    candidate=AgentCandidate(
                        proposal_type="candidate-location",
                        source_event_id="event-0001",
                        source_event_digest="1" * 64,
                        drone_id="drone-vision-01",
                        latitude_microdegrees=44475000,
                        longitude_microdegrees=-79245000,
                        command_type="escalate-rescue",
                    ),
                ),
                AgentResponse(
                    mission_id="mission-0001",
                    agent_name="MissionCoordinator",
                    invocation_id="invocation-0002",
                    correlation_id="correlation-0002",
                    outcome=AgentOutcome.ABSTAINED,
                    reason=AgentResponseReason.TIMEOUT,
                ),
            ),
            parsed,
        )

    def test_refusal_order_is_object_unknown_missing_version_form_then_outcome_binding(
        self,
    ) -> None:
        # Arrange
        documents: tuple[object, ...] = (
            [],
            _candidate(rawError="secret", missionId=...),
            _candidate(missionId=...),
            _candidate(agentResponseVersion=2),
            _candidate(agentResponseVersion=True),
            _candidate(outcome="maybe"),
            _candidate(outcome="abstained"),
        )

        # Act
        outcomes = tuple(_refusal(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (IntegrationRefusal.NOT_AN_OBJECT, "response", documents[0]),
                (IntegrationRefusal.UNKNOWN_MEMBER, "rawError", "secret"),
                (IntegrationRefusal.MISSING_MEMBER, "missionId", None),
                (IntegrationRefusal.VERSION, "agentResponseVersion", 2),
                (IntegrationRefusal.MEMBER_FORM, "agentResponseVersion", True),
                (IntegrationRefusal.MEMBER_FORM, "outcome", "maybe"),
                (IntegrationRefusal.OUTCOME_BINDING, "reason", None),
            ),
            outcomes,
        )

    def test_unknown_member_refusal_is_lexical_and_preserves_the_original_value(
        self,
    ) -> None:
        # Arrange
        out_of_order = _candidate(zUnknown="last", aUnknown="first")
        colliding_names: dict[object, object] = {1: "integer-key", "1": "string-key"}
        colliding_names.update(CANDIDATE)

        # Act
        outcomes = (_refusal(out_of_order), _refusal(colliding_names))

        # Assert
        self.assertEqual(
            (
                (IntegrationRefusal.UNKNOWN_MEMBER, "aUnknown", "first"),
                (IntegrationRefusal.UNKNOWN_MEMBER, "1", "integer-key"),
            ),
            outcomes,
        )

    def test_common_member_refusals_name_the_exact_wire_member_and_value(self) -> None:
        # Arrange
        documents = (
            _candidate(missionId="Mission-0001"),
            _candidate(agentName="Mission-Coordinator"),
            _candidate(invocationId="Invocation-0001"),
            _candidate(correlationId="Correlation-0001"),
        )

        # Act
        errors = []
        for document in documents:
            with pytest.raises(IntegrationError) as captured:
                parse_agent_response(document)
            error = captured.value
            errors.append((error.refusal, error.member, error.value, str(error)))

        # Assert
        self.assertEqual(
            [
                (
                    IntegrationRefusal.MEMBER_FORM,
                    "missionId",
                    "Mission-0001",
                    "member outside its rule: missionId='Mission-0001'",
                ),
                (
                    IntegrationRefusal.MEMBER_FORM,
                    "agentName",
                    "Mission-Coordinator",
                    "member outside its rule: agentName='Mission-Coordinator'",
                ),
                (
                    IntegrationRefusal.MEMBER_FORM,
                    "invocationId",
                    "Invocation-0001",
                    "member outside its rule: invocationId='Invocation-0001'",
                ),
                (
                    IntegrationRefusal.MEMBER_FORM,
                    "correlationId",
                    "Correlation-0001",
                    "member outside its rule: correlationId='Correlation-0001'",
                ),
            ],
            errors,
        )

    def test_candidate_result_is_closed_complete_and_held_to_each_member_rule(self) -> None:
        # Arrange
        documents = (
            _candidate(result=[]),
            _result(prompt="find the subject", sourceEventId=...),
            _result(sourceEventId=...),
            _result(sourceEventId="Event-0001"),
            _result(proposalType="reassignment"),
            _result(sourceEventDigest="A" * 64),
            _result(droneId="Drone-01"),
            _result(latitudeMicrodegrees=90_000_001),
            _result(longitudeMicrodegrees=True),
            _result(commandType="assign-sector"),
        )

        # Act
        outcomes = tuple(_refusal(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (IntegrationRefusal.NOT_AN_OBJECT, "result", []),
                (IntegrationRefusal.UNKNOWN_MEMBER, "result.prompt", "find the subject"),
                (IntegrationRefusal.MISSING_MEMBER, "result.sourceEventId", None),
                (IntegrationRefusal.MEMBER_FORM, "result.sourceEventId", "Event-0001"),
                (IntegrationRefusal.MEMBER_FORM, "result.proposalType", "reassignment"),
                (IntegrationRefusal.MEMBER_FORM, "result.sourceEventDigest", "A" * 64),
                (IntegrationRefusal.MEMBER_FORM, "result.droneId", "Drone-01"),
                (IntegrationRefusal.MEMBER_FORM, "result.latitudeMicrodegrees", 90_000_001),
                (IntegrationRefusal.MEMBER_FORM, "result.longitudeMicrodegrees", True),
                (IntegrationRefusal.MEMBER_FORM, "result.commandType", "assign-sector"),
            ),
            outcomes,
        )

    def test_candidate_location_bounds_are_inclusive(self) -> None:
        # Arrange
        documents = (
            _result(latitudeMicrodegrees=-90_000_000, longitudeMicrodegrees=-180_000_000),
            _result(latitudeMicrodegrees=90_000_000, longitudeMicrodegrees=180_000_000),
        )

        # Act
        round_tripped = tuple(
            agent_response_document(parse_agent_response(document)) for document in documents
        )

        # Assert
        self.assertEqual(documents, round_tripped)

    def test_candidate_outcome_requires_result_and_forbids_reason(self) -> None:
        # Arrange
        without_result = _candidate(result=...)
        with_reason = _candidate(reason="timeout")

        # Act
        outcomes = (_refusal(without_result), _refusal(with_reason))

        # Assert
        self.assertEqual(
            (
                (IntegrationRefusal.OUTCOME_BINDING, "result", None),
                (IntegrationRefusal.OUTCOME_BINDING, "reason", "timeout"),
            ),
            outcomes,
        )

    def test_abstention_reasons_are_closed_and_forbid_candidate_results(self) -> None:
        # Arrange
        invalid_reason = {**ABSTAINED, "reason": "raw-upstream-error"}
        with_result = {**ABSTAINED, "result": CANDIDATE["result"]}

        # Act
        outcomes = (_refusal(invalid_reason), _refusal(with_result))

        # Assert
        self.assertEqual(
            (
                (IntegrationRefusal.MEMBER_FORM, "reason", "raw-upstream-error"),
                (IntegrationRefusal.OUTCOME_BINDING, "result", CANDIDATE["result"]),
            ),
            outcomes,
        )

    def test_a_response_round_trips_without_inventing_an_envelope(self) -> None:
        # Arrange
        responses = tuple(parse_agent_response(document) for document in (CANDIDATE, ABSTAINED))

        # Act
        documents = tuple(agent_response_document(response) for response in responses)

        # Assert
        self.assertEqual(
            ((AGENT_RESPONSE_VERSION, AGENT_RESPONSE_VERSION), (CANDIDATE, ABSTAINED)),
            ((1, 1), documents),
        )


class AgentResponseDecodingTests(unittest.TestCase):
    def test_canonical_bytes_decode_to_the_same_response(self) -> None:
        # Arrange
        raw = canonical.canonical_bytes(CANDIDATE)

        # Act
        response = decode_agent_response(raw)

        # Assert
        self.assertEqual(parse_agent_response(CANDIDATE), response)

    def test_duplicate_keys_and_floating_point_values_are_refused_before_integration_parsing(
        self,
    ) -> None:
        # Arrange
        duplicate = b'{"agentResponseVersion":1,"missionId":"m-1","missionId":"m-2"}'
        floating = b'{"agentResponseVersion":1,"missionId":1.5}'

        # Act
        refusals = []
        for raw in (duplicate, floating):
            with pytest.raises(canonical.CanonicalizationError) as captured:
                decode_agent_response(raw)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [canonical.Refusal.DUPLICATE_KEY, canonical.Refusal.UNSUPPORTED_TYPE],
            refusals,
        )


class AgentResponseTopicBindingTests(unittest.TestCase):
    def test_the_exact_agent_response_topic_is_accepted(self) -> None:
        # Arrange
        response = parse_agent_response(CANDIDATE)
        topic = Topic(
            Family.AGENT_RESPONSE,
            "mission-0001",
            {"agentName": "MissionCoordinator"},
        )

        # Act
        check_agent_response_topic(response, topic)

        # Assert
        self.assertEqual(
            (response.mission_id, response.agent_name),
            (topic.mission_id, topic.parameters["agentName"]),
        )

    def test_the_topic_mission_and_agent_name_must_equal_the_body(self) -> None:
        # Arrange
        response = parse_agent_response(CANDIDATE)
        topics = (
            Topic(Family.AGENT_RESPONSE, "other-mission", {"agentName": "MissionCoordinator"}),
            Topic(Family.AGENT_RESPONSE, "mission-0001", {"agentName": "OtherAgent"}),
        )

        # Act
        outcomes = []
        for topic in topics:
            with pytest.raises(IntegrationError) as captured:
                check_agent_response_topic(response, topic)
            outcomes.append((captured.value.refusal, captured.value.member, captured.value.value))

        # Assert
        self.assertEqual(
            [
                (IntegrationRefusal.TOPIC_BINDING, "missionId", "mission-0001"),
                (IntegrationRefusal.TOPIC_BINDING, "agentName", "MissionCoordinator"),
            ],
            outcomes,
        )

    def test_a_different_topic_family_is_refused_even_when_its_levels_look_compatible(self) -> None:
        # Arrange
        response = parse_agent_response(CANDIDATE)
        topic = Topic(
            Family.AGENT_PROPOSAL,
            "mission-0001",
            {"agentName": "MissionCoordinator", "proposalType": "candidate-location"},
        )

        # Act
        with pytest.raises(IntegrationError) as captured:
            check_agent_response_topic(response, topic)

        # Assert
        self.assertEqual(
            (IntegrationRefusal.TOPIC_BINDING, "family", Family.AGENT_PROPOSAL),
            (captured.value.refusal, captured.value.member, captured.value.value),
        )


if __name__ == "__main__":
    unittest.main()
