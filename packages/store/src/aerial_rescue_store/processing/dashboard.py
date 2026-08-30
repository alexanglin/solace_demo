"""Atomic dashboard mutations, broker inbox completion, and bounded outbox recovery."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, cast

from aerial_rescue_domain.outbox import OutboxEvent, OutboxState

from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    ApplicationOutboxSession,
    StagedApplicationEvent,
    record_publication,
)
from aerial_rescue_store.application_outbox import pending as pending_application
from aerial_rescue_store.application_outbox import reconciliation as reconcile_application
from aerial_rescue_store.application_outbox import stage as stage_application
from aerial_rescue_store.approval_bindings import (
    ApprovalBindingSession,
    StoredApprovalBinding,
)
from aerial_rescue_store.approval_bindings import record as record_binding
from aerial_rescue_store.approvals import ApprovalSession, StoredApproval
from aerial_rescue_store.approvals import record as record_approval
from aerial_rescue_store.audit import AuditReadSession, StoredAuditRecord
from aerial_rescue_store.audit import read_ordered_after as read_audit_suffix
from aerial_rescue_store.dashboard.runs import (
    DashboardRun,
    RunSession,
    mission_predecessor,
    run_by_mission,
)
from aerial_rescue_store.dashboard.runs import (
    mission_lifecycle_for_update as lifecycle_for_update,
)
from aerial_rescue_store.evidence import EvidenceSession, StoredEvidenceDecision
from aerial_rescue_store.evidence import decisions_for as load_evidence_history
from aerial_rescue_store.evidence import load_decision as load_evidence_record
from aerial_rescue_store.idempotency import ClaimOutcome, ClaimSession, StoredClaim
from aerial_rescue_store.idempotency import claim as claim_idempotency
from aerial_rescue_store.idempotency import record_result as record_claim_result
from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome, InboxSession
from aerial_rescue_store.inbox import claim as claim_inbox
from aerial_rescue_store.inbox import complete as complete_inbox
from aerial_rescue_store.proposals import ProposalSession, StoredProposal
from aerial_rescue_store.proposals import load as load_proposal_record
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class DashboardMutationTransaction:
    """Purpose-specific public mutation operations sharing one SQLAlchemy transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind one caller-owned SQLAlchemy transaction."""
        self._session = session

    async def claim(self, request: StoredClaim) -> ClaimOutcome:
        """Claim one operation-specific idempotency key or return its prior response."""
        return await claim_idempotency(cast("ClaimSession", self._session), request)

    async def record_decision(
        self,
        approval: StoredApproval,
        binding: StoredApprovalBinding,
    ) -> None:
        """Persist operator lifecycle and immutable binding in the same unit of work."""
        await record_approval(cast("ApprovalSession", self._session), approval)
        await record_binding(cast("ApprovalBindingSession", self._session), binding)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage exact event bytes before any broker publication occurs."""
        await stage_application(cast("ApplicationOutboxSession", self._session), event)

    async def record_result(self, idempotency_key: str, result: bytes) -> None:
        """Record the immutable canonical HTTP response returned by every exact repeat."""
        await record_claim_result(cast("ClaimSession", self._session), idempotency_key, result)

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Load the immutable proposal inside the decision's atomic authority check."""
        return await load_proposal_record(cast("ProposalSession", self._session), proposal_id)

    async def load_evidence_decision(self, decision_id: str) -> StoredEvidenceDecision:
        """Load the immutable evidence decision inside the same authority transaction."""
        return await load_evidence_record(cast("EvidenceSession", self._session), decision_id)

    async def load_evidence_decisions(self, proposal_id: str) -> tuple[StoredEvidenceDecision, ...]:
        """Load the ordered decision history used to prove the selected row is current."""
        return await load_evidence_history(cast("EvidenceSession", self._session), proposal_id)


class DashboardInboxTransaction:
    """Guaranteed dashboard admission that commits before its caller settles."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind one caller-owned SQLAlchemy transaction."""
        self._session = session

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim one validated broker identity or return its exact prior result."""
        return await claim_inbox(cast("InboxSession", self._session), identity)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Persist recovery evidence inside the claiming transaction."""
        await complete_inbox(
            cast("InboxSession", self._session),
            identity,
            result,
            processed_at,
        )


class DashboardLifecycleTransaction:
    """Decide and stage one mission-lifecycle publication under one exclusive row lock.

    The recorder owns the lifecycle column, so the dashboard reads it to decide whether an
    observed state is a legal successor. Holding that read and the staged row in one
    transaction is what stops a recorder commit landing between the decision and the row.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind one caller-owned SQLAlchemy transaction."""
        self._session = session

    async def mission_lifecycle(self, mission_id: str) -> str:
        """Read the recorder-owned lifecycle under an exclusive row lock."""
        return await lifecycle_for_update(cast("RunSession", self._session), mission_id)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage exact event bytes under the same lock that decided them."""
        await stage_application(cast("ApplicationOutboxSession", self._session), event)

    async def predecessor_run(self, mission_id: str) -> DashboardRun | None:
        """Return the run a reset retained, reachable only through the mission's own link."""
        session = cast("RunSession", self._session)
        predecessor_id = await mission_predecessor(session, mission_id)
        if predecessor_id is None:
            return None
        return await run_by_mission(session, predecessor_id)


class DashboardMutationTransactions:
    """Construct fresh dashboard mutation units of work."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the injected session factory without opening a connection."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[DashboardMutationTransaction]:
        """Return one commit-or-rollback public mutation transaction."""
        return _open(self._factory, DashboardMutationTransaction)


class DashboardInboxTransactions:
    """Construct fresh Guaranteed dashboard processing units of work."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the injected session factory without opening a connection."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[DashboardInboxTransaction]:
        """Return one commit-or-rollback broker inbox transaction."""
        return _open(self._factory, DashboardInboxTransaction)


class DashboardLifecycleTransactions:
    """Construct fresh mission-lifecycle decision-and-staging units of work."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the injected session factory without opening a connection."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[DashboardLifecycleTransaction]:
        """Return one commit-or-rollback mission-lifecycle transaction."""
        return _open(self._factory, DashboardLifecycleTransaction)


class DashboardOutboxTransactions:
    """Read and move dashboard publications in independent short transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the injected session factory without opening a connection."""
        self._factory = factory

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Read one bounded oldest-first staged batch."""
        async with transaction(self._factory) as session:
            return await pending_application(cast("ApplicationOutboxSession", session), producer)

    async def reconciliation(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Read one bounded ambiguity batch without making it publishable."""
        async with transaction(self._factory) as session:
            return await reconcile_application(cast("ApplicationOutboxSession", session), producer)

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record one explicit broker result under an independent compare-and-set."""
        async with transaction(self._factory) as session:
            await record_publication(
                cast("ApplicationOutboxSession", session),
                identity,
                OutboxState.STAGED,
                event,
                confirmed_at,
            )


class DashboardAuditReader:
    """Read bounded mission audit suffixes in independent short transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the injected session factory without opening a connection."""
        self._factory = factory

    async def read_after(
        self,
        mission_id: str,
        after_ordinal: int,
        limit: int,
    ) -> tuple[StoredAuditRecord, ...]:
        """Read one exact keyset page from PostgreSQL authority."""
        async with transaction(self._factory) as session:
            return await read_audit_suffix(
                cast("AuditReadSession", session), mission_id, after_ordinal, limit
            )


@asynccontextmanager
async def _open[TransactionT](
    factory: Callable[[], AsyncSession],
    constructor: Callable[[AsyncSession], TransactionT],
) -> AsyncIterator[TransactionT]:
    """Adapt the shared transaction boundary to one purpose-specific dashboard surface."""
    async with transaction(factory) as session:
        yield constructor(session)
