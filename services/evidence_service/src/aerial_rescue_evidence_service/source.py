"""Independent validation of a proposal's durable source and provenance."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Annotated, Final

from aerial_rescue_contracts.digest import Context, digest, matches, source_event_digest
from aerial_rescue_contracts.envelope import Envelope, check_topic_binding, decode_envelope
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_contracts.topics import Family, parse_topic
from aerial_rescue_domain.scoring import ObservationOrigin
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from aerial_rescue_evidence_service.ports import ProvenanceFact, SourceEvidence
from aerial_rescue_evidence_service.wire import AcceptedProposal

_IDENTIFIER_PATTERN: Final = r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$"
_DIGEST_PATTERN: Final = r"^[0-9a-f]{64}$"
_MAXIMUM_CONTRIBUTORS: Final = 23
_SALIENT_EVENT_TYPE: Final = "aerial-rescue.v1.drone.event.salient"
_MODEL_ID_PREFIX: Final = "model-"
type Identifier = Annotated[str, StringConstraints(pattern=_IDENTIFIER_PATTERN, max_length=64)]
type Kind = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=32),
]
type Latitude = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
type Longitude = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]
type Detail = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class SalientPayload(BaseModel):
    """The closed salient-event payload used only as untrusted observation data."""

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
    observation: Kind
    latitude_microdegrees: Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: Longitude = Field(alias="longitudeMicrodegrees")
    detail: Detail


class ProvenanceRefusal(Enum):
    """Why durable source material cannot support a decision."""

    MISSING = "provenance-missing"
    MISMATCH = "provenance-mismatch"


class ProvenanceError(ValueError):
    """A redacted source or provenance refusal."""

    def __init__(self, refusal: ProvenanceRefusal) -> None:
        """Keep hostile payload content out of diagnostics."""
        super().__init__(refusal.value)
        self.refusal = refusal


def validate_source(
    proposal: AcceptedProposal,
    source: SourceEvidence | None,
) -> tuple[ProvenanceFact, ...]:
    """Validate the complete durable source binding and every provenance digest."""
    if source is None or not source.observations:
        raise ProvenanceError(ProvenanceRefusal.MISSING)
    if len(source.observations) > _MAXIMUM_CONTRIBUTORS:
        raise ProvenanceError(ProvenanceRefusal.MISMATCH)
    evidence_identities = {fact.evidence_item_id for fact in source.observations}
    if len(evidence_identities) != len(source.observations):
        raise ProvenanceError(ProvenanceRefusal.MISMATCH)
    try:
        topic = parse_topic(source.topic)
        envelope = decode_envelope(source.event)
        check_topic_binding(envelope, topic)
        payload = SalientPayload.model_validate(envelope.data, strict=True)
    except ValidationError, ValueError:
        raise ProvenanceError(ProvenanceRefusal.MISMATCH) from None
    if not _source_binding_matches(proposal, topic.family, envelope, payload):
        raise ProvenanceError(ProvenanceRefusal.MISMATCH)
    if not matches(proposal.payload.source_event_digest, source_event_digest(envelope)):
        raise ProvenanceError(ProvenanceRefusal.MISMATCH)
    if not all(
        _provenance_matches(fact, proposal.payload.source_event_id) for fact in source.observations
    ):
        raise ProvenanceError(ProvenanceRefusal.MISMATCH)
    return source.observations


def with_model_provenance(
    proposal: AcceptedProposal,
    observations: tuple[ProvenanceFact, ...],
) -> tuple[ProvenanceFact, ...]:
    """Add one deterministic model fact when durable source evidence has none."""
    if any(fact.origin is ObservationOrigin.LIVE_MODEL for fact in observations):
        return observations
    payload = proposal.payload
    eligible_sensor = any(
        fact.origin is ObservationOrigin.LIVE_SENSOR
        and fact.document.get("sourceEventDigest") == payload.source_event_digest
        for fact in observations
    )
    if not eligible_sensor:
        return observations
    evidence_id = (
        _MODEL_ID_PREFIX
        + hashlib.sha256(
            (payload.proposal_id + "\0" + payload.proposal_digest).encode()
        ).hexdigest()[:57]
    )
    document: dict[str, object] = {
        "canonicalizationVersion": 1,
        "evidenceItemId": evidence_id,
        "sourceId": payload.source_invocation_id,
        "origin": ObservationOrigin.LIVE_MODEL.value,
        "sourceEventId": payload.source_event_id,
        "proposalId": payload.proposal_id,
        "proposalDigest": payload.proposal_digest,
        "agentName": payload.agent_name,
    }
    model = ProvenanceFact(
        evidence_item_id=evidence_id,
        source_id=payload.source_invocation_id,
        origin=ObservationOrigin.LIVE_MODEL,
        provenance_digest=digest(Context.EVIDENCE, document),
        document=document,
        observed_at=proposal.envelope.time,
    )
    return (*observations, model)


def _source_binding_matches(
    proposal: AcceptedProposal,
    family: Family,
    envelope: Envelope,
    payload: SalientPayload,
) -> bool:
    """Require family, event, mission, and authenticated drone identity to agree."""
    event_matches = (
        family is Family.DRONE_EVENT
        and envelope.type == _SALIENT_EVENT_TYPE
        and envelope.id == proposal.payload.source_event_id
    )
    identity_matches = (
        payload.mission_id == proposal.payload.mission_id
        and payload.drone_id == proposal.payload.drone_id
        and envelope.source == f"urn:aerial-rescue:drone:{payload.drone_id}"
    )
    return event_matches and identity_matches


def _provenance_matches(fact: ProvenanceFact, source_event_id: str) -> bool:
    """Recompute a provenance document under the contract-owned evidence context."""
    if not _fact_form_matches(fact, source_event_id):
        return False
    try:
        computed = digest(Context.EVIDENCE, fact.document)
    except ValueError:
        return False
    return matches(fact.provenance_digest, computed)


def _fact_form_matches(fact: ProvenanceFact, source_event_id: str) -> bool:
    """Require the durable row to agree with the identities inside its covered document."""
    identifiers = (fact.evidence_item_id, fact.source_id)
    if not all(re.fullmatch(_IDENTIFIER_PATTERN, value) is not None for value in identifiers):
        return False
    if re.fullmatch(_DIGEST_PATTERN, fact.provenance_digest) is None:
        return False
    try:
        parse_instant(fact.observed_at)
    except ValueError:
        return False
    expected = {
        "evidenceItemId": fact.evidence_item_id,
        "sourceId": fact.source_id,
        "origin": fact.origin.value,
        "sourceEventId": source_event_id,
    }
    return all(fact.document.get(name) == value for name, value in expected.items())
