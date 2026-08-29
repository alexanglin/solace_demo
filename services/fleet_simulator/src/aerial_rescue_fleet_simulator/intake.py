"""What a simulated drone accepts off its own command queue, and what it refuses.

The refusal order is part of the contract, the way ``parse_envelope``'s is. The topic is
read first and decides routing, so a command addressed to another drone or another mission
is refused before this drone parses a payload it does not own; then the command type
against the closed authority table; then the bytes; then the envelope against the topic;
then the payload's own members.

Those last members are read defensively rather than trusted. ``parse_envelope`` validates
the envelope profile and canonical representation while service-local strict models execute
the complete type-specific payload shape. The concrete runtime also executes the committed
JSON Schema before this boundary; this duplicate validation is intentional defence in depth.

Nothing here decides settlement, drives a machine, or reads a clock. The composition root
takes the bytes off a message and decides what to do with a refusal. This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, ClassVar, Final, Literal

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    EnvelopeError,
    EnvelopeRefusal,
    check_topic_binding,
    decode_envelope,
)
from aerial_rescue_contracts.topics import (
    DRONE_PARAMETER,
    IDENTIFIER_PATTERN,
    Family,
    Topic,
    TopicError,
    parse_topic,
)
from aerial_rescue_domain import DomainError
from aerial_rescue_domain.authority import CommandType, command_type
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from aerial_rescue_fleet_simulator import FleetSimulatorError

COMMAND_TYPE_PARAMETER: Final = "commandType"
COMMAND_ID_MEMBER: Final = "commandId"
SECTOR_ID_MEMBER: Final = "sectorId"

_DIGEST_PATTERN: Final = r"^[0-9a-f]{64}$"

type Identifier = Annotated[
    str,
    StringConstraints(pattern=IDENTIFIER_PATTERN, max_length=64),
]
type Digest = Annotated[
    str,
    StringConstraints(pattern=_DIGEST_PATTERN, min_length=64, max_length=64),
]
type Latitude = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
type Longitude = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]


def _strict_one(value: object) -> object:
    """Prevent a JSON boolean from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "version must be the integer one"
        raise ValueError(message)
    return value


type StrictOne = Annotated[Literal[1], BeforeValidator(_strict_one)]


class _ClosedPayload(BaseModel):
    """The common strict, immutable, alias-only payload posture."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )

    mission_id: Identifier = Field(alias="missionId")
    drone_id: Identifier = Field(alias="droneId")
    command_id: Identifier = Field(alias="commandId")


class _AssignSectorPayload(_ClosedPayload):
    """The exact assign-sector payload branch."""

    sector_id: Identifier = Field(alias="sectorId")


class _EscalateRescuePayload(_ClosedPayload):
    """The exact approval-, proposal-, evidence-, and location-bound rescue branch."""

    approval_id: Identifier = Field(alias="approvalId")
    proposal_id: Identifier = Field(alias="proposalId")
    proposal_digest: Digest = Field(alias="proposalDigest")
    proposal_version: StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: StrictOne = Field(alias="evidenceDecisionVersion")
    latitude_microdegrees: Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: Longitude = Field(alias="longitudeMicrodegrees")


class IntakeRefusal(Enum):
    """Why a message on this drone's command queue is not a command it may act on."""

    NO_PAYLOAD = "message carries no payload"
    UNROUTED = "topic is not this drone's command family in this run's mission"
    UNKNOWN_COMMAND_TYPE = "command type is outside the closed authority table"
    UNREADABLE = "payload is not an application event envelope"
    UNBOUND_COMMAND_TYPE = "command type has no payload schema bound to it"
    TOPIC_DISAGREEMENT = "envelope does not bind to the topic it arrived on"
    MALFORMED_COMMAND = "payload does not carry the members its command type needs"


class IntakeError(FleetSimulatorError):
    """A message intake refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class AssignSectorCommand:
    """One completely validated deterministic sector assignment."""

    command_id: str
    sector_id: str
    event_id: str
    correlation_id: str
    sequence: int
    command_type: ClassVar[CommandType] = CommandType.ASSIGN_SECTOR


@dataclass(frozen=True)
class EscalateRescueCommand:
    """One immutable rescue effect retaining every upstream authority binding."""

    command_id: str
    approval_id: str
    proposal_id: str
    proposal_digest: str
    proposal_version: int
    evidence_decision_id: str
    evidence_decision_digest: str
    evidence_decision_version: int
    latitude_microdegrees: int
    longitude_microdegrees: int
    event_id: str
    correlation_id: str
    sequence: int
    command_type: ClassVar[CommandType] = CommandType.ESCALATE_RESCUE


type IncomingCommand = AssignSectorCommand | EscalateRescueCommand


def _routed(topic: str, drone_id: str, mission_id: str) -> Topic:
    """Return the topic this message arrived on, refusing one this drone does not own."""
    try:
        parsed = parse_topic(topic)
    except TopicError as refusal:
        raise IntakeError(IntakeRefusal.UNROUTED, topic) from refusal
    addressed = (
        parsed.family is Family.DRONE_COMMAND
        and parsed.mission_id == mission_id
        and parsed.parameters.get(DRONE_PARAMETER) == drone_id
    )
    if not addressed:
        raise IntakeError(IntakeRefusal.UNROUTED, topic)
    return parsed


def _refused_member(error: ValidationError) -> str:
    """Return only a closed schema member, never an attacker-selected extra key."""
    fields = (
        *_AssignSectorPayload.model_fields.values(),
        *_EscalateRescuePayload.model_fields.values(),
    )
    known = {alias for field in fields if (alias := field.alias) is not None}
    first = error.errors(include_input=False, include_url=False)[0]["loc"]
    candidate = first[0] if first else None
    return candidate if isinstance(candidate, str) and candidate in known else "payload"


def _validate_payload(
    requested: CommandType,
    payload: Mapping[str, object],
) -> _AssignSectorPayload | _EscalateRescuePayload:
    """Execute the exact type-specific closed payload shape."""
    model = (
        _AssignSectorPayload if requested is CommandType.ASSIGN_SECTOR else _EscalateRescuePayload
    )
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise IntakeError(IntakeRefusal.MALFORMED_COMMAND, _refused_member(error)) from error


def accept(payload: bytes | None, topic: str, drone_id: str, mission_id: str) -> IncomingCommand:
    """Return the command these bytes carry, or refuse them with a structured reason.

    Args:
        payload: The message body, or ``None`` when the message carried none.
        topic: The topic the message arrived on, as the broker reported it.
        drone_id: The drone whose queue this is.
        mission_id: The mission this run is flying.

    Returns:
        The accepted command.

    Raises:
        IntakeError: In the order this module documents, carrying the refusal and the
            value that caused it.
    """
    if payload is None:
        raise IntakeError(IntakeRefusal.NO_PAYLOAD, None)
    parsed = _routed(topic, drone_id, mission_id)
    kind = parsed.parameters[COMMAND_TYPE_PARAMETER]
    try:
        requested = command_type(kind)
    except DomainError as refusal:
        raise IntakeError(IntakeRefusal.UNKNOWN_COMMAND_TYPE, kind) from refusal
    try:
        envelope = decode_envelope(payload)
    except canonical.CanonicalizationError as refusal:
        raise IntakeError(IntakeRefusal.UNREADABLE, str(refusal)) from refusal
    except EnvelopeError as refusal:
        raise _unreadable(refusal) from refusal
    try:
        check_topic_binding(envelope, parsed)
    except EnvelopeError as refusal:
        raise IntakeError(IntakeRefusal.TOPIC_DISAGREEMENT, refusal.value) from refusal
    validated = _validate_payload(requested, envelope.data)
    if isinstance(validated, _AssignSectorPayload):
        return AssignSectorCommand(
            command_id=validated.command_id,
            sector_id=validated.sector_id,
            event_id=envelope.id,
            correlation_id=envelope.correlation_id,
            sequence=int(envelope.sequence),
        )
    return EscalateRescueCommand(
        command_id=validated.command_id,
        approval_id=validated.approval_id,
        proposal_id=validated.proposal_id,
        proposal_digest=validated.proposal_digest,
        proposal_version=validated.proposal_version,
        evidence_decision_id=validated.evidence_decision_id,
        evidence_decision_digest=validated.evidence_decision_digest,
        evidence_decision_version=validated.evidence_decision_version,
        latitude_microdegrees=validated.latitude_microdegrees,
        longitude_microdegrees=validated.longitude_microdegrees,
        event_id=envelope.id,
        correlation_id=envelope.correlation_id,
        sequence=int(envelope.sequence),
    )


def _unreadable(refusal: EnvelopeError) -> IntakeError:
    """Return the refusal an unparsable envelope becomes, naming an unbound type as such.

    A command type the authority table knows but no payload schema binds is a different
    fact from malformed bytes: it is the state ADR-0082 leaves ``escalate-rescue`` in, so a
    drone that meets one has met a command the system cannot yet express rather than a
    corrupt message. Both are deterministic, so both are refused; only the diagnosis
    differs, and it differs on the audit trail.
    """
    if refusal.refusal is EnvelopeRefusal.UNKNOWN_TYPE:
        return IntakeError(IntakeRefusal.UNBOUND_COMMAND_TYPE, refusal.value)
    return IntakeError(IntakeRefusal.UNREADABLE, str(refusal))
