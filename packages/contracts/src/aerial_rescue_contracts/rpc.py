"""The command-gateway request/reply bodies, validated at the trust boundary.

``docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md`` records why these
are not CloudEvents: the Event Mesh Tool composes its payload from a context lookup, a
model argument, or a configured literal, so it can produce none of ``id``, ``time``,
``sequence``, or ``traceparent``. The two gateway families therefore carry schema-bound
RPC, and the command gateway republishes each answer as a CloudEvent of its own.

Both bodies stay inside the integer-only canonical profile of ADR-0027, so a recorder can
hash them and either language can produce identical bytes for the same value.

Refusals come in a fixed order, which is part of the contract: not an object; an unknown
member; a missing required member; an unsupported ``rpcVersion``; a member outside its
rule; and, for a response, an outcome that disagrees with the members present.

This module is pure: it performs no input or output, reads no clock, and consumes no
random source.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.topics import (
    IDENTIFIER_PATTERN,
    KIND_PATTERN,
    MAX_IDENTIFIER_LENGTH,
    MAX_KIND_LENGTH,
)

RPC_VERSION: Final = 1
"""The contract version carried inside the bytes; an RPC body has no ``dataschema``."""

VERSION_MEMBER: Final = "rpcVersion"

REQUEST_MEMBERS: Final = (VERSION_MEMBER, "missionId", "operation", "commandType")
"""Every member of a gateway request, all of them required."""

RESPONSE_REQUIRED_MEMBERS: Final = (*REQUEST_MEMBERS, "outcome", "actuated")
RESPONSE_OPTIONAL_MEMBERS: Final = ("authority", "refusal")

_REQUEST_ALLOWED: Final = frozenset(REQUEST_MEMBERS)
_RESPONSE_ALLOWED: Final = frozenset(RESPONSE_REQUIRED_MEMBERS) | frozenset(
    RESPONSE_OPTIONAL_MEMBERS
)

_IDENTIFIER_RULE: Final = (IDENTIFIER_PATTERN, MAX_IDENTIFIER_LENGTH)
_KIND_RULE: Final = (KIND_PATTERN, MAX_KIND_LENGTH)

_TEXT_RULES: Final = {
    "missionId": _IDENTIFIER_RULE,
    "operation": _KIND_RULE,
    "commandType": _KIND_RULE,
}
_OPTIONAL_RULES: Final = {"authority": _KIND_RULE, "refusal": _KIND_RULE}

_ACTUATED_MEMBER: Final = "actuated"
_OUTCOME_MEMBER: Final = "outcome"


class Outcome(Enum):
    """Whether the command gateway answered the request or refused it."""

    ANSWERED = "answered"
    REFUSED = "refused"


class RpcRefusal(Enum):
    """Why a document is not a gateway request or a gateway response."""

    NOT_AN_OBJECT = "body is not an object"
    UNKNOWN_MEMBER = "member outside the profile"
    MISSING_MEMBER = "required member is absent"
    VERSION = "rpcVersion is not the supported contract version"
    MEMBER_FORM = "member outside its rule"
    OUTCOME_BINDING = "outcome does not agree with the members present"


class RpcError(ValueError):
    """A body the profile refuses, carrying the refusal as structured data."""

    def __init__(self, refusal: RpcRefusal, member: str, value: object) -> None:
        """Record the refusal, the member at fault, and the value it carried."""
        super().__init__(f"{refusal.value}: {member}={value!r}")
        self.refusal = refusal
        self.member = member
        self.value = value


@dataclass(frozen=True)
class GatewayRequest:
    """One validated question put to the command gateway."""

    mission_id: str
    operation: str
    command_type: str


@dataclass(frozen=True)
class GatewayResponse:
    """One validated answer from the command gateway.

    ``actuated`` reports whether publishing an executable command followed from this
    request. The command gateway is the sole publisher of such commands
    (``docs/adr/0005-deterministic-command-gateway.md``), so it is the only component
    entitled to set it.
    """

    mission_id: str
    operation: str
    command_type: str
    outcome: Outcome
    actuated: bool
    authority: str | None = None
    refusal: str | None = None


_OUTCOMES: Final[Mapping[str, Outcome]] = {member.value: member for member in Outcome}

_BINDING_ORDER: Final[Mapping[Outcome, tuple[str, str]]] = {
    Outcome.ANSWERED: ("authority", "refusal"),
    Outcome.REFUSED: ("refusal", "authority"),
}
"""The member each outcome requires, then the one it forbids. Total over the outcomes."""


def _members(
    document: object,
    label: str,
    allowed: frozenset[str],
    required: tuple[str, ...],
) -> Mapping[object, object]:
    """Return the body's members, refusing a non-object, an unknown, or a missing member."""
    if not isinstance(document, Mapping):
        raise RpcError(RpcRefusal.NOT_AN_OBJECT, label, document)
    unknown = sorted(str(key) for key in document if key not in allowed)
    if unknown:
        raise RpcError(RpcRefusal.UNKNOWN_MEMBER, unknown[0], document[unknown[0]])
    for member in required:
        if member not in document:
            raise RpcError(RpcRefusal.MISSING_MEMBER, member, None)
    return document


def _version(members: Mapping[object, object]) -> None:
    """Refuse a version that is not an integer, then one that is not the supported value."""
    value = members[VERSION_MEMBER]
    if type(value) is not int:
        raise RpcError(RpcRefusal.MEMBER_FORM, VERSION_MEMBER, value)
    if value != RPC_VERSION:
        raise RpcError(RpcRefusal.VERSION, VERSION_MEMBER, value)


def _text(members: Mapping[object, object], name: str, rule: tuple[str, int]) -> str:
    """Return a member that is a string inside its pattern and length bound."""
    value = members[name]
    pattern, limit = rule
    if not isinstance(value, str) or len(value) > limit or re.fullmatch(pattern, value) is None:
        raise RpcError(RpcRefusal.MEMBER_FORM, name, value)
    return value


def _texts(members: Mapping[object, object]) -> dict[str, str]:
    """Validate the version and the three text members both bodies share."""
    _version(members)
    return {name: _text(members, name, rule) for name, rule in _TEXT_RULES.items()}


def _outcome(members: Mapping[object, object]) -> Outcome:
    """Return the outcome, refusing any spelling outside the closed set."""
    value = members[_OUTCOME_MEMBER]
    member = _OUTCOMES.get(value) if isinstance(value, str) else None
    if member is None:
        raise RpcError(RpcRefusal.MEMBER_FORM, _OUTCOME_MEMBER, value)
    return member


def _actuated(members: Mapping[object, object]) -> bool:
    """Return the actuation report, refusing anything that is not a boolean."""
    value = members[_ACTUATED_MEMBER]
    if not isinstance(value, bool):
        raise RpcError(RpcRefusal.MEMBER_FORM, _ACTUATED_MEMBER, value)
    return value


def _optionals(members: Mapping[object, object]) -> dict[str, str]:
    """Validate whichever optional members are present; absence is not a refusal here."""
    return {
        name: _text(members, name, rule)
        for name, rule in _OPTIONAL_RULES.items()
        if name in members
    }


def _binding(outcome: Outcome, optionals: Mapping[str, str]) -> None:
    """Refuse a response whose outcome disagrees with the optional members present."""
    required, forbidden = _BINDING_ORDER[outcome]
    if required not in optionals:
        raise RpcError(RpcRefusal.OUTCOME_BINDING, required, None)
    if forbidden in optionals:
        raise RpcError(RpcRefusal.OUTCOME_BINDING, forbidden, optionals[forbidden])


def parse_gateway_request(document: object) -> GatewayRequest:
    """Validate a decoded JSON document as a command-gateway request.

    Args:
        document: A decoded JSON value, normally from :func:`decode_gateway_request`.

    Returns:
        The validated request.

    Raises:
        RpcError: If the document is not a gateway request.
    """
    members = _members(document, "request", _REQUEST_ALLOWED, REQUEST_MEMBERS)
    texts = _texts(members)
    return GatewayRequest(
        mission_id=texts["missionId"],
        operation=texts["operation"],
        command_type=texts["commandType"],
    )


def parse_gateway_response(document: object) -> GatewayResponse:
    """Validate a decoded JSON document as a command-gateway response.

    Args:
        document: A decoded JSON value, normally from :func:`decode_gateway_response`.

    Returns:
        The validated response.

    Raises:
        RpcError: If the document is not a gateway response.
    """
    members = _members(document, "response", _RESPONSE_ALLOWED, RESPONSE_REQUIRED_MEMBERS)
    texts = _texts(members)
    outcome = _outcome(members)
    actuated = _actuated(members)
    optionals = _optionals(members)
    _binding(outcome, optionals)
    return GatewayResponse(
        mission_id=texts["missionId"],
        operation=texts["operation"],
        command_type=texts["commandType"],
        outcome=outcome,
        actuated=actuated,
        authority=optionals.get("authority"),
        refusal=optionals.get("refusal"),
    )


def gateway_request_document(request: GatewayRequest) -> dict[str, object]:
    """Return the JSON document of a request, the inverse of :func:`parse_gateway_request`."""
    return {
        VERSION_MEMBER: RPC_VERSION,
        "missionId": request.mission_id,
        "operation": request.operation,
        "commandType": request.command_type,
    }


def gateway_response_document(response: GatewayResponse) -> dict[str, object]:
    """Return the JSON document of a response, the inverse of :func:`parse_gateway_response`.

    An absent optional member is omitted rather than written as null, so the document lies
    inside the canonical profile and can be hashed by a recorder.
    """
    document: dict[str, object] = {
        VERSION_MEMBER: RPC_VERSION,
        "missionId": response.mission_id,
        "operation": response.operation,
        "commandType": response.command_type,
        _OUTCOME_MEMBER: response.outcome.value,
        _ACTUATED_MEMBER: response.actuated,
    }
    if response.authority is not None:
        document["authority"] = response.authority
    if response.refusal is not None:
        document["refusal"] = response.refusal
    return document


def decode_gateway_request(text: str | bytes) -> GatewayRequest:
    """Decode request text through the canonical decoder, so a repeated key is refused.

    Raises:
        CanonicalizationError: If the text is malformed or repeats a key.
        RpcError: If the decoded document is not a gateway request.
    """
    return parse_gateway_request(canonical.decode(text))


def decode_gateway_response(text: str | bytes) -> GatewayResponse:
    """Decode response text through the canonical decoder, so a repeated key is refused.

    Raises:
        CanonicalizationError: If the text is malformed or repeats a key.
        RpcError: If the decoded document is not a gateway response.
    """
    return parse_gateway_response(canonical.decode(text))
