"""The approval protocol: proposal-bound, single-use, expiring, consumed only once.

The states and the binding are ``docs/adr/0006-proposal-bound-single-use-approvals.md``;
how consumption proves the binding and reads time is
``docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md``. The proposal
digest is recomputed through ``aerial_rescue_contracts.digest`` over the parameters the
gateway is about to publish, so an altered parameter, a changed score version, or a claim of
approval inside the parameters is a digest mismatch rather than a trusted string. A clock
reading carries a wall instant and a monotonic duration supplied by the caller, so a wall
clock moved backwards cannot revive an approval. This module is pure: it reads no clock and
performs no input or output; the time to live is injected and has no default.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Final

from aerial_rescue_contracts import digest
from aerial_rescue_contracts.canonical import CanonicalizationError

from aerial_rescue_domain import DomainError


class ApprovalState(Enum):
    """Where a proposal stands in the approval protocol."""

    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    EXECUTED = "executed"


class ApprovalEvent(Enum):
    """What moves a proposal between states."""

    APPROVE = "approve"
    REJECT = "reject"
    EXPIRE = "expire"
    SUPERSEDE = "supersede"
    EXECUTE = "execute"


class ApprovalRefusal(Enum):
    """Why the protocol refuses an operation."""

    TRANSITION = "the approval protocol has no such transition"
    TIME_TO_LIVE = "approval time to live must be positive"
    PARAMETERS = "action parameters cannot be digested"
    NOT_APPROVED = "proposal is not approved; only an approved proposal may be executed"
    ALREADY_CONSUMED = (
        "approval already consumed; a repeat is a hard denial, never an idempotent success"
    )
    SUPERSEDED = "proposal was superseded; a superseded proposal is not approvable"
    EXPIRED = "approval expired before consumption"
    MISSION = "approval binds another mission"
    PROPOSAL = "approval binds another proposal"
    DIGEST = "proposal digest does not match the approved digest"
    CLOCK_REGRESSION = "a clock reads earlier than it did when the approval was issued"


class ApprovalError(DomainError):
    """A refused approval operation, carrying the refusal as structured data."""


@dataclass(frozen=True)
class ClockReading:
    """Both clocks read at one moment by the caller: an aware UTC instant and elapsed time."""

    wall: datetime
    monotonic: timedelta


@dataclass(frozen=True)
class Proposal:
    """What an agent proposed: its mission, its identity, and the digest-covered parameters."""

    mission_id: str
    proposal_id: str
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class Approval:
    """The immutable decision record; only :func:`approve` creates one."""

    state: ApprovalState
    operator_identity: str
    issued: ClockReading
    time_to_live: timedelta
    mission_id: str
    proposal_id: str
    proposal_digest: str

    @property
    def expires_at(self) -> datetime:
        """Return the wall instant shown to the operator and written to the audit trail."""
        return self.issued.wall + self.time_to_live


_TRANSITIONS: Final[Mapping[tuple[ApprovalState, ApprovalEvent], ApprovalState]] = {
    (ApprovalState.REQUESTED, ApprovalEvent.APPROVE): ApprovalState.APPROVED,
    (ApprovalState.REQUESTED, ApprovalEvent.REJECT): ApprovalState.REJECTED,
    (ApprovalState.REQUESTED, ApprovalEvent.EXPIRE): ApprovalState.EXPIRED,
    (ApprovalState.REQUESTED, ApprovalEvent.SUPERSEDE): ApprovalState.SUPERSEDED,
    (ApprovalState.APPROVED, ApprovalEvent.EXECUTE): ApprovalState.EXECUTED,
    (ApprovalState.APPROVED, ApprovalEvent.EXPIRE): ApprovalState.EXPIRED,
    (ApprovalState.APPROVED, ApprovalEvent.SUPERSEDE): ApprovalState.SUPERSEDED,
}
"""The seven legal pairs; every other pair is refused, and EXECUTED follows only APPROVED."""

_CONSUME_REFUSALS: Final[Mapping[ApprovalState, ApprovalRefusal]] = {
    ApprovalState.REQUESTED: ApprovalRefusal.NOT_APPROVED,
    ApprovalState.REJECTED: ApprovalRefusal.NOT_APPROVED,
    ApprovalState.EXPIRED: ApprovalRefusal.EXPIRED,
    ApprovalState.SUPERSEDED: ApprovalRefusal.SUPERSEDED,
    ApprovalState.EXECUTED: ApprovalRefusal.ALREADY_CONSUMED,
}
"""Why a record in each non-approved state cannot be consumed."""

_NO_DURATION: Final = timedelta(0)


def transition(state: ApprovalState, event: ApprovalEvent) -> ApprovalState:
    """Return the state an event leads to, refusing every pair outside the protocol.

    Args:
        state: The current state.
        event: The event applied to it.

    Returns:
        The target state.

    Raises:
        ApprovalError: With ``TRANSITION`` when the protocol has no such edge.
    """
    target = _TRANSITIONS.get((state, event))
    if target is None:
        raise ApprovalError(ApprovalRefusal.TRANSITION, (state, event))
    return target


def proposal_digest(proposal: Proposal) -> str:
    """Return the contracts digest of a proposal's parameters in the proposal context.

    Args:
        proposal: The proposal whose parameters are digested.

    Returns:
        The digest, as lowercase hexadecimal.

    Raises:
        ApprovalError: With ``PARAMETERS`` when the parameters cannot be digested, carrying
            the contracts refusal as the cause.
    """
    try:
        return digest.digest(digest.Context.PROPOSAL, proposal.parameters)
    except (digest.DigestError, CanonicalizationError) as error:
        raise ApprovalError(ApprovalRefusal.PARAMETERS, proposal.parameters) from error


def approve(
    state: ApprovalState,
    proposal: Proposal,
    operator_identity: str,
    issued: ClockReading,
    time_to_live: timedelta,
) -> Approval:
    """Record an operator's approval of a proposal.

    Args:
        state: The proposal's current state; only a requested proposal is approvable.
        proposal: The proposal the operator read.
        operator_identity: The non-secret identity the validated bearer supplied.
        issued: Both clocks as read at the decision.
        time_to_live: The injected window; it has no default.

    Returns:
        The approved record, bound to the mission, the proposal, and the digest.

    Raises:
        ApprovalError: With ``TRANSITION`` when the state is not approvable, ``TIME_TO_LIVE``
            when the window is not positive, or ``PARAMETERS`` when the proposal cannot be
            digested.
    """
    approved = transition(state, ApprovalEvent.APPROVE)
    if time_to_live <= _NO_DURATION:
        raise ApprovalError(ApprovalRefusal.TIME_TO_LIVE, time_to_live)
    return Approval(
        approved,
        operator_identity,
        issued,
        time_to_live,
        proposal.mission_id,
        proposal.proposal_id,
        proposal_digest(proposal),
    )


def supersede(approval: Approval) -> Approval:
    """Return the record superseded by a replan; a superseded proposal is never consumable."""
    return replace(approval, state=transition(approval.state, ApprovalEvent.SUPERSEDE))


def expire(approval: Approval) -> Approval:
    """Return the record expired by the caller; an expired proposal is never consumable."""
    return replace(approval, state=transition(approval.state, ApprovalEvent.EXPIRE))


def consume(approval: Approval, candidate: Proposal, now: ClockReading) -> Approval:
    """Consume an approval for the proposal the gateway is about to publish.

    Refusals come in a fixed order: the record's state; the candidate's mission; its proposal;
    its parameters and their digest against the recorded digest; then the clocks.

    Args:
        approval: The record being consumed.
        candidate: The proposal about to be published, parameters included.
        now: Both clocks as read inside the consuming transaction.

    Returns:
        The executed record, which can never be consumed again.

    Raises:
        ApprovalError: Naming the first refusal in the documented order.
    """
    refusal = _CONSUME_REFUSALS.get(approval.state)
    if refusal is not None:
        raise ApprovalError(refusal, approval.proposal_id)
    _refuse_unless_bound(approval, candidate)
    _refuse_unless_live(approval, now)
    return replace(approval, state=transition(approval.state, ApprovalEvent.EXECUTE))


def _refuse_unless_bound(approval: Approval, candidate: Proposal) -> None:
    """Raise unless the candidate is the mission, proposal, and digest the record binds."""
    if candidate.mission_id != approval.mission_id:
        raise ApprovalError(ApprovalRefusal.MISSION, candidate.mission_id)
    if candidate.proposal_id != approval.proposal_id:
        raise ApprovalError(ApprovalRefusal.PROPOSAL, candidate.proposal_id)
    computed = proposal_digest(candidate)
    if not digest.matches(approval.proposal_digest, computed):
        raise ApprovalError(ApprovalRefusal.DIGEST, computed)


def _refuse_unless_live(approval: Approval, now: ClockReading) -> None:
    """Raise when either clock regressed since issue or either delta reached the window."""
    issued = approval.issued
    if now.wall < issued.wall or now.monotonic < issued.monotonic:
        raise ApprovalError(ApprovalRefusal.CLOCK_REGRESSION, now)
    window = approval.time_to_live
    if now.wall - issued.wall >= window or now.monotonic - issued.monotonic >= window:
        raise ApprovalError(ApprovalRefusal.EXPIRED, now)
