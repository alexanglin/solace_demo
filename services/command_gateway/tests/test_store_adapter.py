"""Service mapping and lazy composition over store-owned command-gateway transactions."""

from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock, call, patch

import pytest
from aerial_rescue_command_gateway.ingress import ApprovalAction
from aerial_rescue_command_gateway.normalization import PendingInvocation
from aerial_rescue_command_gateway.store_adapter import (
    StoreAdapterError,
    StoreAdapterRefusal,
    StoreApplicationOutbox,
    StoreApprovalIngressTransaction,
    StoreApprovalIngressUnitOfWork,
    StoreAuthorizationTransaction,
    StoreAuthorizationUnitOfWork,
    StoreCommandOutbox,
    StoreNormalizationTransaction,
    StoreNormalizationUnitOfWork,
    StoreProgressRecorder,
    StoreRefusalPersistence,
    StoreResultTransaction,
    StoreResultUnitOfWork,
    _milliseconds,
    compose_application_store,
    map_approval_binding,
    map_authorization_approval,
)
from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    StoredApprovalAuthority,
)
from aerial_rescue_store.approval_bindings import StoredApprovalBinding as DurableBinding
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome
from aerial_rescue_store.command_progress import (
    CommandIdentity,
    StoredCommandProgress,
    TransitionFacts,
)
from aerial_rescue_store.evidence import StoredEvidenceDecision
from aerial_rescue_store.idempotency import StoredClaim
from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome
from aerial_rescue_store.outbox import CommandOutboxRecord, StagedCommand
from aerial_rescue_store.pending_invocations import StoredPendingInvocation
from aerial_rescue_store.processing.command_gateway import (
    ApprovalIngressTransaction,
    ApprovalIngressTransactions,
    CommandAuthorizationTransaction,
    CommandAuthorizationTransactions,
    CommandOutboxTransactions,
    CommandProgressTransactions,
    CommandResultTransaction,
    CommandResultTransactions,
    NormalizationTransaction,
    NormalizationTransactions,
    StoredAuthorizationApproval,
)
from aerial_rescue_store.proposals import StoredProposal
from aerial_rescue_store.session import StoreSessionFactory

ACTION = ApprovalAction(
    commandType="escalate-rescue",
    droneId="drone-1",
    latitudeMicrodegrees=45_123_456,
    longitudeMicrodegrees=-75_123_456,
)
DURABLE_APPROVAL = StoredApproval(
    mission_id="mission-1",
    proposal_id="proposal-1",
    state=ApprovalState.APPROVED,
    operator_identity="operator-1",
    issued_wall="2026-08-25T12:00:00.000Z",
    issued_monotonic_milliseconds=1_000,
    time_to_live_milliseconds=300_000,
    proposal_digest="1" * 64,
)
DURABLE_BINDING = DurableBinding(
    approval_id="approval-1",
    proposal_id="proposal-1",
    proposal_version=1,
    evidence_decision_id="decision-1",
    evidence_decision_digest="2" * 64,
    evidence_decision_version=1,
    decision="approve",
    action_payload=(
        b'{"commandType":"escalate-rescue","droneId":"drone-1",'
        b'"latitudeMicrodegrees":45123456,"longitudeMicrodegrees":-75123456}'
    ),
    decision_runtime_id="dashboard-start-1",
    authority_runtime_epoch="gateway-start-1",
    authority_issued_monotonic_milliseconds=-59_000,
    expires_at="2026-08-25T12:05:00.000Z",
)
AUTHORITY = StoredAuthorizationApproval(DURABLE_APPROVAL, DURABLE_BINDING)
PENDING = StoredPendingInvocation(
    "invocation-1", "mission-1", "VisionAgent", "correlation-1", "source-1", "3" * 64
)
COMMAND = StagedCommand(
    "command-1",
    "mission-1",
    "drone-1",
    b"{}",
    "correlation-1",
    None,
    "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
    "2026-08-25T12:01:00.000Z",
)


class _Authorization:
    """Expose only the two authority operations under test."""

    def __init__(self, authority: StoredAuthorizationApproval = AUTHORITY) -> None:
        """Configure the exact durable authority returned by the store."""
        self.authority = authority
        self.requests: list[str] = []
        self.persisted: list[StoredApproval] = []

    async def load_approval(self, proposal_id: str) -> StoredAuthorizationApproval:
        """Return the configured authority pair."""
        self.requests.append(proposal_id)
        return self.authority

    async def persist_consumed(self, approval: StoredApproval) -> None:
        """Record exact approval state sent back to the store."""
        self.persisted.append(approval)


class _ApprovalIngress:
    """Expose one complete approval binding read."""

    def __init__(self) -> None:
        """Start without a gateway authority bind."""
        self.requests: list[str] = []
        self.authorities: list[tuple[str, StoredApprovalAuthority]] = []

    async def load_binding(self, approval_id: str) -> StoredAuthorizationApproval:
        """Return the configured authority pair."""
        self.requests.append(approval_id)
        return AUTHORITY

    async def bind_authority(
        self,
        approval_id: str,
        authority: StoredApprovalAuthority,
    ) -> ApprovalAuthorityDecision:
        """Record the exact authority delegated through the adapter."""
        self.authorities.append((approval_id, authority))
        return ApprovalAuthorityDecision.BOUND


class _Normalization:
    """Expose one durable pending-invocation lookup."""

    def __init__(self) -> None:
        """Start without a recorded transport context."""
        self.requests: list[str] = []
        self.recorded: list[StoredPendingInvocation] = []

    async def record_pending(self, pending: StoredPendingInvocation) -> None:
        """Record the exact durable value delegated by the adapter."""
        self.recorded.append(pending)

    async def load_pending(self, invocation_id: str) -> StoredPendingInvocation:
        """Return complete trusted invocation context."""
        self.requests.append(invocation_id)
        return PENDING


class _Outbox:
    """Expose staged and ambiguous recovery rows and record exact moves."""

    def __init__(self) -> None:
        """Start with no durable moves."""
        self.pending_limits: list[int] = []
        self.reconciliation_limits: list[int] = []
        self.moves: list[tuple[str, OutboxState, OutboxEvent]] = []

    async def pending(self, limit: int) -> tuple[CommandOutboxRecord, ...]:
        """Return one staged command."""
        self.pending_limits.append(limit)
        return (CommandOutboxRecord(COMMAND, OutboxState.STAGED),)

    async def reconciliation(self, limit: int) -> tuple[CommandOutboxRecord, ...]:
        """Return the same command in its ambiguous state."""
        self.reconciliation_limits.append(limit)
        return (CommandOutboxRecord(COMMAND, OutboxState.RECONCILIATION_NEEDED),)

    async def record(
        self,
        command_id: str,
        was: OutboxState,
        event: OutboxEvent,
    ) -> None:
        """Record one store compare-and-set request."""
        self.moves.append((command_id, was, event))


class _OpenedTransactions:
    """Yield one injected durable transaction without adding store behavior."""

    def __init__(self, transaction: object) -> None:
        """Retain the exact transaction yielded by ``open``."""
        self.transaction = transaction

    @asynccontextmanager
    async def open(self) -> AsyncIterator[object]:
        """Yield the injected transaction once."""
        yield self.transaction


class _DurableSession:
    """Observe the store transaction boundary around application-outbox calls."""

    def __init__(self) -> None:
        """Start with no transaction endings."""
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    async def commit(self) -> None:
        """Record one successful unit of work."""
        self.commits += 1

    async def rollback(self) -> None:
        """Record one failed unit of work."""
        self.rollbacks += 1

    async def close(self) -> None:
        """Record one released unit of work."""
        self.closes += 1


class AuthorityMappingTests(unittest.TestCase):
    def test_millisecond_mapping_keeps_zero_negative_and_precision_policies_independent(
        self,
    ) -> None:
        # Arrange
        accepted = (
            (timedelta(milliseconds=1), False, False, 1),
            (timedelta(0), True, False, 0),
            (timedelta(milliseconds=-1), False, True, -1),
        )
        refused = (
            (timedelta(0), False, False),
            (timedelta(milliseconds=-1), False, False),
            (timedelta(microseconds=1), False, False),
            (timedelta(microseconds=-1), False, True),
            (timedelta(milliseconds=-1), True, False),
            (timedelta(0), False, True),
        )

        # Act
        default_mapped = _milliseconds(timedelta(milliseconds=1))
        default_refusals = []
        for duration in (timedelta(0), timedelta(milliseconds=-1)):
            with pytest.raises(StoreAdapterError) as captured:
                _milliseconds(duration)
            default_refusals.append(captured.value.refusal)
        mapped = [
            _milliseconds(
                duration,
                allow_zero=allow_zero,
                allow_negative=allow_negative,
            )
            for duration, allow_zero, allow_negative, _expected in accepted
        ]
        refusals = []
        for duration, allow_zero, allow_negative in refused:
            with pytest.raises(StoreAdapterError) as captured:
                _milliseconds(
                    duration,
                    allow_zero=allow_zero,
                    allow_negative=allow_negative,
                )
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            (1, int, [expected for *_options, expected in accepted], [int, int, int]),
            (default_mapped, type(default_mapped), mapped, [type(value) for value in mapped]),
        )
        self.assertEqual([StoreAdapterRefusal.EXPIRY] * 2, default_refusals)
        self.assertEqual([StoreAdapterRefusal.EXPIRY] * len(refused), refusals)

    def test_complete_durable_authority_maps_without_defaults_or_coercion(self) -> None:
        # Arrange
        authority = AUTHORITY

        # Act
        bound = map_authorization_approval(authority)
        event_binding = map_approval_binding(authority)

        # Assert
        self.assertEqual(
            (
                DURABLE_BINDING.approval_id,
                ApprovalState.APPROVED,
                DURABLE_APPROVAL.operator_identity,
                DURABLE_APPROVAL.mission_id,
                DURABLE_APPROVAL.proposal_id,
                DURABLE_APPROVAL.proposal_digest,
                -59_000,
                300_000,
                ACTION,
                DURABLE_BINDING.authority_runtime_epoch,
                DURABLE_BINDING.evidence_decision_id,
                DURABLE_BINDING.evidence_decision_digest,
                DURABLE_BINDING.evidence_decision_version,
                DURABLE_BINDING.approval_id,
                DURABLE_APPROVAL.mission_id,
                DURABLE_APPROVAL.operator_identity,
                DURABLE_BINDING.decision,
                DURABLE_APPROVAL.issued_wall,
                DURABLE_BINDING.expires_at,
                DURABLE_APPROVAL.proposal_id,
                DURABLE_APPROVAL.proposal_digest,
                DURABLE_BINDING.proposal_version,
                DURABLE_BINDING.evidence_decision_id,
                DURABLE_BINDING.evidence_decision_digest,
                DURABLE_BINDING.evidence_decision_version,
                ACTION,
                DURABLE_BINDING.decision_runtime_id,
                DURABLE_APPROVAL.time_to_live_milliseconds,
            ),
            (
                bound.approval_id,
                bound.approval.state,
                bound.approval.operator_identity,
                bound.approval.mission_id,
                bound.approval.proposal_id,
                bound.approval.proposal_digest,
                round(bound.approval.issued.monotonic.total_seconds() * 1_000),
                round(bound.approval.time_to_live.total_seconds() * 1_000),
                bound.action,
                bound.runtime_epoch,
                bound.evidence_decision_id,
                bound.evidence_decision_digest,
                bound.evidence_decision_version,
                event_binding.approval_id,
                event_binding.mission_id,
                event_binding.operator_id,
                event_binding.decision,
                event_binding.issued_at,
                event_binding.expires_at,
                event_binding.proposal_id,
                event_binding.proposal_digest,
                event_binding.proposal_version,
                event_binding.evidence_decision_id,
                event_binding.evidence_decision_digest,
                event_binding.evidence_decision_version,
                event_binding.action,
                event_binding.decision_runtime_id,
                event_binding.time_to_live_milliseconds,
            ),
        )

    def test_unbound_authorization_mapping_never_carries_the_dashboard_monotonic_reading(
        self,
    ) -> None:
        # Arrange
        authority = replace(
            AUTHORITY,
            approval=replace(DURABLE_APPROVAL, issued_monotonic_milliseconds=999_999),
            binding=replace(
                DURABLE_BINDING,
                authority_runtime_epoch=None,
                authority_issued_monotonic_milliseconds=None,
            ),
        )

        # Act
        bound = map_authorization_approval(authority)

        # Assert
        self.assertEqual(
            (None, 0),
            (
                bound.runtime_epoch,
                round(bound.approval.issued.monotonic.total_seconds() * 1_000),
            ),
        )

    def test_malformed_action_expiry_or_cross_identity_authority_fails_closed(self) -> None:
        # Arrange
        cases = (
            replace(AUTHORITY, binding=replace(DURABLE_BINDING, action_payload=b"not-json")),
            replace(
                AUTHORITY,
                binding=replace(
                    DURABLE_BINDING,
                    expires_at="2026-08-25T12:06:00.000Z",
                ),
            ),
            replace(
                AUTHORITY,
                binding=replace(DURABLE_BINDING, proposal_id="proposal-other"),
            ),
            replace(
                AUTHORITY,
                approval=replace(DURABLE_APPROVAL, issued_monotonic_milliseconds=-1),
            ),
            replace(
                AUTHORITY,
                binding=replace(DURABLE_BINDING, proposal_version=2),
            ),
            replace(
                AUTHORITY,
                binding=replace(DURABLE_BINDING, decision="reject", expires_at=None),
            ),
            replace(
                AUTHORITY,
                approval=replace(DURABLE_APPROVAL, issued_wall="not-an-instant"),
            ),
            replace(
                AUTHORITY,
                binding=replace(
                    DURABLE_BINDING,
                    authority_issued_monotonic_milliseconds=None,
                ),
            ),
            replace(
                AUTHORITY,
                binding=replace(DURABLE_BINDING, authority_runtime_epoch=""),
            ),
            replace(
                AUTHORITY,
                approval=replace(DURABLE_APPROVAL, time_to_live_milliseconds=0),
                binding=replace(
                    DURABLE_BINDING,
                    expires_at=DURABLE_APPROVAL.issued_wall,
                ),
            ),
        )

        # Act
        refusals = []
        messages = []
        for authority in cases:
            with pytest.raises(StoreAdapterError) as captured:
                map_authorization_approval(authority)
            refusals.append(captured.value.refusal)
            messages.append(str(captured.value))

        # Assert
        self.assertEqual(
            [
                StoreAdapterRefusal.ACTION,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.IDENTITY,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.DECISION,
                StoreAdapterRefusal.DECISION,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.EXPIRY,
            ],
            refusals,
        )
        self.assertEqual([refusal.value for refusal in refusals], messages)

    def test_zero_clock_origin_and_one_millisecond_ttl_are_valid_boundaries(self) -> None:
        # Arrange
        authority = replace(
            AUTHORITY,
            approval=replace(
                DURABLE_APPROVAL,
                issued_monotonic_milliseconds=0,
                time_to_live_milliseconds=1,
            ),
            binding=replace(
                DURABLE_BINDING,
                authority_issued_monotonic_milliseconds=0,
                expires_at="2026-08-25T12:00:00.001Z",
            ),
        )

        # Act
        bound = map_authorization_approval(authority)
        event_binding = map_approval_binding(authority)

        # Assert
        self.assertEqual(
            (0, 1, "2026-08-25T12:00:00.001Z"),
            (
                round(bound.approval.issued.monotonic.total_seconds() * 1_000),
                round(bound.approval.time_to_live.total_seconds() * 1_000),
                event_binding.expires_at,
            ),
        )


class TransactionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_maps_the_locked_record_and_persists_only_executed_state(
        self,
    ) -> None:
        # Arrange
        raw = _Authorization()
        adapter = StoreAuthorizationTransaction(cast("CommandAuthorizationTransaction", raw))

        # Act
        bound = await adapter.load_approval(DURABLE_BINDING.proposal_id)
        consumed = replace(bound, approval=replace(bound.approval, state=ApprovalState.EXECUTED))
        await adapter.persist_consumed(consumed)

        # Assert
        self.assertEqual(
            replace(DURABLE_APPROVAL, state=ApprovalState.EXECUTED),
            raw.persisted[0],
        )
        self.assertEqual([DURABLE_BINDING.proposal_id], raw.requests)
        self.assertIs(type(raw.persisted[0].issued_monotonic_milliseconds), int)
        self.assertIs(type(raw.persisted[0].time_to_live_milliseconds), int)

    async def test_negative_gateway_origin_never_rewrites_the_dashboard_diagnostic(
        self,
    ) -> None:
        # Arrange
        authority = replace(
            AUTHORITY,
            approval=replace(DURABLE_APPROVAL, issued_monotonic_milliseconds=999_999),
        )
        raw = _Authorization(authority)
        adapter = StoreAuthorizationTransaction(cast("CommandAuthorizationTransaction", raw))

        # Act
        bound = await adapter.load_approval(DURABLE_BINDING.proposal_id)
        await adapter.persist_consumed(
            replace(bound, approval=replace(bound.approval, state=ApprovalState.EXECUTED))
        )

        # Assert
        self.assertEqual(
            (999_999, -59_000),
            (
                raw.persisted[0].issued_monotonic_milliseconds,
                round(bound.approval.issued.monotonic.total_seconds() * 1_000),
            ),
        )

    async def test_consumed_write_preserves_the_dashboard_monotonic_diagnostic(self) -> None:
        # Arrange
        diagnostic = 999_999
        authority = replace(
            AUTHORITY,
            approval=replace(DURABLE_APPROVAL, issued_monotonic_milliseconds=diagnostic),
        )
        raw = _Authorization(authority)
        adapter = StoreAuthorizationTransaction(cast("CommandAuthorizationTransaction", raw))
        bound = await adapter.load_approval(DURABLE_BINDING.proposal_id)
        consumed = replace(bound, approval=replace(bound.approval, state=ApprovalState.EXECUTED))

        # Act
        await adapter.persist_consumed(consumed)

        # Assert
        self.assertEqual(
            (diagnostic, -59_000),
            (
                raw.persisted[0].issued_monotonic_milliseconds,
                round(bound.approval.issued.monotonic.total_seconds() * 1_000),
            ),
        )

    async def test_only_executed_approval_with_integral_milliseconds_can_be_persisted(self) -> None:
        # Arrange
        raw = _Authorization()
        adapter = StoreAuthorizationTransaction(cast("CommandAuthorizationTransaction", raw))
        bound = map_authorization_approval(AUTHORITY)
        cases = (
            bound,
            replace(
                bound,
                approval=replace(
                    bound.approval,
                    state=ApprovalState.EXECUTED,
                    time_to_live=timedelta(microseconds=1),
                ),
            ),
            replace(
                bound,
                approval=replace(
                    bound.approval,
                    state=ApprovalState.EXECUTED,
                    time_to_live=timedelta(0),
                ),
            ),
            replace(
                bound,
                approval=replace(
                    bound.approval,
                    state=ApprovalState.EXECUTED,
                    time_to_live=timedelta(milliseconds=-1),
                ),
            ),
        )

        # Act
        refusals = []
        for approval in cases:
            with pytest.raises(StoreAdapterError) as captured:
                await adapter.persist_consumed(approval)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                StoreAdapterRefusal.DECISION,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.EXPIRY,
                StoreAdapterRefusal.EXPIRY,
            ],
            refusals,
        )

    async def test_zero_issued_monotonic_and_one_millisecond_ttl_persist_exact_ints(self) -> None:
        # Arrange
        authority = replace(
            AUTHORITY,
            approval=replace(
                DURABLE_APPROVAL,
                issued_monotonic_milliseconds=0,
                time_to_live_milliseconds=1,
            ),
            binding=replace(
                DURABLE_BINDING,
                authority_issued_monotonic_milliseconds=0,
                expires_at="2026-08-25T12:00:00.001Z",
            ),
        )
        raw = _Authorization(authority)
        adapter = StoreAuthorizationTransaction(cast("CommandAuthorizationTransaction", raw))
        bound = map_authorization_approval(authority)
        consumed = replace(
            bound,
            approval=replace(bound.approval, state=ApprovalState.EXECUTED),
        )

        # Act
        await adapter.persist_consumed(consumed)

        # Assert
        self.assertEqual(
            (0, 1),
            (
                raw.persisted[0].issued_monotonic_milliseconds,
                raw.persisted[0].time_to_live_milliseconds,
            ),
        )
        self.assertIs(type(raw.persisted[0].issued_monotonic_milliseconds), int)
        self.assertIs(type(raw.persisted[0].time_to_live_milliseconds), int)

    async def test_consumed_write_refuses_durable_state_identity_or_expiry_drift(self) -> None:
        # Arrange
        raw = _Authorization()
        adapter = StoreAuthorizationTransaction(cast("CommandAuthorizationTransaction", raw))
        bound = map_authorization_approval(AUTHORITY)
        consumed = replace(bound, approval=replace(bound.approval, state=ApprovalState.EXECUTED))
        cases = (
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        state=ApprovalState.EXECUTED,
                    ),
                ),
                StoreAdapterRefusal.DECISION,
            ),
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        mission_id="mission-other",
                    ),
                ),
                StoreAdapterRefusal.IDENTITY,
            ),
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        proposal_id="proposal-other",
                    ),
                ),
                StoreAdapterRefusal.IDENTITY,
            ),
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        operator_identity="operator-other",
                    ),
                ),
                StoreAdapterRefusal.IDENTITY,
            ),
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        proposal_digest="9" * 64,
                    ),
                ),
                StoreAdapterRefusal.IDENTITY,
            ),
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        issued_wall="2026-08-25T12:00:00.001Z",
                    ),
                ),
                StoreAdapterRefusal.EXPIRY,
            ),
            (
                replace(
                    consumed,
                    durable_approval=replace(
                        consumed.durable_approval,
                        time_to_live_milliseconds=300_001,
                    ),
                ),
                StoreAdapterRefusal.EXPIRY,
            ),
        )

        # Act
        observations = []
        for approval, _expected in cases:
            with pytest.raises(StoreAdapterError) as captured:
                await adapter.persist_consumed(approval)
            observations.append((captured.value.refusal, str(captured.value)))

        # Assert
        self.assertEqual(
            [(expected, expected.value) for _approval, expected in cases],
            observations,
        )
        self.assertEqual([], raw.persisted)

    async def test_approval_and_normalization_map_complete_store_authority_both_directions(
        self,
    ) -> None:
        # Arrange
        raw_approval = _ApprovalIngress()
        approval = StoreApprovalIngressTransaction(cast("ApprovalIngressTransaction", raw_approval))
        raw_normalization = _Normalization()
        normalization = StoreNormalizationTransaction(
            cast("NormalizationTransaction", raw_normalization)
        )

        # Act
        binding = await approval.load_binding(DURABLE_BINDING.approval_id)
        authority_outcome = await approval.bind_authority(
            DURABLE_BINDING.approval_id,
            StoredApprovalAuthority("gateway-start-1", -59_000),
        )
        pending = await normalization.load_pending(PENDING.invocation_id)
        await normalization.record_pending(
            PendingInvocation(
                mission_id=pending.mission_id,
                agent_name=pending.agent_name,
                invocation_id=pending.invocation_id,
                correlation_id=pending.correlation_id,
                source_event_id=pending.source_event_id,
                source_event_digest=pending.source_event_digest,
            )
        )

        # Assert
        self.assertEqual(
            (
                DURABLE_BINDING.approval_id,
                ACTION,
                PENDING.mission_id,
                PENDING.source_event_digest,
                [PENDING],
                [DURABLE_BINDING.approval_id],
                [PENDING.invocation_id],
                ApprovalAuthorityDecision.BOUND,
                [
                    (
                        DURABLE_BINDING.approval_id,
                        StoredApprovalAuthority("gateway-start-1", -59_000),
                    )
                ],
            ),
            (
                binding.approval_id,
                binding.action,
                pending.mission_id,
                pending.source_event_digest,
                raw_normalization.recorded,
                raw_approval.requests,
                raw_normalization.requests,
                authority_outcome,
                raw_approval.authorities,
            ),
        )

    async def test_authorization_delegates_every_atomic_store_effect_without_reordering(
        self,
    ) -> None:
        # Arrange
        raw = AsyncMock(spec=CommandAuthorizationTransaction)
        inbox = cast("InboxIdentity", object())
        inbox_outcome = cast("InboxOutcome", object())
        claim = cast("StoredClaim", object())
        claim_outcome = object()
        proposal = cast("StoredProposal", object())
        decision = cast("StoredEvidenceDecision", object())
        audit = cast("AuditRecord", object())
        application = cast("StagedApplicationEvent", object())
        progress_identity = cast("CommandIdentity", object())
        raw.claim.return_value = inbox_outcome
        raw.claim_command.return_value = claim_outcome
        raw.load_proposal.return_value = proposal
        raw.load_decision.return_value = decision
        adapter = StoreAuthorizationTransaction(raw)

        # Act
        results = (
            await adapter.claim(inbox),
            await adapter.claim_command(claim),
            await adapter.load_proposal("proposal-1"),
            await adapter.load_decision("decision-1"),
        )
        await adapter.append_audit(audit)
        await adapter.stage_application(application)
        await adapter.stage_command(COMMAND)
        await adapter.initialize_progress(progress_identity, "updated")
        await adapter.record_result("key-1", b"result")
        await adapter.complete(inbox, b"complete", "processed")

        # Assert
        self.assertEqual(
            (inbox_outcome, claim_outcome, proposal, decision),
            results,
        )
        self.assertEqual(
            [
                call.claim(inbox),
                call.claim_command(claim),
                call.load_proposal("proposal-1"),
                call.load_decision("decision-1"),
                call.append_audit(audit),
                call.stage_application(application),
                call.stage_command(COMMAND),
                call.initialize_progress(progress_identity, "updated"),
                call.record_result("key-1", b"result"),
                call.complete(inbox, b"complete", "processed"),
            ],
            raw.mock_calls,
        )

    async def test_approval_result_and_normalization_transactions_delegate_exact_facts(
        self,
    ) -> None:
        # Arrange
        inbox = cast("InboxIdentity", object())
        inbox_outcome = cast("InboxOutcome", object())
        current = cast("StoredCommandProgress", object())
        became = cast("StoredCommandProgress", object())
        facts = cast("TransitionFacts", object())
        proposal = cast("StoredProposal", object())
        event = cast("StagedApplicationEvent", object())
        approval_raw = AsyncMock(spec=ApprovalIngressTransaction)
        result_raw = AsyncMock(spec=CommandResultTransaction)
        normalization_raw = AsyncMock(spec=NormalizationTransaction)
        approval_raw.claim.return_value = inbox_outcome
        approval_raw.bind_authority.return_value = ApprovalAuthorityDecision.BOUND
        result_raw.claim.return_value = inbox_outcome
        result_raw.load_progress.return_value = current
        result_raw.transition.return_value = became
        normalization_raw.claim.return_value = inbox_outcome
        approval = StoreApprovalIngressTransaction(approval_raw)
        result = StoreResultTransaction(result_raw)
        normalization = StoreNormalizationTransaction(normalization_raw)

        # Act
        observed = (
            await approval.claim(inbox),
            await result.claim(inbox),
            await result.load_progress("command-1"),
            await result.transition(current, CommandEvent.SEND, SendBudget(5), facts),
            await normalization.claim(inbox),
        )
        bound = await approval.bind_authority(
            "approval-1", StoredApprovalAuthority("gateway-start-1", -59_000)
        )
        await approval.complete(inbox, b"approved", "processed")
        await result.complete(inbox, b"result", "processed")
        await normalization.record_proposal(proposal)
        await normalization.stage(event)
        await normalization.complete(inbox, b"normalized", "processed")

        # Assert
        self.assertEqual(
            (
                inbox_outcome,
                inbox_outcome,
                current,
                became,
                inbox_outcome,
                ApprovalAuthorityDecision.BOUND,
            ),
            (*observed, bound),
        )
        self.assertEqual(
            [
                call.claim(inbox),
                call.bind_authority(
                    "approval-1", StoredApprovalAuthority("gateway-start-1", -59_000)
                ),
                call.complete(inbox, b"approved", "processed"),
            ],
            approval_raw.mock_calls,
        )
        self.assertEqual(
            [
                call.claim(inbox),
                call.load_progress("command-1"),
                call.transition(current, CommandEvent.SEND, SendBudget(5), facts),
                call.complete(inbox, b"result", "processed"),
            ],
            result_raw.mock_calls,
        )
        self.assertEqual(
            [
                call.claim(inbox),
                call.record_proposal(proposal),
                call.stage(event),
                call.complete(inbox, b"normalized", "processed"),
            ],
            normalization_raw.mock_calls,
        )


class UnitOfWorkAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_refusal_persistence_returns_the_committed_store_outcome(self) -> None:
        # Arrange
        refusals = AsyncMock()
        fact = cast("BrokerRefusalCandidate", object())
        committed = cast("BrokerRefusalOutcome", object())
        refusals.record.return_value = committed
        persistence = StoreRefusalPersistence(refusals)

        # Act
        observed = await persistence.refuse(fact)

        # Assert
        self.assertIs(committed, observed)
        refusals.record.assert_awaited_once_with(fact)

    async def test_units_of_work_preserve_refusal_and_transaction_capability_boundaries(
        self,
    ) -> None:
        # Arrange
        authorization_raw = AsyncMock(spec=CommandAuthorizationTransaction)
        approval_raw = AsyncMock(spec=ApprovalIngressTransaction)
        result_raw = AsyncMock(spec=CommandResultTransaction)
        normalization_raw = AsyncMock(spec=NormalizationTransaction)
        refusals = AsyncMock()
        fact = cast("BrokerRefusalCandidate", object())
        outcome = cast("BrokerRefusalOutcome", object())
        refusals.record.return_value = outcome
        authorization = StoreAuthorizationUnitOfWork(
            cast(
                "CommandAuthorizationTransactions",
                _OpenedTransactions(authorization_raw),
            ),
            refusals,
        )
        approval = StoreApprovalIngressUnitOfWork(
            cast("ApprovalIngressTransactions", _OpenedTransactions(approval_raw)),
            refusals,
        )
        result = StoreResultUnitOfWork(
            cast("CommandResultTransactions", _OpenedTransactions(result_raw)),
            refusals,
        )
        normalization = StoreNormalizationUnitOfWork(
            cast("NormalizationTransactions", _OpenedTransactions(normalization_raw))
        )

        # Act
        refusal_outcomes = (
            await authorization.refuse(fact),
            await approval.refuse(fact),
            await result.refuse(fact),
        )
        async with authorization.begin() as authorization_transaction:
            observed_authorization = authorization_transaction
        async with approval.begin() as approval_transaction:
            observed_approval = approval_transaction
        async with result.begin() as result_transaction:
            observed_result = result_transaction
        async with normalization.begin() as normalization_transaction:
            observed_normalization = normalization_transaction

        # Assert
        self.assertEqual((outcome, outcome, outcome), refusal_outcomes)
        self.assertEqual(
            (
                StoreAuthorizationTransaction,
                StoreApprovalIngressTransaction,
                StoreResultTransaction,
                StoreNormalizationTransaction,
            ),
            tuple(
                type(capability)
                for capability in (
                    observed_authorization,
                    observed_approval,
                    observed_result,
                    observed_normalization,
                )
            ),
        )
        self.assertEqual(
            [call.record(fact), call.record(fact), call.record(fact)], refusals.mock_calls
        )


class OutboxAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_staged_and_ambiguous_reads_preserve_state_and_confirmation_semantics(
        self,
    ) -> None:
        # Arrange
        raw = _Outbox()
        adapter = StoreCommandOutbox(cast("CommandOutboxTransactions", raw))

        # Act
        staged = await adapter.pending(50)
        ambiguous = await adapter.reconciliation(50)
        await adapter.record(
            COMMAND.command_id,
            OutboxState.STAGED,
            OutboxEvent.CONFIRM,
            "2026-08-25T12:01:01.000Z",
        )
        await adapter.record(
            COMMAND.command_id,
            OutboxState.STAGED,
            OutboxEvent.AMBIGUOUS,
            None,
        )

        # Assert
        self.assertEqual(
            (
                OutboxState.STAGED,
                OutboxState.RECONCILIATION_NEEDED,
                COMMAND,
                COMMAND,
                [50],
                [50],
                [
                    (COMMAND.command_id, OutboxState.STAGED, OutboxEvent.CONFIRM),
                    (COMMAND.command_id, OutboxState.STAGED, OutboxEvent.AMBIGUOUS),
                ],
            ),
            (
                staged[0].state,
                ambiguous[0].state,
                staged[0].command,
                ambiguous[0].command,
                raw.pending_limits,
                raw.reconciliation_limits,
                raw.moves,
            ),
        )

    async def test_confirmation_instants_and_ambiguity_are_not_interchangeable(self) -> None:
        # Arrange
        adapter = StoreCommandOutbox(cast("CommandOutboxTransactions", _Outbox()))
        cases = (
            (OutboxEvent.CONFIRM, None),
            (OutboxEvent.CONFIRM, "not-an-instant"),
            (OutboxEvent.AMBIGUOUS, "2026-08-25T12:01:01.000Z"),
        )

        # Act
        refusals = []
        for event, confirmed_at in cases:
            with pytest.raises(StoreAdapterError) as captured:
                await adapter.record(COMMAND.command_id, OutboxState.STAGED, event, confirmed_at)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([StoreAdapterRefusal.CONFIRMATION] * 3, refusals)

    async def test_application_outbox_wraps_each_read_and_move_in_one_transaction(
        self,
    ) -> None:
        # Arrange
        sessions: list[_DurableSession] = []

        def factory() -> _DurableSession:
            session = _DurableSession()
            sessions.append(session)
            return session

        staged = cast("StagedApplicationEvent", object())
        identity = ApplicationEventIdentity("command-gateway", "event-1")
        adapter = StoreApplicationOutbox(cast("StoreSessionFactory", factory))

        # Act
        with (
            patch(
                "aerial_rescue_command_gateway.store_adapter.pending_application",
                new_callable=AsyncMock,
                return_value=(staged,),
            ) as pending,
            patch(
                "aerial_rescue_command_gateway.store_adapter.reconciliation_application",
                new_callable=AsyncMock,
                return_value=(staged,),
            ) as reconciliation,
            patch(
                "aerial_rescue_command_gateway.store_adapter.record_application_publication",
                new_callable=AsyncMock,
            ) as record,
        ):
            observed = (
                await adapter.pending("command-gateway"),
                await adapter.reconciliation("command-gateway"),
            )
            await adapter.record(
                identity,
                OutboxEvent.CONFIRM,
                "2026-08-25T12:01:01.000Z",
            )

        # Assert
        self.assertEqual(((staged,), (staged,)), observed)
        self.assertEqual([(1, 0, 1)] * 3, [(s.commits, s.rollbacks, s.closes) for s in sessions])
        pending.assert_awaited_once_with(sessions[0], "command-gateway")
        reconciliation.assert_awaited_once_with(sessions[1], "command-gateway")
        record.assert_awaited_once_with(
            sessions[2],
            identity,
            OutboxState.STAGED,
            OutboxEvent.CONFIRM,
            "2026-08-25T12:01:01.000Z",
        )

    async def test_progress_recorder_preserves_the_exact_transition_request(self) -> None:
        # Arrange
        raw = AsyncMock(spec=CommandProgressTransactions)
        current = cast("StoredCommandProgress", object())
        became = cast("StoredCommandProgress", object())
        facts = cast("TransitionFacts", object())
        raw.transition.return_value = became
        adapter = StoreProgressRecorder(raw)

        # Act
        observed = await adapter.transition(
            current,
            CommandEvent.SEND,
            SendBudget(5),
            facts,
        )

        # Assert
        self.assertIs(became, observed)
        raw.transition.assert_awaited_once_with(
            current,
            CommandEvent.SEND,
            SendBudget(5),
            facts,
        )


class CompositionTests(unittest.TestCase):
    def test_application_store_composition_is_lazy_and_exposes_every_runtime_capability(
        self,
    ) -> None:
        # Arrange
        opened = 0

        def factory() -> object:
            nonlocal opened
            opened += 1
            return object()

        # Act
        application = compose_application_store(
            cast("StoreSessionFactory", factory),
            lambda: "2026-08-25T12:00:00.000Z",
        )

        # Assert
        self.assertEqual(
            (
                0,
                (
                    StoreAuthorizationUnitOfWork,
                    StoreApprovalIngressUnitOfWork,
                    StoreResultUnitOfWork,
                    StoreNormalizationUnitOfWork,
                    StoreCommandOutbox,
                    StoreApplicationOutbox,
                    StoreProgressRecorder,
                    StoreRefusalPersistence,
                ),
            ),
            (
                opened,
                tuple(
                    type(capability)
                    for capability in (
                        application.authorization,
                        application.approval_ingress,
                        application.results,
                        application.normalization,
                        application.outbox,
                        application.application_outbox,
                        application.progress,
                        application.refusals,
                    )
                ),
            ),
        )

    def test_composition_binds_each_repository_and_shared_refusal_dependency_exactly(self) -> None:
        # Arrange
        def unopened_factory() -> object:
            return object()

        def observed_at() -> str:
            return "2026-08-25T12:00:00.000Z"

        factory = cast("StoreSessionFactory", unopened_factory)
        repositories = [object() for _ in range(7)]

        # Act
        with (
            patch(
                "aerial_rescue_command_gateway.store_adapter.CommandAuthorizationTransactions",
                return_value=repositories[0],
            ) as authorization_constructor,
            patch(
                "aerial_rescue_command_gateway.store_adapter.ApprovalIngressTransactions",
                return_value=repositories[1],
            ) as approval_constructor,
            patch(
                "aerial_rescue_command_gateway.store_adapter.CommandResultTransactions",
                return_value=repositories[2],
            ) as result_constructor,
            patch(
                "aerial_rescue_command_gateway.store_adapter.NormalizationTransactions",
                return_value=repositories[3],
            ) as normalization_constructor,
            patch(
                "aerial_rescue_command_gateway.store_adapter.CommandOutboxTransactions",
                return_value=repositories[4],
            ) as outbox_constructor,
            patch(
                "aerial_rescue_command_gateway.store_adapter.CommandProgressTransactions",
                return_value=repositories[5],
            ) as progress_constructor,
            patch(
                "aerial_rescue_command_gateway.store_adapter.BrokerRefusalRecorder",
                return_value=repositories[6],
            ) as refusal_constructor,
        ):
            application = compose_application_store(factory, observed_at)

        # Assert
        self.assertEqual(
            [call(factory)] * 6,
            [
                *authorization_constructor.mock_calls,
                *approval_constructor.mock_calls,
                *result_constructor.mock_calls,
                *normalization_constructor.mock_calls,
                *outbox_constructor.mock_calls,
                *progress_constructor.mock_calls,
            ],
        )
        refusal_constructor.assert_called_once_with(factory, observed_at)
        self.assertEqual(
            (
                {
                    "_transactions": repositories[0],
                    "_refusals": repositories[6],
                },
                {
                    "_transactions": repositories[1],
                    "_refusals": repositories[6],
                },
                {
                    "_transactions": repositories[2],
                    "_refusals": repositories[6],
                },
                {"_transactions": repositories[3]},
                {"_transactions": repositories[4]},
                {"_session_factory": factory},
                {"_transactions": repositories[5]},
                {"_refusals": repositories[6]},
            ),
            tuple(
                vars(capability)
                for capability in (
                    application.authorization,
                    application.approval_ingress,
                    application.results,
                    application.normalization,
                    application.outbox,
                    application.application_outbox,
                    application.progress,
                    application.refusals,
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()
