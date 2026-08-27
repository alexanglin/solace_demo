"""Authorize operator commands atomically, then settle their guaranteed delivery.

ADR-0146's boundary is represented directly: inbox and idempotency claims, exact approval
consumption when required, append-only audit, command staging, and initial command progress all
commit together.  Broker acceptance happens only after that transaction successfully exits.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical, digest
from aerial_rescue_contracts.digest import (
    evidence_decision_digest_matches,
    proposal_digest_matches,
)
from aerial_rescue_contracts.envelope import envelope_document
from aerial_rescue_domain.approvals import (
    ApprovalError,
    ApprovalRefusal,
    ApprovalState,
    ClockReading,
    Proposal,
    consume,
)
from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_domain.scoring import EvidenceBand
from aerial_rescue_store.command_progress import CommandIdentity
from aerial_rescue_store.evidence import EvidenceDecisionOutcome, StoredEvidenceDecision
from aerial_rescue_store.idempotency import StoredClaim
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.outbox import StagedCommand
from aerial_rescue_store.proposals import StoredProposal

from aerial_rescue_command_gateway.command_artifacts import (
    AuthorizationArtifacts,
    AuthorizationStamp,
    build_authorization_artifacts,
)
from aerial_rescue_command_gateway.ingress import (
    AssignSectorAction,
    EscalateRescueAction,
    IngressError,
    OperatorCommandIngress,
    accept_ingress,
)
from aerial_rescue_command_gateway.ports import (
    AuthorizationTransaction,
    AuthorizationUnitOfWork,
    BoundApproval,
    GuaranteedDelivery,
    SettlementPort,
)
from aerial_rescue_command_gateway.refusal import reject_after_refusal

__all__ = [
    "AuthorizationClock",
    "AuthorizationOutcome",
    "AuthorizationResult",
    "AuthorizationStamp",
    "handle_operator_command",
]

CONSUMER: Final = "command-gateway"
REFUSAL_CHANNEL: Final = "command-gateway-operator-command"
_PROPOSAL_KEYS: Final = frozenset(
    {
        "canonicalizationVersion",
        "proposalVersion",
        "missionId",
        "proposalId",
        "proposalType",
        "agentName",
        "sourceInvocationId",
        "sourceEventId",
        "sourceEventDigest",
        "commandType",
        "droneId",
        "latitudeMicrodegrees",
        "longitudeMicrodegrees",
        "proposalDigest",
    }
)
_DECISION_KEYS: Final = frozenset(
    {
        "canonicalizationVersion",
        "evidenceDecisionVersion",
        "missionId",
        "proposalId",
        "proposalDigest",
        "proposalVersion",
        "evidenceDecisionId",
        "outcome",
        "scoreVersion",
        "score",
        "band",
        "contributors",
        "evidenceDecisionDigest",
    }
)


class AuthorizationOutcome(Enum):
    """Whether a command was newly authorized, safely refused, or already complete."""

    AUTHORIZED = "authorized"
    REFUSED = "refused"
    DUPLICATE = "duplicate"


class AuthorizationRefusal(Enum):
    """Redacted handler-level failures which cannot become an audit reason."""

    INGRESS_KIND = "ingress is not an operator command"
    DUPLICATE_RESULT = "an exact duplicate has no durable result"
    IDEMPOTENCY_RESULT = "a known command has no durable prior result"


class AuthorizationError(ValueError):
    """A redacted failure which leaves a guaranteed delivery unsettled."""

    def __init__(self, refusal: AuthorizationRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class AuthorizationClock:
    """The two approval clocks and process epoch read inside command handling."""

    reading: ClockReading
    runtime_epoch: str


@dataclass(frozen=True)
class AuthorizationResult:
    """The exact durable response associated with one operator command."""

    outcome: AuthorizationOutcome
    result: bytes
    reason: str | None = None


@dataclass(frozen=True)
class _Decision:
    """The pure authorization result before durable artifacts are built."""

    authorized: bool
    reason: str | None
    approval: BoundApproval | None


def _canonical_object(payload: bytes) -> dict[str, object] | None:
    """Return a canonical object without coercing malformed persisted bytes."""
    try:
        decoded = canonical.decode(payload)
    except ValueError:
        return None
    if not isinstance(decoded, Mapping):
        return None
    return {str(key): value for key, value in decoded.items() if isinstance(key, str)}


def _proposal_matches(
    stored: StoredProposal,
    action: EscalateRescueAction,
    payload: Mapping[str, object],
) -> bool:
    """Verify the immutable row, self digest, and requested exact action agree."""
    expected = (
        stored.mission_id,
        stored.proposal_id,
        stored.proposal_digest,
        stored.drone_id,
        stored.command_type,
        stored.latitude_microdegrees,
        stored.longitude_microdegrees,
    )
    received = (
        payload.get("missionId"),
        payload.get("proposalId"),
        payload.get("proposalDigest"),
        payload.get("droneId"),
        payload.get("commandType"),
        payload.get("latitudeMicrodegrees"),
        payload.get("longitudeMicrodegrees"),
    )
    row_consistent = (
        set(payload) == _PROPOSAL_KEYS
        and payload.get("canonicalizationVersion") == 1
        and payload.get("proposalVersion") == 1
        and payload.get("sourceEventId") == stored.source_event_id
        and payload.get("sourceEventDigest") == stored.source_event_digest
        and payload.get("agentName") == stored.agent_name
        and payload.get("sourceInvocationId") == stored.invocation_id
        and payload.get("proposalType") == stored.proposal_type
        and canonical.canonical_bytes(payload) == stored.payload
        and proposal_digest_matches(payload)
        and received == expected
    )
    action_consistent = expected[1:] == (
        action.proposal_id,
        action.proposal_digest,
        action.drone_id,
        action.command_type,
        action.latitude_microdegrees,
        action.longitude_microdegrees,
    )
    return row_consistent and action_consistent and action.proposal_version == 1


def _decision_matches(
    stored: StoredEvidenceDecision,
    action: EscalateRescueAction,
    payload: Mapping[str, object],
) -> bool:
    """Verify the selected evidence row is exactly one corroborated contributing decision."""
    return (
        set(payload) == _DECISION_KEYS
        and payload.get("canonicalizationVersion") == 1
        and payload.get("proposalVersion") == 1
        and canonical.canonical_bytes(payload) == stored.payload
        and evidence_decision_digest_matches(payload)
        and stored.mission_id == payload.get("missionId")
        and stored.proposal_id == payload.get("proposalId")
        and stored.proposal_digest == payload.get("proposalDigest")
        and stored.decision_id == payload.get("evidenceDecisionId")
        and stored.decision_digest == payload.get("evidenceDecisionDigest")
        and stored.decision_version == payload.get("evidenceDecisionVersion")
        and stored.score_version == payload.get("scoreVersion")
        and stored.score == payload.get("score")
        and stored.band is not None
        and stored.band.value == payload.get("band")
        and stored.outcome.value == payload.get("outcome")
        and stored.contributors is not None
        and canonical.decode(stored.contributors) == payload.get("contributors")
        and stored.outcome is EvidenceDecisionOutcome.CONTRIBUTING
        and stored.band is EvidenceBand.CORROBORATED
        and stored.proposal_id == action.proposal_id
        and stored.proposal_digest == action.proposal_digest
        and stored.decision_id == action.evidence_decision_id
        and stored.decision_digest == action.evidence_decision_digest
        and stored.decision_version == action.evidence_decision_version
    )


def _approval_reason(error: ApprovalError, state: ApprovalState) -> str:
    """Map the domain's fixed refusal table onto the closed audit contract."""
    if error.refusal is ApprovalRefusal.NOT_APPROVED:
        return "approval-rejected" if state is ApprovalState.REJECTED else "approval-missing"
    mapping = {
        ApprovalRefusal.TRANSITION: "approval-missing",
        ApprovalRefusal.TIME_TO_LIVE: "approval-missing",
        ApprovalRefusal.EXPIRED: "approval-expired",
        ApprovalRefusal.CLOCK_REGRESSION: "approval-expired",
        ApprovalRefusal.SUPERSEDED: "approval-superseded",
        ApprovalRefusal.ALREADY_CONSUMED: "approval-consumed",
        ApprovalRefusal.MISSION: "proposal-mismatch",
        ApprovalRefusal.PROPOSAL: "proposal-mismatch",
        ApprovalRefusal.DIGEST: "proposal-mismatch",
        ApprovalRefusal.PARAMETERS: "proposal-mismatch",
    }
    refusal = error.refusal
    if not isinstance(refusal, ApprovalRefusal):
        return "approval-missing"
    return mapping[refusal]


def _consume_bound_approval(
    bound: BoundApproval,
    stored: StoredProposal,
    proposal_payload: Mapping[str, object] | None,
    now: AuthorizationClock,
) -> tuple[BoundApproval | None, str | None]:
    """Consume only an approved record bound to this gateway's current clock epoch."""
    candidate = Proposal(
        stored.mission_id,
        stored.proposal_id,
        proposal_payload if proposal_payload is not None else {},
    )
    if bound.approval.state is ApprovalState.APPROVED and bound.runtime_epoch != now.runtime_epoch:
        return None, "approval-expired"
    try:
        executed = consume(bound.approval, candidate, now.reading)
    except ApprovalError as error:
        return None, _approval_reason(error, bound.approval.state)
    return replace(bound, approval=executed), None


async def _evidence_refusal(
    transaction: AuthorizationTransaction,
    action: EscalateRescueAction,
) -> str | None:
    """Return the closed refusal when the selected evidence decision is not exact."""
    stored = await transaction.load_decision(action.evidence_decision_id)
    payload = _canonical_object(stored.payload)
    if payload is None or not _decision_matches(stored, action, payload):
        return "evidence-decision-mismatch"
    return None


def _approval_binding_refusal(bound: BoundApproval, action: EscalateRescueAction) -> str | None:
    """Verify the operator decision binds the evidence and executable fields exactly."""
    evidence_mismatch = (
        bound.evidence_decision_id != action.evidence_decision_id
        or bound.evidence_decision_digest != action.evidence_decision_digest
        or bound.evidence_decision_version != action.evidence_decision_version
    )
    if evidence_mismatch:
        return "evidence-decision-mismatch"
    expected_action = {
        "commandType": action.command_type,
        "droneId": action.drone_id,
        "latitudeMicrodegrees": action.latitude_microdegrees,
        "longitudeMicrodegrees": action.longitude_microdegrees,
    }
    if bound.action.model_dump() != expected_action:
        return "action-mismatch"
    return None


async def _authorize_escalation(
    transaction: AuthorizationTransaction,
    ingress: OperatorCommandIngress,
    action: EscalateRescueAction,
    now: AuthorizationClock,
) -> _Decision:
    """Consume an exact live approval only after every authoritative binding agrees."""
    stored_proposal = await transaction.load_proposal(action.proposal_id)
    bound = await transaction.load_approval(action.proposal_id)
    proposal_payload = _canonical_object(stored_proposal.payload)
    consumed, reason = _consume_bound_approval(bound, stored_proposal, proposal_payload, now)
    if reason is not None:
        return _Decision(False, reason, None)
    if (
        ingress.payload.mission_id != stored_proposal.mission_id
        or proposal_payload is None
        or not _proposal_matches(stored_proposal, action, proposal_payload)
    ):
        return _Decision(False, "proposal-mismatch", None)
    reason = await _evidence_refusal(transaction, action)
    if reason is not None:
        return _Decision(False, reason, None)
    reason = _approval_binding_refusal(bound, action)
    if reason is not None:
        return _Decision(False, reason, None)
    return _Decision(True, None, consumed)


def _identity(ingress: OperatorCommandIngress) -> InboxIdentity:
    """Bind the inbox identity to canonical envelope bytes and its producer source."""
    document = envelope_document(ingress.envelope)
    canonical_bytes = canonical.canonical_bytes(document)
    return InboxIdentity(
        consumer=CONSUMER,
        source=ingress.envelope.source,
        event_id=ingress.envelope.id,
        mission_id=ingress.payload.mission_id,
        canonical_digest=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def _claim(ingress: OperatorCommandIngress, occurred_at: str) -> StoredClaim:
    """Return the command-idempotency claim for the exact canonical request body."""
    body = {
        "canonicalizationVersion": 1,
        "body": envelope_document(ingress.envelope),
    }
    return StoredClaim(
        idempotency_key=ingress.payload.command_id,
        kind=IdempotencyKind.COMMAND,
        body_digest=digest.digest(digest.Context.IDEMPOTENCY_BODY, body),
        mission_id=ingress.payload.mission_id,
        claimed_at=occurred_at,
    )


def _duplicate_inbox_result(inbox: InboxOutcome) -> AuthorizationResult:
    """Return an exact completed inbox result or reject impossible durable state."""
    if inbox.result is None:
        raise AuthorizationError(AuthorizationRefusal.DUPLICATE_RESULT)
    return AuthorizationResult(AuthorizationOutcome.DUPLICATE, inbox.result)


def _command_effects(
    artifacts: AuthorizationArtifacts,
) -> tuple[StagedCommand, CommandIdentity] | None:
    """Return one complete command pair or fail closed on an internal partial result."""
    command = artifacts.command
    progress = artifacts.progress
    if command is None and progress is None:
        return None
    if command is None or progress is None:
        message = "authorization command artifacts are incomplete"
        raise RuntimeError(message)
    return command, progress


async def _persist_decision(
    transaction: AuthorizationTransaction,
    ingress: OperatorCommandIngress,
    stamp: AuthorizationStamp,
    decision: _Decision,
) -> AuthorizationResult:
    """Persist the complete ADR-0146 effect set for one new authorization decision."""
    approval_id = decision.approval.approval_id if decision.approval is not None else None
    artifacts = build_authorization_artifacts(
        ingress,
        stamp,
        authorized=decision.authorized,
        approval_id=approval_id,
        reason=decision.reason,
    )
    if decision.approval is not None:
        await transaction.persist_consumed(decision.approval)
    await transaction.append_audit(artifacts.audit_record)
    await transaction.stage_application(artifacts.audit_event)
    command_effects = _command_effects(artifacts)
    if command_effects is not None:
        command, progress = command_effects
        await transaction.stage_command(command)
        await transaction.initialize_progress(progress, stamp.occurred_at)
    await transaction.record_result(ingress.payload.command_id, artifacts.result)
    await transaction.complete(_identity(ingress), artifacts.result, stamp.occurred_at)
    outcome = (
        AuthorizationOutcome.AUTHORIZED if decision.authorized else AuthorizationOutcome.REFUSED
    )
    return AuthorizationResult(outcome, artifacts.result, decision.reason)


async def _process_claimed(
    transaction: AuthorizationTransaction,
    ingress: OperatorCommandIngress,
    stamp: AuthorizationStamp,
    now: AuthorizationClock,
) -> AuthorizationResult:
    """Claim command idempotency and execute or replay the protected operation."""
    claimed = await transaction.claim_command(_claim(ingress, stamp.occurred_at))
    if claimed.decision is IdempotencyDecision.RETURN_PRIOR_RESULT:
        if claimed.result is None:
            raise AuthorizationError(AuthorizationRefusal.IDEMPOTENCY_RESULT)
        await transaction.complete(_identity(ingress), claimed.result, stamp.occurred_at)
        return AuthorizationResult(AuthorizationOutcome.DUPLICATE, claimed.result)
    if claimed.decision is IdempotencyDecision.DENY:
        decision = _Decision(False, "idempotency-conflict", None)
    elif isinstance(ingress.payload.action, AssignSectorAction):
        decision = _Decision(True, None, None)
    else:
        decision = await _authorize_escalation(
            transaction,
            ingress,
            ingress.payload.action,
            now,
        )
    return await _persist_decision(transaction, ingress, stamp, decision)


async def handle_operator_command(
    delivery: GuaranteedDelivery,
    stamp: AuthorizationStamp,
    now: AuthorizationClock,
    unit_of_work: AuthorizationUnitOfWork,
    settlement: SettlementPort,
) -> AuthorizationResult:
    """Commit every authorization effect, then accept the guaranteed message."""
    try:
        accepted = accept_ingress(delivery.payload, delivery.topic)
    except IngressError as error:
        await reject_after_refusal(
            delivery,
            REFUSAL_CHANNEL,
            error.refusal.name.lower().replace("_", "-"),
            unit_of_work,
            settlement,
        )
        raise
    if not isinstance(accepted, OperatorCommandIngress):
        await reject_after_refusal(
            delivery,
            REFUSAL_CHANNEL,
            "unexpected-family",
            unit_of_work,
            settlement,
        )
        raise AuthorizationError(AuthorizationRefusal.INGRESS_KIND)
    identity = _identity(accepted)
    result: AuthorizationResult
    async with unit_of_work.begin() as transaction:
        inbox = await transaction.claim(identity)
        if inbox.decision is InboxDecision.DUPLICATE:
            result = _duplicate_inbox_result(inbox)
        else:
            result = await _process_claimed(transaction, accepted, stamp, now)
    await settlement.accept(accepted.envelope.id)
    return result
