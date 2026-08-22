"""The deny-by-default gateway-operation table that closes the operation kind set.

Every refusal is asserted with the value it carries, and the table is asserted total over
the operations and inside the topic kind grammar, so a row cannot be dropped, misspelt, or
widened without a failing test. The level this table closes is rendered by a language
model, so an invented spelling is the expected input rather than the exceptional one
(``docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md``).
"""

from __future__ import annotations

import re
import unittest
from enum import Enum

from aerial_rescue_contracts.topics import (
    KIND_PATTERN,
    MAX_KIND_LENGTH,
    Family,
    Topic,
    format_topic,
    parse_topic,
)

from aerial_rescue_domain.operations import (
    Operation,
    OperationError,
    OperationRefusal,
    actuates,
    operation,
)

UNLISTED = (
    "propose-command",
    "Command-Authority",
    "command_authority",
    "command-authority ",
    "",
    7,
    None,
)


def _refusal_of(text: object) -> tuple[Enum, object]:
    """Return the refusal resolving ``text`` raises, failing the test if it is accepted."""
    try:
        operation(text)
    except OperationError as error:
        return (error.refusal, error.value)
    message = f"accepted: {text!r}"
    raise AssertionError(message)


def _round_trip(kind: str) -> str:
    """Format a gateway request topic carrying ``kind`` and read the kind back from it."""
    topic = Topic(Family.GATEWAY_REQUEST, "m-1", {"operation": kind})
    return parse_topic(format_topic(topic)).parameters["operation"]


class OperationTests(unittest.TestCase):
    def test_the_operations_are_the_documented_kinds(self) -> None:
        # Arrange
        expected = {"command-authority"}

        # Act
        kinds = {member.value for member in Operation}

        # Assert
        self.assertEqual(expected, kinds)

    def test_every_operation_is_inside_the_topic_kind_grammar(self) -> None:
        # Arrange
        kinds = tuple(member.value for member in Operation)

        # Act
        checks = tuple(
            (
                re.fullmatch(KIND_PATTERN, kind) is not None,
                len(kind) <= MAX_KIND_LENGTH,
                _round_trip(kind),
            )
            for kind in kinds
        )

        # Assert
        self.assertEqual(tuple((True, True, kind) for kind in kinds), checks)

    def test_exact_spelling_resolves_to_the_member(self) -> None:
        # Arrange
        text = "command-authority"

        # Act
        member = operation(text)

        # Assert
        self.assertIs(Operation.COMMAND_AUTHORITY, member)

    def test_an_operation_absent_from_the_table_is_refused(self) -> None:
        # Arrange
        texts = UNLISTED

        # Act
        refusals = tuple(_refusal_of(text) for text in texts)

        # Assert
        self.assertEqual(
            tuple((OperationRefusal.UNKNOWN_OPERATION, text) for text in texts), refusals
        )


class ActuationTests(unittest.TestCase):
    def test_the_actuation_table_is_total_over_the_operations(self) -> None:
        # Arrange
        members = tuple(Operation)

        # Act
        reports = tuple(actuates(member) for member in members)

        # Assert
        self.assertEqual((False,), reports)

    def test_no_operation_defined_so_far_actuates_anything(self) -> None:
        # Arrange
        members = tuple(Operation)

        # Act
        actuating = {member for member in members if actuates(member)}

        # Assert
        self.assertEqual(set(), actuating)


class OperationErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = OperationError(OperationRefusal.UNKNOWN_OPERATION, "propose-command")

        # Act
        message = str(error)

        # Assert
        self.assertEqual(
            "operation is absent from the gateway-operation table: 'propose-command'", message
        )


if __name__ == "__main__":
    unittest.main()
