"""The deterministic policy that answers one command-gateway request.

The safety claim this module carries is negative: whatever a model asks for, the answer is
an answer and never an action. Every test therefore asserts the actuation report as well as
the outcome, because ``actuated`` is what the egress spike puts on the wire
(``docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md``).
"""

from __future__ import annotations

import unittest

from aerial_rescue_command_gateway.policy import AUTHORITY_NAMES, PolicyRefusal, answer
from aerial_rescue_contracts.rpc import GatewayRequest, Outcome
from aerial_rescue_domain.authority import Authority
from aerial_rescue_domain.operations import Operation

REQUEST_ID = "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e"
MISSION = "m-2026-0001"


def _request(
    operation: str = "command-authority", command_type: str = "escalate-rescue"
) -> GatewayRequest:
    """Return one validated request, varying only what a test is about."""
    return GatewayRequest(mission_id=MISSION, operation=operation, command_type=command_type)


class AuthorityQueryTests(unittest.TestCase):
    def test_a_rescue_escalation_is_reported_as_requiring_an_operator_approval(self) -> None:
        # Arrange
        request = _request(command_type="escalate-rescue")

        # Act
        response = answer(request, REQUEST_ID)

        # Assert
        self.assertEqual(
            (Outcome.ANSWERED, "operator-approval", None, False),
            (response.outcome, response.authority, response.refusal, response.actuated),
        )

    def test_a_sector_assignment_is_reported_as_decided_by_gateway_policy(self) -> None:
        # Arrange
        request = _request(command_type="assign-sector")

        # Act
        response = answer(request, REQUEST_ID)

        # Assert
        self.assertEqual(
            (Outcome.ANSWERED, "gateway-policy", None, False),
            (response.outcome, response.authority, response.refusal, response.actuated),
        )

    def test_the_answer_echoes_the_mission_operation_command_type_and_request(self) -> None:
        # Arrange
        request = _request()

        # Act
        response = answer(request, REQUEST_ID)

        # Assert
        self.assertEqual(
            (MISSION, "command-authority", "escalate-rescue", REQUEST_ID),
            (
                response.mission_id,
                response.operation,
                response.command_type,
                response.request_id,
            ),
        )


class RefusalTests(unittest.TestCase):
    def test_an_operation_outside_the_table_is_refused_by_name(self) -> None:
        # Arrange
        request = _request(operation="propose-command")

        # Act
        response = answer(request, REQUEST_ID)

        # Assert
        self.assertEqual(
            (Outcome.REFUSED, None, "unknown-operation", False),
            (response.outcome, response.authority, response.refusal, response.actuated),
        )

    def test_a_command_type_outside_the_table_is_refused_by_name(self) -> None:
        # Arrange
        request = _request(command_type="launch-strike")

        # Act
        response = answer(request, REQUEST_ID)

        # Assert
        self.assertEqual(
            (Outcome.REFUSED, None, "unknown-command-type", False),
            (response.outcome, response.authority, response.refusal, response.actuated),
        )

    def test_an_unknown_operation_is_refused_before_the_command_type_is_examined(self) -> None:
        # Arrange
        request = _request(operation="propose-command", command_type="launch-strike")

        # Act
        response = answer(request, REQUEST_ID)

        # Assert
        self.assertEqual("unknown-operation", response.refusal)

    def test_every_refusal_still_echoes_what_was_asked_and_which_request(self) -> None:
        # Arrange
        requests = (
            _request(operation="propose-command", command_type="launch-strike"),
            _request(command_type="launch-strike"),
        )

        # Act
        responses = tuple(answer(request, REQUEST_ID) for request in requests)

        # Assert
        self.assertEqual(
            (
                (MISSION, "propose-command", "launch-strike", REQUEST_ID),
                (MISSION, "command-authority", "launch-strike", REQUEST_ID),
            ),
            tuple(
                (
                    response.mission_id,
                    response.operation,
                    response.command_type,
                    response.request_id,
                )
                for response in responses
            ),
        )


class NonActuationTests(unittest.TestCase):
    def test_no_answer_this_policy_can_produce_reports_an_actuation(self) -> None:
        # Arrange
        requests = (
            _request(),
            _request(command_type="assign-sector"),
            _request(operation="propose-command"),
            _request(command_type="launch-strike"),
        )

        # Act
        reports = tuple(answer(request, REQUEST_ID).actuated for request in requests)

        # Assert
        self.assertEqual(tuple(False for _ in requests), reports)


class AuthorityNameTests(unittest.TestCase):
    def test_the_wire_names_are_total_over_the_authorities(self) -> None:
        # Arrange
        members = tuple(Authority)

        # Act
        names = tuple(AUTHORITY_NAMES[member] for member in members)

        # Assert
        self.assertEqual(("gateway-policy", "operator-approval"), names)

    def test_every_refusal_name_is_distinct_from_every_authority_name(self) -> None:
        # Arrange
        authorities = {name for name in AUTHORITY_NAMES.values()}

        # Act
        refusals = {member.value for member in PolicyRefusal}

        # Assert
        self.assertEqual(set(), authorities & refusals)

    def test_the_only_operation_that_exists_is_the_one_the_policy_answers(self) -> None:
        # Arrange
        members = tuple(Operation)

        # Act
        answered = tuple(
            answer(_request(operation=member.value), REQUEST_ID).outcome for member in members
        )

        # Assert
        self.assertEqual(tuple(Outcome.ANSWERED for _ in members), answered)


if __name__ == "__main__":
    unittest.main()
