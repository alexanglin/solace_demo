"""Strict, representation-aware command-gateway broker ingress.

Topic parsing happens before payload decoding.  That makes the authorization surface visible
without trusting hostile bytes and, importantly, denies broker-delivered ``AGENT_PROPOSAL``
messages even when their payload is malformed: ADR-0146 removed that subscription because this
service is the proposal producer, not one of its consumers.

CloudEvent parsing and topic binding remain owned by :mod:`aerial_rescue_contracts`.  Pydantic
models here are the service-local closed payload twins required at a trust boundary; they do not
reimplement envelopes, topics, canonical JSON, RPC, or the Agent Response representation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Final, Literal

from aerial_rescue_contracts.envelope import Envelope, check_topic_binding, decode_envelope
from aerial_rescue_contracts.integration import (
    AgentResponse,
    check_agent_response_topic,
    decode_agent_response,
)
from aerial_rescue_contracts.rpc import GatewayRequest, decode_gateway_request
from aerial_rescue_contracts.topics import Family, Topic, parse_topic
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

_IDENTIFIER_PATTERN: Final = r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$"
_DIGEST_PATTERN: Final = r"^[0-9a-f]{64}$"
_INSTANT_PATTERN: Final = (
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$"
)

type Identifier = Annotated[str, StringConstraints(pattern=_IDENTIFIER_PATTERN, max_length=64)]
type Digest = Annotated[
    str,
    StringConstraints(pattern=_DIGEST_PATTERN, min_length=64, max_length=64),
]
type Instant = Annotated[str, StringConstraints(pattern=_INSTANT_PATTERN, max_length=24)]
type Latitude = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
type Longitude = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]


def _strict_one(value: object) -> object:
    """Keep a JSON boolean from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "version must be the integer one"
        raise ValueError(message)
    return value


type StrictOne = Annotated[Literal[1], BeforeValidator(_strict_one)]


class _ClosedModel(BaseModel):
    """The common closed, strict, immutable payload posture."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )


class AssignSectorAction(_ClosedModel):
    """The complete deterministic sector-assignment action."""

    command_type: Literal["assign-sector"] = Field(alias="commandType")
    drone_id: Identifier = Field(alias="droneId")
    sector_id: Identifier = Field(alias="sectorId")


class EscalateRescueAction(_ClosedModel):
    """The complete proposal-, evidence-, and location-bound escalation action."""

    command_type: Literal["escalate-rescue"] = Field(alias="commandType")
    drone_id: Identifier = Field(alias="droneId")
    proposal_id: Identifier = Field(alias="proposalId")
    proposal_digest: Digest = Field(alias="proposalDigest")
    proposal_version: StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: StrictOne = Field(alias="evidenceDecisionVersion")
    latitude_microdegrees: Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: Longitude = Field(alias="longitudeMicrodegrees")


type OperatorAction = AssignSectorAction | EscalateRescueAction


class OperatorCommandPayload(_ClosedModel):
    """The dashboard-minted command request accepted from its guaranteed queue."""

    operator_command_version: StrictOne = Field(alias="operatorCommandVersion")
    mission_id: Identifier = Field(alias="missionId")
    command_id: Identifier = Field(alias="commandId")
    operator_id: Identifier = Field(alias="operatorId")
    action: OperatorAction


class ApprovalAction(_ClosedModel):
    """The exact rescue action an approval decision binds."""

    command_type: Literal["escalate-rescue"] = Field(alias="commandType")
    drone_id: Identifier = Field(alias="droneId")
    latitude_microdegrees: Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: Longitude = Field(alias="longitudeMicrodegrees")


class OperatorApprovalPayload(_ClosedModel):
    """One proposal- and evidence-bound approval or rejection."""

    operator_approval_version: StrictOne = Field(alias="operatorApprovalVersion")
    mission_id: Identifier = Field(alias="missionId")
    approval_id: Identifier = Field(alias="approvalId")
    operator_id: Identifier = Field(alias="operatorId")
    decision: Literal["approve", "reject"]
    issued_at: Instant = Field(alias="issuedAt")
    expires_at: Instant | None = Field(default=None, alias="expiresAt")
    proposal_id: Identifier = Field(alias="proposalId")
    proposal_digest: Digest = Field(alias="proposalDigest")
    proposal_version: StrictOne = Field(alias="proposalVersion")
    evidence_decision_id: Identifier = Field(alias="evidenceDecisionId")
    evidence_decision_digest: Digest = Field(alias="evidenceDecisionDigest")
    evidence_decision_version: StrictOne = Field(alias="evidenceDecisionVersion")
    action: ApprovalAction

    @model_validator(mode="after")
    def _bind_expiry_to_decision(self) -> OperatorApprovalPayload:
        """Require expiry only on the approving branch, exactly as the closed schema does."""
        has_expiry = self.expires_at is not None
        if (self.decision == "approve") is not has_expiry:
            message = "approval expiry does not match decision branch"
            raise ValueError(message)
        return self


class CommandResultPayload(_ClosedModel):
    """One drone's typed lifecycle report for an existing command."""

    mission_id: Identifier = Field(alias="missionId")
    drone_id: Identifier = Field(alias="droneId")
    command_id: Identifier = Field(alias="commandId")
    outcome: Literal["acknowledged", "succeeded", "failed"]


@dataclass(frozen=True)
class OperatorCommandIngress:
    """A fully validated operator command and its envelope context."""

    topic: Topic
    envelope: Envelope
    payload: OperatorCommandPayload


@dataclass(frozen=True)
class OperatorApprovalIngress:
    """A fully validated operator approval decision and its envelope context."""

    topic: Topic
    envelope: Envelope
    payload: OperatorApprovalPayload


@dataclass(frozen=True)
class CommandResultIngress:
    """A fully validated command result and its envelope context."""

    topic: Topic
    envelope: Envelope
    payload: CommandResultPayload


@dataclass(frozen=True)
class GatewayRequestIngress:
    """A validated gateway RPC request and its parsed destination."""

    topic: Topic
    request: GatewayRequest


@dataclass(frozen=True)
class AgentResponseIngress:
    """A validated direct Agent Response and its parsed destination."""

    topic: Topic
    response: AgentResponse


type AcceptedIngress = (
    OperatorCommandIngress
    | OperatorApprovalIngress
    | CommandResultIngress
    | GatewayRequestIngress
    | AgentResponseIngress
)


class IngressRefusal(Enum):
    """Why broker input cannot cross the command-gateway trust boundary."""

    TOPIC = "destination is not an application topic"
    UNAUTHORIZED_FAMILY = "topic family is not command-gateway ingress authority"
    ENVELOPE = "notification is not a valid canonical CloudEvent"
    PAYLOAD = "notification payload does not match its closed schema"
    BINDING = "body identities do not bind to the arriving topic"


class IngressError(ValueError):
    """A redacted ingress refusal which never retains hostile bytes."""

    def __init__(self, refusal: IngressRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


def _topic(topic_text: object) -> Topic:
    """Parse one destination without retaining its untrusted original on refusal."""
    try:
        return parse_topic(topic_text)
    except ValueError:
        raise IngressError(IngressRefusal.TOPIC) from None


def _envelope(raw: bytes, topic: Topic) -> Envelope:
    """Canonical-decode and topic-bind a notification."""
    try:
        envelope = decode_envelope(raw)
        check_topic_binding(envelope, topic)
    except ValueError:
        raise IngressError(IngressRefusal.ENVELOPE) from None
    return envelope


def _payload[PayloadModel: _ClosedModel](
    model: type[PayloadModel], envelope: Envelope
) -> PayloadModel:
    """Validate a closed service-local payload twin."""
    try:
        return model.model_validate(envelope.data, strict=True)
    except ValidationError:
        raise IngressError(IngressRefusal.PAYLOAD) from None


def _operator_command(raw: bytes, topic: Topic) -> OperatorCommandIngress:
    envelope = _envelope(raw, topic)
    payload = _payload(OperatorCommandPayload, envelope)
    return OperatorCommandIngress(topic, envelope, payload)


def _operator_approval(raw: bytes, topic: Topic) -> OperatorApprovalIngress:
    envelope = _envelope(raw, topic)
    payload = _payload(OperatorApprovalPayload, envelope)
    return OperatorApprovalIngress(topic, envelope, payload)


def _command_result(raw: bytes, topic: Topic) -> CommandResultIngress:
    envelope = _envelope(raw, topic)
    payload = _payload(CommandResultPayload, envelope)
    return CommandResultIngress(topic, envelope, payload)


def _gateway_request(raw: bytes, topic: Topic) -> GatewayRequestIngress:
    """Validate and bind one RPC request without turning it into a CloudEvent."""
    try:
        request = decode_gateway_request(raw)
    except ValueError:
        raise IngressError(IngressRefusal.PAYLOAD) from None
    if request.mission_id != topic.mission_id or request.operation != topic.parameters["operation"]:
        raise IngressError(IngressRefusal.BINDING)
    return GatewayRequestIngress(topic, request)


def _agent_response(raw: bytes, topic: Topic) -> AgentResponseIngress:
    """Validate and bind the one direct integration document."""
    try:
        response = decode_agent_response(raw)
    except ValueError:
        raise IngressError(IngressRefusal.PAYLOAD) from None
    try:
        check_agent_response_topic(response, topic)
    except ValueError:
        raise IngressError(IngressRefusal.BINDING) from None
    return AgentResponseIngress(topic, response)


def accept_ingress(raw: bytes, topic_text: object) -> AcceptedIngress:
    """Return one authorized, parsed broker document or fail closed before mutation."""
    topic = _topic(topic_text)
    handlers: dict[Family, Callable[[bytes, Topic], AcceptedIngress]] = {
        Family.OPERATOR_COMMAND: _operator_command,
        Family.OPERATOR_APPROVAL: _operator_approval,
        Family.DRONE_COMMAND_RESULT: _command_result,
        Family.GATEWAY_REQUEST: _gateway_request,
        Family.AGENT_RESPONSE: _agent_response,
    }
    handler = handlers.get(topic.family)
    if handler is None:
        raise IngressError(IngressRefusal.UNAUTHORIZED_FAMILY)
    return handler(raw, topic)
