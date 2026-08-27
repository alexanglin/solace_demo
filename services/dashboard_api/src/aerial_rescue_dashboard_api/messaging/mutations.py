"""Durable broker-backed dashboard command and proposal-decision mutations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Protocol, cast

from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_contracts import canonical, digest
from aerial_rescue_contracts.envelope import (
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.instant import format_instant, parse_instant
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.approval_bindings import StoredApprovalBinding
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.evidence import EvidenceDecisionOutcome, StoredEvidenceDecision
from aerial_rescue_store.idempotency import ClaimOutcome, StoredClaim
from aerial_rescue_store.proposals import StoredProposal

from aerial_rescue_dashboard_api.boundary.mutation_boundary import AuthorizedMutation

_EMPTY_HEADERS = canonical.canonical_bytes({})
_CANONICALIZATION_VERSION = 1
_MINIMUM_LIVE_SOURCES = 2


class MutationRefusal(Enum):
    """Why an admitted mutation cannot become a durable operator event."""

    REQUEST = "the admitted mutation has no closed object document"
    DUPLICATE_RESULT = "the durable repeat has no prior response"
    AUTHORITY_MISMATCH = "the proposal or evidence binding differs from durable authority"
    APPROVAL_INELIGIBLE = "the selected evidence cannot authorize rescue escalation"
    SEQUENCE = "the dashboard producer sequence is outside the envelope profile"


class DashboardMutationError(ValueError):
    """A typed fail-closed mutation refusal carrying no request or authority bytes."""

    def __init__(self, refusal: MutationRefusal) -> None:
        """Retain only the closed refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class MutationStamp:
    """Trusted identities, clocks, and sequence for one new operator event."""

    event_id: str
    entity_id: str
    occurred_at: str
    monotonic_milliseconds: int
    sequence: int
    traceparent: str


class MutationTransaction(Protocol):
    """The exact atomic store capabilities used by one public mutation."""

    async def claim(self, request: StoredClaim) -> ClaimOutcome:
        """Claim one route-specific idempotency identity."""

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Load one immutable proposal authority record."""

    async def load_evidence_decision(self, decision_id: str) -> StoredEvidenceDecision:
        """Load one immutable evidence-decision authority record."""

    async def load_evidence_decisions(self, proposal_id: str) -> tuple[StoredEvidenceDecision, ...]:
        """Load the ordered decision history needed to reject stale selections."""

    async def record_decision(
        self,
        approval: StoredApproval,
        binding: StoredApprovalBinding,
    ) -> None:
        """Persist the approval lifecycle and exact immutable binding."""

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact broker event in the application outbox."""

    async def record_result(self, idempotency_key: str, result: bytes) -> None:
        """Persist the exact canonical response for safe repeats."""


class MutationTransactions(Protocol):
    """Factory for fresh commit-or-rollback dashboard mutation transactions."""

    def open(self) -> AbstractAsyncContextManager[MutationTransaction]:
        """Return one atomic transaction."""


class DashboardMutationService:
    """Validate authority and atomically stage the dashboard's two event families."""

    def __init__(
        self,
        *,
        transactions: MutationTransactions,
        runtime_id: str,
        stamps: Callable[[], MutationStamp],
        schemas: PayloadSchemaExecutor,
        approval_time_to_live_milliseconds: int,
    ) -> None:
        """Bind explicit store, identity, schema, and approval-time dependencies."""
        if approval_time_to_live_milliseconds <= 0:
            raise DashboardMutationError(MutationRefusal.REQUEST)
        self._transactions = transactions
        self._runtime_id = runtime_id
        self._stamps = stamps
        self._schemas = schemas
        self._approval_time_to_live_milliseconds = approval_time_to_live_milliseconds

    @property
    def schemas(self) -> PayloadSchemaExecutor:
        """Expose the same registry to focused boundary tests and runtime recovery."""
        return self._schemas

    async def command(self, mutation: AuthorizedMutation) -> bytes:
        """Claim, construct, stage, and answer one operator command atomically."""
        document = _mutation_document(mutation)
        mission_id = _text(document, "missionId")
        action = _mapping(document, "action")
        command_type = _text(action, "commandType")
        stamp = self._stamps()
        async with self._transactions.open() as transaction:
            prior = await _claim_or_prior(
                transaction,
                mutation,
                IdempotencyKind.DASHBOARD_COMMAND,
                mission_id,
                stamp.occurred_at,
            )
            if prior is not None:
                return prior
            payload: dict[str, object] = {
                "operatorCommandVersion": 1,
                "missionId": mission_id,
                "commandId": stamp.entity_id,
                "operatorId": mutation.operator_id,
                "action": dict(action),
            }
            topic = Topic(
                Family.OPERATOR_COMMAND,
                mission_id,
                {"commandType": command_type},
            )
            staged = self._event(topic, payload, stamp, correlation_id=stamp.entity_id)
            response = canonical.canonical_bytes(
                {
                    "operationVersion": "dashboard-command-response/v1",
                    "missionId": mission_id,
                    "commandId": stamp.entity_id,
                    "eventId": stamp.event_id,
                }
            )
            await transaction.stage(staged)
            await transaction.record_result(mutation.ingress.idempotency_key, response)
            return response

    async def decide(self, mutation: AuthorizedMutation) -> bytes:
        """Rebind one proposal decision and persist its approval event atomically."""
        document = _mutation_document(mutation)
        mission_id = _text(document, "missionId")
        proposal_id = _text(document, "proposalId")
        evidence_decision_id = _text(document, "evidenceDecisionId")
        decision = _text(document, "decision")
        action = _mapping(document, "action")
        stamp = self._stamps()
        async with self._transactions.open() as transaction:
            prior = await _claim_or_prior(
                transaction,
                mutation,
                IdempotencyKind.DASHBOARD_DECISION,
                mission_id,
                stamp.occurred_at,
            )
            if prior is not None:
                return prior
            _proposal, evidence = await _validated_authority(
                transaction,
                document,
                proposal_id,
                evidence_decision_id,
            )
            if decision == "approve" and not _eligible(evidence):
                raise DashboardMutationError(MutationRefusal.APPROVAL_INELIGIBLE)
            expires_at = _expires_at(
                stamp.occurred_at,
                self._approval_time_to_live_milliseconds,
            )
            payload = _approval_payload(
                document,
                mutation.operator_id,
                stamp,
                expires_at if decision == "approve" else None,
            )
            topic = Topic(Family.OPERATOR_APPROVAL, mission_id, {"decision": decision})
            staged = self._event(topic, payload, stamp, correlation_id=proposal_id)
            approval = StoredApproval(
                mission_id=mission_id,
                proposal_id=proposal_id,
                state=(ApprovalState.APPROVED if decision == "approve" else ApprovalState.REJECTED),
                operator_identity=mutation.operator_id,
                issued_wall=stamp.occurred_at,
                issued_monotonic_milliseconds=stamp.monotonic_milliseconds,
                time_to_live_milliseconds=self._approval_time_to_live_milliseconds,
                proposal_digest=_text(document, "proposalDigest"),
            )
            binding = StoredApprovalBinding(
                approval_id=stamp.entity_id,
                proposal_id=proposal_id,
                proposal_version=_integer(document, "proposalVersion"),
                evidence_decision_id=evidence_decision_id,
                evidence_decision_digest=_text(document, "evidenceDecisionDigest"),
                evidence_decision_version=_integer(document, "evidenceDecisionVersion"),
                decision=decision,
                action_payload=canonical.canonical_bytes(action),
                decision_runtime_id=self._runtime_id,
                authority_runtime_epoch=None,
                authority_issued_monotonic_milliseconds=None,
                expires_at=expires_at if decision == "approve" else None,
            )
            response_document: dict[str, object] = {
                "operationVersion": "dashboard-proposal-decision-response/v1",
                "missionId": mission_id,
                "proposalId": proposal_id,
                "approvalId": stamp.entity_id,
                "eventId": stamp.event_id,
                "decision": decision,
                "issuedAt": stamp.occurred_at,
            }
            if decision == "approve":
                response_document["expiresAt"] = expires_at
            response = canonical.canonical_bytes(response_document)
            await transaction.record_decision(approval, binding)
            await transaction.stage(staged)
            await transaction.record_result(mutation.ingress.idempotency_key, response)
            return response

    def _event(
        self,
        topic: Topic,
        payload: dict[str, object],
        stamp: MutationStamp,
        *,
        correlation_id: str,
    ) -> StagedApplicationEvent:
        """Build and revalidate one exact outbound CloudEvent and topic binding."""
        sequence = sequence_text(stamp.sequence)
        if sequence is None:
            raise DashboardMutationError(MutationRefusal.SEQUENCE)
        kind = event_type(topic)
        binding = binding_for(kind)
        self._schemas.validate(binding.dataschema, payload)
        document: dict[str, object] = {
            "specversion": "1.0",
            "id": stamp.event_id,
            "source": f"urn:aerial-rescue:dashboard-api:{self._runtime_id}",
            "type": kind,
            "subject": topic.mission_id,
            "time": stamp.occurred_at,
            "datacontenttype": "application/json",
            "dataschema": binding.dataschema,
            "data": payload,
            "sequence": sequence,
            "correlationid": correlation_id,
            "traceparent": stamp.traceparent,
        }
        envelope = parse_envelope(document)
        check_topic_binding(envelope, topic)
        encoded = canonical.canonical_bytes(envelope_document(envelope))
        return StagedApplicationEvent(
            producer="dashboard-api",
            event_id=stamp.event_id,
            family=topic.family.name.lower().replace("_", "-"),
            topic=format_topic(topic),
            headers=_EMPTY_HEADERS,
            payload=encoded,
            traceparent=stamp.traceparent,
            tracestate=None,
            correlation_id=correlation_id,
            causation_id=None,
            staged_at=stamp.occurred_at,
        )


async def _validated_authority(
    transaction: MutationTransaction,
    document: Mapping[str, object],
    proposal_id: str,
    evidence_decision_id: str,
) -> tuple[StoredProposal, StoredEvidenceDecision]:
    """Load and rebind the currently authoritative proposal decision atomically."""
    proposal = await transaction.load_proposal(proposal_id)
    evidence = await transaction.load_evidence_decision(evidence_decision_id)
    evidence_history = await transaction.load_evidence_decisions(proposal_id)
    if not evidence_history or evidence_history[-1] != evidence:
        raise DashboardMutationError(MutationRefusal.AUTHORITY_MISMATCH)
    _rebind_authority(document, proposal, evidence)
    return proposal, evidence


async def _claim_or_prior(
    transaction: MutationTransaction,
    mutation: AuthorizedMutation,
    kind: IdempotencyKind,
    mission_id: str,
    claimed_at: str,
) -> bytes | None:
    """Claim the exact canonical HTTP body or return its committed prior response."""
    body = {
        "canonicalizationVersion": _CANONICALIZATION_VERSION,
        "body": canonical.decode(mutation.ingress.canonical_body),
    }
    outcome = await transaction.claim(
        StoredClaim(
            idempotency_key=mutation.ingress.idempotency_key,
            kind=kind,
            body_digest=digest.digest(digest.Context.IDEMPOTENCY_BODY, body),
            mission_id=mission_id,
            claimed_at=claimed_at,
        )
    )
    if outcome.decision is IdempotencyDecision.EXECUTE:
        return None
    if outcome.decision is IdempotencyDecision.RETURN_PRIOR_RESULT and outcome.result is not None:
        return outcome.result
    raise DashboardMutationError(MutationRefusal.DUPLICATE_RESULT)


def _rebind_authority(
    request: Mapping[str, object],
    proposal: StoredProposal,
    evidence: StoredEvidenceDecision,
) -> None:
    """Constant-time bind every requested action member to canonical durable authority."""
    proposal_payload = _canonical_mapping(proposal.payload)
    evidence_payload = _canonical_mapping(evidence.payload)
    action = _mapping(request, "action")
    requested_proposal_digest = _text(request, "proposalDigest")
    requested_evidence_digest = _text(request, "evidenceDecisionDigest")
    proposal_digest = digest.proposal_digest(proposal_payload)
    evidence_digest = digest.evidence_decision_digest(evidence_payload)
    proposal_matches = (
        proposal.mission_id == _text(request, "missionId")
        and proposal.proposal_id == _text(request, "proposalId")
        and proposal.proposal_id == _text(proposal_payload, "proposalId")
        and proposal.proposal_digest == _text(proposal_payload, "proposalDigest")
        and digest.matches(requested_proposal_digest, proposal.proposal_digest)
        and digest.matches(proposal_digest, proposal.proposal_digest)
        and _integer(request, "proposalVersion") == _integer(proposal_payload, "proposalVersion")
        and proposal.command_type == _text(action, "commandType")
        and proposal.drone_id == _text(action, "droneId")
        and proposal.latitude_microdegrees == _integer(action, "latitudeMicrodegrees")
        and proposal.longitude_microdegrees == _integer(action, "longitudeMicrodegrees")
    )
    evidence_matches = (
        evidence.mission_id == proposal.mission_id
        and evidence.proposal_id == proposal.proposal_id
        and evidence.proposal_digest == proposal.proposal_digest
        and evidence.decision_id == _text(request, "evidenceDecisionId")
        and evidence.decision_id == _text(evidence_payload, "evidenceDecisionId")
        and evidence.decision_version == _integer(request, "evidenceDecisionVersion")
        and evidence.decision_version == _integer(evidence_payload, "evidenceDecisionVersion")
        and evidence.decision_digest == _text(evidence_payload, "evidenceDecisionDigest")
        and digest.matches(requested_evidence_digest, evidence.decision_digest)
        and digest.matches(evidence_digest, evidence.decision_digest)
    )
    if not proposal_matches or not evidence_matches:
        raise DashboardMutationError(MutationRefusal.AUTHORITY_MISMATCH)


def _eligible(evidence: StoredEvidenceDecision) -> bool:
    """Require corroborated contributing evidence from two distinct live sources."""
    if (
        evidence.outcome is not EvidenceDecisionOutcome.CONTRIBUTING
        or evidence.band is None
        or evidence.band.value != "corroborated"
    ):
        return False
    payload = _canonical_mapping(evidence.payload)
    contributors = payload.get("contributors")
    if not isinstance(contributors, list):
        return False
    sources: set[str] = set()
    for item in contributors:
        if not isinstance(item, Mapping):
            return False
        source = item.get("sourceId")
        origin = item.get("origin")
        if not isinstance(source, str) or origin not in {"live-model", "live-sensor"}:
            return False
        sources.add(source)
    return len(sources) >= _MINIMUM_LIVE_SOURCES


def _approval_payload(
    request: Mapping[str, object],
    operator_id: str,
    stamp: MutationStamp,
    expires_at: str | None,
) -> dict[str, object]:
    """Build the exact approval or rejection payload from the rebound request."""
    payload: dict[str, object] = {
        "operatorApprovalVersion": 1,
        "missionId": _text(request, "missionId"),
        "approvalId": stamp.entity_id,
        "operatorId": operator_id,
        "decision": _text(request, "decision"),
        "issuedAt": stamp.occurred_at,
        "proposalId": _text(request, "proposalId"),
        "proposalDigest": _text(request, "proposalDigest"),
        "proposalVersion": _integer(request, "proposalVersion"),
        "evidenceDecisionId": _text(request, "evidenceDecisionId"),
        "evidenceDecisionDigest": _text(request, "evidenceDecisionDigest"),
        "evidenceDecisionVersion": _integer(request, "evidenceDecisionVersion"),
        "action": dict(_mapping(request, "action")),
    }
    if expires_at is not None:
        payload["expiresAt"] = expires_at
    return payload


def _expires_at(issued_at: str, time_to_live_milliseconds: int) -> str:
    """Return the canonical approval expiry from the accepted injected duration."""
    return format_instant(
        parse_instant(issued_at) + timedelta(milliseconds=time_to_live_milliseconds)
    )


def _mutation_document(mutation: AuthorizedMutation) -> Mapping[str, object]:
    value = mutation.ingress.document.model_dump(mode="python", by_alias=True)
    if not isinstance(value, Mapping):
        raise DashboardMutationError(MutationRefusal.REQUEST)
    return cast("Mapping[str, object]", value)


def _canonical_mapping(payload: bytes) -> Mapping[str, object]:
    try:
        value = canonical.decode(payload)
        if canonical.canonical_bytes(value) != payload or not isinstance(value, Mapping):
            raise DashboardMutationError(MutationRefusal.AUTHORITY_MISMATCH)
    except (TypeError, ValueError) as error:
        raise DashboardMutationError(MutationRefusal.AUTHORITY_MISMATCH) from error
    return cast("Mapping[str, object]", value)


def _mapping(document: Mapping[str, object], member: str) -> Mapping[str, object]:
    value = document.get(member)
    if not isinstance(value, Mapping):
        raise DashboardMutationError(MutationRefusal.REQUEST)
    return cast("Mapping[str, object]", value)


def _text(document: Mapping[str, object], member: str) -> str:
    value = document.get(member)
    if not isinstance(value, str):
        raise DashboardMutationError(MutationRefusal.REQUEST)
    return value


def _integer(document: Mapping[str, object], member: str) -> int:
    value = document.get(member)
    if type(value) is not int:
        raise DashboardMutationError(MutationRefusal.REQUEST)
    return value
