"""Concrete command-gateway composition over store-owned SQLAlchemy transactions.

This module is deliberately an adapter, not a persistence implementation.  The store package
owns every SQLAlchemy statement and transaction boundary; the service maps complete durable
records into its domain ports without defaults, coercion, or connection side effects.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, cast

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.canonical import CanonicalizationError
from aerial_rescue_contracts.instant import InstantError, format_instant, parse_instant
from aerial_rescue_domain.approvals import Approval, ApprovalState, ClockReading
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    ApplicationOutboxSession,
    StagedApplicationEvent,
)
from aerial_rescue_store.application_outbox import (
    pending as pending_application,
)
from aerial_rescue_store.application_outbox import (
    reconciliation as reconciliation_application,
)
from aerial_rescue_store.application_outbox import (
    record_publication as record_application_publication,
)
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
from aerial_rescue_store.outbox import CommandOutboxRecord, StagedCommand
from aerial_rescue_store.pending_invocations import StoredPendingInvocation
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.command_gateway import (
    ApprovalIngressTransaction as DurableApprovalIngressTransaction,
)
from aerial_rescue_store.processing.command_gateway import (
    ApprovalIngressTransactions,
    CommandAuthorizationTransactions,
    CommandOutboxTransactions,
    CommandProgressTransactions,
    CommandResultTransactions,
    NormalizationTransactions,
    StoredAuthorizationApproval,
)
from aerial_rescue_store.processing.command_gateway import (
    CommandAuthorizationTransaction as DurableAuthorizationTransaction,
)
from aerial_rescue_store.processing.command_gateway import (
    CommandResultTransaction as DurableResultTransaction,
)
from aerial_rescue_store.processing.command_gateway import (
    NormalizationTransaction as DurableNormalizationTransaction,
)
from aerial_rescue_store.proposals import StoredProposal
from aerial_rescue_store.session import StoreSessionFactory, transaction
from pydantic import ValidationError

from aerial_rescue_command_gateway.ingress import ApprovalAction
from aerial_rescue_command_gateway.normalization import PendingInvocation
from aerial_rescue_command_gateway.ports import (
    ApprovalIngressTransaction,
    ApprovalIngressUnitOfWork,
    AuthorizationTransaction,
    AuthorizationUnitOfWork,
    BoundApproval,
    NormalizationTransaction,
    NormalizationUnitOfWork,
    ProgressRecorder,
    ResultTransaction,
    ResultUnitOfWork,
    StoredApprovalBinding,
)
from aerial_rescue_command_gateway.publication import CommandPublication
from aerial_rescue_command_gateway.refusal import RefusalPersistence

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class StoreAdapterRefusal(Enum):
    """Why durable command-gateway authority cannot cross the service boundary."""

    ACTION = "the durable approval action is not one closed canonical action"
    EXPIRY = "the durable approval expiry does not exactly match its issued instant and TTL"
    IDENTITY = "the durable authority records do not bind the same identity"
    DECISION = "the immutable decision and mutable approval lifecycle are incompatible"
    CONFIRMATION = "the outbox outcome and confirmation instant are incompatible"


class StoreAdapterError(ValueError):
    """A redacted refusal while mapping complete durable authority."""

    def __init__(self, refusal: StoreAdapterRefusal) -> None:
        """Expose a closed reason without durable payload bytes."""
        super().__init__(refusal.value)
        self.refusal = refusal


def _action(payload: bytes) -> ApprovalAction:
    """Decode one exact closed approval action without a fallback representation."""
    try:
        decoded = canonical.decode(payload)
        return ApprovalAction.model_validate(decoded)
    except CanonicalizationError, ValidationError:
        raise StoreAdapterError(StoreAdapterRefusal.ACTION) from None


def _milliseconds(
    duration: timedelta,
    *,
    allow_zero: bool = False,
    allow_negative: bool = False,
) -> int:
    """Return exact integral milliseconds, refusing precision loss or invalid sign."""
    microseconds = duration // timedelta(microseconds=1)
    invalid_sign = (microseconds < 0 and not allow_negative) or (
        microseconds == 0 and not allow_zero
    )
    if invalid_sign or microseconds % 1_000 != 0:
        raise StoreAdapterError(StoreAdapterRefusal.EXPIRY)
    return microseconds // 1_000


def _approval(
    authority: StoredAuthorizationApproval,
    *,
    use_gateway_authority: bool,
) -> Approval:
    """Map and cross-check one complete durable approval authority pair."""
    approval = authority.approval
    binding = authority.binding
    if approval.proposal_id != binding.proposal_id:
        raise StoreAdapterError(StoreAdapterRefusal.IDENTITY)
    if binding.proposal_version != 1 or binding.evidence_decision_version != 1:
        raise StoreAdapterError(StoreAdapterRefusal.DECISION)
    approving_state = approval.state in {
        ApprovalState.APPROVED,
        ApprovalState.EXECUTED,
        ApprovalState.EXPIRED,
        ApprovalState.SUPERSEDED,
    }
    if (binding.decision == "approve") is not approving_state:
        raise StoreAdapterError(StoreAdapterRefusal.DECISION)
    authority_pair = (
        binding.authority_runtime_epoch,
        binding.authority_issued_monotonic_milliseconds,
    )
    valid_authority = authority_pair == (None, None) or (
        isinstance(authority_pair[0], str)
        and bool(authority_pair[0])
        and type(authority_pair[1]) is int
    )
    valid_durations = (
        type(approval.issued_monotonic_milliseconds) is int
        and approval.issued_monotonic_milliseconds >= 0
        and type(approval.time_to_live_milliseconds) is int
        and approval.time_to_live_milliseconds > 0
        and valid_authority
    )
    if not valid_durations:
        raise StoreAdapterError(StoreAdapterRefusal.EXPIRY)
    try:
        issued_wall = parse_instant(approval.issued_wall)
    except InstantError:
        raise StoreAdapterError(StoreAdapterRefusal.EXPIRY) from None
    time_to_live = timedelta(milliseconds=approval.time_to_live_milliseconds)
    issued_monotonic_milliseconds = approval.issued_monotonic_milliseconds
    if use_gateway_authority:
        issued_monotonic_milliseconds = (
            binding.authority_issued_monotonic_milliseconds
            if binding.authority_issued_monotonic_milliseconds is not None
            else 0
        )
    mapped = Approval(
        state=approval.state,
        operator_identity=approval.operator_identity,
        issued=ClockReading(
            wall=issued_wall,
            monotonic=timedelta(milliseconds=issued_monotonic_milliseconds),
        ),
        time_to_live=time_to_live,
        mission_id=approval.mission_id,
        proposal_id=approval.proposal_id,
        proposal_digest=approval.proposal_digest,
    )
    expected_expiry = format_instant(mapped.expires_at) if binding.decision == "approve" else None
    if binding.expires_at != expected_expiry:
        raise StoreAdapterError(StoreAdapterRefusal.EXPIRY)
    return mapped


def map_authorization_approval(authority: StoredAuthorizationApproval) -> BoundApproval:
    """Map complete durable authority into the authorization port without defaults."""
    binding = authority.binding
    return BoundApproval(
        approval_id=binding.approval_id,
        approval=_approval(authority, use_gateway_authority=True),
        evidence_decision_id=binding.evidence_decision_id,
        evidence_decision_digest=binding.evidence_decision_digest,
        evidence_decision_version=binding.evidence_decision_version,
        action=_action(binding.action_payload),
        runtime_epoch=binding.authority_runtime_epoch,
    )


def map_approval_binding(authority: StoredAuthorizationApproval) -> StoredApprovalBinding:
    """Assemble the event-verification authority from both exact durable records."""
    approval = _approval(authority, use_gateway_authority=False)
    binding = authority.binding
    return StoredApprovalBinding(
        approval_id=binding.approval_id,
        mission_id=approval.mission_id,
        operator_id=approval.operator_identity,
        decision=binding.decision,
        issued_at=format_instant(approval.issued.wall),
        expires_at=binding.expires_at,
        proposal_id=approval.proposal_id,
        proposal_digest=approval.proposal_digest,
        proposal_version=binding.proposal_version,
        evidence_decision_id=binding.evidence_decision_id,
        evidence_decision_digest=binding.evidence_decision_digest,
        evidence_decision_version=binding.evidence_decision_version,
        action=_action(binding.action_payload),
        decision_runtime_id=binding.decision_runtime_id,
        time_to_live_milliseconds=authority.approval.time_to_live_milliseconds,
    )


def _stored_approval(bound: BoundApproval) -> StoredApproval:
    """Map only a domain-consumed approval back to its exact guarded store shape."""
    approval = bound.approval
    if approval.state is not ApprovalState.EXECUTED:
        raise StoreAdapterError(StoreAdapterRefusal.DECISION)
    return StoredApproval(
        mission_id=approval.mission_id,
        proposal_id=approval.proposal_id,
        state=approval.state,
        operator_identity=approval.operator_identity,
        issued_wall=format_instant(approval.issued.wall),
        issued_monotonic_milliseconds=_milliseconds(
            approval.issued.monotonic,
            allow_zero=True,
            allow_negative=True,
        ),
        time_to_live_milliseconds=_milliseconds(approval.time_to_live),
        proposal_digest=approval.proposal_digest,
    )


class StoreAuthorizationTransaction:
    """Map service authorization calls onto one store-owned atomic transaction."""

    def __init__(self, transaction: DurableAuthorizationTransaction) -> None:
        """Retain the already-open store transaction."""
        self._transaction = transaction

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Delegate broker inbox identity claiming inside this transaction."""
        return await self._transaction.claim(identity)

    async def claim_command(self, claim: StoredClaim) -> ClaimOutcome:
        """Delegate exact command idempotency claiming."""
        return await self._transaction.claim_command(claim)

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Load immutable proposal authority."""
        return await self._transaction.load_proposal(proposal_id)

    async def load_decision(self, decision_id: str) -> StoredEvidenceDecision:
        """Load immutable evidence-decision authority."""
        return await self._transaction.load_decision(decision_id)

    async def load_approval(self, proposal_id: str) -> BoundApproval:
        """Load, lock, validate, and map complete approval authority."""
        return map_authorization_approval(await self._transaction.load_approval(proposal_id))

    async def persist_consumed(self, approval: BoundApproval) -> None:
        """Persist only the exact domain-consumed approval through the guarded repository."""
        await self._transaction.persist_consumed(_stored_approval(approval))

    async def append_audit(self, record: AuditRecord) -> None:
        """Append authorization audit evidence inside this transaction."""
        await self._transaction.append_audit(record)

    async def stage_application(self, event: StagedApplicationEvent) -> None:
        """Stage the authorization audit publication inside this transaction."""
        await self._transaction.stage_application(event)

    async def stage_command(self, command: StagedCommand) -> None:
        """Stage the executable command inside this transaction."""
        await self._transaction.stage_command(command)

    async def initialize_progress(self, identity: CommandIdentity, updated_at: str) -> None:
        """Initialize durable command progress inside this transaction."""
        await self._transaction.initialize_progress(identity, updated_at)

    async def record_result(self, key: str, result: bytes) -> None:
        """Record the exact idempotency response inside this transaction."""
        await self._transaction.record_result(key, result)

    async def complete(self, identity: InboxIdentity, result: bytes, processed_at: str) -> None:
        """Complete the inbox claim before transaction commit."""
        await self._transaction.complete(identity, result, processed_at)


class StoreApprovalIngressTransaction:
    """Map complete stored approval authority for broker-event verification."""

    def __init__(self, transaction: DurableApprovalIngressTransaction) -> None:
        """Retain the already-open store transaction."""
        self._transaction = transaction

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Delegate approval inbox claiming."""
        return await self._transaction.claim(identity)

    async def load_binding(self, approval_id: str) -> StoredApprovalBinding:
        """Load and map every immutable and mutable approval fact."""
        return map_approval_binding(await self._transaction.load_binding(approval_id))

    async def bind_authority(
        self,
        approval_id: str,
        authority: StoredApprovalAuthority,
    ) -> ApprovalAuthorityDecision:
        """Bind verified authority through the store-owned transaction repository."""
        return await self._transaction.bind_authority(approval_id, authority)

    async def complete(self, identity: InboxIdentity, result: bytes, processed_at: str) -> None:
        """Complete verification inside the transaction."""
        await self._transaction.complete(identity, result, processed_at)


class StoreResultTransaction:
    """Delegate result inbox and progress operations to one store transaction."""

    def __init__(self, transaction: DurableResultTransaction) -> None:
        """Retain the already-open store transaction."""
        self._transaction = transaction

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Delegate result inbox claiming."""
        return await self._transaction.claim(identity)

    async def load_progress(self, command_id: str) -> StoredCommandProgress:
        """Load exact durable command progress."""
        return await self._transaction.load_progress(command_id)

    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Persist one compared domain transition."""
        return await self._transaction.transition(current, event, budget, facts)

    async def complete(self, identity: InboxIdentity, result: bytes, processed_at: str) -> None:
        """Complete result processing before transaction commit."""
        await self._transaction.complete(identity, result, processed_at)


class StoreNormalizationTransaction:
    """Map trusted pending context and delegate one normalization transaction."""

    def __init__(self, transaction: DurableNormalizationTransaction) -> None:
        """Retain the already-open store transaction."""
        self._transaction = transaction

    async def record_pending(self, pending: PendingInvocation) -> None:
        """Map and persist trusted transport context without defaults."""
        await self._transaction.record_pending(
            StoredPendingInvocation(
                invocation_id=pending.invocation_id,
                mission_id=pending.mission_id,
                agent_name=pending.agent_name,
                correlation_id=pending.correlation_id,
                source_event_id=pending.source_event_id,
                source_event_digest=pending.source_event_digest,
            )
        )

    async def load_pending(self, invocation_id: str) -> PendingInvocation:
        """Map all trusted forward context without a default."""
        pending = await self._transaction.load_pending(invocation_id)
        return PendingInvocation(
            mission_id=pending.mission_id,
            agent_name=pending.agent_name,
            invocation_id=pending.invocation_id,
            correlation_id=pending.correlation_id,
            source_event_id=pending.source_event_id,
            source_event_digest=pending.source_event_digest,
        )

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Delegate response inbox claiming."""
        return await self._transaction.claim(identity)

    async def record_proposal(self, proposal: StoredProposal) -> None:
        """Persist immutable normalized proposal authority."""
        await self._transaction.record_proposal(proposal)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact normalization publication."""
        await self._transaction.stage(event)

    async def complete(self, identity: InboxIdentity, result: bytes, processed_at: str) -> None:
        """Complete response deduplication inside the transaction."""
        await self._transaction.complete(identity, result, processed_at)


@asynccontextmanager
async def _authorization_context(
    transactions: CommandAuthorizationTransactions,
) -> AsyncIterator[AuthorizationTransaction]:
    """Preserve the store commit-or-rollback boundary around authorization mapping."""
    async with transactions.open() as transaction:
        yield StoreAuthorizationTransaction(transaction)


@asynccontextmanager
async def _approval_context(
    transactions: ApprovalIngressTransactions,
) -> AsyncIterator[ApprovalIngressTransaction]:
    """Preserve the store boundary around approval verification."""
    async with transactions.open() as transaction:
        yield StoreApprovalIngressTransaction(transaction)


@asynccontextmanager
async def _result_context(
    transactions: CommandResultTransactions,
) -> AsyncIterator[ResultTransaction]:
    """Preserve the store boundary around result processing."""
    async with transactions.open() as transaction:
        yield StoreResultTransaction(transaction)


@asynccontextmanager
async def _normalization_context(
    transactions: NormalizationTransactions,
) -> AsyncIterator[NormalizationTransaction]:
    """Preserve the store boundary around Agent Response normalization."""
    async with transactions.open() as transaction:
        yield StoreNormalizationTransaction(transaction)


class StoreRefusalPersistence:
    """Expose the store recorder through the service's refusal capability."""

    def __init__(self, refusals: BrokerRefusalRecorder) -> None:
        """Retain the lazy, transaction-owning refusal recorder."""
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return only after the bounded refusal fact commits."""
        return await self._refusals.record(fact)


class StoreAuthorizationUnitOfWork:
    """Service unit of work backed by fresh store authorization transactions."""

    def __init__(
        self,
        transactions: CommandAuthorizationTransactions,
        refusals: BrokerRefusalRecorder,
    ) -> None:
        """Retain the lazy transaction factory."""
        self._transactions = transactions
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed-ingress evidence in its own transaction."""
        return await self._refusals.record(fact)

    def begin(self) -> AbstractAsyncContextManager[AuthorizationTransaction]:
        """Open one transaction only when the handler enters it."""
        return _authorization_context(self._transactions)


class StoreApprovalIngressUnitOfWork:
    """Service unit of work backed by fresh store approval transactions."""

    def __init__(
        self,
        transactions: ApprovalIngressTransactions,
        refusals: BrokerRefusalRecorder,
    ) -> None:
        """Retain the lazy transaction factory."""
        self._transactions = transactions
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed-ingress evidence in its own transaction."""
        return await self._refusals.record(fact)

    def begin(self) -> AbstractAsyncContextManager[ApprovalIngressTransaction]:
        """Open one transaction only when the handler enters it."""
        return _approval_context(self._transactions)


class StoreResultUnitOfWork:
    """Service unit of work backed by fresh store result transactions."""

    def __init__(
        self,
        transactions: CommandResultTransactions,
        refusals: BrokerRefusalRecorder,
    ) -> None:
        """Retain the lazy transaction factory."""
        self._transactions = transactions
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed-ingress evidence in its own transaction."""
        return await self._refusals.record(fact)

    def begin(self) -> AbstractAsyncContextManager[ResultTransaction]:
        """Open one transaction only when the handler enters it."""
        return _result_context(self._transactions)


class StoreNormalizationUnitOfWork:
    """Service unit of work backed by fresh store normalization transactions."""

    def __init__(self, transactions: NormalizationTransactions) -> None:
        """Retain the lazy transaction factory."""
        self._transactions = transactions

    def begin(self) -> AbstractAsyncContextManager[NormalizationTransaction]:
        """Open one transaction only when the handler enters it."""
        return _normalization_context(self._transactions)


class StoreCommandOutbox:
    """Map store-owned bounded command rows into publication-worker records."""

    def __init__(self, transactions: CommandOutboxTransactions) -> None:
        """Retain the independent short-transaction operations."""
        self._transactions = transactions

    async def pending(self, limit: int) -> tuple[CommandPublication, ...]:
        """Return oldest staged publications with their compared state."""
        return _publications(await self._transactions.pending(limit))

    async def reconciliation(self, limit: int) -> tuple[CommandPublication, ...]:
        """Return ambiguous publications for evidence-only reconciliation."""
        return _publications(await self._transactions.reconciliation(limit))

    async def record(
        self,
        command_id: str,
        was: OutboxState,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Require confirmation evidence shape before committing the state transition."""
        valid = (event is OutboxEvent.CONFIRM and confirmed_at is not None) or (
            event is OutboxEvent.AMBIGUOUS and confirmed_at is None
        )
        if not valid:
            raise StoreAdapterError(StoreAdapterRefusal.CONFIRMATION)
        if confirmed_at is not None:
            try:
                parse_instant(confirmed_at)
            except InstantError:
                raise StoreAdapterError(StoreAdapterRefusal.CONFIRMATION) from None
        await self._transactions.record(command_id, was, event)


class StoreApplicationOutbox:
    """Read and move general application publications in independent transactions."""

    def __init__(self, session_factory: StoreSessionFactory) -> None:
        """Retain the lazy session factory without opening a connection."""
        self._session_factory = session_factory

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return one bounded oldest-first staged application batch."""
        async with transaction(self._session_factory) as session:
            return await pending_application(cast("ApplicationOutboxSession", session), producer)

    async def reconciliation(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return ambiguous rows for evidence-only reconciliation."""
        async with transaction(self._session_factory) as session:
            return await reconciliation_application(
                cast("ApplicationOutboxSession", session), producer
            )

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Commit one staged row's broker outcome independently."""
        async with transaction(self._session_factory) as session:
            await record_application_publication(
                cast("ApplicationOutboxSession", session),
                identity,
                OutboxState.STAGED,
                event,
                confirmed_at,
            )


def _publications(records: tuple[CommandOutboxRecord, ...]) -> tuple[CommandPublication, ...]:
    """Map complete store records without losing their compared state."""
    return tuple(CommandPublication(record.command, record.state) for record in records)


class StoreProgressRecorder:
    """Commit scheduler-driven progress through store-owned short transactions."""

    def __init__(self, transactions: CommandProgressTransactions) -> None:
        """Retain the lazy store transaction operations."""
        self._transactions = transactions

    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Persist one compared domain transition."""
        return await self._transactions.transition(current, event, budget, facts)


@dataclass(frozen=True)
class ApplicationStore:
    """Every persistence capability the command-gateway application runtime needs."""

    authorization: AuthorizationUnitOfWork
    approval_ingress: ApprovalIngressUnitOfWork
    results: ResultUnitOfWork
    normalization: NormalizationUnitOfWork
    outbox: StoreCommandOutbox
    application_outbox: StoreApplicationOutbox
    progress: ProgressRecorder
    refusals: RefusalPersistence


def compose_application_store(
    session_factory: StoreSessionFactory,
    observed_at: Callable[[], str],
) -> ApplicationStore:
    """Construct every service adapter lazily, opening no database connection."""
    authorization = CommandAuthorizationTransactions(session_factory)
    approvals = ApprovalIngressTransactions(session_factory)
    results = CommandResultTransactions(session_factory)
    normalization = NormalizationTransactions(session_factory)
    outbox = CommandOutboxTransactions(session_factory)
    progress = CommandProgressTransactions(session_factory)
    refusals = BrokerRefusalRecorder(session_factory, observed_at)
    return ApplicationStore(
        authorization=StoreAuthorizationUnitOfWork(authorization, refusals),
        approval_ingress=StoreApprovalIngressUnitOfWork(approvals, refusals),
        results=StoreResultUnitOfWork(results, refusals),
        normalization=StoreNormalizationUnitOfWork(normalization),
        outbox=StoreCommandOutbox(outbox),
        application_outbox=StoreApplicationOutbox(session_factory),
        progress=StoreProgressRecorder(progress),
        refusals=StoreRefusalPersistence(refusals),
    )
