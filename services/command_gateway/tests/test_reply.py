"""Where an answer may be sent, decided from user properties the requestor supplied.

This is the injection guard. The reply topic and the correlation metadata both arrive from
whoever sent the request, and the component reading them is the only one permitted to
publish executable commands (``docs/adr/0005-deterministic-command-gateway.md``). Obeying
an arbitrary reply topic would aim that component wherever a caller liked, so every test
here is about refusing rather than about accepting.
"""

from __future__ import annotations

import json
import unittest
from enum import Enum
from typing import Final

from aerial_rescue_command_gateway.reply import (
    MAX_REPLY_METADATA_BYTES,
    REPLY_METADATA_KEY,
    REPLY_TOPIC_KEY,
    ReplyError,
    ReplyRefusal,
    ReplyTarget,
    reply_target,
)

REQUESTOR: Final = "a9cfb2dc-ebc9-433b-9b35-45c2ca5c43cd"
REQUEST_ID: Final = "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e"
REPLY_TOPIC: Final = f"aerial-rescue/v1/reply/gateway/response/{REQUESTOR}"
METADATA: Final = json.dumps([{"request_id": REQUEST_ID, "response_topic": REPLY_TOPIC}])


def _properties(**changes: object) -> dict[str, object]:
    """Return the user properties a well-formed request carries, with members replaced."""
    properties: dict[str, object] = {
        REPLY_TOPIC_KEY: REPLY_TOPIC,
        REPLY_METADATA_KEY: METADATA,
    }
    for name, value in changes.items():
        key = REPLY_TOPIC_KEY if name == "topic" else REPLY_METADATA_KEY
        if value is ...:
            del properties[key]
        else:
            properties[key] = value
    return properties


def _refusal_of(properties: object) -> tuple[Enum, object]:
    """Return the refusal resolving ``properties`` raises, failing the test if accepted."""
    try:
        reply_target(properties)
    except ReplyError as error:
        return (error.refusal, error.value)
    message = f"accepted: {properties!r}"
    raise AssertionError(message)


class ReplyTargetTests(unittest.TestCase):
    def test_well_formed_properties_resolve_to_the_topic_request_and_echo(self) -> None:
        # Arrange
        properties = _properties()

        # Act
        target = reply_target(properties)

        # Assert
        self.assertEqual(
            ReplyTarget(topic=REPLY_TOPIC, request_id=REQUEST_ID, metadata=METADATA), target
        )

    def test_the_metadata_is_echoed_byte_for_byte_rather_than_re_encoded(self) -> None:
        # Arrange
        spaced = json.dumps([{"response_topic": REPLY_TOPIC, "request_id": REQUEST_ID}], indent=2)

        # Act
        target = reply_target(_properties(metadata=spaced))

        # Assert
        self.assertEqual(spaced, target.metadata)

    def test_the_last_entry_of_the_stack_names_the_request_being_answered(self) -> None:
        # Arrange
        outer = {"request_id": "c1", "response_topic": REPLY_TOPIC}
        inner = {"request_id": REQUEST_ID, "response_topic": REPLY_TOPIC}

        # Act
        target = reply_target(_properties(metadata=json.dumps([outer, inner])))

        # Assert
        self.assertEqual(REQUEST_ID, target.request_id)


class ReplyTopicRefusalTests(unittest.TestCase):
    def test_an_absent_or_non_text_reply_topic_is_refused(self) -> None:
        # Arrange
        values = (..., 7, None, ["topic"])

        # Act
        refusals = tuple(_refusal_of(_properties(topic=value)) for value in values)

        # Assert
        self.assertEqual(
            tuple(
                (ReplyRefusal.MISSING_TOPIC, None if value is ... else value) for value in values
            ),
            refusals,
        )

    def test_a_reply_topic_that_is_not_an_application_topic_is_refused(self) -> None:
        # Arrange
        values = ("reply/" + REQUESTOR, "", "aerial-rescue/v2/reply/gateway/response/x")

        # Act
        refusals = tuple(_refusal_of(_properties(topic=value)) for value in values)

        # Assert
        self.assertEqual(tuple((ReplyRefusal.TOPIC_FORM, value) for value in values), refusals)

    def test_a_topic_on_another_family_is_refused(self) -> None:
        # Arrange
        value = "aerial-rescue/v1/reply/drone/d-1/command/escalate-rescue"

        # Act
        refusal = _refusal_of(_properties(topic=value))

        # Assert
        self.assertEqual((ReplyRefusal.TOPIC_FAMILY, value), refusal)

    def test_a_gateway_response_topic_on_a_real_mission_is_refused(self) -> None:
        # Arrange
        value = f"aerial-rescue/v1/m-2026-0001/gateway/response/{REQUESTOR}"

        # Act
        refusal = _refusal_of(_properties(topic=value))

        # Assert
        self.assertEqual((ReplyRefusal.TOPIC_MISSION, value), refusal)


class ReplyMetadataRefusalTests(unittest.TestCase):
    def test_absent_or_non_text_metadata_is_refused(self) -> None:
        # Arrange
        values = (..., 7, None, [{"request_id": REQUEST_ID}])

        # Act
        refusals = tuple(_refusal_of(_properties(metadata=value)) for value in values)

        # Assert
        self.assertEqual(
            tuple(
                (ReplyRefusal.MISSING_METADATA, None if value is ... else value) for value in values
            ),
            refusals,
        )

    def test_metadata_of_exactly_the_bound_is_accepted(self) -> None:
        # Arrange
        entry = {"request_id": REQUEST_ID, "response_topic": REPLY_TOPIC}
        padding = MAX_REPLY_METADATA_BYTES - len(json.dumps([entry]).encode())
        entry["response_topic"] = REPLY_TOPIC + "x" * padding
        exact = json.dumps([entry])

        # Act
        target = reply_target(_properties(metadata=exact))

        # Assert
        self.assertEqual(
            (MAX_REPLY_METADATA_BYTES, REQUEST_ID),
            (len(target.metadata.encode()), target.request_id),
        )

    def test_metadata_longer_than_the_bound_is_refused_before_it_is_parsed(self) -> None:
        # Arrange
        oversized = "[" + "a" * MAX_REPLY_METADATA_BYTES + "]"

        # Act
        refusal = _refusal_of(_properties(metadata=oversized))

        # Assert
        self.assertEqual((ReplyRefusal.METADATA_LENGTH, len(oversized.encode())), refusal)

    def test_metadata_that_is_not_a_json_array_is_refused(self) -> None:
        # Arrange
        values = ("not json", '{"request_id": "c1"}', '"text"', "7")

        # Act
        refusals = tuple(_refusal_of(_properties(metadata=value)) for value in values)

        # Assert
        self.assertEqual(tuple((ReplyRefusal.METADATA_FORM, value) for value in values), refusals)

    def test_an_empty_metadata_stack_is_refused(self) -> None:
        # Arrange
        value = "[]"

        # Act
        refusal = _refusal_of(_properties(metadata=value))

        # Assert
        self.assertEqual((ReplyRefusal.METADATA_EMPTY, value), refusal)

    def test_a_stack_whose_last_entry_names_no_usable_request_is_refused(self) -> None:
        # Arrange
        entries: tuple[object, ...] = (
            {},
            {"request_id": None},
            {"request_id": "B3F1"},
            {"request_id": 7},
            "text",
        )

        # Act
        refusals = tuple(
            _refusal_of(_properties(metadata=json.dumps([entry]))) for entry in entries
        )

        # Assert
        self.assertEqual(
            tuple(
                (
                    ReplyRefusal.REQUEST_ID,
                    entry.get("request_id") if isinstance(entry, dict) else None,
                )
                for entry in entries
            ),
            refusals,
        )


class PropertiesShapeTests(unittest.TestCase):
    def test_user_properties_that_are_not_a_mapping_are_refused(self) -> None:
        # Arrange
        values = (None, "properties", [REPLY_TOPIC])

        # Act
        refusals = tuple(_refusal_of(value) for value in values)

        # Assert
        self.assertEqual(tuple((ReplyRefusal.NOT_A_MAPPING, value) for value in values), refusals)


if __name__ == "__main__":
    unittest.main()
