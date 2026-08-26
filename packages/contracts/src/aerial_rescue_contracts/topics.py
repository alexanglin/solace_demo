"""Typed builder and parser for the application topic families.

The thirteen families are the ones ``docs/CONTRACTS.md`` names under ``aerial-rescue/v1``,
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

RESERVED_REPLY_MISSION: Final = "reply"
"""The mission level of the command-gateway reply channel, which names no mission.

Solace AI Connector fixes a requestor's reply topic once per session, before any mission
exists, so the level cannot carry one (``docs/adr/0070``). It is refused as an envelope
``subject`` rather than as a topic level, because the reply channel's own topic has to stay
formattable and parseable by the component that publishes to it.
"""

DECISIONS: Final = frozenset({"approve", "reject"})
WILDCARD_CHARACTERS: Final = frozenset({"*", ">"})
MISSION_PARAMETER: Final = "missionId"
DRONE_PARAMETER: Final = "droneId"
SECTOR_PARAMETER: Final = "sectorId"

_PREFIX_LEVELS: Final = tuple(namespace_prefix().split("/"))
_TYPE_PREFIX: Final = namespace_prefix().replace("/", ".") + "."
_IDENTIFIER_PARAMETERS: Final = frozenset(
    {MISSION_PARAMETER, DRONE_PARAMETER, SECTOR_PARAMETER, "commandId", "requestId"}
)


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
    """The thirteen topic families, as templates after the mission identifier level."""

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
    MISSION_EVENT = "mission/event/{eventType}"
    SECTOR_EVENT = "sector/{sectorId}/event/{eventType}"

    @property
    def levels(self) -> tuple[str, ...]:
        """Return the template split into levels."""
        return tuple(self.value.split("/"))

    @property
    def parameters(self) -> tuple[str, ...]:
        """Return the placeholder names in template order, mission identifier excluded."""
        return tuple(_placeholder(level) for level in self.levels if _is_placeholder(level))

    @property
    def literal_suffix(self) -> str:
        """Return the template's literal levels joined with dots, which names the family.

        Distinct from :attr:`type_suffix`, which keeps the kind and decision placeholders
        because a CloudEvents type fills them with the value the event carried. A name for
        the family itself carries none of them: one name covers every kind in the family.
        """
        return ".".join(level for level in self.levels if not _is_placeholder(level))

    @property
    def type_suffix(self) -> str:
        """Return the CloudEvents type suffix: the template without its instance levels."""
        kept = (
            level
            for level in self.levels
            if not (_is_placeholder(level) and rule_for(_placeholder(level)) in _INSTANCE_RULES)
        )
        return ".".join(kept)


class Delivery(Enum):
    """What delivery guarantee a family is owed (ADR-0079)."""

    DIRECT = "direct"
    GUARANTEED = "guaranteed"
    REQUEST_REPLY = "request-reply"


_DELIVERY: Final[Mapping[Family, Delivery]] = {
    Family.OPERATOR_COMMAND: Delivery.GUARANTEED,
    Family.OPERATOR_APPROVAL: Delivery.GUARANTEED,
    Family.DRONE_TELEMETRY: Delivery.DIRECT,
    Family.DRONE_EVENT: Delivery.GUARANTEED,
    Family.DRONE_COMMAND: Delivery.GUARANTEED,
    Family.DRONE_COMMAND_RESULT: Delivery.GUARANTEED,
    Family.GATEWAY_REQUEST: Delivery.REQUEST_REPLY,
    Family.GATEWAY_RESPONSE: Delivery.REQUEST_REPLY,
    Family.AGENT_PROPOSAL: Delivery.GUARANTEED,
    Family.AGENT_RESPONSE: Delivery.GUARANTEED,
    Family.AUDIT: Delivery.GUARANTEED,
    Family.MISSION_EVENT: Delivery.GUARANTEED,
    Family.SECTOR_EVENT: Delivery.GUARANTEED,
}
"""Total over the families; a test asserts it.

``DRONE_TELEMETRY`` is direct because a current position supersedes a stale one, which is
what ``docs/CONTRACTS.md`` has always said. The two gateway families are neither: their
reply queue is a temporary one Solace AI Connector names and binds itself, so this project
provisions no endpoint for them
(``docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md``).
"""


def delivery_for(family: Family) -> Delivery:
    """Return the delivery guarantee a family is owed.

    Args:
        family: The topic family.

    Returns:
        The guarantee. The lookup is total, so there is no refusal: a family added
        without a row fails the table's own test rather than falling back here.
    """
    return _DELIVERY[family]


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


def validated_level(parameter: str, value: str) -> str:
    """Return a level value, refusing it by the rule of the parameter it occupies.

    Public because the broker adapter builds subscription strings, which the topic grammar
    refuses to build itself, and a subscription carrying a concrete identifier level must
    hold that level to the same rule a published topic would. Applying the rule there
    instead would put the identifier form in a second home.

    Args:
        parameter: The template parameter the level occupies, such as ``droneId``.
        value: The candidate level.

    Returns:
        The value, unchanged, when it obeys the parameter's rule.

    Raises:
        TopicError: With the refusal belonging to that parameter's rule, naming both the
            parameter and the value.
    """
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
    levels = [namespace_prefix(), validated_level(MISSION_PARAMETER, topic.mission_id)]
    for level in topic.family.levels:
        if _is_placeholder(level):
            name = _placeholder(level)
            levels.append(validated_level(name, topic.parameters[name]))
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
        _placeholder(expected): validated_level(_placeholder(expected), levels[index])
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
    mission_id = validated_level(MISSION_PARAMETER, levels[2])
    return Topic(family, mission_id, _bind(family.levels, levels[3:]))


def event_type(topic: Topic) -> str:
    """Return the CloudEvents type of a topic: its family suffix with the kind levels filled."""
    segments = []
    for level in topic.family.type_suffix.split("."):
        if _is_placeholder(level):
            name = _placeholder(level)
            segments.append(validated_level(name, topic.parameters[name]))
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
