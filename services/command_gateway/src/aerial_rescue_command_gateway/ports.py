"""Typed broker and transaction seams owned by the command gateway."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

from aerial_rescue_domain.approvals import Approval
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    StoredApprovalAuthority,
)
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome
from aerial_rescue_store.command_progress import (
    CommandIdentity,
    StoredCommandProgress,
    TransitionFacts,
)
from aerial_rescue_store.evidence import StoredEvidenceDecision
from aerial_rescue_store.idempotency import ClaimOutcome, StoredClaim
from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome
from aerial_rescue_store.outbox import StagedCommand
from aerial_rescue_store.proposals import StoredProposal

from aerial_rescue_command_gateway.ingress import ApprovalAction
from aerial_rescue_command_gateway.normalization import PendingInvocation


def _require_approval_id(approval_id: str) -> None:
    """Refuse an authority record that has no durable approval identity."""
    if not approval_id:
        message = "approval_id must not be blank"
        raise ValueError(message)


@dataclass(frozen=True)
class DirectDelivery:
    """One direct integration message, which has no settlement capability."""

    topic: str
    payload: bytes
    properties: Mapping[str, object]


@dataclass(frozen=True)
class GuaranteedDelivery:
    """One guaranteed message which must be settled only after durable processing."""

    topic: str
    payload: bytes


@dataclass(frozen=True)
class BoundApproval:
    """The complete exact binding held under the approval row lock.

    The current store approval row does not yet carry all of these fields.  Keeping the
    service port complete makes that missing adapter impossible to conceal with defaults.
    """

    approval_id: str
    approval: Approval
    durable_approval: StoredApproval
    evidence_decision_id: str
    evidence_decision_digest: str
    evidence_decision_version: int
    action: ApprovalAction
    runtime_epoch: str | None

    def __post_init__(self) -> None:
        """Refuse an authority record that has no durable approval identity."""
        _require_approval_id(self.approval_id)


@dataclass(frozen=True)
class StoredApprovalBinding:
    """The complete dashboard-persisted approval event binding.

    No current package-store row can construct this value by itself: the adapter must join the
    approval, proposal, evidence-decision, action, and runtime-epoch facts without defaults.
    """

    approval_id: str
    mission_id: str
    operator_id: str
    decision: str
    issued_at: str
    expires_at: str | None
    proposal_id: str
    proposal_digest: str
    proposal_version: int
    evidence_decision_id: str
    evidence_decision_digest: str
    evidence_decision_version: int
    action: ApprovalAction
    decision_runtime_id: str
    time_to_live_milliseconds: int


class SettlementPort(Protocol):
    """The only guaranteed-receiver capability an accepted handler receives."""

    async def accept(self, event_id: str) -> None:
        """Settle one delivery after its transaction has committed."""

    async def reject(self) -> None:
        """Permanently reject one malformed delivery after its refusal commits."""


class RefusalUnitOfWork(Protocol):
    """The separate durable refusal transaction shared by Guaranteed handlers."""

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit a new fact or reuse the exact first observation."""


class AuthorizationTransaction(Protocol):
    """All effects in ADR-0146's operator-command authorization transaction."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the broker identity or return its exact prior durable result."""

    async def claim_command(self, claim: StoredClaim) -> ClaimOutcome:
        """Claim the command identifier and body digest inside this transaction."""

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Load the immutable normalized proposal named by an escalation."""

    async def load_decision(self, decision_id: str) -> StoredEvidenceDecision:
        """Load the immutable evidence decision named by an escalation."""

    async def load_approval(self, proposal_id: str) -> BoundApproval:
        """Load and lock the one approval associated with a proposal."""

    async def persist_consumed(self, approval: BoundApproval) -> None:
        """Persist the exact approval after pure domain consumption succeeds."""

    async def append_audit(self, record: AuditRecord) -> None:
        """Append one durable authorization fact."""

    async def stage_application(self, event: StagedApplicationEvent) -> None:
        """Stage the audit event for confirmed guaranteed publication."""

    async def stage_command(self, command: StagedCommand) -> None:
        """Stage one command in the established bounded command outbox."""

    async def initialize_progress(self, identity: CommandIdentity, updated_at: str) -> None:
        """Persist accepted and unsent progress for a newly authorized command."""

    async def record_result(self, key: str, result: bytes) -> None:
        """Record the exact response future command duplicates receive."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the broker inbox claim with the same durable response."""


class AuthorizationContext(Protocol):
    """An async transaction which commits only on successful exit."""

    async def __aenter__(self) -> AuthorizationTransaction:
        """Return the typed authorization operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success and roll back on every exception."""


class AuthorizationUnitOfWork(RefusalUnitOfWork, Protocol):
    """Construct one operator-command transaction."""

    def begin(self) -> AuthorizationContext:
        """Return a fresh transaction context."""


class ProgressRecorder(Protocol):
    """The durable compare-and-set around one domain command transition."""

    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Apply and persist one transition against the compared current progress."""


class ResultTransaction(ProgressRecorder, Protocol):
    """Every effect in one guaranteed command-result transaction."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the result identity or return its exact prior result."""

    async def load_progress(self, command_id: str) -> StoredCommandProgress:
        """Load authoritative durable progress for the named command."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the result inbox claim inside this transaction."""


class ResultContext(Protocol):
    """An async command-result transaction which commits on successful exit."""

    async def __aenter__(self) -> ResultTransaction:
        """Return result-processing operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success and roll back on every exception."""


class ResultUnitOfWork(RefusalUnitOfWork, Protocol):
    """Construct one command-result transaction."""

    def begin(self) -> ResultContext:
        """Return a fresh transaction context."""


class ApprovalIngressTransaction(Protocol):
    """Durable verification operations for one operator approval event."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the broker identity or return its exact prior result."""

    async def load_binding(self, approval_id: str) -> StoredApprovalBinding:
        """Load the complete authoritative binding assembled without defaults."""

    async def bind_authority(
        self,
        approval_id: str,
        authority: StoredApprovalAuthority,
    ) -> ApprovalAuthorityDecision:
        """Bind the verified decision to this gateway epoch exactly once."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the broker inbox claim with its exact verification result."""


class ApprovalIngressContext(Protocol):
    """An async approval-ingress transaction which commits on successful exit."""

    async def __aenter__(self) -> ApprovalIngressTransaction:
        """Return the typed verification operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success and roll back on every exception."""


class ApprovalIngressUnitOfWork(RefusalUnitOfWork, Protocol):
    """Construct one operator-approval verification transaction."""

    def begin(self) -> ApprovalIngressContext:
        """Return a fresh transaction context."""


class NormalizationTransaction(Protocol):
    """Every effect in one Agent Response normalization transaction."""

    async def record_pending(self, pending: PendingInvocation) -> None:
        """Persist transport-authenticated invocation context immutably."""

    async def load_pending(self, invocation_id: str) -> PendingInvocation:
        """Return trusted forward context for the pending invocation."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the response identity or return its exact prior result."""

    async def record_proposal(self, proposal: StoredProposal) -> None:
        """Persist one immutable normalized proposal."""

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact application publication."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the inbox claim with its exact durable result."""


class NormalizationContext(Protocol):
    """An async transaction which commits only on successful exit."""

    async def __aenter__(self) -> NormalizationTransaction:
        """Return the typed transaction operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success and roll back on every exception."""


class NormalizationUnitOfWork(Protocol):
    """Construct one Agent Response transaction."""

    def begin(self) -> NormalizationContext:
        """Return a fresh transaction context."""
