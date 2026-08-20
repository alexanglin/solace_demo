"""Typed builder and parser for the application topic families.

The eleven families are the ones ``docs/CONTRACTS.md`` names under ``aerial-rescue/v1``,
and the grammar of every variable level is the decision in
``docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md``. Because every level is
drawn from an allowlist, a formatted topic can never carry a Solace subscription
wildcard, a reserved prefix, an empty level, or a separator inside a level.

This module is pure: it performs no input or output and reads no clock.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import namespace_prefix

MAX_TOPIC_BYTES: Final = 250
"""The Solace SMF bound on a topic, in UTF-8 bytes."""

MAX_IDENTIFIER_LENGTH: Final = 64
MAX_KIND_LENGTH: Final = 32
MAX_AGENT_NAME_LENGTH: Final = 64

IDENTIFIER_PATTERN: Final = "^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$"
"""Mission, drone, command, and request identifiers: lowercase ASCII, interior hyphens."""

KIND_PATTERN: Final = "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
"""Kind levels such as a command or event type; bounded separately by ``MAX_KIND_LENGTH``."""

AGENT_NAME_PATTERN: Final = "^[A-Za-z0-9_]{1,64}$"
"""The ASCII subset of the character class Agent Mesh 1.28.7 coerces agent names to."""

TYPE_PATTERN: Final = "^aerial-rescue\\.v1(?:\\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*){2,3}$"
"""The form of a CloudEvents ``type`` derived from a topic."""

DECISIONS: Final = frozenset({"approve", "reject"})
WILDCARD_CHARACTERS: Final = frozenset({"*", ">"})
MISSION_PARAMETER: Final = "missionId"

_PREFIX_LEVELS: Final = tuple(namespace_prefix().split("/"))
_TYPE_PREFIX: Final = namespace_prefix().replace("/", ".") + "."
_IDENTIFIER_PARAMETERS: Final = frozenset({MISSION_PARAMETER, "droneId", "commandId", "requestId"})


class Rule(Enum):
    """Which grammar a variable level obeys."""

    IDENTIFIER = "identifier"
    KIND = "kind"
    AGENT_NAME = "agent name"
    DECISION = "decision"


_INSTANCE_RULES: Final = frozenset({Rule.IDENTIFIER, Rule.AGENT_NAME})


def rule_for(parameter: str) -> Rule:
    """Return the grammar a named placeholder obeys."""
    if parameter in _IDENTIFIER_PARAMETERS:
        return Rule.IDENTIFIER
    if parameter == "agentName":
        return Rule.AGENT_NAME
    if parameter == "decision":
        return Rule.DECISION
    return Rule.KIND


def _is_placeholder(level: str) -> bool:
    """Report whether a template level names a parameter.

    Only template levels reach this function, and a literal level never begins with a
    brace, so the opening brace alone decides.
    """
    return level.startswith("{")


def _placeholder(level: str) -> str:
    """Return the parameter a template level names."""
    return level[1:-1]


class Family(Enum):
    """The eleven topic families, as templates after the mission identifier level."""

    OPERATOR_COMMAND = "operator/command/{commandType}"
    OPERATOR_APPROVAL = "operator/approval/{decision}"
    DRONE_TELEMETRY = "drone/{droneId}/telemetry"
    DRONE_EVENT = "drone/{droneId}/event/{eventType}"
    DRONE_COMMAND = "drone/{droneId}/command/{commandType}"
    DRONE_COMMAND_RESULT = "drone/{droneId}/command-result/{commandId}"
    GATEWAY_REQUEST = "gateway/request/{operation}"
    GATEWAY_RESPONSE = "gateway/response/{requestId}"
    AGENT_PROPOSAL = "agent/proposal/{agentName}/{proposalType}"
    AGENT_RESPONSE = "agent/response/{agentName}"
    AUDIT = "audit/{recordType}"

    @property
    def levels(self) -> tuple[str, ...]:
        """Return the template split into levels."""
        return tuple(self.value.split("/"))

    @property
    def parameters(self) -> tuple[str, ...]:
        """Return the placeholder names in template order, mission identifier excluded."""
        return tuple(_placeholder(level) for level in self.levels if _is_placeholder(level))

    @property
    def type_suffix(self) -> str:
        """Return the CloudEvents type suffix: the template without its instance levels."""
        kept = (
            level
            for level in self.levels
            if not (_is_placeholder(level) and rule_for(_placeholder(level)) in _INSTANCE_RULES)
        )
        return ".".join(kept)


class TopicRefusal(Enum):
    """Why text or a value is not an application topic."""

    UNSUPPORTED_TYPE = "topic is not a string"
    LENGTH = "topic longer than the broker bound"
    WILDCARD = "topic carries a subscription wildcard"
    PREFIX = "topic outside the versioned application namespace"
    SHAPE = "topic matches no application family"
    IDENTIFIER_FORM = "level outside the identifier form"
    KIND_FORM = "level outside the kind form"
    AGENT_NAME_FORM = "level outside the agent name form"
    DECISION_VALUE = "decision is neither approve nor reject"
    PARAMETER_SET = "parameters do not match the family"


_REFUSAL_BY_RULE: Final = {
    Rule.IDENTIFIER: TopicRefusal.IDENTIFIER_FORM,
    Rule.KIND: TopicRefusal.KIND_FORM,
    Rule.AGENT_NAME: TopicRefusal.AGENT_NAME_FORM,
    Rule.DECISION: TopicRefusal.DECISION_VALUE,
}


class TopicError(ValueError):
    """A value that is not an application topic, carrying the refusal as structured data."""

    def __init__(self, refusal: TopicRefusal, value: object, parameter: str | None = None) -> None:
        """Record the refusal, the offending value, and the parameter it occupied, if any."""
        if parameter is None:
            super().__init__(f"{refusal.value}: {value!r}")
        else:
            super().__init__(f"{refusal.value}: {parameter}={value!r}")
        self.refusal = refusal
        self.value = value
        self.parameter = parameter


@dataclass(frozen=True)
class Topic:
    """One application topic, as the values that identify it rather than as text."""

    family: Family
    mission_id: str
    parameters: Mapping[str, str]


def _conforms(rule: Rule, value: str) -> bool:
    """Report whether a level value obeys its rule."""
    if rule is Rule.IDENTIFIER:
        return re.fullmatch(IDENTIFIER_PATTERN, value) is not None
    if rule is Rule.KIND:
        return len(value) <= MAX_KIND_LENGTH and re.fullmatch(KIND_PATTERN, value) is not None
    if rule is Rule.AGENT_NAME:
        return re.fullmatch(AGENT_NAME_PATTERN, value) is not None
    return value in DECISIONS


def _validated(parameter: str, value: str) -> str:
    """Return a level value, refusing it by the rule of the parameter it occupies."""
    rule = rule_for(parameter)
    if not _conforms(rule, value):
        raise TopicError(_REFUSAL_BY_RULE[rule], value, parameter)
    return value


def format_topic(topic: Topic) -> str:
    """Return the topic text for a topic value.

    Args:
        topic: The family, mission identifier, and the family's parameters.

    Returns:
        The topic text, which never carries a wildcard, a reserved prefix, or an empty level.

    Raises:
        TopicError: If the parameters do not match the family or a level breaks its rule.
    """
    if set(topic.parameters) != set(topic.family.parameters):
        raise TopicError(TopicRefusal.PARAMETER_SET, tuple(sorted(topic.parameters)))
    levels = [namespace_prefix(), _validated(MISSION_PARAMETER, topic.mission_id)]
    for level in topic.family.levels:
        if _is_placeholder(level):
            name = _placeholder(level)
            levels.append(_validated(name, topic.parameters[name]))
        else:
            levels.append(level)
    return "/".join(levels)


def _matches_template(template: tuple[str, ...], levels: list[str]) -> bool:
    """Report whether levels fit a template: same count, equal literals."""
    if len(template) != len(levels):
        return False
    return all(
        _is_placeholder(expected) or expected == levels[index]
        for index, expected in enumerate(template)
    )


def _bind(template: tuple[str, ...], levels: list[str]) -> dict[str, str]:
    """Validate and bind every placeholder of a template, in template order."""
    return {
        _placeholder(expected): _validated(_placeholder(expected), levels[index])
        for index, expected in enumerate(template)
        if _is_placeholder(expected)
    }


def _family_for(levels: list[str], text: str) -> Family:
    """Return the family whose template the family levels fit."""
    for family in Family:
        if _matches_template(family.levels, levels):
            return family
    raise TopicError(TopicRefusal.SHAPE, text)


def parse_topic(text: object) -> Topic:
    """Parse topic text into a topic value, refusing in a fixed order.

    The order is part of the contract: not a string, longer than the broker bound, a
    subscription wildcard anywhere, a foreign prefix, a shape matching no family, then each
    level against its rule with the mission identifier first.

    Args:
        text: The candidate topic text.

    Returns:
        The topic value, such that ``format_topic`` reproduces the text.

    Raises:
        TopicError: If the text is not an application topic.
    """
    if not isinstance(text, str):
        raise TopicError(TopicRefusal.UNSUPPORTED_TYPE, text)
    if len(text.encode()) > MAX_TOPIC_BYTES:
        raise TopicError(TopicRefusal.LENGTH, text)
    if any(character in WILDCARD_CHARACTERS for character in text):
        raise TopicError(TopicRefusal.WILDCARD, text)
    levels = text.split("/")
    if tuple(levels[:2]) != _PREFIX_LEVELS:
        raise TopicError(TopicRefusal.PREFIX, text)
    family = _family_for(levels[3:], text)
    mission_id = _validated(MISSION_PARAMETER, levels[2])
    return Topic(family, mission_id, _bind(family.levels, levels[3:]))


def event_type(topic: Topic) -> str:
    """Return the CloudEvents type of a topic: its family suffix with the kind levels filled."""
    segments = []
    for level in topic.family.type_suffix.split("."):
        if _is_placeholder(level):
            name = _placeholder(level)
            segments.append(_validated(name, topic.parameters[name]))
        else:
            segments.append(level)
    return _TYPE_PREFIX + ".".join(segments)


def parse_event_type(text: object) -> tuple[Family, dict[str, str]]:
    """Recover the family and the kind parameters from a CloudEvents type.

    Args:
        text: The candidate type.

    Returns:
        The family and the kind or decision parameters the type carries.

    Raises:
        TopicError: If the text is not a string, lies outside the namespace, matches no
            family, or carries a kind level outside its rule.
    """
    if not isinstance(text, str):
        raise TopicError(TopicRefusal.UNSUPPORTED_TYPE, text)
    if not text.startswith(_TYPE_PREFIX):
        raise TopicError(TopicRefusal.PREFIX, text)
    segments = text.removeprefix(_TYPE_PREFIX).split(".")
    for family in Family:
        template = tuple(family.type_suffix.split("."))
        if _matches_template(template, segments):
            return family, _bind(template, segments)
    raise TopicError(TopicRefusal.SHAPE, text)
