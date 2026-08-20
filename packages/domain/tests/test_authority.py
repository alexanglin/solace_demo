"""The deny-by-default command-authority table that closes the commandType set.

Every refusal is asserted with the value it carries, and the table is asserted total over
the command types and inside the topic kind grammar, so a row cannot be dropped, misspelt,
or widened without a failing test.
"""

from __future__ import annotations

import re
import unittest
from enum import Enum

import pytest
from aerial_rescue_contracts.topics import (
    KIND_PATTERN,
    MAX_KIND_LENGTH,
    Family,
    Topic,
    format_topic,
    parse_topic,
)

from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_domain.authority import (
    Authority,
    AuthorityError,
    AuthorityRefusal,
    CommandType,
    authority_for,
    authorize,
    command_type,
)

UNLISTED = ("launch-strike", "Escalate-Rescue", "escalate_rescue", "escalate-rescue ", "", 7, None)
NOT_CONSUMED = (
    ApprovalState.REQUESTED,
    ApprovalState.APPROVED,
    ApprovalState.REJECTED,
    ApprovalState.EXPIRED,
    ApprovalState.SUPERSEDED,
    None,
)


def _type_refusal_of(text: object) -> tuple[Enum, object]:
    """Return the refusal parsing ``text`` raises, failing the test if it is accepted."""
    try:
        command_type(text)
    except AuthorityError as error:
        return (error.refusal, error.value)
    message = f"accepted: {text!r}"
    raise AssertionError(message)


def _authorize_refusal_of(text: object, approval: ApprovalState | None) -> tuple[Enum, object]:
    """Return the refusal authorizing ``text`` raises, failing the test if it is accepted."""
    try:
        authorize(text, approval)
    except AuthorityError as error:
        return (error.refusal, error.value)
    message = f"accepted: {text!r} with {approval!r}"
    raise AssertionError(message)


def _round_trip(kind: str) -> str:
    """Format a drone command topic carrying ``kind`` and read the kind back from it."""
    topic = Topic(Family.DRONE_COMMAND, "m-1", {"droneId": "d-1", "commandType": kind})
    return parse_topic(format_topic(topic)).parameters["commandType"]


class CommandTypeTests(unittest.TestCase):
    def test_the_command_types_are_the_two_documented_kinds(self) -> None:
        # Arrange
        expected = {"assign-sector", "escalate-rescue"}

        # Act
        kinds = {member.value for member in CommandType}

        # Assert
        self.assertEqual(expected, kinds)

    def test_every_command_type_is_inside_the_topic_kind_grammar(self) -> None:
        # Arrange
        kinds = tuple(member.value for member in CommandType)

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

    def test_the_table_is_total_over_the_command_types(self) -> None:
        # Arrange
        members = tuple(CommandType)

        # Act
        authorities = tuple(authority_for(member) for member in members)

        # Assert
        self.assertEqual((Authority.GATEWAY_POLICY, Authority.OPERATOR_APPROVAL), authorities)

    def test_only_the_rescue_escalation_requires_an_approval(self) -> None:
        # Arrange
        members = tuple(CommandType)

        # Act
        gated = {
            member for member in members if authority_for(member) is Authority.OPERATOR_APPROVAL
        }

        # Assert
        self.assertEqual({CommandType.ESCALATE_RESCUE}, gated)

    def test_exact_spelling_parses_to_the_member(self) -> None:
        # Arrange
        texts = ("assign-sector", "escalate-rescue")

        # Act
        members = tuple(command_type(text) for text in texts)

        # Assert
        self.assertEqual((CommandType.ASSIGN_SECTOR, CommandType.ESCALATE_RESCUE), members)

    def test_b23_a_command_type_absent_from_the_table_is_refused(self) -> None:
        # Arrange
        texts = UNLISTED

        # Act
        refusals = tuple(_type_refusal_of(text) for text in texts)

        # Assert
        self.assertEqual(
            tuple((AuthorityRefusal.UNKNOWN_COMMAND_TYPE, text) for text in texts), refusals
        )


class AuthorizeTests(unittest.TestCase):
    def test_sector_assignment_publishes_with_no_approval(self) -> None:
        # Arrange
        text = "assign-sector"

        # Act
        command = authorize(text, None)

        # Assert
        self.assertIs(CommandType.ASSIGN_SECTOR, command)

    def test_an_escalation_publishes_only_after_a_consumed_approval(self) -> None:
        # Arrange
        text = "escalate-rescue"

        # Act
        command = authorize(text, ApprovalState.EXECUTED)

        # Assert
        self.assertIs(CommandType.ESCALATE_RESCUE, command)

    def test_b25_an_escalation_with_any_other_approval_state_or_none_is_refused(self) -> None:
        # Arrange
        approvals = NOT_CONSUMED

        # Act
        refusals = tuple(
            _authorize_refusal_of("escalate-rescue", approval) for approval in approvals
        )

        # Assert
        self.assertEqual(
            tuple((AuthorityRefusal.APPROVAL_REQUIRED, "escalate-rescue") for _ in approvals),
            refusals,
        )

    def test_an_unknown_command_type_is_refused_before_the_approval_is_examined(self) -> None:
        # Arrange
        text = "launch-strike"

        # Act
        with pytest.raises(AuthorityError) as captured:
            authorize(text, ApprovalState.EXECUTED)

        # Assert
        self.assertEqual(
            (AuthorityRefusal.UNKNOWN_COMMAND_TYPE, text),
            (captured.value.refusal, captured.value.value),
        )


class AuthorityErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = AuthorityError(AuthorityRefusal.UNKNOWN_COMMAND_TYPE, "launch-strike")

        # Act
        message = str(error)

        # Assert
        self.assertEqual(
            "command type is absent from the command-authority table: 'launch-strike'", message
        )


if __name__ == "__main__":
    unittest.main()
