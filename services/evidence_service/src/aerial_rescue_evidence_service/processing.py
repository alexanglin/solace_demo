"""Commit evidence decisions and outbox publications before broker settlement."""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity
from aerial_rescue_store.proposals import StoredProposal

from aerial_rescue_evidence_service.evaluation import (
    EvidenceRejectionReason,
    evaluate,
    refused,
)
from aerial_rescue_evidence_service.ports import (
    DecisionStamp,
    EvidenceTransaction,
    EvidenceUnitOfWork,
    InboundDelivery,
    SettlementPort,
)
from aerial_rescue_evidence_service.publication import DecisionArtifacts, build_artifacts
from aerial_rescue_evidence_service.source import (
    ProvenanceError,
    validate_source,
    with_model_provenance,
)
from aerial_rescue_evidence_service.wire import AcceptedProposal, IngressError, accept_proposal

CONSUMER: Final = "evidence-service"
REFUSAL_CHANNEL: Final = "evidence-service-agent-proposal"
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class ProcessingOutcome(Enum):
    """Whether processing committed new effects or reused the prior exact result."""

    COMMITTED = "committed"
    DUPLICATE = "duplicate"


class ProcessingRefusal(Enum):
    """Why a proposal cannot enter or complete the durable transaction."""

    CANONICAL_DIGEST = "broker canonical digest is malformed"
    PROPOSAL_MISMATCH = "broker proposal differs from the authoritative immutable proposal"
    DUPLICATE_RESULT = "exact duplicate has no durable result"


class ProcessingError(ValueError):
    """A redacted durable-processing refusal."""

    def __init__(self, refusal: ProcessingRefusal) -> None:
        """Expose the closed reason and no untrusted bytes."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class ProcessingResult:
    """The exact durable result associated with the proposal inbox identity."""

    outcome: ProcessingOutcome
    result: bytes


async def handle_delivery(
    delivery: InboundDelivery,
    stamp: DecisionStamp,
    unit_of_work: EvidenceUnitOfWork,
    settlement: SettlementPort,
) -> ProcessingResult:
    """Process one guaranteed proposal, then settle only after transaction exit."""
    try:
        proposal = accept_proposal(delivery.payload, delivery.topic)
    except IngressError as error:
        await _reject(
            delivery,
            error.refusal.name.lower().replace("_", "-"),
            unit_of_work,
            settlement,
        )
        raise
    if _DIGEST_PATTERN.fullmatch(delivery.canonical_digest) is None:
        await _reject(
            delivery,
            ProcessingRefusal.CANONICAL_DIGEST.name.lower().replace("_", "-"),
            unit_of_work,
            settlement,
        )
        raise ProcessingError(ProcessingRefusal.CANONICAL_DIGEST)
    identity = InboxIdentity(
        consumer=CONSUMER,
        source=proposal.envelope.source,
        event_id=proposal.envelope.id,
        mission_id=proposal.payload.mission_id,
        canonical_digest=delivery.canonical_digest,
    )
    async with unit_of_work.begin() as transaction:
        result = await _inside_transaction(transaction, identity, proposal, stamp)
    await settlement.accept(proposal.envelope.id)
    return result


async def _reject(
    delivery: InboundDelivery,
    refusal_code: str,
    unit_of_work: EvidenceUnitOfWork,
    settlement: SettlementPort,
) -> None:
    """Commit body-free evidence, then reject only the message bound to settlement."""
    family: str | None = None
    source: str | None = None
    with suppress(ValueError):
        family = parse_topic(delivery.topic).family.literal_suffix
    with suppress(ValueError):
        source = decode_envelope(delivery.payload).source
    fact = BrokerRefusalCandidate(
        consumer=CONSUMER,
        source=source,
        family=family,
        channel=REFUSAL_CHANNEL,
        refusal_code=refusal_code,
        raw_digest=hashlib.sha256(delivery.payload).hexdigest(),
    )
    await unit_of_work.refuse(fact)
    await settlement.reject()


async def _inside_transaction(
    transaction: EvidenceTransaction,
    identity: InboxIdentity,
    proposal: AcceptedProposal,
    stamp: DecisionStamp,
) -> ProcessingResult:
    """Apply every durable effect within the caller-owned transaction."""
    claim = await transaction.claim(identity)
    if claim.decision is InboxDecision.DUPLICATE:
        if claim.result is None:
            raise ProcessingError(ProcessingRefusal.DUPLICATE_RESULT)
        return ProcessingResult(ProcessingOutcome.DUPLICATE, claim.result)
    stored = await transaction.load_proposal(proposal.payload.proposal_id)
    if stored != _stored_proposal(proposal):
        raise ProcessingError(ProcessingRefusal.PROPOSAL_MISMATCH)
    source = await transaction.source_for(
        proposal.payload.mission_id,
        proposal.payload.source_event_id,
    )
    try:
        evaluation = evaluate(
            proposal.payload.mission_id,
            proposal.payload.proposal_id,
            with_model_provenance(proposal, validate_source(proposal, source)),
        )
    except ProvenanceError as error:
        evaluation = refused(EvidenceRejectionReason(error.refusal.value))
    artifacts = build_artifacts(proposal, evaluation, stamp)
    await _persist(transaction, artifacts, identity)
    return ProcessingResult(ProcessingOutcome.COMMITTED, artifacts.decision_event.payload)


async def _persist(
    transaction: EvidenceTransaction,
    artifacts: DecisionArtifacts,
    identity: InboxIdentity,
) -> None:
    """Persist items, decision, publications, and inbox completion in order."""
    for item in artifacts.items:
        await transaction.record_item(item)
    await transaction.record_decision(artifacts.decision)
    await transaction.stage(artifacts.decision_event)
    await transaction.stage(artifacts.audit_event)
    await transaction.complete(
        identity,
        artifacts.decision_event.payload,
        artifacts.decision.decided_at,
    )


def _stored_proposal(proposal: AcceptedProposal) -> StoredProposal:
    """Reconstruct the exact authoritative row from an accepted proposal event."""
    payload = proposal.payload
    envelope = proposal.envelope
    return StoredProposal(
        proposal_id=payload.proposal_id,
        mission_id=payload.mission_id,
        source_event_id=payload.source_event_id,
        source_event_digest=payload.source_event_digest,
        agent_name=payload.agent_name,
        invocation_id=payload.source_invocation_id,
        proposal_type=payload.proposal_type,
        proposal_digest=payload.proposal_digest,
        payload=canonical.canonical_bytes(envelope.data),
        drone_id=payload.drone_id,
        latitude_microdegrees=payload.latitude_microdegrees,
        longitude_microdegrees=payload.longitude_microdegrees,
        command_type=payload.command_type,
        issued_at=envelope.time,
        sequence=int(envelope.sequence),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        traceparent=envelope.traceparent,
    )
