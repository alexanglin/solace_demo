"""The closed Agent Mesh integration response at the application boundary.

``docs/adr/0148-close-the-application-data-plane-wire-documents.md`` makes this
body the sole application integration document that is not a CloudEvent.  It is
direct, non-authoritative evidence: a command gateway must validate it and commit
a canonical proposal before any part of the response acquires durable authority.

Refusals have a fixed order: not an object; an unknown member; a missing common
member; version; member form; and outcome/member binding.  Canonical decoding runs
before these checks so duplicate keys, floating point values, and other values
outside the integer-only profile cannot be hidden by Python's JSON decoder.

This module is pure and framework-free.  It performs no I/O and does not read a
clock, random source, broker, or model runtime.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.topics import (
    AGENT_NAME_PATTERN,
    IDENTIFIER_PATTERN,
    Family,
    Topic,
)

AGENT_RESPONSE_VERSION: Final = 1
"""The version carried inside an integration body that has no ``dataschema``."""

_VERSION_MEMBER: Final = "agentResponseVersion"
_OUTCOME_MEMBER: Final = "outcome"
_RESULT_MEMBER: Final = "result"
_REASON_MEMBER: Final = "reason"

_COMMON_MEMBERS: Final = (
    _VERSION_MEMBER,
    "missionId",
    "agentName",
    "invocationId",
    "correlationId",
    _OUTCOME_MEMBER,
)
_OPTIONAL_MEMBERS: Final = (_RESULT_MEMBER, _REASON_MEMBER)
_ALLOWED_MEMBERS: Final = frozenset((*_COMMON_MEMBERS, *_OPTIONAL_MEMBERS))

_RESULT_MEMBERS: Final = (
    "proposalType",
    "sourceEventId",
    "sourceEventDigest",
    "droneId",
    "latitudeMicrodegrees",
    "longitudeMicrodegrees",
    "commandType",
)
_RESULT_ALLOWED_MEMBERS: Final = frozenset(_RESULT_MEMBERS)

_DIGEST_PATTERN: Final = "^[0-9a-f]{64}$"
_MIN_LATITUDE_MICRODEGREES: Final = -90_000_000
_MAX_LATITUDE_MICRODEGREES: Final = 90_000_000
_MIN_LONGITUDE_MICRODEGREES: Final = -180_000_000
_MAX_LONGITUDE_MICRODEGREES: Final = 180_000_000


class AgentOutcome(Enum):
    """The only assertions a structured Agent Mesh response can make."""

    CANDIDATE = "candidate"
    ABSTAINED = "abstained"


class AgentResponseReason(Enum):
    """Redacted reasons for an Agent Mesh response that makes no assertion."""

    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport-error"
    MODEL_ERROR = "model-error"
    INVALID_OUTPUT = "invalid-output"
    IDENTITY_MISMATCH = "identity-mismatch"


class IntegrationRefusal(Enum):
    """Why a document is not the closed Agent Mesh integration response."""

    NOT_AN_OBJECT = "body is not an object"
    UNKNOWN_MEMBER = "member outside the profile"
    MISSING_MEMBER = "required member is absent"
    VERSION = "agentResponseVersion is not the supported contract version"
    MEMBER_FORM = "member outside its rule"
    OUTCOME_BINDING = "outcome does not agree with the members present"
    TOPIC_BINDING = "body does not bind to the arriving topic"


class IntegrationError(ValueError):
    """A structured integration refusal with the offending member and value."""

    def __init__(self, refusal: IntegrationRefusal, member: str, value: object) -> None:
        """Record the compatibility-stable refusal fields."""
        super().__init__(f"{refusal.value}: {member}={value!r}")
        self.refusal = refusal
        self.member = member
        self.value = value


@dataclass(frozen=True)
class AgentCandidate:
    """The closed candidate assertion returned by the Agent Mesh gateway."""

    proposal_type: str
    source_event_id: str
    source_event_digest: str
    drone_id: str
    latitude_microdegrees: int
    longitude_microdegrees: int
    command_type: str


@dataclass(frozen=True)
class AgentResponse:
    """One accepted structured Agent Mesh integration response."""

    mission_id: str
    agent_name: str
    invocation_id: str
    correlation_id: str
    outcome: AgentOutcome
    candidate: AgentCandidate | None = None
    reason: AgentResponseReason | None = None


_OUTCOMES: Final[Mapping[str, AgentOutcome]] = {outcome.value: outcome for outcome in AgentOutcome}
_REASONS: Final[Mapping[str, AgentResponseReason]] = {
    reason.value: reason for reason in AgentResponseReason
}


def _unknown_member(
    document: Mapping[object, object],
    allowed: frozenset[str],
) -> tuple[str, object] | None:
    """Return the deterministically first unknown member and its original value."""
    unknown = ((str(key), key) for key in document if key not in allowed)
    selected = min(unknown, key=lambda item: item[0], default=None)
    if selected is None:
        return None
    name, key = selected
    return name, document[key]


def _members(
    document: object,
    label: str,
    allowed: frozenset[str],
    required: tuple[str, ...],
) -> Mapping[object, object]:
    """Return a closed complete object or raise its first structural refusal."""
    if not isinstance(document, Mapping):
        raise IntegrationError(IntegrationRefusal.NOT_AN_OBJECT, label, document)
    unknown = _unknown_member(document, allowed)
    if unknown is not None:
        name, value = unknown
        raise IntegrationError(IntegrationRefusal.UNKNOWN_MEMBER, name, value)
    for name in required:
        if name not in document:
            raise IntegrationError(IntegrationRefusal.MISSING_MEMBER, name, None)
    return document


def _version(members: Mapping[object, object]) -> None:
    """Refuse a non-integer version before an unsupported integer version."""
    value = members[_VERSION_MEMBER]
    if type(value) is not int:
        raise IntegrationError(IntegrationRefusal.MEMBER_FORM, _VERSION_MEMBER, value)
    if value != AGENT_RESPONSE_VERSION:
        raise IntegrationError(IntegrationRefusal.VERSION, _VERSION_MEMBER, value)


def _text(value: object, member: str, pattern: str) -> str:
    """Return text matching one exact wire rule."""
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise IntegrationError(IntegrationRefusal.MEMBER_FORM, member, value)
    return value


def _constant(value: object, member: str, expected: str) -> str:
    """Return a required text constant without accepting a lookalike type or value."""
    if not isinstance(value, str) or value != expected:
        raise IntegrationError(IntegrationRefusal.MEMBER_FORM, member, value)
    return value


def _integer(value: object, member: str, lower: int, upper: int) -> int:
    """Return an integer within inclusive bounds, explicitly refusing booleans."""
    if type(value) is not int or value < lower or value > upper:
        raise IntegrationError(IntegrationRefusal.MEMBER_FORM, member, value)
    return value


def _outcome(value: object) -> AgentOutcome:
    """Return the closed response outcome."""
    outcome = _OUTCOMES.get(value) if isinstance(value, str) else None
    if outcome is None:
        raise IntegrationError(IntegrationRefusal.MEMBER_FORM, _OUTCOME_MEMBER, value)
    return outcome


def _reason(value: object) -> AgentResponseReason:
    """Return one redacted abstention reason."""
    reason = _REASONS.get(value) if isinstance(value, str) else None
    if reason is None:
        raise IntegrationError(IntegrationRefusal.MEMBER_FORM, _REASON_MEMBER, value)
    return reason


def _candidate(value: object) -> AgentCandidate:
    """Validate and return the candidate result branch."""
    try:
        members = _members(value, _RESULT_MEMBER, _RESULT_ALLOWED_MEMBERS, _RESULT_MEMBERS)
    except IntegrationError as error:
        if error.refusal in {
            IntegrationRefusal.UNKNOWN_MEMBER,
            IntegrationRefusal.MISSING_MEMBER,
        }:
            raise IntegrationError(
                error.refusal,
                f"result.{error.member}",
                error.value,
            ) from error
        raise
    proposal_type = _constant(
        members["proposalType"],
        "result.proposalType",
        "candidate-location",
    )
    source_event_id = _text(
        members["sourceEventId"],
        "result.sourceEventId",
        IDENTIFIER_PATTERN,
    )
    source_event_digest = _text(
        members["sourceEventDigest"],
        "result.sourceEventDigest",
        _DIGEST_PATTERN,
    )
    drone_id = _text(members["droneId"], "result.droneId", IDENTIFIER_PATTERN)
    latitude = _integer(
        members["latitudeMicrodegrees"],
        "result.latitudeMicrodegrees",
        _MIN_LATITUDE_MICRODEGREES,
        _MAX_LATITUDE_MICRODEGREES,
    )
    longitude = _integer(
        members["longitudeMicrodegrees"],
        "result.longitudeMicrodegrees",
        _MIN_LONGITUDE_MICRODEGREES,
        _MAX_LONGITUDE_MICRODEGREES,
    )
    command_type = _constant(
        members["commandType"],
        "result.commandType",
        "escalate-rescue",
    )
    return AgentCandidate(
        proposal_type=proposal_type,
        source_event_id=source_event_id,
        source_event_digest=source_event_digest,
        drone_id=drone_id,
        latitude_microdegrees=latitude,
        longitude_microdegrees=longitude,
        command_type=command_type,
    )


def _optionals(
    members: Mapping[object, object],
) -> tuple[AgentCandidate | None, AgentResponseReason | None]:
    """Validate whichever branch members are present before checking their binding."""
    candidate = _candidate(members[_RESULT_MEMBER]) if _RESULT_MEMBER in members else None
    reason = _reason(members[_REASON_MEMBER]) if _REASON_MEMBER in members else None
    return candidate, reason


def _bind_outcome(
    outcome: AgentOutcome,
    candidate: AgentCandidate | None,
    reason: AgentResponseReason | None,
    members: Mapping[object, object],
) -> None:
    """Require and forbid the branch members selected by the outcome."""
    if outcome is AgentOutcome.CANDIDATE:
        if candidate is None:
            raise IntegrationError(IntegrationRefusal.OUTCOME_BINDING, _RESULT_MEMBER, None)
        if reason is not None:
            raise IntegrationError(
                IntegrationRefusal.OUTCOME_BINDING,
                _REASON_MEMBER,
                members[_REASON_MEMBER],
            )
        return
    if reason is None:
        raise IntegrationError(IntegrationRefusal.OUTCOME_BINDING, _REASON_MEMBER, None)
    if candidate is not None:
        raise IntegrationError(
            IntegrationRefusal.OUTCOME_BINDING,
            _RESULT_MEMBER,
            members[_RESULT_MEMBER],
        )


def parse_agent_response(document: object) -> AgentResponse:
    """Validate a decoded document as the closed Agent Mesh integration response."""
    members = _members(document, "response", _ALLOWED_MEMBERS, _COMMON_MEMBERS)
    _version(members)
    mission_id = _text(members["missionId"], "missionId", IDENTIFIER_PATTERN)
    agent_name = _text(members["agentName"], "agentName", AGENT_NAME_PATTERN)
    invocation_id = _text(members["invocationId"], "invocationId", IDENTIFIER_PATTERN)
    correlation_id = _text(members["correlationId"], "correlationId", IDENTIFIER_PATTERN)
    outcome = _outcome(members[_OUTCOME_MEMBER])
    candidate, reason = _optionals(members)
    _bind_outcome(outcome, candidate, reason, members)
    return AgentResponse(
        mission_id=mission_id,
        agent_name=agent_name,
        invocation_id=invocation_id,
        correlation_id=correlation_id,
        outcome=outcome,
        candidate=candidate,
        reason=reason,
    )


def agent_response_document(response: AgentResponse) -> dict[str, object]:
    """Return the integration document, the inverse of :func:`parse_agent_response`."""
    document: dict[str, object] = {
        _VERSION_MEMBER: AGENT_RESPONSE_VERSION,
        "missionId": response.mission_id,
        "agentName": response.agent_name,
        "invocationId": response.invocation_id,
        "correlationId": response.correlation_id,
        _OUTCOME_MEMBER: response.outcome.value,
    }
    if response.candidate is not None:
        candidate = response.candidate
        document[_RESULT_MEMBER] = {
            "proposalType": candidate.proposal_type,
            "sourceEventId": candidate.source_event_id,
            "sourceEventDigest": candidate.source_event_digest,
            "droneId": candidate.drone_id,
            "latitudeMicrodegrees": candidate.latitude_microdegrees,
            "longitudeMicrodegrees": candidate.longitude_microdegrees,
            "commandType": candidate.command_type,
        }
    if response.reason is not None:
        document[_REASON_MEMBER] = response.reason.value
    return document


def decode_agent_response(text: str | bytes) -> AgentResponse:
    """Decode canonical JSON before applying the integration-body contract."""
    return parse_agent_response(canonical.decode(text))


def check_agent_response_topic(response: AgentResponse, topic: Topic) -> None:
    """Refuse an integration response that disagrees with its arriving topic."""
    if topic.family is not Family.AGENT_RESPONSE:
        raise IntegrationError(IntegrationRefusal.TOPIC_BINDING, "family", topic.family)
    if topic.mission_id != response.mission_id:
        raise IntegrationError(
            IntegrationRefusal.TOPIC_BINDING,
            "missionId",
            response.mission_id,
        )
    if topic.parameters.get("agentName") != response.agent_name:
        raise IntegrationError(
            IntegrationRefusal.TOPIC_BINDING,
            "agentName",
            response.agent_name,
        )
