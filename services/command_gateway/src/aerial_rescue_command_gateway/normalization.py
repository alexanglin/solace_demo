"""Normalize a trusted Agent Response into exact proposal and audit publications.

Every identifier, instant, sequence, trace member, digest, and destination is supplied by
trusted application context.  Model output contributes only the closed candidate fields already
accepted by :mod:`aerial_rescue_contracts.integration`; it cannot choose an envelope identity,
topic, audit result, or executable command destination.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import proposal_digest
from aerial_rescue_contracts.envelope import (
    binding_for,
    check_topic_binding,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.integration import AgentOutcome
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.proposals import StoredProposal

from aerial_rescue_command_gateway.ingress import AgentResponseIngress

PRODUCER: Final = "command-gateway"
PROPOSAL_TYPE: Final = "candidate-location"
PROPOSAL_VERSION: Final = 1
AUDIT_VERSION: Final = 1
_EMPTY_HEADERS: Final = canonical.canonical_bytes({})


class NormalizationRefusal(Enum):
    """Why trusted context cannot normalize an Agent Response."""

    IDENTITY_MISMATCH = "agent response does not match its pending trusted invocation"
    DIGEST_MISMATCH = "agent response source digest does not match trusted forward context"
    RESPONSE_KIND = "ingress is not a direct Agent Response"
    SEQUENCE = "producer sequence is outside the CloudEvents profile"
    DUPLICATE_RESULT = "exact duplicate has no durable normalization result"
    TRANSPORT_CONTEXT = "agent response transport context is not the closed trusted property set"
    TRANSPORT_CONTEXT_CONFLICT = "agent response invocation conflicts with durable trusted context"


class NormalizationError(ValueError):
    """A redacted normalization refusal."""

    def __init__(self, refusal: NormalizationRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class PendingInvocation:
    """Trusted forward context stored before Agent Mesh work begins."""

    mission_id: str
    agent_name: str
    invocation_id: str
    correlation_id: str
    source_event_id: str
    source_event_digest: str


@dataclass(frozen=True)
class NormalizationStamp:
    """Every output member minted outside model control."""

    producer_id: str
    proposal_id: str
    proposal_event_id: str
    audit_record_id: str
    audit_event_id: str
    occurred_at: str
    proposal_sequence: int
    audit_sequence: int
    traceparent: str
    tracestate: str | None = None


@dataclass(frozen=True)
class NormalizationArtifacts:
    """The optional proposal and exact events one response creates."""

    proposal: StoredProposal | None
    events: tuple[StagedApplicationEvent, ...]
    result: bytes


@dataclass(frozen=True)
class _EventSpec:
    """The family-specific values required to construct one notification."""

    topic: Topic
    payload: dict[str, object]
    event_id: str
    sequence: int


def _bind_pending(ingress: AgentResponseIngress, pending: PendingInvocation) -> None:
    """Refuse any identity or provenance value not supplied by trusted forward context."""
    response = ingress.response
    received = (
        response.mission_id,
        response.agent_name,
        response.invocation_id,
        response.correlation_id,
    )
    expected = (
        pending.mission_id,
        pending.agent_name,
        pending.invocation_id,
        pending.correlation_id,
    )
    if received != expected:
        raise NormalizationError(NormalizationRefusal.IDENTITY_MISMATCH)
    candidate = response.candidate
    if candidate is None:
        return
    if candidate.source_event_id != pending.source_event_id:
        raise NormalizationError(NormalizationRefusal.IDENTITY_MISMATCH)
    if candidate.source_event_digest != pending.source_event_digest:
        raise NormalizationError(NormalizationRefusal.DIGEST_MISMATCH)


def _proposal_payload(
    ingress: AgentResponseIngress,
    pending: PendingInvocation,
    stamp: NormalizationStamp,
) -> dict[str, object]:
    """Return the closed proposal payload with its recomputable digest."""
    candidate = ingress.response.candidate
    if candidate is None:
        raise NormalizationError(NormalizationRefusal.RESPONSE_KIND)
    payload: dict[str, object] = {
        "canonicalizationVersion": 1,
        "proposalVersion": PROPOSAL_VERSION,
        "missionId": pending.mission_id,
        "proposalId": stamp.proposal_id,
        "proposalType": candidate.proposal_type,
        "agentName": pending.agent_name,
        "sourceInvocationId": pending.invocation_id,
        "sourceEventId": pending.source_event_id,
        "sourceEventDigest": pending.source_event_digest,
        "commandType": candidate.command_type,
        "droneId": candidate.drone_id,
        "latitudeMicrodegrees": candidate.latitude_microdegrees,
        "longitudeMicrodegrees": candidate.longitude_microdegrees,
    }
    payload["proposalDigest"] = proposal_digest(payload)
    return payload


def _audit_payload(
    ingress: AgentResponseIngress,
    pending: PendingInvocation,
    stamp: NormalizationStamp,
    proposal: dict[str, object] | None,
) -> dict[str, object]:
    """Return the redacted audit branch corresponding to the response."""
    response = ingress.response
    payload: dict[str, object] = {
        "auditVersion": AUDIT_VERSION,
        "missionId": pending.mission_id,
        "recordId": stamp.audit_record_id,
        "recordType": "proposal-normalization",
        "agentName": pending.agent_name,
        "invocationId": pending.invocation_id,
        "correlationId": pending.correlation_id,
        "outcome": "normalized" if proposal is not None else "abstained",
    }
    if proposal is None:
        if response.reason is None:
            raise NormalizationError(NormalizationRefusal.RESPONSE_KIND)
        payload["reason"] = response.reason.value
    else:
        payload.update(
            {
                "sourceEventId": pending.source_event_id,
                "sourceEventDigest": pending.source_event_digest,
                "proposalId": stamp.proposal_id,
                "proposalDigest": proposal["proposalDigest"],
                "proposalVersion": PROPOSAL_VERSION,
            }
        )
    return payload


def _event(
    pending: PendingInvocation,
    stamp: NormalizationStamp,
    spec: _EventSpec,
) -> bytes:
    """Build canonical bytes and prove their envelope/topic binding."""
    sequence = sequence_text(spec.sequence)
    if sequence is None:
        raise NormalizationError(NormalizationRefusal.SEQUENCE)
    kind = event_type(spec.topic)
    document: dict[str, object] = {
        "specversion": "1.0",
        "id": spec.event_id,
        "source": f"urn:aerial-rescue:command-gateway:{stamp.producer_id}",
        "type": kind,
        "subject": pending.mission_id,
        "time": stamp.occurred_at,
        "datacontenttype": "application/json",
        "dataschema": binding_for(kind).dataschema,
        "data": spec.payload,
        "sequence": sequence,
        "correlationid": pending.correlation_id,
        "causationid": pending.source_event_id,
        "traceparent": stamp.traceparent,
    }
    if stamp.tracestate is not None:
        document["tracestate"] = stamp.tracestate
    envelope = parse_envelope(document)
    check_topic_binding(envelope, spec.topic)
    return canonical.canonical_bytes(document)


def _staged(payload: bytes, family: Family, topic: Topic) -> StagedApplicationEvent:
    """Map one self-validated event to the exact application-outbox row."""
    envelope = parse_envelope(canonical.decode(payload))
    return StagedApplicationEvent(
        producer=PRODUCER,
        event_id=envelope.id,
        family=family.name.lower().replace("_", "-"),
        topic=format_topic(topic),
        headers=_EMPTY_HEADERS,
        payload=payload,
        traceparent=envelope.traceparent,
        tracestate=envelope.tracestate,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        staged_at=envelope.time,
    )


def _stored_proposal(
    payload: dict[str, object],
    event: StagedApplicationEvent,
    pending: PendingInvocation,
    stamp: NormalizationStamp,
) -> StoredProposal:
    """Map a normalized proposal to every immutable migrated column."""
    return StoredProposal(
        proposal_id=stamp.proposal_id,
        mission_id=pending.mission_id,
        source_event_id=pending.source_event_id,
        source_event_digest=pending.source_event_digest,
        agent_name=pending.agent_name,
        invocation_id=pending.invocation_id,
        proposal_type=str(payload["proposalType"]),
        proposal_digest=str(payload["proposalDigest"]),
        payload=canonical.canonical_bytes(payload),
        drone_id=str(payload["droneId"]),
        latitude_microdegrees=int(str(payload["latitudeMicrodegrees"])),
        longitude_microdegrees=int(str(payload["longitudeMicrodegrees"])),
        command_type=str(payload["commandType"]),
        issued_at=stamp.occurred_at,
        sequence=stamp.proposal_sequence,
        correlation_id=pending.correlation_id,
        causation_id=event.causation_id,
        traceparent=event.traceparent,
    )


def build_normalization(
    ingress: AgentResponseIngress,
    pending: PendingInvocation,
    stamp: NormalizationStamp,
) -> NormalizationArtifacts:
    """Validate trusted context and construct all exact durable effects."""
    _bind_pending(ingress, pending)
    proposal_payload = (
        _proposal_payload(ingress, pending, stamp)
        if ingress.response.outcome is AgentOutcome.CANDIDATE
        else None
    )
    events: list[StagedApplicationEvent] = []
    proposal: StoredProposal | None = None
    result: bytes
    if proposal_payload is not None:
        proposal_topic = Topic(
            Family.AGENT_PROPOSAL,
            pending.mission_id,
            {"agentName": pending.agent_name, "proposalType": PROPOSAL_TYPE},
        )
        proposal_bytes = _event(
            pending,
            stamp,
            _EventSpec(
                proposal_topic,
                proposal_payload,
                stamp.proposal_event_id,
                stamp.proposal_sequence,
            ),
        )
        proposal_event = _staged(proposal_bytes, Family.AGENT_PROPOSAL, proposal_topic)
        events.append(proposal_event)
        proposal = _stored_proposal(proposal_payload, proposal_event, pending, stamp)
        result = proposal_event.payload
    audit_topic = Topic(
        Family.AUDIT,
        pending.mission_id,
        {"recordType": "proposal-normalization"},
    )
    audit_bytes = _event(
        pending,
        stamp,
        _EventSpec(
            audit_topic,
            _audit_payload(ingress, pending, stamp, proposal_payload),
            stamp.audit_event_id,
            stamp.audit_sequence,
        ),
    )
    audit_event = _staged(audit_bytes, Family.AUDIT, audit_topic)
    events.append(audit_event)
    if proposal is None:
        result = audit_event.payload
    return NormalizationArtifacts(proposal, tuple(events), result)
