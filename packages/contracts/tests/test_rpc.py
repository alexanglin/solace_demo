"""The command-gateway request/reply bodies at the trust boundary.

Every refusal is asserted by its structured reason and the member it names. The two
baseline documents are the ones committed as golden fixtures, so the unit suite and the
schema oracle judge one and the same request and one and the same reply
(``docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md``).
"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.rpc import (
    REQUEST_MEMBERS,
    RESPONSE_REQUIRED_MEMBERS,
    RPC_VERSION,
    GatewayRequest,
    GatewayResponse,
    Outcome,
    RpcError,
    RpcRefusal,
    decode_gateway_request,
    decode_gateway_response,
    gateway_request_document,
    gateway_response_document,
    parse_gateway_request,
    parse_gateway_response,
)
from aerial_rescue_contracts.topics import MAX_KIND_LENGTH

BASELINES = Path(__file__).parent / "baselines"
"""Committed wire-contract documents, each byte-identical to its golden fixture.

They sit in their own directory so ``tests/`` stays inside the fan-out bound as more
contracts are bound (``docs/adr/0033-bound-directory-fan-out.md``).
"""

REQUEST_BASELINE: dict[str, object] = cast(
    "dict[str, object]",
    json.loads((BASELINES / "rpc_request_baseline.json").read_text(encoding="utf-8")),
)
"""The same request committed as its golden fixture, so both suites judge one document."""

RESPONSE_BASELINE: dict[str, object] = cast(
    "dict[str, object]",
    json.loads((BASELINES / "rpc_response_baseline.json").read_text(encoding="utf-8")),
)
"""The same reply committed as its golden fixture, for the same reason."""


def _request(**changes: object) -> dict[str, object]:
    """Return the request baseline with members replaced or, for ``...``, removed."""
    return _changed(REQUEST_BASELINE, changes)


def _response(**changes: object) -> dict[str, object]:
    """Return the response baseline with members replaced or, for ``...``, removed."""
    return _changed(RESPONSE_BASELINE, changes)


def _changed(baseline: dict[str, object], changes: dict[str, object]) -> dict[str, object]:
    """Return a fresh copy of a baseline with members replaced or removed."""
    document = deepcopy(baseline)
    for name, value in changes.items():
        if value is ...:
            del document[name]
        else:
            document[name] = value
    return document


def _request_refusal(document: object) -> tuple[RpcRefusal, str, object]:
    """Return the refusal parsing a request raises, failing the test if it is accepted."""
    try:
        parse_gateway_request(document)
    except RpcError as error:
        return (error.refusal, error.member, error.value)
    message = f"accepted: {document!r}"
    raise AssertionError(message)


def _response_refusal(document: object) -> tuple[RpcRefusal, str, object]:
    """Return the refusal parsing a response raises, failing the test if it is accepted."""
    try:
        parse_gateway_response(document)
    except RpcError as error:
        return (error.refusal, error.member, error.value)
    message = f"accepted: {document!r}"
    raise AssertionError(message)


class RequestParsingTests(unittest.TestCase):
    def test_the_baseline_request_parses_to_a_gateway_request(self) -> None:
        # Arrange
        document = _request()

        # Act
        parsed = parse_gateway_request(document)

        # Assert
        self.assertEqual(
            GatewayRequest(
                mission_id="m-2026-0001",
                operation="command-authority",
                command_type="escalate-rescue",
            ),
            parsed,
        )

    def test_a_request_that_is_not_an_object_is_refused(self) -> None:
        # Arrange
        document = [_request()]

        # Act
        with pytest.raises(RpcError) as captured:
            parse_gateway_request(document)

        # Assert
        self.assertEqual(
            (RpcRefusal.NOT_AN_OBJECT, "request", document),
            (captured.value.refusal, captured.value.member, captured.value.value),
        )

    def test_each_required_request_member_is_required(self) -> None:
        # Arrange
        members = REQUEST_MEMBERS

        # Act
        outcomes = tuple(_request_refusal(_request(**{member: ...})) for member in members)

        # Assert
        self.assertEqual(
            tuple((RpcRefusal.MISSING_MEMBER, member, None) for member in members), outcomes
        )

    def test_a_member_outside_the_request_profile_is_refused_before_a_missing_one(self) -> None:
        # Arrange
        document = _request(droneId="drone-vision-01", missionId=...)

        # Act
        outcome = _request_refusal(document)

        # Assert
        self.assertEqual(
            (RpcRefusal.UNKNOWN_MEMBER, "droneId", "drone-vision-01"),
            outcome,
        )

    def test_every_request_member_outside_its_rule_is_refused(self) -> None:
        # Arrange
        documents = (
            _request(missionId="M-2026-0001"),
            _request(missionId=1),
            _request(operation="Command-Authority"),
            _request(operation=None),
            _request(commandType="escalate rescue"),
            _request(commandType=True),
        )

        # Act
        outcomes = tuple(_request_refusal(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (RpcRefusal.MEMBER_FORM, "missionId", "M-2026-0001"),
                (RpcRefusal.MEMBER_FORM, "missionId", 1),
                (RpcRefusal.MEMBER_FORM, "operation", "Command-Authority"),
                (RpcRefusal.MEMBER_FORM, "operation", None),
                (RpcRefusal.MEMBER_FORM, "commandType", "escalate rescue"),
                (RpcRefusal.MEMBER_FORM, "commandType", True),
            ),
            outcomes,
        )

    def test_a_command_type_at_the_kind_length_bound_is_accepted(self) -> None:
        # Arrange
        document = _request(commandType="a" * MAX_KIND_LENGTH)

        # Act
        parsed = parse_gateway_request(document)

        # Assert
        self.assertEqual("a" * MAX_KIND_LENGTH, parsed.command_type)

    def test_a_boolean_is_not_accepted_as_the_rpc_version(self) -> None:
        # Arrange
        document = _request(rpcVersion=True)

        # Act
        outcome = _request_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.MEMBER_FORM, "rpcVersion", True), outcome)

    def test_a_version_other_than_the_supported_one_is_refused(self) -> None:
        # Arrange
        document = _request(rpcVersion=RPC_VERSION + 1)

        # Act
        outcome = _request_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.VERSION, "rpcVersion", RPC_VERSION + 1), outcome)

    def test_a_request_round_trips_through_its_document(self) -> None:
        # Arrange
        request = parse_gateway_request(_request())

        # Act
        document = gateway_request_document(request)

        # Assert
        self.assertEqual(_request(), document)

    def test_request_text_is_decoded_through_the_canonical_decoder(self) -> None:
        # Arrange
        text = canonical.canonical_bytes(_request())

        # Act
        parsed = decode_gateway_request(text)

        # Assert
        self.assertEqual(parse_gateway_request(_request()), parsed)

    def test_decoding_refuses_a_repeated_key_rather_than_merging_it(self) -> None:
        # Arrange
        text = '{"rpcVersion":1,"missionId":"m-1","missionId":"m-2"}'

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            decode_gateway_request(text)

        # Assert
        self.assertEqual(canonical.Refusal.DUPLICATE_KEY, captured.value.refusal)


class ResponseParsingTests(unittest.TestCase):
    def test_the_baseline_response_parses_to_a_gateway_response(self) -> None:
        # Arrange
        document = _response()

        # Act
        parsed = parse_gateway_response(document)

        # Assert
        self.assertEqual(
            GatewayResponse(
                mission_id="m-2026-0001",
                request_id="b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e",
                operation="command-authority",
                command_type="escalate-rescue",
                outcome=Outcome.ANSWERED,
                actuated=False,
                authority="operator-approval",
            ),
            parsed,
        )

    def test_a_refused_response_carries_a_refusal_and_no_authority(self) -> None:
        # Arrange
        document = _response(outcome="refused", refusal="unknown-operation", authority=...)

        # Act
        parsed = parse_gateway_response(document)

        # Assert
        self.assertEqual(
            (Outcome.REFUSED, None, "unknown-operation"),
            (parsed.outcome, parsed.authority, parsed.refusal),
        )

    def test_each_required_response_member_is_required(self) -> None:
        # Arrange
        members = RESPONSE_REQUIRED_MEMBERS

        # Act
        outcomes = tuple(_response_refusal(_response(**{member: ...})) for member in members)

        # Assert
        self.assertEqual(
            tuple((RpcRefusal.MISSING_MEMBER, member, None) for member in members), outcomes
        )

    def test_a_response_that_is_not_an_object_is_refused(self) -> None:
        # Arrange
        document = "answered"

        # Act
        with pytest.raises(RpcError) as captured:
            parse_gateway_response(document)

        # Assert
        self.assertEqual(
            (RpcRefusal.NOT_AN_OBJECT, "response", document),
            (captured.value.refusal, captured.value.member, captured.value.value),
        )

    def test_a_response_names_the_request_it_answers(self) -> None:
        # Arrange
        document = _response(requestId="d4e5f6a7-8b9c-4d0e-1f2a-3b4c5d6e7f80")

        # Act
        parsed = parse_gateway_response(document)

        # Assert
        self.assertEqual("d4e5f6a7-8b9c-4d0e-1f2a-3b4c5d6e7f80", parsed.request_id)

    def test_a_request_identifier_outside_its_rule_is_refused(self) -> None:
        # Arrange
        document = _response(requestId="B3F1C2D4")

        # Act
        outcome = _response_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.MEMBER_FORM, "requestId", "B3F1C2D4"), outcome)

    def test_a_member_outside_the_response_profile_is_refused(self) -> None:
        # Arrange
        document = _response(commandId="c-2026-0001")

        # Act
        outcome = _response_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.UNKNOWN_MEMBER, "commandId", "c-2026-0001"), outcome)

    def test_every_response_member_outside_its_rule_is_refused(self) -> None:
        # Arrange
        documents = (
            _response(outcome="ANSWERED"),
            _response(outcome=1),
            _response(actuated="false"),
            _response(authority="Operator-Approval"),
            _response(outcome="refused", refusal="not a kind", authority=...),
        )

        # Act
        outcomes = tuple(_response_refusal(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (RpcRefusal.MEMBER_FORM, "outcome", "ANSWERED"),
                (RpcRefusal.MEMBER_FORM, "outcome", 1),
                (RpcRefusal.MEMBER_FORM, "actuated", "false"),
                (RpcRefusal.MEMBER_FORM, "authority", "Operator-Approval"),
                (RpcRefusal.MEMBER_FORM, "refusal", "not a kind"),
            ),
            outcomes,
        )

    def test_an_answered_response_without_an_authority_is_refused(self) -> None:
        # Arrange
        document = _response(authority=...)

        # Act
        outcome = _response_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.OUTCOME_BINDING, "authority", None), outcome)

    def test_an_answered_response_carrying_a_refusal_is_refused(self) -> None:
        # Arrange
        document = _response(refusal="unknown-operation")

        # Act
        outcome = _response_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.OUTCOME_BINDING, "refusal", "unknown-operation"), outcome)

    def test_a_refused_response_without_a_refusal_is_refused(self) -> None:
        # Arrange
        document = _response(outcome="refused", authority=...)

        # Act
        outcome = _response_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.OUTCOME_BINDING, "refusal", None), outcome)

    def test_a_refused_response_carrying_an_authority_is_refused(self) -> None:
        # Arrange
        document = _response(outcome="refused", refusal="unknown-operation")

        # Act
        outcome = _response_refusal(document)

        # Assert
        self.assertEqual((RpcRefusal.OUTCOME_BINDING, "authority", "operator-approval"), outcome)

    def test_a_response_round_trips_through_its_document(self) -> None:
        # Arrange
        documents = (
            _response(),
            _response(outcome="refused", refusal="unknown-operation", authority=...),
        )

        # Act
        round_tripped = tuple(
            gateway_response_document(parse_gateway_response(document)) for document in documents
        )

        # Assert
        self.assertEqual(documents, round_tripped)

    def test_response_text_is_decoded_through_the_canonical_decoder(self) -> None:
        # Arrange
        text = canonical.canonical_bytes(_response())

        # Act
        parsed = decode_gateway_response(text)

        # Assert
        self.assertEqual(parse_gateway_response(_response()), parsed)

    def test_a_response_document_lies_inside_the_canonical_profile(self) -> None:
        # Arrange
        response = parse_gateway_response(_response())

        # Act
        encoded = canonical.canonical_bytes(gateway_response_document(response))

        # Assert
        self.assertEqual(
            b'{"actuated":false,"authority":"operator-approval","commandType":"escalate-rescue",'
            b'"missionId":"m-2026-0001","operation":"command-authority","outcome":"answered",'
            b'"requestId":"b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e","rpcVersion":1}',
            encoded,
        )


class RpcErrorTests(unittest.TestCase):
    def test_the_message_names_refusal_member_and_value(self) -> None:
        # Arrange
        error = RpcError(RpcRefusal.MEMBER_FORM, "operation", "Command-Authority")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("member outside its rule: operation='Command-Authority'", message)


if __name__ == "__main__":
    unittest.main()
