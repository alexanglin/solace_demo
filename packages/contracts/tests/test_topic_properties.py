"""Property-based invariants of the topic grammar.

Module-level functions with ``derandomize`` for the same reason as
``test_canonical_properties.py``.
"""

from __future__ import annotations

import re

import pytest
from aerial_rescue_contracts import namespace_prefix
from aerial_rescue_contracts.topics import (
    AGENT_NAME_PATTERN,
    DECISIONS,
    IDENTIFIER_PATTERN,
    KIND_PATTERN,
    MAX_KIND_LENGTH,
    Family,
    Rule,
    Topic,
    TopicError,
    TopicRefusal,
    event_type,
    format_topic,
    parse_event_type,
    parse_topic,
    rule_for,
)
from hypothesis import given, settings
from hypothesis import strategies as st

IDENTIFIERS = st.from_regex(IDENTIFIER_PATTERN, fullmatch=True)
KINDS = st.from_regex(KIND_PATTERN, fullmatch=True).filter(
    lambda kind: len(kind) <= MAX_KIND_LENGTH
)
AGENT_NAMES = st.from_regex(AGENT_NAME_PATTERN, fullmatch=True)
DECISION_VALUES = st.sampled_from(sorted(DECISIONS))
STRATEGY_BY_RULE = {
    Rule.IDENTIFIER: IDENTIFIERS,
    Rule.KIND: KINDS,
    Rule.AGENT_NAME: AGENT_NAMES,
    Rule.DECISION: DECISION_VALUES,
}
FORBIDDEN_CHARACTERS = frozenset("*>#!+ ")
KIND_RULES = frozenset({Rule.KIND, Rule.DECISION})


@st.composite
def topics(draw: st.DrawFn) -> Topic:
    """Draw a well-formed topic of any family."""
    family = draw(st.sampled_from(list(Family)))
    parameters = {name: draw(STRATEGY_BY_RULE[rule_for(name)]) for name in family.parameters}
    return Topic(family, draw(IDENTIFIERS), parameters)


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(topics())
def test_format_then_parse_round_trips(topic: Topic) -> None:
    # Arrange
    text = format_topic(topic)

    # Act
    parsed = parse_topic(text)

    # Assert
    assert parsed == topic


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(topics())
def test_no_formatted_topic_carries_a_wildcard_reserved_character_or_empty_level(
    topic: Topic,
) -> None:
    # Arrange
    prefix = namespace_prefix() + "/"

    # Act
    text = format_topic(topic)

    # Assert
    assert (
        not (FORBIDDEN_CHARACTERS & set(text)),
        "//" not in text,
        text.endswith("/"),
        text.startswith(prefix),
    ) == (True, True, False, True)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(st.text(max_size=70).filter(lambda value: re.fullmatch(IDENTIFIER_PATTERN, value) is None))
def test_every_text_outside_the_identifier_grammar_is_refused(value: str) -> None:
    # Arrange
    topic = Topic(Family.AUDIT, value, {"recordType": "note"})

    # Act
    with pytest.raises(TopicError) as captured:
        format_topic(topic)

    # Assert
    assert (captured.value.refusal, captured.value.parameter) == (
        TopicRefusal.IDENTIFIER_FORM,
        "missionId",
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(topics(), st.sampled_from("*>"), st.integers(min_value=0, max_value=7))
def test_injecting_a_wildcard_into_any_level_makes_the_topic_unparsable(
    topic: Topic, wildcard: str, position: int
) -> None:
    # Arrange
    levels = format_topic(topic).split("/")
    index = position % len(levels)
    levels[index] = levels[index] + wildcard

    # Act
    with pytest.raises(TopicError) as captured:
        parse_topic("/".join(levels))

    # Assert
    assert captured.value.refusal is TopicRefusal.WILDCARD


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(topics())
def test_event_type_round_trips_through_parse_event_type(topic: Topic) -> None:
    # Arrange
    kinds = {
        name: value for name, value in topic.parameters.items() if rule_for(name) in KIND_RULES
    }

    # Act
    recovered = parse_event_type(event_type(topic))

    # Assert
    assert recovered == (topic.family, kinds)
