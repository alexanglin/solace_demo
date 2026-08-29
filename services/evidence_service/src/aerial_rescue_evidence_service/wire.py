"""Strict proposal ingress for the evidence service.

The contract package owns canonical decoding, CloudEvents, topic binding, and the
proposal digest. This service-local Pydantic twin independently enforces the closed
payload schema before an untrusted proposal reaches persistence or scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Final, Literal

from aerial_rescue_contracts.digest import proposal_digest_matches
from aerial_rescue_contracts.envelope import (
    Envelope,
    check_topic_binding,
    decode_envelope,
)
from aerial_rescue_contracts.topics import Family, parse_topic
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

_IDENTIFIER_PATTERN: Final = r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$"
_AGENT_NAME_PATTERN: Final = r"^[A-Za-z0-9_]{1,64}$"
_DIGEST_PATTERN: Final = r"^[0-9a-f]{64}$"

type Identifier = Annotated[str, StringConstraints(pattern=_IDENTIFIER_PATTERN, max_length=64)]
type AgentName = Annotated[str, StringConstraints(pattern=_AGENT_NAME_PATTERN, max_length=64)]
type Digest = Annotated[
    str,
    StringConstraints(pattern=_DIGEST_PATTERN, min_length=64, max_length=64),
]
type Latitude = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
type Longitude = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]


def _strict_one(value: object) -> object:
    """Keep a JSON boolean from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "version must be the integer one"
        raise ValueError(message)
    return value


type StrictOne = Annotated[Literal[1], BeforeValidator(_strict_one)]


class ProposalPayload(BaseModel):
    """The exact accepted agent-proposal payload."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )

    canonicalization_version: StrictOne = Field(alias="canonicalizationVersion")
    proposal_version: StrictOne = Field(alias="proposalVersion")
    mission_id: Identifier = Field(alias="missionId")
    proposal_id: Identifier = Field(alias="proposalId")
    proposal_type: Literal["candidate-location"] = Field(alias="proposalType")
    agent_name: AgentName = Field(alias="agentName")
    source_invocation_id: Identifier = Field(alias="sourceInvocationId")
    source_event_id: Identifier = Field(alias="sourceEventId")
    source_event_digest: Digest = Field(alias="sourceEventDigest")
    command_type: Literal["escalate-rescue"] = Field(alias="commandType")
    drone_id: Identifier = Field(alias="droneId")
    latitude_microdegrees: Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: Longitude = Field(alias="longitudeMicrodegrees")
    proposal_digest: Digest = Field(alias="proposalDigest")


class IngressRefusal(Enum):
    """Why an inbound proposal cannot reach evidence processing."""

    UNREADABLE = "proposal is not a valid canonical CloudEvent"
    UNROUTED = "message did not arrive on the proposal family"
    PAYLOAD = "proposal payload does not match its closed schema"
    DIGEST_MISMATCH = "proposal digest does not bind its complete accepted payload"


class IngressError(ValueError):
    """A redacted evidence-ingress refusal."""

    def __init__(self, refusal: IngressRefusal) -> None:
        """Expose only the closed reason, never the hostile input."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class AcceptedProposal:
    """One independently validated proposal and its envelope context."""

    envelope: Envelope
    payload: ProposalPayload


def accept_proposal(raw: str | bytes, topic_text: object) -> AcceptedProposal:
    """Canonical-decode, bind, validate, and integrity-check one proposal."""
    try:
        topic = parse_topic(topic_text)
    except ValueError:
        raise IngressError(IngressRefusal.UNROUTED) from None
    if topic.family is not Family.AGENT_PROPOSAL:
        raise IngressError(IngressRefusal.UNROUTED)
    try:
        envelope = decode_envelope(raw)
        check_topic_binding(envelope, topic)
    except ValueError:
        raise IngressError(IngressRefusal.UNREADABLE) from None
    try:
        payload = ProposalPayload.model_validate(envelope.data, strict=True)
    except ValidationError:
        raise IngressError(IngressRefusal.PAYLOAD) from None
    if not proposal_digest_matches(envelope.data):
        raise IngressError(IngressRefusal.DIGEST_MISMATCH)
    return AcceptedProposal(envelope, payload)
