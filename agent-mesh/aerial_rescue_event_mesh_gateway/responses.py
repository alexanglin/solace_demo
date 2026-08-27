"""Closed, redacted Agent Response construction for the application boundary."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Final

_IDENTIFIER = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_AGENT_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_OUTPUT_MEMBERS: Final = frozenset({"latitudeMicrodegrees", "longitudeMicrodegrees"})
_FORWARDED_IDENTITY_MEMBERS: Final = (
    "missionId",
    "eventMissionId",
    "droneId",
    "eventDroneId",
    "sourceEventId",
    "sourceEventDigest",
    "correlationId",
    "agentName",
)


class AgentResponseReason(StrEnum):
    """The closed, redacted abstention vocabulary from ADR-0148."""

    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport-error"
    MODEL_ERROR = "model-error"
    INVALID_OUTPUT = "invalid-output"
    IDENTITY_MISMATCH = "identity-mismatch"


class AgentResponseContextError(ValueError):
    """Trusted context cannot form even the common response identity."""


def _string(mapping: Mapping[str, object], name: str) -> str | None:
    value = mapping.get(name)
    return value if isinstance(value, str) else None


def _identifier(mapping: Mapping[str, object], name: str) -> str | None:
    value = _string(mapping, name)
    if value is None or _IDENTIFIER.fullmatch(value) is None:
        return None
    return value


def _agent_name(mapping: Mapping[str, object]) -> str | None:
    value = _string(mapping, "agentName")
    if value is None or _AGENT_NAME.fullmatch(value) is None:
        return None
    return value


def _hash_member(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if isinstance(value, (str, int, bool)) or value is None:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return '"invalid"'


def deterministic_invocation_id(forwarded_context: Mapping[str, object]) -> str:
    """Derive one stable official A2A task identity for an exact source delivery."""
    preimage = "\n".join(
        f"{name}={_hash_member(forwarded_context, name)}" for name in _FORWARDED_IDENTITY_MEMBERS
    ).encode("utf-8")
    digest = hashlib.sha256(preimage).hexdigest()
    return f"gdk-task-{digest[:32]}"


def _common_response(
    forwarded_context: Mapping[str, object], invocation_id: str
) -> dict[str, object]:
    mission_id = _identifier(forwarded_context, "missionId")
    correlation_id = _identifier(forwarded_context, "correlationId")
    agent_name = _agent_name(forwarded_context)
    if (
        mission_id is None
        or correlation_id is None
        or agent_name is None
        or _IDENTIFIER.fullmatch(invocation_id) is None
    ):
        raise AgentResponseContextError
    return {
        "agentResponseVersion": 1,
        "missionId": mission_id,
        "agentName": agent_name,
        "invocationId": invocation_id,
        "correlationId": correlation_id,
    }


def _trusted_candidate_identity(
    forwarded_context: Mapping[str, object],
) -> tuple[str, str, str] | None:
    mission_id = _identifier(forwarded_context, "missionId")
    event_mission_id = _identifier(forwarded_context, "eventMissionId")
    drone_id = _identifier(forwarded_context, "droneId")
    event_drone_id = _identifier(forwarded_context, "eventDroneId")
    source_event_id = _identifier(forwarded_context, "sourceEventId")
    source_event_digest = _string(forwarded_context, "sourceEventDigest")
    if (
        mission_id is None
        or mission_id != event_mission_id
        or drone_id is None
        or drone_id != event_drone_id
        or source_event_id is None
        or source_event_digest is None
        or _LOWERCASE_SHA256.fullmatch(source_event_digest) is None
    ):
        return None
    return source_event_id, source_event_digest, drone_id


def _coordinate(value: object, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def _candidate_coordinates(structured_output: object) -> tuple[int, int] | None:
    if not isinstance(structured_output, Mapping):
        return None
    if set(structured_output) != _MODEL_OUTPUT_MEMBERS:
        return None
    latitude = _coordinate(
        structured_output.get("latitudeMicrodegrees"),
        minimum=-90_000_000,
        maximum=90_000_000,
    )
    longitude = _coordinate(
        structured_output.get("longitudeMicrodegrees"),
        minimum=-180_000_000,
        maximum=180_000_000,
    )
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def build_agent_response(
    *,
    forwarded_context: Mapping[str, object],
    invocation_id: str,
    structured_output: object = None,
    failure_reason: AgentResponseReason | None = None,
    untrusted_failure: object = None,
) -> dict[str, object]:
    """Wrap untrusted model output in trusted identities or return one abstention."""
    del untrusted_failure
    common = _common_response(forwarded_context, invocation_id)
    candidate_identity = _trusted_candidate_identity(forwarded_context)
    if candidate_identity is None:
        return {
            **common,
            "outcome": "abstained",
            "reason": AgentResponseReason.IDENTITY_MISMATCH.value,
        }
    if failure_reason is not None:
        return {**common, "outcome": "abstained", "reason": failure_reason.value}

    coordinates = _candidate_coordinates(structured_output)
    if coordinates is None:
        return {
            **common,
            "outcome": "abstained",
            "reason": AgentResponseReason.INVALID_OUTPUT.value,
        }

    source_event_id, source_event_digest, drone_id = candidate_identity
    latitude, longitude = coordinates
    return {
        **common,
        "outcome": "candidate",
        "result": {
            "proposalType": "candidate-location",
            "sourceEventId": source_event_id,
            "sourceEventDigest": source_event_digest,
            "droneId": drone_id,
            "latitudeMicrodegrees": latitude,
            "longitudeMicrodegrees": longitude,
            "commandType": "escalate-rescue",
        },
    }


def failure_reason_from_payload(
    simplified_payload: Mapping[str, object],
) -> AgentResponseReason | None:
    """Classify only closed internal failure markers; never return upstream text."""
    internal_reason = simplified_payload.get("aerial_rescue_failure_reason")
    if internal_reason in {
        AgentResponseReason.TIMEOUT.value,
        AgentResponseReason.TRANSPORT_ERROR.value,
        AgentResponseReason.MODEL_ERROR.value,
    }:
        return AgentResponseReason(str(internal_reason))

    response = simplified_payload.get("a2a_task_response")
    if not isinstance(response, Mapping) or "error" not in response:
        return None
    error = response.get("error")
    if isinstance(error, Mapping):
        data = error.get("data")
        if isinstance(data, Mapping) and data.get("error_type") in {
            "connection_error",
            "transport_error",
            "a2a_transport_error",
        }:
            return AgentResponseReason.TRANSPORT_ERROR
    return AgentResponseReason.MODEL_ERROR
