"""Canonical evidence-decision and typed-audit publication construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import evidence_decision_digest
from aerial_rescue_contracts.envelope import check_topic_binding, parse_envelope, sequence_text
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.evidence import StoredEvidenceDecision, StoredEvidenceItem

from aerial_rescue_evidence_service.evaluation import Evaluation
from aerial_rescue_evidence_service.ports import DecisionStamp
from aerial_rescue_evidence_service.wire import AcceptedProposal

PRODUCER: Final = "evidence-service"
DECISION_VERSION: Final = 1
AUDIT_VERSION: Final = 1
_EMPTY_HEADERS: Final = canonical.canonical_bytes({})


class PublicationRefusal(Enum):
    """Why trusted decision inputs cannot form a contract event."""

    SEQUENCE = "producer sequence is outside the CloudEvents profile"


class PublicationError(ValueError):
    """A decision publication that cannot be constructed."""

    def __init__(self, refusal: PublicationRefusal) -> None:
        """Expose the closed construction reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class DecisionArtifacts:
    """The database decision and its two exact application publications."""

    decision: StoredEvidenceDecision
    items: tuple[StoredEvidenceItem, ...]
    audit_record: AuditRecord
    decision_event: StagedApplicationEvent
    audit_event: StagedApplicationEvent


@dataclass(frozen=True)
class _EventSpec:
    """The family-specific members needed to build one application event."""

    topic: Topic
    payload: dict[str, object]
    event_id: str
    sequence: int
    causation_id: str


def build_artifacts(
    proposal: AcceptedProposal,
    evaluation: Evaluation,
    stamp: DecisionStamp,
) -> DecisionArtifacts:
    """Build and self-validate one decision, event, and corresponding audit."""
    decision_payload = _decision_payload(proposal, evaluation, stamp)
    decision_topic = Topic(
        Family.EVIDENCE_DECISION,
        proposal.payload.mission_id,
        {"proposalId": proposal.payload.proposal_id},
    )
    decision_event = _event(
        proposal,
        stamp,
        _EventSpec(
            decision_topic,
            decision_payload,
            stamp.decision_event_id,
            stamp.decision_sequence,
            proposal.envelope.id,
        ),
    )
    audit_payload = _audit_payload(proposal, evaluation, stamp, decision_payload)
    audit_topic = Topic(
        Family.AUDIT,
        proposal.payload.mission_id,
        {"recordType": "evidence-decision"},
    )
    audit_event = _event(
        proposal,
        stamp,
        _EventSpec(
            audit_topic,
            audit_payload,
            stamp.audit_event_id,
            stamp.audit_sequence,
            stamp.decision_event_id,
        ),
    )
    return DecisionArtifacts(
        decision=_stored_decision(proposal, evaluation, stamp, decision_payload),
        items=evaluation.items,
        audit_record=_audit_record(proposal, stamp, audit_event),
        decision_event=_staged(decision_event, Family.EVIDENCE_DECISION),
        audit_event=_staged(audit_event, Family.AUDIT),
    )


def _decision_payload(
    proposal: AcceptedProposal,
    evaluation: Evaluation,
    stamp: DecisionStamp,
) -> dict[str, object]:
    """Return the closed branch selected by the evaluation."""
    payload: dict[str, object] = {
        "canonicalizationVersion": 1,
        "evidenceDecisionVersion": DECISION_VERSION,
        "missionId": proposal.payload.mission_id,
        "proposalId": proposal.payload.proposal_id,
        "proposalDigest": proposal.payload.proposal_digest,
        "proposalVersion": proposal.payload.proposal_version,
        "evidenceDecisionId": stamp.decision_id,
        "outcome": evaluation.outcome.value,
    }
    if evaluation.reason is not None:
        payload["reason"] = evaluation.reason.value
    else:
        payload.update(_contributing_members(evaluation))
    payload["evidenceDecisionDigest"] = evidence_decision_digest(payload)
    return payload


def _contributing_members(evaluation: Evaluation) -> dict[str, object]:
    """Return score and contributor members for the contributing branch."""
    return {
        "scoreVersion": evaluation.score_version,
        "score": evaluation.score,
        "band": evaluation.band.value if evaluation.band is not None else None,
        "contributors": [
            {
                "evidenceItemId": item.evidence_item_id,
                "sourceId": item.source_id,
                "origin": item.origin.value,
                "weight": item.weight,
                "provenanceDigest": item.provenance_digest,
            }
            for item in evaluation.contributors
        ],
    }


def _audit_payload(
    proposal: AcceptedProposal,
    evaluation: Evaluation,
    stamp: DecisionStamp,
    decision_payload: dict[str, object],
) -> dict[str, object]:
    """Return the redacted audit branch corresponding exactly to the decision."""
    payload: dict[str, object] = {
        "auditVersion": AUDIT_VERSION,
        "missionId": proposal.payload.mission_id,
        "recordId": stamp.audit_record_id,
        "recordType": "evidence-decision",
        "proposalId": proposal.payload.proposal_id,
        "proposalDigest": proposal.payload.proposal_digest,
        "proposalVersion": proposal.payload.proposal_version,
        "evidenceDecisionId": stamp.decision_id,
        "evidenceDecisionDigest": decision_payload["evidenceDecisionDigest"],
        "outcome": evaluation.outcome.value,
    }
    if evaluation.reason is not None:
        payload["reason"] = evaluation.reason.value
    return payload


def _event(
    proposal: AcceptedProposal,
    stamp: DecisionStamp,
    spec: _EventSpec,
) -> bytes:
    """Build canonical bytes and prove their envelope/topic binding."""
    rendered_sequence = sequence_text(spec.sequence)
    if rendered_sequence is None:
        raise PublicationError(PublicationRefusal.SEQUENCE)
    event_type = (
        "aerial-rescue.v1.evidence.decision"
        if spec.topic.family is Family.EVIDENCE_DECISION
        else "aerial-rescue.v1.audit.evidence-decision"
    )
    schema = (
        "evidence-decision.schema.json"
        if spec.topic.family is Family.EVIDENCE_DECISION
        else "audit.schema.json"
    )
    document = {
        "specversion": "1.0",
        "id": spec.event_id,
        "source": f"urn:aerial-rescue:evidence-service:{stamp.producer_id}",
        "type": event_type,
        "subject": proposal.payload.mission_id,
        "time": stamp.decided_at,
        "datacontenttype": "application/json",
        "dataschema": f"https://aerial-rescue.invalid/schemas/v1/payload/{schema}",
        "data": spec.payload,
        "sequence": rendered_sequence,
        "correlationid": proposal.envelope.correlation_id,
        "causationid": spec.causation_id,
        "traceparent": stamp.traceparent,
    }
    envelope = parse_envelope(document)
    check_topic_binding(envelope, spec.topic)
    return canonical.canonical_bytes(document)


def _staged(payload: bytes, family: Family) -> StagedApplicationEvent:
    """Map one validated event to the exact application-outbox row."""
    envelope = parse_envelope(canonical.decode(payload))
    topic = (
        Topic(family, envelope.subject, {"proposalId": str(envelope.data["proposalId"])})
        if family is Family.EVIDENCE_DECISION
        else Topic(family, envelope.subject, {"recordType": "evidence-decision"})
    )
    return StagedApplicationEvent(
        producer=PRODUCER,
        event_id=envelope.id,
        family="evidence-decision" if family is Family.EVIDENCE_DECISION else "audit",
        topic=format_topic(topic),
        headers=_EMPTY_HEADERS,
        payload=payload,
        traceparent=envelope.traceparent,
        tracestate=envelope.tracestate,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        staged_at=envelope.time,
    )


def _audit_record(
    proposal: AcceptedProposal,
    stamp: DecisionStamp,
    audit_event: bytes,
) -> AuditRecord:
    """Map the exact published audit event into the authoritative mission timeline.

    ADR-0205 fixes ``audit_record.kind`` as the committed envelope's own type, so it is read
    from the event rather than restated; a literal here cannot bind to its canonical envelope.
    """
    return AuditRecord(
        mission_id=proposal.payload.mission_id,
        kind=parse_envelope(canonical.decode(audit_event)).type,
        occurred_at=stamp.decided_at,
        payload=audit_event,
        correlation_id=proposal.envelope.correlation_id,
        causation_id=stamp.decision_event_id,
        traceparent=stamp.traceparent,
    )


def _stored_decision(
    proposal: AcceptedProposal,
    evaluation: Evaluation,
    stamp: DecisionStamp,
    payload: dict[str, object],
) -> StoredEvidenceDecision:
    """Map the exact decision payload to its append-only database row."""
    contributors = (
        canonical.canonical_bytes(payload["contributors"]) if "contributors" in payload else None
    )
    return StoredEvidenceDecision(
        decision_id=stamp.decision_id,
        mission_id=proposal.payload.mission_id,
        proposal_id=proposal.payload.proposal_id,
        proposal_digest=proposal.payload.proposal_digest,
        decision_digest=str(payload["evidenceDecisionDigest"]),
        decision_version=DECISION_VERSION,
        score_version=evaluation.score_version,
        score=evaluation.score,
        band=evaluation.band,
        outcome=evaluation.outcome,
        contributors=contributors,
        payload=canonical.canonical_bytes(payload),
        decided_at=stamp.decided_at,
        sequence=stamp.decision_sequence,
    )
