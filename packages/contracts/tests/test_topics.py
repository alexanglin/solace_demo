"""The fifteen topic families, their level grammar, and the type binding.

Each refusal is asserted by its structured reason and, where a level is at fault, by the
parameter it occupied, so a producer learns which value to fix rather than reading prose.
"""

from __future__ import annotations

import re
import unittest

import pytest
from aerial_rescue_contracts import namespace_prefix
from aerial_rescue_contracts.topics import (
    MAX_AGENT_NAME_LENGTH,
    MAX_IDENTIFIER_LENGTH,
    MAX_KIND_LENGTH,
    MAX_TOPIC_BYTES,
    TYPE_PATTERN,
    Family,
    Topic,
    TopicError,
    TopicRefusal,
    event_type,
    format_topic,
    parse_event_type,
    parse_topic,
)

EXAMPLES = (
    Topic(Family.OPERATOR_COMMAND, "m1", {"commandType": "reassign-sector"}),
    Topic(Family.OPERATOR_APPROVAL, "m1", {"decision": "approve"}),
    Topic(Family.DRONE_TELEMETRY, "m1", {"droneId": "d1"}),
    Topic(Family.DRONE_EVENT, "m1", {"droneId": "d1", "eventType": "salient"}),
    Topic(Family.DRONE_COMMAND, "m1", {"droneId": "d1", "commandType": "reassign-sector"}),
    Topic(Family.DRONE_COMMAND_RESULT, "m1", {"droneId": "d1", "commandId": "c1"}),
    Topic(Family.GATEWAY_REQUEST, "m1", {"operation": "fleet-status"}),
    Topic(Family.GATEWAY_RESPONSE, "reply", {"requestorId": "r1"}),
    Topic(Family.GATEWAY_RECORD, "m1", {"requestId": "r1"}),
    Topic(
        Family.AGENT_PROPOSAL,
        "m1",
        {"agentName": "MissionCoordinator", "proposalType": "reassignment"},
    ),
    Topic(Family.AGENT_RESPONSE, "m1", {"agentName": "MissionCoordinator"}),
    Topic(Family.AUDIT, "m1", {"recordType": "note"}),
)
EXAMPLE_TEXTS = (
    "aerial-rescue/v1/m1/operator/command/reassign-sector",
    "aerial-rescue/v1/m1/operator/approval/approve",
    "aerial-rescue/v1/m1/drone/d1/telemetry",
    "aerial-rescue/v1/m1/drone/d1/event/salient",
    "aerial-rescue/v1/m1/drone/d1/command/reassign-sector",
    "aerial-rescue/v1/m1/drone/d1/command-result/c1",
    "aerial-rescue/v1/m1/gateway/request/fleet-status",
    "aerial-rescue/v1/reply/gateway/response/r1",
    "aerial-rescue/v1/m1/gateway/record/r1",
    "aerial-rescue/v1/m1/agent/proposal/MissionCoordinator/reassignment",
    "aerial-rescue/v1/m1/agent/response/MissionCoordinator",
    "aerial-rescue/v1/m1/audit/note",
)
EXAMPLE_TYPES = (
    "aerial-rescue.v1.operator.command.reassign-sector",
    "aerial-rescue.v1.operator.approval.approve",
    "aerial-rescue.v1.drone.telemetry",
    "aerial-rescue.v1.drone.event.salient",
    "aerial-rescue.v1.drone.command.reassign-sector",
    "aerial-rescue.v1.drone.command-result",
    "aerial-rescue.v1.gateway.request.fleet-status",
    "aerial-rescue.v1.gateway.response",
    "aerial-rescue.v1.gateway.record",
    "aerial-rescue.v1.agent.proposal.reassignment",
    "aerial-rescue.v1.agent.response",
    "aerial-rescue.v1.audit.note",
)
FORBIDDEN_IDENTIFIERS = (
    "d*1",
    "d>1",
    "#d1",
    "!d1",
    "d+1",
    "d/1",
    "d 1",
    "",
    "D1",
    "d_1",
    "-d1",
    "d1-",
    "d.1",
    "dé1",
)


def _error_of(topic: Topic) -> TopicError:
    """Return the error formatting ``topic`` raises, failing the test if it is accepted."""
    try:
        format_topic(topic)
    except TopicError as error:
        return error
    message = f"accepted: {topic!r}"
    raise AssertionError(message)


def _parse_error_of(text: object) -> TopicError:
    """Return the error parsing ``text`` raises, failing the test if it is accepted."""
    try:
        parse_topic(text)
    except TopicError as error:
        return error
    message = f"accepted: {text!r}"
    raise AssertionError(message)


def _type_refusal_of(text: object) -> tuple[TopicRefusal, object]:
    """Return the refusal and value parsing a type raises, failing the test if accepted."""
    try:
        parse_event_type(text)
    except TopicError as error:
        return (error.refusal, error.value)
    message = f"accepted: {text!r}"
    raise AssertionError(message)


def _drone(level: str) -> Topic:
    """Return a telemetry topic whose drone identifier is ``level``."""
    return Topic(Family.DRONE_TELEMETRY, "m1", {"droneId": level})


def _agent(name: str) -> Topic:
    """Return an agent response topic for ``name``."""
    return Topic(Family.AGENT_RESPONSE, "m1", {"agentName": name})


def _event(kind: str) -> Topic:
    """Return a drone event topic whose event type is ``kind``."""
    return Topic(Family.DRONE_EVENT, "m1", {"droneId": "d1", "eventType": kind})


class FamilyFormattingTests(unittest.TestCase):
    def test_each_family_formats_to_its_documented_template(self) -> None:
        # Arrange
        topics = EXAMPLES

        # Act
        formatted = tuple(format_topic(topic) for topic in topics)

        # Assert
        self.assertEqual(EXAMPLE_TEXTS, formatted)

    def test_every_family_begins_with_the_namespace_prefix(self) -> None:
        # Arrange
        topics = EXAMPLES

        # Act
        prefixes = {"/".join(format_topic(topic).split("/")[:2]) for topic in topics}

        # Assert
        self.assertEqual({namespace_prefix()}, prefixes)

    def test_lifecycle_topics_format_parse_and_bind_to_their_concrete_types(self) -> None:
        # Arrange
        cases = (
            (
                "DRONE_EVENT",
                "drone/{droneId}/event/{eventType}",
                {"droneId": "drone-01", "eventType": "connectivity-changed"},
                "aerial-rescue/v1/mission-01/drone/drone-01/event/connectivity-changed",
                "aerial-rescue.v1.drone.event.connectivity-changed",
            ),
            (
                "MISSION_EVENT",
                "mission/event/{eventType}",
                {"eventType": "lifecycle"},
                "aerial-rescue/v1/mission-01/mission/event/lifecycle",
                "aerial-rescue.v1.mission.event.lifecycle",
            ),
            (
                "SECTOR_EVENT",
                "sector/{sectorId}/event/{eventType}",
                {"sectorId": "sector-01", "eventType": "lifecycle"},
                "aerial-rescue/v1/mission-01/sector/sector-01/event/lifecycle",
                "aerial-rescue.v1.sector.event.lifecycle",
            ),
            (
                "EVIDENCE_DECISION",
                "evidence/decision/{proposalId}",
                {"proposalId": "1"},
                "aerial-rescue/v1/mission-01/evidence/decision/1",
                "aerial-rescue.v1.evidence.decision",
            ),
        )

        # Act
        outcomes: list[tuple[str, str | None, str | None, str | None, tuple[str, str] | None]] = []
        for name, _template, parameters, topic_text, type_text in cases:
            family = Family.__members__.get(name)
            if family is None:
                outcomes.append((name, None, None, None, None))
                continue
            topic = Topic(family, "mission-01", parameters)
            outcomes.append(
                (
                    name,
                    family.value,
                    format_topic(topic),
                    event_type(topic),
                    (parse_topic(topic_text).family.name, parse_event_type(type_text)[0].name),
                )
            )

        # Assert
        self.assertEqual(
            [
                (name, template, topic_text, type_text, (name, name))
                for name, template, _, topic_text, type_text in cases
            ],
            outcomes,
        )


class FamilyInvariantTests(unittest.TestCase):
    def test_the_fifteen_templates_are_pairwise_distinguishable_by_literal_levels(self) -> None:
        # Arrange
        signatures = tuple(
            (
                len(family.levels),
                tuple(level for level in family.levels if not level.startswith("{")),
            )
            for family in Family
        )

        # Act
        distinct = len(set(signatures))

        # Assert
        self.assertEqual((15, 15), (len(Family), distinct))

    def test_parameters_and_type_suffix_are_read_from_the_template(self) -> None:
        # Arrange
        family = Family.AGENT_PROPOSAL

        # Act
        facts = (family.parameters, family.type_suffix, Family.DRONE_COMMAND_RESULT.type_suffix)

        # Assert
        self.assertEqual(
            (
                ("agentName", "proposalType"),
                "agent.proposal.{proposalType}",
                "drone.command-result",
            ),
            facts,
        )


class IdentifierRuleTests(unittest.TestCase):
    def test_each_forbidden_character_in_a_drone_identifier_is_refused(self) -> None:
        # Arrange
        levels = FORBIDDEN_IDENTIFIERS

        # Act
        outcomes = tuple(
            (_error_of(_drone(level)).refusal, _error_of(_drone(level)).parameter)
            for level in levels
        )

        # Assert
        self.assertEqual(((TopicRefusal.IDENTIFIER_FORM, "droneId"),) * len(levels), outcomes)

    def test_a_refused_identifier_names_its_parameter_and_value(self) -> None:
        # Arrange
        topic = _drone("d*1")

        # Act
        with pytest.raises(TopicError) as captured:
            format_topic(topic)

        # Assert
        self.assertEqual(("droneId", "d*1"), (captured.value.parameter, captured.value.value))

    def test_identifier_length_bounds(self) -> None:
        # Arrange
        at_bound = Topic(Family.AUDIT, "m" * MAX_IDENTIFIER_LENGTH, {"recordType": "note"})
        over = Topic(Family.AUDIT, "m" * (MAX_IDENTIFIER_LENGTH + 1), {"recordType": "note"})

        # Act
        outcomes = (format_topic(at_bound), _error_of(over).refusal, _error_of(over).parameter)

        # Assert
        self.assertEqual(
            (
                "aerial-rescue/v1/" + "m" * MAX_IDENTIFIER_LENGTH + "/audit/note",
                TopicRefusal.IDENTIFIER_FORM,
                "missionId",
            ),
            outcomes,
        )

    def test_a_lowercase_uuid_and_a_single_character_are_identifiers(self) -> None:
        # Arrange
        uuid = "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6b"
        topics = (
            Topic(Family.DRONE_COMMAND_RESULT, "m1", {"droneId": "d1", "commandId": uuid}),
            Topic(Family.GATEWAY_RESPONSE, "reply", {"requestorId": "r"}),
            Topic(Family.GATEWAY_RECORD, "7", {"requestId": "r"}),
        )

        # Act
        formatted = tuple(format_topic(topic) for topic in topics)

        # Assert
        self.assertEqual(
            (
                "aerial-rescue/v1/m1/drone/d1/command-result/" + uuid,
                "aerial-rescue/v1/reply/gateway/response/r",
                "aerial-rescue/v1/7/gateway/record/r",
            ),
            formatted,
        )


class KindRuleTests(unittest.TestCase):
    def test_kind_bounds_and_forms(self) -> None:
        # Arrange
        accepted = ("salient", "reassign-sector", "a1", "t" * MAX_KIND_LENGTH)
        refused = (
            "Salient",
            "1salient",
            "re--assign",
            "-salient",
            "salient-",
            "t" * (MAX_KIND_LENGTH + 1),
            "",
            "re_assign",
        )

        # Act
        outcomes = (
            tuple(format_topic(_event(kind)).rsplit("/", 1)[1] for kind in accepted),
            tuple(
                (_error_of(_event(kind)).refusal, _error_of(_event(kind)).parameter)
                for kind in refused
            ),
        )

        # Assert
        self.assertEqual(
            (accepted, ((TopicRefusal.KIND_FORM, "eventType"),) * len(refused)), outcomes
        )


class AgentNameRuleTests(unittest.TestCase):
    def test_agent_mesh_name_shapes_are_accepted(self) -> None:
        # Arrange
        names = (
            "MissionCoordinator",
            "sam_test_agent",
            "_leading",
            "1Agent",
            "A",
            "a" * MAX_AGENT_NAME_LENGTH,
        )

        # Act
        tails = tuple(format_topic(_agent(name)).rsplit("/", 1)[1] for name in names)

        # Assert
        self.assertEqual(names, tails)

    def test_other_agent_name_shapes_are_refused(self) -> None:
        # Arrange
        names = (
            "drone-vision-01",
            "Mission*",
            "Mission>",
            "Mission/Coordinator",
            "Mission Coordinator",
            "",
            "a" * (MAX_AGENT_NAME_LENGTH + 1),
            "Coördinator",
        )

        # Act
        outcomes = tuple(
            (_error_of(_agent(name)).refusal, _error_of(_agent(name)).parameter) for name in names
        )

        # Assert
        self.assertEqual(((TopicRefusal.AGENT_NAME_FORM, "agentName"),) * len(names), outcomes)


class DecisionRuleTests(unittest.TestCase):
    def test_only_approve_and_reject_are_decisions(self) -> None:
        # Arrange
        accepted = ("approve", "reject")
        refused = ("approved", "Approve", "maybe", "")

        # Act
        outcomes = (
            tuple(
                format_topic(Topic(Family.OPERATOR_APPROVAL, "m1", {"decision": decision})).rsplit(
                    "/", 1
                )[1]
                for decision in accepted
            ),
            tuple(
                _error_of(Topic(Family.OPERATOR_APPROVAL, "m1", {"decision": decision})).refusal
                for decision in refused
            ),
        )

        # Assert
        self.assertEqual((accepted, (TopicRefusal.DECISION_VALUE,) * len(refused)), outcomes)


class ParameterSetTests(unittest.TestCase):
    def test_a_missing_or_extra_parameter_is_refused(self) -> None:
        # Arrange
        topics = (
            Topic(Family.DRONE_EVENT, "m1", {"droneId": "d1"}),
            Topic(Family.DRONE_TELEMETRY, "m1", {"droneId": "d1", "eventType": "salient"}),
            Topic(Family.AUDIT, "m1", {"recordtype": "note"}),
        )

        # Act
        outcomes = tuple((_error_of(topic).refusal, _error_of(topic).value) for topic in topics)

        # Assert
        self.assertEqual(
            (
                (TopicRefusal.PARAMETER_SET, ("droneId",)),
                (TopicRefusal.PARAMETER_SET, ("droneId", "eventType")),
                (TopicRefusal.PARAMETER_SET, ("recordtype",)),
            ),
            outcomes,
        )


class LengthTests(unittest.TestCase):
    def test_the_longest_well_formed_topics_fit_the_broker_bound(self) -> None:
        # Arrange
        full = "z" * MAX_IDENTIFIER_LENGTH
        longest = (
            Topic(Family.DRONE_COMMAND_RESULT, full, {"droneId": full, "commandId": full}),
            Topic(
                Family.AGENT_PROPOSAL,
                full,
                {"agentName": "Z" * MAX_AGENT_NAME_LENGTH, "proposalType": "z" * MAX_KIND_LENGTH},
            ),
        )

        # Act
        lengths = tuple(len(format_topic(topic).encode()) for topic in longest)

        # Assert
        self.assertEqual(((232, 194), True), (lengths, max(lengths) <= MAX_TOPIC_BYTES))


class ParsingTests(unittest.TestCase):
    def test_each_documented_topic_parses_back_to_its_topic(self) -> None:
        # Arrange
        texts = EXAMPLE_TEXTS

        # Act
        parsed = tuple(parse_topic(text) for text in texts)

        # Assert
        self.assertEqual(EXAMPLES, parsed)

    def test_a_non_string_is_refused(self) -> None:
        # Arrange
        value = ["aerial-rescue", "v1"]

        # Act
        with pytest.raises(TopicError) as captured:
            parse_topic(value)

        # Assert
        self.assertEqual(
            (TopicRefusal.UNSUPPORTED_TYPE, value), (captured.value.refusal, captured.value.value)
        )

    def test_text_over_the_byte_bound_is_refused_before_any_other_rule(self) -> None:
        # Arrange
        text = "other/*/" + "m" * (MAX_TOPIC_BYTES - 7)

        # Act
        with pytest.raises(TopicError) as captured:
            parse_topic(text)

        # Assert
        self.assertEqual(
            (TopicRefusal.LENGTH, MAX_TOPIC_BYTES + 1, text),
            (captured.value.refusal, len(text.encode()), captured.value.value),
        )

    def test_text_at_the_byte_bound_is_judged_by_the_later_rules(self) -> None:
        # Arrange
        text = "aerial-rescue/v1/" + "m" * (MAX_TOPIC_BYTES - 17)

        # Act
        with pytest.raises(TopicError) as captured:
            parse_topic(text)

        # Assert
        self.assertEqual(
            (TopicRefusal.SHAPE, MAX_TOPIC_BYTES), (captured.value.refusal, len(text.encode()))
        )

    def test_a_subscription_wildcard_is_refused_before_the_prefix_rule(self) -> None:
        # Arrange
        texts = (
            "other/v1/*/audit/x",
            "aerial-rescue/v1/m1/drone/>",
            "aerial-rescue/v1/*/drone/*/telemetry",
        )

        # Act
        outcomes = tuple(
            (_parse_error_of(text).refusal, _parse_error_of(text).value) for text in texts
        )

        # Assert
        self.assertEqual(tuple((TopicRefusal.WILDCARD, text) for text in texts), outcomes)

    def test_a_foreign_prefix_is_refused(self) -> None:
        # Arrange
        texts = (
            "aerial-rescue/v2/m1/audit/note",
            "aerial-rescue/validation/a2a/v1/agent/request/ValidationAgent",
            "Aerial-Rescue/v1/m1/audit/note",
            "/aerial-rescue/v1/m1/audit/note",
            "aerial-rescue",
        )

        # Act
        outcomes = tuple(
            (_parse_error_of(text).refusal, _parse_error_of(text).value) for text in texts
        )

        # Assert
        self.assertEqual(tuple((TopicRefusal.PREFIX, text) for text in texts), outcomes)

    def test_an_unknown_shape_is_refused(self) -> None:
        # Arrange
        texts = (
            "aerial-rescue/v1/m1/drone/d1/status",
            "aerial-rescue/v1/m1/audit/note/",
            "aerial-rescue/v1/m1",
            "aerial-rescue/v1",
            "aerial-rescue/v1/m1/drone/d1/command/reassign/now",
        )

        # Act
        outcomes = tuple(
            (_parse_error_of(text).refusal, _parse_error_of(text).value) for text in texts
        )

        # Assert
        self.assertEqual(tuple((TopicRefusal.SHAPE, text) for text in texts), outcomes)

    def test_an_empty_or_reserved_level_is_refused_as_the_parameter_it_occupies(self) -> None:
        # Arrange
        cases = (
            ("aerial-rescue/v1//audit/note", TopicRefusal.IDENTIFIER_FORM, "missionId"),
            ("aerial-rescue/v1/m1/drone//telemetry", TopicRefusal.IDENTIFIER_FORM, "droneId"),
            ("aerial-rescue/v1/m1/drone/#d1/telemetry", TopicRefusal.IDENTIFIER_FORM, "droneId"),
            ("aerial-rescue/v1/m1/drone/+/event/salient", TopicRefusal.IDENTIFIER_FORM, "droneId"),
            ("aerial-rescue/v1/m1/drone/d1/event/!salient", TopicRefusal.KIND_FORM, "eventType"),
            (
                "aerial-rescue/v1/m1/agent/response/Mission Coordinator",
                TopicRefusal.AGENT_NAME_FORM,
                "agentName",
            ),
            (
                "aerial-rescue/v1/m1/operator/approval/maybe",
                TopicRefusal.DECISION_VALUE,
                "decision",
            ),
        )

        # Act
        outcomes = tuple(
            (_parse_error_of(text).refusal, _parse_error_of(text).parameter) for text, _, _ in cases
        )

        # Assert
        self.assertEqual(tuple((refusal, parameter) for _, refusal, parameter in cases), outcomes)


class EventTypeTests(unittest.TestCase):
    def test_event_type_drops_identifier_levels_and_keeps_kind_levels(self) -> None:
        # Arrange
        topics = EXAMPLES

        # Act
        types = tuple(event_type(topic) for topic in topics)

        # Assert
        self.assertEqual(EXAMPLE_TYPES, types)

    def test_every_event_type_matches_the_shared_type_pattern(self) -> None:
        # Arrange
        types = tuple(event_type(topic) for topic in EXAMPLES)

        # Act
        matched = tuple(re.fullmatch(TYPE_PATTERN, value) is not None for value in types)

        # Assert
        self.assertEqual((True,) * len(types), matched)

    def test_parse_event_type_recovers_the_family_and_its_kinds(self) -> None:
        # Arrange
        text = "aerial-rescue.v1.drone.command.reassign-sector"

        # Act
        recovered = parse_event_type(text)

        # Assert
        self.assertEqual((Family.DRONE_COMMAND, {"commandType": "reassign-sector"}), recovered)

    def test_event_type_refusals(self) -> None:
        # Arrange
        cases = (
            42,
            "com.example.x",
            "aerial-rescue.v1",
            "aerial-rescue.v1.drone.status",
            "aerial-rescue.v1.drone.command-result.c1",
            "aerial-rescue.v1.drone.command.Reassign",
            "aerial-rescue.v1.operator.approval.maybe",
        )

        # Act
        outcomes = tuple(_type_refusal_of(case) for case in cases)

        # Assert
        self.assertEqual(
            (
                (TopicRefusal.UNSUPPORTED_TYPE, 42),
                (TopicRefusal.PREFIX, "com.example.x"),
                (TopicRefusal.PREFIX, "aerial-rescue.v1"),
                (TopicRefusal.SHAPE, "aerial-rescue.v1.drone.status"),
                (TopicRefusal.SHAPE, "aerial-rescue.v1.drone.command-result.c1"),
                (TopicRefusal.KIND_FORM, "Reassign"),
                (TopicRefusal.DECISION_VALUE, "maybe"),
            ),
            outcomes,
        )

    def test_event_type_refuses_a_kind_level_outside_its_rule(self) -> None:
        # Arrange
        topic = Topic(Family.DRONE_COMMAND, "m1", {"droneId": "d1", "commandType": "Reassign"})

        # Act
        with pytest.raises(TopicError) as captured:
            event_type(topic)

        # Assert
        self.assertEqual(
            (TopicRefusal.KIND_FORM, "commandType"),
            (captured.value.refusal, captured.value.parameter),
        )


class TopicErrorTests(unittest.TestCase):
    def test_the_message_names_refusal_parameter_and_value(self) -> None:
        # Arrange
        errors = (
            TopicError(TopicRefusal.IDENTIFIER_FORM, "d*1", "droneId"),
            TopicError(TopicRefusal.WILDCARD, "a/*"),
        )

        # Act
        messages = tuple(str(error) for error in errors)

        # Assert
        self.assertEqual(
            (
                "level outside the identifier form: droneId='d*1'",
                "topic carries a subscription wildcard: 'a/*'",
            ),
            messages,
        )


if __name__ == "__main__":
    unittest.main()


class FamilyNameTests(unittest.TestCase):
    def test_each_family_is_named_by_its_literal_levels(self) -> None:
        # Arrange
        expected = (
            "operator.command",
            "operator.approval",
            "drone.telemetry",
            "drone.event",
            "drone.command",
            "drone.command-result",
            "gateway.request",
            "gateway.response",
            "gateway.record",
            "agent.proposal",
            "agent.response",
            "evidence.decision",
            "audit",
            "mission.event",
            "sector.event",
        )

        # Act
        suffixes = tuple(family.literal_suffix for family in Family)

        # Assert
        self.assertEqual(expected, suffixes)

    def test_no_two_families_share_a_name(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        suffixes = {family.literal_suffix for family in families}

        # Assert
        self.assertEqual(len(families), len(suffixes))

    def test_a_family_name_carries_no_placeholder(self) -> None:
        # Arrange
        braces = frozenset("{}")

        # Act
        offending = tuple(family for family in Family if braces & set(family.literal_suffix))

        # Assert
        self.assertEqual((), offending)
