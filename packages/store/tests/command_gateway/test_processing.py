"""Command-gateway units of work over purpose-specific SQLAlchemy repositories."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Final, cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_domain.commands import CommandEvent, CommandProgress, CommandState, SendBudget
from aerial_rescue_domain.idempotency import IdempotencyKind
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_domain.scoring import EvidenceBand
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    StoredApprovalAuthority,
    StoredApprovalBinding,
)
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.command_progress import (
    CommandIdentity,
    StoredCommandProgress,
    TransitionFacts,
)
from aerial_rescue_store.evidence import EvidenceDecisionOutcome, StoredEvidenceDecision
from aerial_rescue_store.idempotency import ClaimOutcome, StoredClaim
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.outbox import CommandOutboxRecord, StagedCommand
from aerial_rescue_store.pending_invocations import (
    PendingInvocationDecision,
    StoredPendingInvocation,
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
from aerial_rescue_store.proposals import StoredProposal
from sqlalchemy.ext.asyncio import AsyncSession

IDENTITY: Final = InboxIdentity("command-gateway", "source", "event-1", "mission-1", "1" * 64)
APPROVAL: Final = StoredApproval(
    "mission-1",
    "proposal-1",
    ApprovalState.APPROVED,
    "operator-1",
    "2026-08-25T12:00:00.000Z",
    1_000,
    300_000,
    "2" * 64,
)
BINDING: Final = StoredApprovalBinding(
    "approval-1",
    "proposal-1",
    1,
    "decision-1",
    "3" * 64,
    1,
    "approve",
    b'{"commandType":"escalate-rescue"}',
    "dashboard-start-1",
    "gateway-start-1",
    -59_000,
    "2026-08-25T12:05:00.000Z",
)
AUTHORITY_BINDING: Final = StoredApprovalAuthority("gateway-start-1", -59_000)
AUTHORITY: Final = StoredAuthorizationApproval(APPROVAL, BINDING)
PENDING: Final = StoredPendingInvocation(
    "invocation-1", "mission-1", "VisionAgent", "correlation-1", "source-1", "4" * 64
)
PROPOSAL: Final = StoredProposal(
    "proposal-1",
    "mission-1",
    "source-1",
    "4" * 64,
    "VisionAgent",
    "invocation-1",
    "candidate-location",
    "2" * 64,
    b"{}",
    "drone-1",
    1,
    2,
    "escalate-rescue",
    "2026-08-25T12:00:00.000Z",
    1,
    "correlation-1",
    "source-1",
    "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
)
DECISION: Final = StoredEvidenceDecision(
    "decision-1",
    "mission-1",
    "proposal-1",
    "2" * 64,
    "3" * 64,
    1,
    1,
    75,
    EvidenceBand.CORROBORATED,
    EvidenceDecisionOutcome.CONTRIBUTING,
    b"[]",
    b"{}",
    "2026-08-25T12:01:00.000Z",
    1,
)
CLAIM: Final = StoredClaim(
    "command-1",
    IdempotencyKind.COMMAND,
    "5" * 64,
    "mission-1",
    "2026-08-25T12:01:00.000Z",
)
INBOX_OUTCOME: Final = InboxOutcome(InboxDecision.CLAIMED, None)
COMMAND: Final = StagedCommand(
    "command-1",
    "mission-1",
    "drone-1",
    b"{}",
    "correlation-1",
    "event-1",
    PROPOSAL.traceparent,
    "2026-08-25T12:01:00.000Z",
)
APPLICATION_EVENT: Final = StagedApplicationEvent(
    "command-gateway",
    "audit-1",
    "audit",
    "aerial-rescue/v1/mission-1/audit/command-authorization",
    b"{}",
    b"{}",
    PROPOSAL.traceparent,
    None,
    "correlation-1",
    "event-1",
    "2026-08-25T12:01:00.000Z",
)
AUDIT: Final = AuditRecord(
    "mission-1",
    "command-authorization",
    "2026-08-25T12:01:00.000Z",
    b"{}",
    "correlation-1",
    "event-1",
    PROPOSAL.traceparent,
)
PROGRESS: Final = StoredCommandProgress(
    CommandIdentity("command-1", "mission-1", "drone-1"),
    CommandProgress(CommandState.ACCEPTED, 0),
    None,
    None,
    None,
    "2026-08-25T12:01:00.000Z",
)
FACTS: Final = TransitionFacts(
    "2026-08-25T12:01:01.000Z",
    "2026-08-25T12:01:07.000Z",
    None,
    "2026-08-25T12:01:01.000Z",
)


@dataclass
class _Session:
    """Record transaction finalization without opening a database."""

    calls: list[str] = field(default_factory=list)

    async def commit(self) -> None:
        """Record a commit."""
        self.calls.append("commit")

    async def rollback(self) -> None:
        """Record a rollback."""
        self.calls.append("rollback")

    async def close(self) -> None:
        """Record session release."""
        self.calls.append("close")


def _factory(session: _Session) -> AsyncSession:
    """Expose the deterministic session through the injected SQLAlchemy type."""
    return cast("AsyncSession", session)


class AuthorizationTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_authorization_effect_shares_one_session_and_commit(self) -> None:
        # Arrange
        session = _Session()
        claim_outcome = cast("ClaimOutcome", object())

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.command_gateway.claim_inbox",
                AsyncMock(return_value=INBOX_OUTCOME),
            ) as claim_inbox,
            patch(
                "aerial_rescue_store.processing.command_gateway.claim_idempotency",
                AsyncMock(return_value=claim_outcome),
            ) as claim_command,
            patch(
                "aerial_rescue_store.processing.command_gateway.load_proposal",
                AsyncMock(return_value=PROPOSAL),
            ) as load_proposal,
            patch(
                "aerial_rescue_store.processing.command_gateway.load_decision",
                AsyncMock(return_value=DECISION),
            ) as load_decision,
            patch(
                "aerial_rescue_store.processing.command_gateway.load_approval_for_update",
                AsyncMock(return_value=APPROVAL),
            ) as load_approval,
            patch(
                "aerial_rescue_store.processing.command_gateway.load_binding_by_proposal",
                AsyncMock(return_value=BINDING),
            ) as load_binding,
            patch(
                "aerial_rescue_store.processing.command_gateway.persist_approval", AsyncMock()
            ) as persist,
            patch(
                "aerial_rescue_store.processing.command_gateway.append_audit",
                AsyncMock(return_value=4),
            ) as append,
            patch(
                "aerial_rescue_store.processing.command_gateway.stage_application", AsyncMock()
            ) as stage_application,
            patch(
                "aerial_rescue_store.processing.command_gateway.stage_command", AsyncMock()
            ) as stage_command,
            patch(
                "aerial_rescue_store.processing.command_gateway.initialize_progress",
                AsyncMock(return_value=PROGRESS),
            ) as initialize,
            patch(
                "aerial_rescue_store.processing.command_gateway.record_claim_result", AsyncMock()
            ) as record_result,
            patch(
                "aerial_rescue_store.processing.command_gateway.complete_inbox", AsyncMock()
            ) as complete,
        ):
            transactions = CommandAuthorizationTransactions(lambda: _factory(session))
            async with transactions.open() as transaction:
                inbox = await transaction.claim(IDENTITY)
                command_claim = await transaction.claim_command(CLAIM)
                proposal = await transaction.load_proposal(PROPOSAL.proposal_id)
                decision = await transaction.load_decision(DECISION.decision_id)
                approval = await transaction.load_approval(PROPOSAL.proposal_id)
                await transaction.persist_consumed(APPROVAL)
                await transaction.append_audit(AUDIT)
                await transaction.stage_application(APPLICATION_EVENT)
                await transaction.stage_command(COMMAND)
                await transaction.initialize_progress(PROGRESS.identity, PROGRESS.updated_at)
                await transaction.record_result(COMMAND.command_id, b"result")
                await transaction.complete(IDENTITY, b"result", PROGRESS.updated_at)

        # Assert
        repository_calls = (
            claim_inbox,
            claim_command,
            load_proposal,
            load_decision,
            load_approval,
            load_binding,
            persist,
            append,
            stage_application,
            stage_command,
            initialize,
            record_result,
            complete,
        )
        self.assertEqual(
            (
                INBOX_OUTCOME,
                claim_outcome,
                PROPOSAL,
                DECISION,
                AUTHORITY,
                ["commit", "close"],
                (session,) * len(repository_calls),
            ),
            (
                inbox,
                command_claim,
                proposal,
                decision,
                approval,
                session.calls,
                tuple(call.await_args_list[0].args[0] for call in repository_calls),
            ),
        )

    async def test_a_repository_failure_rolls_back_the_complete_authorization_set(self) -> None:
        # Arrange
        session = _Session()
        failure = RuntimeError("injected command stage failure")

        # Act
        with patch(
            "aerial_rescue_store.processing.command_gateway.stage_command",
            AsyncMock(side_effect=failure),
        ):
            transactions = CommandAuthorizationTransactions(lambda: _factory(session))
            with pytest.raises(RuntimeError) as captured:
                async with transactions.open() as transaction:
                    await transaction.stage_command(COMMAND)

        # Assert
        self.assertEqual((failure, ["rollback", "close"]), (captured.value, session.calls))


class OtherTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_result_and_normalization_transactions_commit_their_owned_sets(
        self,
    ) -> None:
        # Arrange
        sessions = (_Session(), _Session(), _Session())

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.command_gateway.claim_inbox",
                AsyncMock(return_value=INBOX_OUTCOME),
            ),
            patch(
                "aerial_rescue_store.processing.command_gateway.load_binding_by_approval",
                AsyncMock(return_value=BINDING),
            ),
            patch(
                "aerial_rescue_store.processing.command_gateway.load_approval_for_update",
                AsyncMock(return_value=APPROVAL),
            ),
            patch(
                "aerial_rescue_store.processing.command_gateway.bind_approval_authority",
                AsyncMock(return_value=ApprovalAuthorityDecision.BOUND),
            ) as bind_authority,
            patch("aerial_rescue_store.processing.command_gateway.complete_inbox", AsyncMock()),
            patch(
                "aerial_rescue_store.processing.command_gateway.load_progress",
                AsyncMock(return_value=PROGRESS),
            ),
            patch(
                "aerial_rescue_store.processing.command_gateway.record_progress",
                AsyncMock(return_value=PROGRESS),
            ),
            patch(
                "aerial_rescue_store.processing.command_gateway.load_pending",
                AsyncMock(return_value=PENDING),
            ),
            patch(
                "aerial_rescue_store.processing.command_gateway.record_pending",
                AsyncMock(return_value=PendingInvocationDecision.STORED),
            ) as record_pending,
            patch("aerial_rescue_store.processing.command_gateway.record_proposal", AsyncMock()),
            patch("aerial_rescue_store.processing.command_gateway.stage_application", AsyncMock()),
        ):
            async with ApprovalIngressTransactions(
                lambda: _factory(sessions[0])
            ).open() as approval:
                authority = await approval.load_binding(BINDING.approval_id)
                await approval.claim(IDENTITY)
                authority_outcome = await approval.bind_authority(
                    BINDING.approval_id,
                    AUTHORITY_BINDING,
                )
                await approval.complete(IDENTITY, b"verified", PROGRESS.updated_at)
            async with CommandResultTransactions(lambda: _factory(sessions[1])).open() as result:
                current = await result.load_progress(PROGRESS.identity.command_id)
                transitioned = await result.transition(
                    current, CommandEvent.SEND, SendBudget(5), FACTS
                )
            async with NormalizationTransactions(
                lambda: _factory(sessions[2])
            ).open() as normalization:
                pending_decision = await normalization.record_pending(PENDING)
                pending = await normalization.load_pending(PENDING.invocation_id)
                await normalization.claim(IDENTITY)
                await normalization.record_proposal(PROPOSAL)
                await normalization.stage(APPLICATION_EVENT)
                await normalization.complete(IDENTITY, b"normalized", PROGRESS.updated_at)
        session_is_bound = bind_authority.await_args_list[0].args[0] is sessions[0]

        # Assert
        self.assertEqual(
            (
                AUTHORITY,
                ApprovalAuthorityDecision.BOUND,
                PROGRESS,
                PROGRESS,
                PendingInvocationDecision.STORED,
                PENDING,
                1,
                True,
                (["commit", "close"],) * 3,
            ),
            (
                authority,
                authority_outcome,
                current,
                transitioned,
                pending_decision,
                pending,
                record_pending.await_count,
                session_is_bound,
                tuple(session.calls for session in sessions),
            ),
        )


class RecoveryTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_outbox_and_progress_recovery_use_fresh_bounded_transactions(self) -> None:
        # Arrange
        sessions = [_Session(), _Session(), _Session(), _Session()]
        records = (CommandOutboxRecord(COMMAND, OutboxState.STAGED),)

        def factory() -> AsyncSession:
            return _factory(sessions.pop(0))

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.command_gateway.read_commands",
                AsyncMock(return_value=records),
            ) as read,
            patch(
                "aerial_rescue_store.processing.command_gateway.record_command_publication",
                AsyncMock(),
            ) as record,
            patch(
                "aerial_rescue_store.processing.command_gateway.record_progress",
                AsyncMock(return_value=PROGRESS),
            ) as transition,
        ):
            outbox = CommandOutboxTransactions(factory)
            staged = await outbox.pending(50)
            ambiguous = await outbox.reconciliation(50)
            await outbox.record(COMMAND.command_id, OutboxState.STAGED, OutboxEvent.CONFIRM)
            progress = await CommandProgressTransactions(factory).transition(
                PROGRESS, CommandEvent.SEND, SendBudget(5), FACTS
            )

        # Assert
        self.assertEqual(
            (
                (records, records),
                PROGRESS,
                [OutboxState.STAGED, OutboxState.RECONCILIATION_NEEDED],
                1,
                1,
            ),
            (
                (staged, ambiguous),
                progress,
                [call.args[1] for call in read.await_args_list],
                record.await_count,
                transition.await_count,
            ),
        )


if __name__ == "__main__":
    unittest.main()
