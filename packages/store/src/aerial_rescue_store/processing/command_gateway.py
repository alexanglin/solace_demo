"""Atomic command-gateway units of work over package-owned SQLAlchemy repositories."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState

from aerial_rescue_store.application_outbox import (
    ApplicationOutboxSession,
    StagedApplicationEvent,
)
from aerial_rescue_store.application_outbox import stage as stage_application
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    ApprovalBindingSession,
    StoredApprovalAuthority,
    StoredApprovalBinding,
)
from aerial_rescue_store.approval_bindings import bind_authority as bind_approval_authority
from aerial_rescue_store.approval_bindings import load_by_approval as load_binding_by_approval
from aerial_rescue_store.approval_bindings import load_by_proposal as load_binding_by_proposal
from aerial_rescue_store.approvals import ApprovalSession, StoredApproval
from aerial_rescue_store.approvals import load_for_update as load_approval_for_update
from aerial_rescue_store.approvals import persist_consumed as persist_approval
from aerial_rescue_store.audit import AuditRecord, OrdinalSession
from aerial_rescue_store.audit import append as append_audit
from aerial_rescue_store.command_progress import (
    CommandIdentity,
    CommandProgressSession,
    StoredCommandProgress,
    TransitionFacts,
)
from aerial_rescue_store.command_progress import initialize as initialize_progress
from aerial_rescue_store.command_progress import load as load_progress
from aerial_rescue_store.command_progress import record_transition as record_progress
from aerial_rescue_store.evidence import EvidenceSession, StoredEvidenceDecision, load_decision
from aerial_rescue_store.idempotency import ClaimOutcome, ClaimSession, StoredClaim
from aerial_rescue_store.idempotency import claim as claim_idempotency
from aerial_rescue_store.idempotency import record_result as record_claim_result
from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome, InboxSession
from aerial_rescue_store.inbox import claim as claim_inbox
from aerial_rescue_store.inbox import complete as complete_inbox
from aerial_rescue_store.outbox import (
    CommandOutboxRecord,
    OutboxReadSession,
    OutboxSession,
    StagedCommand,
)
from aerial_rescue_store.outbox import pending as read_commands
from aerial_rescue_store.outbox import record_publication as record_command_publication
from aerial_rescue_store.outbox import stage as stage_command
from aerial_rescue_store.pending_invocations import (
    PendingInvocationDecision,
    PendingInvocationSession,
    StoredPendingInvocation,
)
from aerial_rescue_store.pending_invocations import load as load_pending
from aerial_rescue_store.pending_invocations import record as record_pending
from aerial_rescue_store.proposals import ProposalSession, StoredProposal
from aerial_rescue_store.proposals import load as load_proposal
from aerial_rescue_store.proposals import record as record_proposal
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class StoredAuthorizationApproval:
    """Mutable approval state paired with its complete immutable operator binding."""

    approval: StoredApproval
    binding: StoredApprovalBinding


class CommandAuthorizationTransaction:
    """All durable effects in ADR-0146's enlarged authorization transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the one session whose commit makes every effect atomic."""
        self._session = session

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim one guaranteed operator-command identity."""
        return await claim_inbox(cast("InboxSession", self._session), identity)

    async def claim_command(self, request: StoredClaim) -> ClaimOutcome:
        """Claim the command idempotency key and exact body digest."""
        return await claim_idempotency(cast("ClaimSession", self._session), request)

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Load the immutable proposal authority."""
        return await load_proposal(cast("ProposalSession", self._session), proposal_id)

    async def load_decision(self, decision_id: str) -> StoredEvidenceDecision:
        """Load the exact evidence decision selected by the operator command."""
        return await load_decision(cast("EvidenceSession", self._session), decision_id)

    async def load_approval(self, proposal_id: str) -> StoredAuthorizationApproval:
        """Lock approval state and load its complete immutable decision binding."""
        approval = await load_approval_for_update(
            cast("ApprovalSession", self._session), proposal_id
        )
        binding = await load_binding_by_proposal(
            cast("ApprovalBindingSession", self._session), proposal_id
        )
        return StoredAuthorizationApproval(approval, binding)

    async def persist_consumed(self, approval: StoredApproval) -> None:
        """Persist only approval state returned by guarded domain consumption."""
        await persist_approval(cast("ApprovalSession", self._session), approval)

    async def append_audit(self, record: AuditRecord) -> int:
        """Append authorization evidence under the same commit."""
        return await append_audit(cast("OrdinalSession", self._session), record)

    async def stage_application(self, event: StagedApplicationEvent) -> None:
        """Stage the exact authorization audit publication."""
        await stage_application(cast("ApplicationOutboxSession", self._session), event)

    async def stage_command(self, command: StagedCommand) -> None:
        """Stage one exact drone command under the central bound."""
        await stage_command(cast("OutboxSession", self._session), command)

    async def initialize_progress(self, identity: CommandIdentity, updated_at: str) -> None:
        """Initialize accepted, unsent command progress."""
        await initialize_progress(
            cast("CommandProgressSession", self._session), identity, updated_at
        )

    async def record_result(self, key: str, result: bytes) -> None:
        """Store the exact result returned for later command duplicates."""
        await record_claim_result(cast("ClaimSession", self._session), key, result)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the inbox claim after every authorization effect is staged."""
        await complete_inbox(cast("InboxSession", self._session), identity, result, processed_at)


class ApprovalIngressTransaction:
    """Verify one guaranteed approval event against complete durable authority."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the one session used for claim, authoritative read, and completion."""
        self._session = session

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim one guaranteed approval identity."""
        return await claim_inbox(cast("InboxSession", self._session), identity)

    async def load_binding(self, approval_id: str) -> StoredAuthorizationApproval:
        """Load the immutable binding and lock its current approval lifecycle row."""
        binding = await load_binding_by_approval(
            cast("ApprovalBindingSession", self._session), approval_id
        )
        approval = await load_approval_for_update(
            cast("ApprovalSession", self._session), binding.proposal_id
        )
        return StoredAuthorizationApproval(approval, binding)

    async def bind_authority(
        self,
        approval_id: str,
        authority: StoredApprovalAuthority,
    ) -> ApprovalAuthorityDecision:
        """Bind verified authority inside the same inbox transaction exactly once."""
        return await bind_approval_authority(
            cast("ApprovalBindingSession", self._session),
            approval_id,
            authority,
        )

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete approval verification before broker settlement."""
        await complete_inbox(cast("InboxSession", self._session), identity, result, processed_at)


class CommandResultTransaction:
    """Join command-result inbox identity and command progress in one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the session whose commit precedes result settlement."""
        self._session = session

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim one guaranteed command-result identity."""
        return await claim_inbox(cast("InboxSession", self._session), identity)

    async def load_progress(self, command_id: str) -> StoredCommandProgress:
        """Load authoritative command lifecycle progress."""
        return await load_progress(cast("CommandProgressSession", self._session), command_id)

    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Persist one domain-decided lifecycle edge under compare-and-set."""
        return await record_progress(
            cast("CommandProgressSession", self._session), current, event, budget, facts
        )

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete result processing inside the progress transaction."""
        await complete_inbox(cast("InboxSession", self._session), identity, result, processed_at)


class NormalizationTransaction:
    """Persist direct Agent Response normalization as one durable boundary."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the session shared by context, inbox, proposal, and outbox writes."""
        self._session = session

    async def record_pending(
        self,
        pending: StoredPendingInvocation,
    ) -> PendingInvocationDecision:
        """Store transport-authenticated context once inside normalization."""
        return await record_pending(cast("PendingInvocationSession", self._session), pending)

    async def load_pending(self, invocation_id: str) -> StoredPendingInvocation:
        """Load trusted context persisted before Agent Mesh work began."""
        return await load_pending(cast("PendingInvocationSession", self._session), invocation_id)

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the direct response identity for restart-safe deduplication."""
        return await claim_inbox(cast("InboxSession", self._session), identity)

    async def record_proposal(self, proposal: StoredProposal) -> None:
        """Persist one immutable normalized proposal."""
        await record_proposal(cast("ProposalSession", self._session), proposal)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact proposal or audit publication."""
        await stage_application(cast("ApplicationOutboxSession", self._session), event)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the direct-response inbox claim with its exact result."""
        await complete_inbox(cast("InboxSession", self._session), identity, result, processed_at)


class _Transactions[TransactionT]:
    """Construct fresh purpose-specific transactions over one lazy session factory."""

    def __init__(
        self,
        factory: Callable[[], AsyncSession],
        constructor: Callable[[AsyncSession], TransactionT],
    ) -> None:
        """Retain lazy construction without opening a connection."""
        self._factory = factory
        self._constructor = constructor

    def open(self) -> AbstractAsyncContextManager[TransactionT]:
        """Return a fresh commit-or-rollback transaction context."""
        return _open(self._factory, self._constructor)


class CommandAuthorizationTransactions(_Transactions[CommandAuthorizationTransaction]):
    """Construct command authorization transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Bind authorization operations to the injected lazy factory."""
        super().__init__(factory, CommandAuthorizationTransaction)


class ApprovalIngressTransactions(_Transactions[ApprovalIngressTransaction]):
    """Construct approval-verification transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Bind approval verification to the injected lazy factory."""
        super().__init__(factory, ApprovalIngressTransaction)


class CommandResultTransactions(_Transactions[CommandResultTransaction]):
    """Construct command-result transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Bind result processing to the injected lazy factory."""
        super().__init__(factory, CommandResultTransaction)


class NormalizationTransactions(_Transactions[NormalizationTransaction]):
    """Construct direct Agent Response normalization transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Bind normalization to the injected lazy factory."""
        super().__init__(factory, NormalizationTransaction)


class CommandOutboxTransactions:
    """Read and move command publications in independent short transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the lazy session factory used by restart recovery."""
        self._factory = factory

    async def pending(self, limit: int) -> tuple[CommandOutboxRecord, ...]:
        """Return one bounded oldest-first staged batch."""
        return await self._read(OutboxState.STAGED, limit)

    async def reconciliation(self, limit: int) -> tuple[CommandOutboxRecord, ...]:
        """Return ambiguous rows for evidence-only reconciliation, never blind retry."""
        return await self._read(OutboxState.RECONCILIATION_NEEDED, limit)

    async def _read(self, state: OutboxState, limit: int) -> tuple[CommandOutboxRecord, ...]:
        """Read one state in a transaction that ends before broker I/O."""
        async with transaction(self._factory) as session:
            return await read_commands(cast("OutboxReadSession", session), state, limit)

    async def record(
        self,
        command_id: str,
        was: OutboxState,
        event: OutboxEvent,
    ) -> None:
        """Commit one per-row broker outcome independently of neighboring rows."""
        async with transaction(self._factory) as session:
            await record_command_publication(cast("OutboxSession", session), command_id, was, event)


class CommandProgressTransactions:
    """Persist scheduler-driven command transitions in fresh short transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the lazy session factory."""
        self._factory = factory

    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Commit one compared command-progress transition."""
        async with transaction(self._factory) as session:
            return await record_progress(
                cast("CommandProgressSession", session), current, event, budget, facts
            )


@asynccontextmanager
async def _open[TransactionT](
    factory: Callable[[], AsyncSession],
    constructor: Callable[[AsyncSession], TransactionT],
) -> AsyncIterator[TransactionT]:
    """Adapt the shared transaction boundary to one purpose-specific operation set."""
    async with transaction(factory) as session:
        yield constructor(session)
