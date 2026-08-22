"""The deny-by-default broker authorization tables that decide who may use which topic family.

The nine roles and their grants are the decision in
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``. The threat
model calls this matrix load-bearing, so the tests here assert it from the family's side as
well as the role's: the publisher set of every family is named, and every role is put to
the drone-command topic so that catalogue cases B17, B18, and B19 are checked against all
nine roles rather than against the three the catalogue happens to name. The key space is
nine roles by two directions by eleven families, which is small enough to enumerate
exhaustively; nothing here samples.
"""

from __future__ import annotations

import re
import unittest
from enum import Enum

import pytest
from aerial_rescue_contracts.topics import KIND_PATTERN, MAX_KIND_LENGTH, Family

from aerial_rescue_domain.principals import (
    Access,
    Principal,
    PrincipalError,
    PrincipalRefusal,
    authorize,
    grants,
    may_use,
    may_use_a2a,
    may_use_reply_channel,
    principal,
)

UNLISTED = (
    "scenario-service",
    "Command-Gateway",
    "command_gateway",
    "command-gateway ",
    "",
    7,
    None,
)

PUBLISHERS = {
    Family.OPERATOR_COMMAND: frozenset({Principal.DASHBOARD_API}),
    Family.OPERATOR_APPROVAL: frozenset({Principal.DASHBOARD_API}),
    Family.DRONE_TELEMETRY: frozenset({Principal.FLEET_SIMULATOR}),
    Family.DRONE_EVENT: frozenset({Principal.FLEET_SIMULATOR}),
    Family.DRONE_COMMAND: frozenset({Principal.COMMAND_GATEWAY}),
    Family.DRONE_COMMAND_RESULT: frozenset({Principal.FLEET_SIMULATOR}),
    Family.GATEWAY_REQUEST: frozenset({Principal.EVENT_MESH_TOOL}),
    Family.GATEWAY_RESPONSE: frozenset({Principal.COMMAND_GATEWAY}),
    Family.AGENT_PROPOSAL: frozenset({Principal.AGENT_MESH_AGENT}),
    Family.AGENT_RESPONSE: frozenset({Principal.AGENT_MESH_AGENT, Principal.EVENT_MESH_GATEWAY}),
    Family.AUDIT: frozenset({Principal.COMMAND_GATEWAY, Principal.EVIDENCE_SERVICE}),
}


def _name_refusal_of(text: object) -> tuple[Enum, object]:
    """Return the refusal parsing ``text`` raises, failing the test if it is accepted."""
    try:
        principal(text)
    except PrincipalError as error:
        return (error.refusal, error.value)
    message = f"accepted: {text!r}"
    raise AssertionError(message)


def _authorize_refusal_of(role: Principal, access: Access, family: Family) -> Enum:
    """Return the refusal authorizing ``family`` raises, failing the test if it is accepted."""
    try:
        authorize(role, access, family)
    except PrincipalError as error:
        return error.refusal
    message = f"accepted: {role!r} {access!r} {family!r}"
    raise AssertionError(message)


def _publishers_of(family: Family) -> frozenset[Principal]:
    """Return every role the publish table lets reach ``family``."""
    return frozenset(role for role in Principal if may_use(role, Access.PUBLISH, family))


class PrincipalTests(unittest.TestCase):
    def test_the_roles_are_the_nine_documented_names(self) -> None:
        # Arrange
        expected = {
            "fleet-simulator",
            "command-gateway",
            "dashboard-api",
            "evidence-service",
            "recorder",
            "event-mesh-gateway",
            "event-mesh-tool",
            "agent-mesh-agent",
            "discovery",
        }

        # Act
        names = {member.value for member in Principal}

        # Assert
        self.assertEqual(expected, names)

    def test_every_role_name_is_inside_the_topic_kind_grammar(self) -> None:
        # Arrange
        names = tuple(member.value for member in Principal)

        # Act
        checks = tuple(
            (re.fullmatch(KIND_PATTERN, name) is not None, len(name) <= MAX_KIND_LENGTH)
            for name in names
        )

        # Assert
        self.assertEqual(tuple((True, True) for _ in names), checks)

    def test_exact_spelling_parses_to_the_member(self) -> None:
        # Arrange
        texts = tuple(member.value for member in Principal)

        # Act
        members = tuple(principal(text) for text in texts)

        # Assert
        self.assertEqual(tuple(Principal), members)

    def test_a_name_absent_from_the_table_is_refused(self) -> None:
        # Arrange
        texts = UNLISTED

        # Act
        refusals = tuple(_name_refusal_of(text) for text in texts)

        # Assert
        self.assertEqual(
            tuple((PrincipalRefusal.UNKNOWN_PRINCIPAL, text) for text in texts), refusals
        )


class GrantTests(unittest.TestCase):
    def test_both_tables_are_total_over_the_roles(self) -> None:
        # Arrange
        roles = tuple(Principal)

        # Act
        sizes = tuple(
            (len(grants(role, Access.PUBLISH)), len(grants(role, Access.SUBSCRIBE)))
            for role in roles
        )

        # Assert
        self.assertEqual(
            ((3, 1), (3, 5), (2, 7), (1, 2), (0, 11), (1, 1), (1, 0), (2, 0), (0, 0)), sizes
        )

    def test_each_family_has_exactly_the_documented_publishers(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        publishers = {family: _publishers_of(family) for family in families}

        # Assert
        self.assertEqual(PUBLISHERS, publishers)

    def test_no_role_both_publishes_and_subscribes_to_one_family(self) -> None:
        # Arrange
        roles = tuple(Principal)

        # Act
        overlaps = tuple(
            grants(role, Access.PUBLISH) & grants(role, Access.SUBSCRIBE) for role in roles
        )

        # Assert
        self.assertEqual(tuple(frozenset() for _ in roles), overlaps)

    def test_b17_no_role_but_the_command_gateway_may_publish_a_drone_command(self) -> None:
        # Arrange
        family = Family.DRONE_COMMAND

        # Act
        publishers = _publishers_of(family)

        # Assert
        self.assertEqual(frozenset({Principal.COMMAND_GATEWAY}), publishers)

    def test_b18_the_event_mesh_tool_may_publish_only_a_gateway_request(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_TOOL

        # Act
        published = grants(role, Access.PUBLISH)

        # Assert
        self.assertEqual(frozenset({Family.GATEWAY_REQUEST}), published)

    def test_b19_the_recorder_publishes_nothing_and_the_dashboard_only_operator_families(
        self,
    ) -> None:
        # Arrange
        roles = (Principal.RECORDER, Principal.DASHBOARD_API)

        # Act
        published = tuple(grants(role, Access.PUBLISH) for role in roles)

        # Assert
        self.assertEqual(
            (
                frozenset(),
                frozenset({Family.OPERATOR_COMMAND, Family.OPERATOR_APPROVAL}),
            ),
            published,
        )

    def test_the_recorder_subscribes_to_every_family(self) -> None:
        # Arrange
        role = Principal.RECORDER

        # Act
        subscribed = grants(role, Access.SUBSCRIBE)

        # Assert
        self.assertEqual(frozenset(Family), subscribed)

    def test_the_discovery_role_holds_no_grant_in_either_direction(self) -> None:
        # Arrange
        role = Principal.DISCOVERY

        # Act
        held = (grants(role, Access.PUBLISH), grants(role, Access.SUBSCRIBE), may_use_a2a(role))

        # Assert
        self.assertEqual((frozenset(), frozenset(), False), held)

    def test_the_event_mesh_tool_subscribes_to_no_family_at_all(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_TOOL

        # Act
        subscribed = grants(role, Access.SUBSCRIBE)

        # Assert
        self.assertEqual(frozenset(), subscribed)

    def test_the_event_mesh_tool_cannot_read_a_mission_s_gateway_responses(self) -> None:
        # Arrange
        role = Principal.EVENT_MESH_TOOL

        # Act
        reachable = may_use(role, Access.SUBSCRIBE, Family.GATEWAY_RESPONSE)

        # Assert
        self.assertFalse(reachable)

    def test_only_the_event_mesh_tool_may_use_the_reply_channel(self) -> None:
        # Arrange
        roles = tuple(Principal)

        # Act
        allowed = frozenset(role for role in roles if may_use_reply_channel(role))

        # Assert
        self.assertEqual(frozenset({Principal.EVENT_MESH_TOOL}), allowed)

    def test_only_the_three_agent_mesh_roles_may_use_the_a2a_namespace(self) -> None:
        # Arrange
        roles = tuple(Principal)

        # Act
        allowed = frozenset(role for role in roles if may_use_a2a(role))

        # Assert
        self.assertEqual(
            frozenset(
                {
                    Principal.AGENT_MESH_AGENT,
                    Principal.EVENT_MESH_GATEWAY,
                    Principal.EVENT_MESH_TOOL,
                }
            ),
            allowed,
        )


class AuthorizeTests(unittest.TestCase):
    def test_a_granted_publication_returns_the_family(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY

        # Act
        family = authorize(role, Access.PUBLISH, Family.DRONE_COMMAND)

        # Assert
        self.assertIs(Family.DRONE_COMMAND, family)

    def test_a_granted_subscription_returns_the_family(self) -> None:
        # Arrange
        role = Principal.FLEET_SIMULATOR

        # Act
        family = authorize(role, Access.SUBSCRIBE, Family.DRONE_COMMAND)

        # Assert
        self.assertIs(Family.DRONE_COMMAND, family)

    def test_b17_an_agent_publishing_a_drone_command_is_refused(self) -> None:
        # Arrange
        role = Principal.AGENT_MESH_AGENT

        # Act
        with pytest.raises(PrincipalError) as captured:
            authorize(role, Access.PUBLISH, Family.DRONE_COMMAND)

        # Assert
        self.assertEqual(
            (
                PrincipalRefusal.DENIED,
                ("agent-mesh-agent", "publish", Family.DRONE_COMMAND.value),
            ),
            (captured.value.refusal, captured.value.value),
        )

    def test_every_role_denied_a_family_is_refused_in_both_directions(self) -> None:
        # Arrange
        role = Principal.DISCOVERY

        # Act
        refusals = tuple(
            _authorize_refusal_of(role, access, family) for access in Access for family in Family
        )

        # Assert
        self.assertEqual(tuple(PrincipalRefusal.DENIED for _ in refusals), refusals)


class PrincipalErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = PrincipalError(PrincipalRefusal.UNKNOWN_PRINCIPAL, "scenario-service")

        # Act
        message = str(error)

        # Assert
        self.assertEqual(
            "role is absent from the broker authorization table: 'scenario-service'", message
        )


if __name__ == "__main__":
    unittest.main()
