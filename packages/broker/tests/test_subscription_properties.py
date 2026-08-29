"""Property-based invariants of the subscription strings, over adversarial level values.

``derandomize`` for the same reason the contracts package gives: a flapping example set
would make a gate's verdict a moving number.

The values drawn here are deliberately hostile. Every one is legal under its own level rule
and equal to a literal level of some other family, so a topic drawn from one family is as
close as the grammar allows to a topic of another. A pattern that widens by one level is
caught here rather than at the broker.
"""

from __future__ import annotations

import pytest
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.topics import (
    RESERVED_REPLY_MISSION,
    Family,
    Rule,
    Topic,
    format_topic,
    rule_for,
)
from hypothesis import given, settings
from hypothesis import strategies as st

SHADOWING = ("drone", "agent", "audit", "operator", "gateway", "telemetry", "command", "event")
LEVEL_VALUES = {
    Rule.IDENTIFIER: st.sampled_from([*SHADOWING, "command-result", "proposal", "m-1"]),
    Rule.KIND: st.sampled_from([*SHADOWING, "command-result", "response", "assign-sector"]),
    Rule.AGENT_NAME: st.sampled_from(["Telemetry", "drone", "A_1", "audit"]),
    Rule.DECISION: st.sampled_from(["approve", "reject"]),
}


@st.composite
def shadowing_topics(draw: st.DrawFn) -> tuple[Family, str]:
    """Draw a family and one of its topics whose variable levels shadow literal levels."""
    family = draw(st.sampled_from(list(Family)))
    values = {name: draw(LEVEL_VALUES[rule_for(name)]) for name in family.parameters}
    mission = (
        RESERVED_REPLY_MISSION
        if family is Family.GATEWAY_RESPONSE
        else draw(LEVEL_VALUES[Rule.IDENTIFIER])
    )
    return (family, format_topic(Topic(family, mission, values)))


def _covers(pattern: str, topic: str) -> bool:
    """Report whether a Solace subscription using only whole-level ``*`` covers ``topic``."""
    wanted = pattern.split("/")
    found = topic.split("/")
    return len(wanted) == len(found) and all(
        level in {"*", actual} for level, actual in zip(wanted, found, strict=True)
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(shadowing_topics())
def test_exactly_one_family_pattern_covers_any_well_formed_topic(drawn: tuple[Family, str]) -> None:
    # Arrange
    family, topic = drawn

    # Act
    covering = frozenset(
        candidate for candidate in Family if _covers(subscription_for(candidate), topic)
    )

    # Assert
    assert covering == frozenset({family})
