"""Property-based invariants of the command gateway's pure core.

Two properties carry the safety claim. Whatever a model puts in a request, the answer never
reports an actuation; and whatever a requestor puts in the reply topic, the resolved target
is either the reserved reply channel or a refusal, never another topic.

Module-level functions with ``derandomize`` so the mutation score cannot flap.
"""

from __future__ import annotations

import json

import pytest
from aerial_rescue_command_gateway.policy import answer
from aerial_rescue_command_gateway.reply import (
    REPLY_METADATA_KEY,
    REPLY_TOPIC_KEY,
    ReplyError,
    reply_target,
)
from aerial_rescue_contracts.rpc import GatewayRequest
from aerial_rescue_contracts.topics import (
    IDENTIFIER_PATTERN,
    KIND_PATTERN,
    MAX_IDENTIFIER_LENGTH,
    MAX_KIND_LENGTH,
    RESERVED_REPLY_MISSION,
    Family,
    parse_topic,
)
from hypothesis import given, settings
from hypothesis import strategies as st

IDENTIFIERS = st.from_regex(IDENTIFIER_PATTERN, fullmatch=True).filter(
    lambda text: len(text) <= MAX_IDENTIFIER_LENGTH
)
KINDS = st.from_regex(KIND_PATTERN, fullmatch=True).filter(
    lambda text: len(text) <= MAX_KIND_LENGTH
)
TOPIC_TEXT = st.text(max_size=80)


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(IDENTIFIERS, KINDS, KINDS)
def test_no_request_whatever_it_asks_produces_an_answer_that_actuates(
    mission_id: str, operation: str, command_type: str
) -> None:
    # Arrange
    request = GatewayRequest(mission_id=mission_id, operation=operation, command_type=command_type)

    # Act
    response = answer(request, "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e")

    # Assert
    assert response.actuated is False


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(IDENTIFIERS, KINDS, KINDS)
def test_every_answer_echoes_the_request_it_answers(
    mission_id: str, operation: str, command_type: str
) -> None:
    # Arrange
    request = GatewayRequest(mission_id=mission_id, operation=operation, command_type=command_type)

    # Act
    response = answer(request, "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e")

    # Assert
    assert (response.mission_id, response.operation, response.command_type) == (
        mission_id,
        operation,
        command_type,
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=500)
@given(TOPIC_TEXT)
def test_a_resolved_reply_target_is_always_on_the_reserved_reply_channel(topic: str) -> None:
    # Arrange
    properties = {
        REPLY_TOPIC_KEY: topic,
        REPLY_METADATA_KEY: json.dumps(
            [{"request_id": "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e", "response_topic": topic}]
        ),
    }

    # Act
    try:
        resolved = parse_topic(reply_target(properties).topic)
    except ReplyError:
        resolved = None

    # Assert
    assert resolved is None or (
        resolved.family is Family.GATEWAY_RESPONSE and resolved.mission_id == RESERVED_REPLY_MISSION
    )
