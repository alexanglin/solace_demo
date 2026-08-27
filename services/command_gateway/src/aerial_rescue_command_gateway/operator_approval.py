"""Verify dashboard-persisted operator approvals before settling their broker event.

The dashboard transaction is the approval writer.  This consumer does not recreate or mutate
that decision from broker bytes: it compares the event with a complete authoritative binding,
records the inbox outcome, commits, and only then accepts the guaranteed delivery.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import envelope_document
from aerial_rescue_contracts.instant import InstantError, format_instant, parse_instant
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    StoredApprovalAuthority,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity

from aerial_rescue_command_gateway.authorization import AuthorizationClock
from aerial_rescue_command_gateway.ingress import (
    IngressError,
    OperatorApprovalIngress,
    accept_ingress,
)
from aerial_rescue_command_gateway.ports import (
    ApprovalIngressTransaction,
    ApprovalIngressUnitOfWork,
    GuaranteedDelivery,
    SettlementPort,
    StoredApprovalBinding,
)
from aerial_rescue_command_gateway.refusal import reject_after_refusal

CONSUMER: Final = "command-gateway"
REFUSAL_CHANNEL: Final = "command-gateway-operator-approval"


class ApprovalIngressOutcome(Enum):
    """Whether persisted authority matches this event or the event was already handled."""

    VERIFIED = "verified"
    MISMATCH = "persisted-binding-mismatch"
    EXPIRED = "expired-before-gateway-binding"
    CLOCK_REGRESSION = "wall-clock-regressed-before-gateway-binding"
    EPOCH_MISMATCH = "approval-is-bound-to-another-gateway-epoch"
    DUPLICATE = "duplicate"


class ApprovalIngressRefusal(Enum):
    """Handler failures that cannot safely become ordinary verification results."""

    INGRESS_KIND = "ingress is not an operator approval"
    DUPLICATE_RESULT = "an exact duplicate has no durable result"


class ApprovalIngressError(ValueError):
    """A redacted approval-ingress failure which leaves broker input unsettled."""

    def __init__(self, refusal: ApprovalIngressRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class ApprovalIngressResult:
    """The exact durable verification response."""

    outcome: ApprovalIngressOutcome
    result: bytes


def _identity(ingress: OperatorApprovalIngress) -> InboxIdentity:
    """Bind one broker identity to the canonical event and dashboard producer source."""
    encoded = canonical.canonical_bytes(envelope_document(ingress.envelope))
    return InboxIdentity(
        consumer=CONSUMER,
        source=ingress.envelope.source,
        event_id=ingress.envelope.id,
        mission_id=ingress.payload.mission_id,
        canonical_digest=hashlib.sha256(encoded).hexdigest(),
    )


def _matches(ingress: OperatorApprovalIngress, stored: StoredApprovalBinding) -> bool:
    """Compare every broker-carried authority field with the persisted binding."""
    payload = ingress.payload
    return (
        ingress.envelope.source == f"urn:aerial-rescue:dashboard-api:{stored.decision_runtime_id}"
        and stored.approval_id == payload.approval_id
        and stored.mission_id == payload.mission_id
        and stored.operator_id == payload.operator_id
        and stored.decision == payload.decision
        and stored.issued_at == payload.issued_at
        and stored.expires_at == payload.expires_at
        and stored.proposal_id == payload.proposal_id
        and stored.proposal_digest == payload.proposal_digest
        and stored.proposal_version == payload.proposal_version
        and stored.evidence_decision_id == payload.evidence_decision_id
        and stored.evidence_decision_digest == payload.evidence_decision_digest
        and stored.evidence_decision_version == payload.evidence_decision_version
        and stored.action == payload.action
    )


def _authority(
    stored: StoredApprovalBinding,
    clock: AuthorizationClock,
) -> tuple[StoredApprovalAuthority | None, ApprovalIngressOutcome | None]:
    """Rebase the trusted issue wall instant into this gateway's monotonic origin."""
    try:
        issued_wall = parse_instant(stored.issued_at)
        current_wall = parse_instant(format_instant(clock.reading.wall))
    except InstantError:
        return None, ApprovalIngressOutcome.CLOCK_REGRESSION
    elapsed = current_wall - issued_wall
    if elapsed < timedelta(0):
        return None, ApprovalIngressOutcome.CLOCK_REGRESSION
    time_to_live = stored.time_to_live_milliseconds
    if type(time_to_live) is not int or time_to_live <= 0:
        return None, ApprovalIngressOutcome.MISMATCH
    elapsed_milliseconds = elapsed // timedelta(milliseconds=1)
    if elapsed_milliseconds >= time_to_live:
        return None, ApprovalIngressOutcome.EXPIRED
    monotonic = clock.reading.monotonic
    if monotonic < timedelta(0) or not clock.runtime_epoch:
        return None, ApprovalIngressOutcome.CLOCK_REGRESSION
    current_monotonic_milliseconds = monotonic // timedelta(milliseconds=1)
    return (
        StoredApprovalAuthority(
            clock.runtime_epoch,
            current_monotonic_milliseconds - elapsed_milliseconds,
        ),
        None,
    )


def _result(ingress: OperatorApprovalIngress, outcome: ApprovalIngressOutcome) -> bytes:
    """Return the canonical verification response stored by broker inbox."""
    return canonical.canonical_bytes(
        {
            "approvalIngress": outcome.value,
            "approvalId": ingress.payload.approval_id,
        }
    )


async def _verify_new_claim(
    ingress: OperatorApprovalIngress,
    clock: AuthorizationClock,
    transaction: ApprovalIngressTransaction,
) -> ApprovalIngressOutcome:
    """Compare a new event and conditionally bind this gateway's clock authority."""
    binding = await transaction.load_binding(ingress.payload.approval_id)
    if not _matches(ingress, binding):
        return ApprovalIngressOutcome.MISMATCH
    authority, refusal = _authority(binding, clock)
    if refusal is not None:
        return refusal
    if authority is None:
        return ApprovalIngressOutcome.MISMATCH
    bound = await transaction.bind_authority(ingress.payload.approval_id, authority)
    if bound is ApprovalAuthorityDecision.EPOCH_CONFLICT:
        return ApprovalIngressOutcome.EPOCH_MISMATCH
    return ApprovalIngressOutcome.VERIFIED


async def handle_operator_approval(
    delivery: GuaranteedDelivery,
    clock: AuthorizationClock,
    unit_of_work: ApprovalIngressUnitOfWork,
    settlement: SettlementPort,
) -> ApprovalIngressResult:
    """Verify, commit inbox state, and only then accept the guaranteed approval event."""
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
    if not isinstance(accepted, OperatorApprovalIngress):
        await reject_after_refusal(
            delivery,
            REFUSAL_CHANNEL,
            "unexpected-family",
            unit_of_work,
            settlement,
        )
        raise ApprovalIngressError(ApprovalIngressRefusal.INGRESS_KIND)
    identity = _identity(accepted)
    result: ApprovalIngressResult
    async with unit_of_work.begin() as transaction:
        claim = await transaction.claim(identity)
        if claim.decision is InboxDecision.DUPLICATE:
            if claim.result is None:
                raise ApprovalIngressError(ApprovalIngressRefusal.DUPLICATE_RESULT)
            result = ApprovalIngressResult(ApprovalIngressOutcome.DUPLICATE, claim.result)
        else:
            outcome = await _verify_new_claim(accepted, clock, transaction)
            durable_result = _result(accepted, outcome)
            await transaction.complete(identity, durable_result, accepted.envelope.time)
            result = ApprovalIngressResult(outcome, durable_result)
    await settlement.accept(accepted.envelope.id)
    return result
