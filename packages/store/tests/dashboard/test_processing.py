"""Dashboard-owned atomic mutations, inbox completion, and outbox recovery."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Final, cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_domain.idempotency import IdempotencyKind
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.approval_bindings import StoredApprovalBinding
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.audit import StoredAuditRecord
from aerial_rescue_store.evidence import StoredEvidenceDecision
from aerial_rescue_store.idempotency import ClaimOutcome, StoredClaim
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.dashboard import (
    DashboardAuditReader,
    DashboardInboxTransactions,
    DashboardMutationTransactions,
    DashboardOutboxTransactions,
)
from aerial_rescue_store.proposals import StoredProposal
from sqlalchemy.ext.asyncio import AsyncSession

TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"
CLAIM: Final = StoredClaim(
    "123e4567-e89b-42d3-a456-426614174000",
    IdempotencyKind.DASHBOARD_DECISION,
    "1" * 64,
    "mission-1",
    "2026-08-26T12:00:00.000Z",
)
EVENT: Final = StagedApplicationEvent(
    "dashboard-api",
    "event-1",
    "operator-approval",
    "aerial-rescue/v1/mission-1/operator/approval/approve",
    b"{}",
    b"{}",
    TRACEPARENT,
    None,
    "correlation-1",
    None,
    "2026-08-26T12:00:00.000Z",
)
APPROVAL: Final = StoredApproval(
    "mission-1",
    "proposal-1",
    ApprovalState.APPROVED,
    "local-operator",
    "2026-08-26T12:00:00.000Z",
    1_000,
    60_000,
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
    "runtime-1",
    None,
    None,
    "2026-08-26T12:01:00.000Z",
)
IDENTITY: Final = InboxIdentity("dashboard-api", "source-1", "event-1", "mission-1", "4" * 64)


@dataclass
class _Session:
    calls: list[str] = field(default_factory=list)

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")

    async def close(self) -> None:
        self.calls.append("close")


def _factory(session: _Session) -> AsyncSession:
    return cast("AsyncSession", session)


class DashboardMutationTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_decision_claim_approval_binding_event_and_response_share_one_commit(
        self,
    ) -> None:
        # Arrange
        session = _Session()
        claim_outcome = cast("ClaimOutcome", object())

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.dashboard.claim_idempotency",
                AsyncMock(return_value=claim_outcome),
            ) as claim,
            patch(
                "aerial_rescue_store.processing.dashboard.record_approval", AsyncMock()
            ) as approval,
            patch(
                "aerial_rescue_store.processing.dashboard.record_binding", AsyncMock()
            ) as binding,
            patch(
                "aerial_rescue_store.processing.dashboard.stage_application", AsyncMock()
            ) as stage,
            patch(
                "aerial_rescue_store.processing.dashboard.record_claim_result", AsyncMock()
            ) as result,
        ):
            transactions = DashboardMutationTransactions(lambda: _factory(session))
            async with transactions.open() as transaction:
                observed = await transaction.claim(CLAIM)
                await transaction.record_decision(APPROVAL, BINDING)
                await transaction.stage(EVENT)
                await transaction.record_result(CLAIM.idempotency_key, b'{"accepted":true}')

        # Assert
        self.assertIs(claim_outcome, observed)
        self.assertEqual(["commit", "close"], session.calls)
        self.assertEqual(
            (session,) * 5,
            tuple(
                call.await_args_list[0].args[0]
                for call in (claim, approval, binding, stage, result)
            ),
        )

    async def test_failure_rolls_back_the_claim_and_every_staged_effect(self) -> None:
        # Arrange
        session = _Session()
        failure = RuntimeError("injected stage failure")

        # Act
        with patch(
            "aerial_rescue_store.processing.dashboard.stage_application",
            AsyncMock(side_effect=failure),
        ):
            transactions = DashboardMutationTransactions(lambda: _factory(session))
            with pytest.raises(RuntimeError) as captured:
                async with transactions.open() as transaction:
                    await transaction.stage(EVENT)

        # Assert
        self.assertEqual((failure, ["rollback", "close"]), (captured.value, session.calls))

    async def test_authoritative_proposal_and_evidence_reads_share_the_mutation_transaction(
        self,
    ) -> None:
        # Arrange
        session = _Session()
        proposal = cast("StoredProposal", object())
        evidence = cast("StoredEvidenceDecision", object())

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.dashboard.load_proposal_record",
                AsyncMock(return_value=proposal),
            ) as load_proposal,
            patch(
                "aerial_rescue_store.processing.dashboard.load_evidence_record",
                AsyncMock(return_value=evidence),
            ) as load_evidence,
            patch(
                "aerial_rescue_store.processing.dashboard.load_evidence_history",
                AsyncMock(return_value=(evidence,)),
            ) as load_history,
        ):
            transactions = DashboardMutationTransactions(lambda: _factory(session))
            async with transactions.open() as transaction:
                observed = (
                    await transaction.load_proposal("proposal-1"),
                    await transaction.load_evidence_decision("decision-1"),
                    await transaction.load_evidence_decisions("proposal-1"),
                )

        # Assert
        self.assertEqual((proposal, evidence, (evidence,)), observed)
        self.assertEqual(["commit", "close"], session.calls)
        self.assertEqual(
            (session, session, session),
            (
                load_proposal.await_args_list[0].args[0],
                load_evidence.await_args_list[0].args[0],
                load_history.await_args_list[0].args[0],
            ),
        )


class DashboardInboxTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbox_claim_and_completion_commit_before_the_caller_can_settle(self) -> None:
        # Arrange
        session = _Session()
        outcome = InboxOutcome(InboxDecision.CLAIMED, None)

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.dashboard.claim_inbox",
                AsyncMock(return_value=outcome),
            ),
            patch("aerial_rescue_store.processing.dashboard.complete_inbox", AsyncMock()),
        ):
            transactions = DashboardInboxTransactions(lambda: _factory(session))
            async with transactions.open() as transaction:
                observed = await transaction.claim(IDENTITY)
                await transaction.complete(
                    IDENTITY, b'{"auditOrdinal":7}', "2026-08-26T12:00:01.000Z"
                )

        # Assert
        self.assertEqual((outcome, ["commit", "close"]), (observed, session.calls))


class DashboardOutboxTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_publication_state_change_uses_an_independent_short_transaction(
        self,
    ) -> None:
        # Arrange
        sessions: list[_Session] = []

        def factory() -> AsyncSession:
            session = _Session()
            sessions.append(session)
            return _factory(session)

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.dashboard.pending_application",
                AsyncMock(return_value=(EVENT,)),
            ),
            patch(
                "aerial_rescue_store.processing.dashboard.reconcile_application",
                AsyncMock(return_value=()),
            ),
            patch("aerial_rescue_store.processing.dashboard.record_publication", AsyncMock()),
        ):
            outbox = DashboardOutboxTransactions(factory)
            pending = await outbox.pending("dashboard-api")
            ambiguous = await outbox.reconciliation("dashboard-api")
            await outbox.record(
                ApplicationEventIdentity("dashboard-api", "event-1"),
                OutboxEvent.CONFIRM,
                "2026-08-26T12:00:01.000Z",
            )

        # Assert
        self.assertEqual(
            ((EVENT,), (), [["commit", "close"], ["commit", "close"], ["commit", "close"]]),
            (pending, ambiguous, [item.calls for item in sessions]),
        )


class DashboardAuditReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_bounded_keyset_page_uses_a_fresh_short_transaction(self) -> None:
        # Arrange
        session = _Session()
        records = cast("tuple[StoredAuditRecord, ...]", (object(),))

        # Act
        with patch(
            "aerial_rescue_store.processing.dashboard.read_audit_suffix",
            AsyncMock(return_value=records),
        ) as read:
            reader = DashboardAuditReader(lambda: _factory(session))
            observed = await reader.read_after("mission-1", after_ordinal=7, limit=50)

        # Assert
        self.assertEqual((records, ["commit", "close"]), (observed, session.calls))
        self.assertEqual((session, "mission-1", 7, 50), read.await_args_list[0].args)


if __name__ == "__main__":
    unittest.main()
