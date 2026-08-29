"""Evidence-service transaction composition over purpose-specific store repositories."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Final, cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_domain.evidence import EvidenceState
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_domain.scoring import EvidenceBand, ObservationOrigin
from aerial_rescue_store.application_outbox import ApplicationEventIdentity, StagedApplicationEvent
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.evidence import (
    EvidenceDecisionOutcome,
    StoredEvidenceDecision,
    StoredEvidenceItem,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.evidence import (
    EvidenceApplicationOutbox,
    EvidenceProcessingTransactions,
)
from aerial_rescue_store.processing.source_evidence import StoredSourceEvidence
from aerial_rescue_store.proposals import StoredProposal
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.unit]

IDENTITY: Final = InboxIdentity(
    consumer="evidence-service",
    source="urn:aerial-rescue:command-gateway:gateway-1",
    event_id="proposal-event-1",
    mission_id="mission-1",
    canonical_digest="1" * 64,
)
PROPOSAL: Final = StoredProposal(
    proposal_id="proposal-1",
    mission_id="mission-1",
    source_event_id="source-event-1",
    source_event_digest="2" * 64,
    agent_name="VisionAgent",
    invocation_id="invocation-1",
    proposal_type="candidate-location",
    proposal_digest="3" * 64,
    payload=b'{"proposal":"accepted"}',
    drone_id="drone-1",
    latitude_microdegrees=47_123_901,
    longitude_microdegrees=-122_653_114,
    command_type="escalate-rescue",
    issued_at="2026-08-25T12:00:00.000Z",
    sequence=1,
    correlation_id="correlation-1",
    causation_id="source-event-1",
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
)
SOURCE: Final = StoredSourceEvidence(
    topic="aerial-rescue/v1/mission-1/drone/drone-1/event/salient",
    canonical_event=b'{"source":"accepted"}',
    facts=(),
)
ITEM: Final = StoredEvidenceItem(
    evidence_id="evidence-1",
    mission_id="mission-1",
    proposal_id="proposal-1",
    source_id="sensor-1",
    source_kind=ObservationOrigin.LIVE_SENSOR,
    lifecycle=EvidenceState.CONTRIBUTING,
    provenance_digest="4" * 64,
    payload=b'{"evidence":"accepted"}',
    observed_at="2026-08-25T12:00:00.500Z",
)
DECISION: Final = StoredEvidenceDecision(
    decision_id="decision-1",
    mission_id="mission-1",
    proposal_id="proposal-1",
    proposal_digest=PROPOSAL.proposal_digest,
    decision_digest="5" * 64,
    decision_version=1,
    score_version=1,
    score=75,
    band=EvidenceBand.CORROBORATED,
    outcome=EvidenceDecisionOutcome.CONTRIBUTING,
    contributors=b"[]",
    payload=b'{"decision":"accepted"}',
    decided_at="2026-08-25T12:00:01.000Z",
    sequence=2,
)
AUDIT: Final = AuditRecord(
    mission_id="mission-1",
    kind="evidence-decision",
    occurred_at=DECISION.decided_at,
    payload=b'{"audit":"accepted"}',
    correlation_id=PROPOSAL.correlation_id,
    causation_id=IDENTITY.event_id,
    traceparent=PROPOSAL.traceparent,
)
EVENT: Final = StagedApplicationEvent(
    producer="evidence-service",
    event_id="decision-event-1",
    family="evidence-decision",
    topic="aerial-rescue/v1/mission-1/evidence/decision/proposal-1",
    headers=b"{}",
    payload=DECISION.payload,
    traceparent=PROPOSAL.traceparent,
    tracestate=None,
    correlation_id=PROPOSAL.correlation_id,
    causation_id=IDENTITY.event_id,
    staged_at=DECISION.decided_at,
)


@dataclass
class _Session:
    """Record transaction finalization without opening PostgreSQL."""

    calls: list[str] = field(default_factory=list)

    async def commit(self) -> None:
        """Record commit."""
        self.calls.append("commit")

    async def rollback(self) -> None:
        """Record rollback."""
        self.calls.append("rollback")

    async def close(self) -> None:
        """Record release."""
        self.calls.append("close")


def _factory(session: _Session) -> AsyncSession:
    """Expose one deterministic fake through the injected SQLAlchemy session type."""
    return cast("AsyncSession", session)


class EvidenceProcessingTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_evidence_effects_share_one_session_and_commit_together(self) -> None:
        # Arrange
        session = _Session()
        claimed = InboxOutcome(InboxDecision.CLAIMED, None)

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.evidence.claim",
                AsyncMock(return_value=claimed),
            ) as claim,
            patch(
                "aerial_rescue_store.processing.evidence.load_proposal",
                AsyncMock(return_value=PROPOSAL),
            ) as load_proposal,
            patch(
                "aerial_rescue_store.processing.evidence.load_source_evidence",
                AsyncMock(return_value=SOURCE),
            ) as load_source,
            patch(
                "aerial_rescue_store.processing.evidence.record_item", AsyncMock()
            ) as record_item,
            patch(
                "aerial_rescue_store.processing.evidence.record_decision",
                AsyncMock(),
            ) as record_decision,
            patch(
                "aerial_rescue_store.processing.evidence.append",
                AsyncMock(return_value=9),
            ) as append,
            patch("aerial_rescue_store.processing.evidence.stage", AsyncMock()) as stage,
            patch("aerial_rescue_store.processing.evidence.complete", AsyncMock()) as complete,
        ):
            transactions = EvidenceProcessingTransactions(lambda: _factory(session))
            async with transactions.open() as transaction:
                outcome = await transaction.claim(IDENTITY)
                proposal = await transaction.load_proposal(PROPOSAL.proposal_id)
                source = await transaction.load_source(
                    IDENTITY.mission_id, PROPOSAL.source_event_id
                )
                await transaction.record_item(ITEM)
                await transaction.record_decision(DECISION)
                ordinal = await transaction.append_audit(AUDIT)
                await transaction.stage(EVENT)
                await transaction.complete(IDENTITY, DECISION.payload, DECISION.decided_at)

        # Assert
        self.assertEqual(
            (
                claimed,
                PROPOSAL,
                SOURCE,
                9,
                ["commit", "close"],
                (session,) * 8,
            ),
            (
                outcome,
                proposal,
                source,
                ordinal,
                session.calls,
                (
                    claim.await_args_list[0].args[0],
                    load_proposal.await_args_list[0].args[0],
                    load_source.await_args_list[0].args[0],
                    record_item.await_args_list[0].args[0],
                    record_decision.await_args_list[0].args[0],
                    append.await_args_list[0].args[0],
                    stage.await_args_list[0].args[0],
                    complete.await_args_list[0].args[0],
                ),
            ),
        )

    async def test_repository_failure_rolls_back_every_effect_and_never_commits(self) -> None:
        # Arrange
        session = _Session()
        failure = RuntimeError("injected decision insert failure")

        # Act
        with patch(
            "aerial_rescue_store.processing.evidence.record_decision",
            AsyncMock(side_effect=failure),
        ):
            transactions = EvidenceProcessingTransactions(lambda: _factory(session))
            with pytest.raises(RuntimeError) as captured:
                async with transactions.open() as transaction:
                    await transaction.record_decision(DECISION)

        # Assert
        self.assertEqual((failure, ["rollback", "close"]), (captured.value, session.calls))

    async def test_each_recovery_attempt_uses_a_fresh_transaction_and_preserves_duplicate_result(
        self,
    ) -> None:
        # Arrange
        first_session = _Session()
        recovered_session = _Session()
        sessions = [first_session, recovered_session]
        first = InboxOutcome(InboxDecision.CLAIMED, None)
        duplicate = InboxOutcome(InboxDecision.DUPLICATE, DECISION.payload)

        # Act
        with patch(
            "aerial_rescue_store.processing.evidence.claim",
            AsyncMock(side_effect=(first, duplicate)),
        ):
            transactions = EvidenceProcessingTransactions(lambda: _factory(sessions.pop(0)))
            async with transactions.open() as transaction:
                first_outcome = await transaction.claim(IDENTITY)
            async with transactions.open() as transaction:
                recovered = await transaction.claim(IDENTITY)

        # Assert
        self.assertEqual(
            (
                InboxDecision.CLAIMED,
                duplicate,
                0,
                ["commit", "close"],
                ["commit", "close"],
            ),
            (
                first_outcome.decision,
                recovered,
                len(sessions),
                first_session.calls,
                recovered_session.calls,
            ),
        )


class EvidenceApplicationOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_reads_and_each_publication_outcome_use_fresh_transactions(self) -> None:
        # Arrange
        pending_session = _Session()
        outcome_session = _Session()
        sessions = [pending_session, outcome_session]
        identity = ApplicationEventIdentity(EVENT.producer, EVENT.event_id)

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.evidence.pending",
                AsyncMock(return_value=(EVENT,)),
            ) as pending,
            patch(
                "aerial_rescue_store.processing.evidence.record_publication",
                AsyncMock(return_value=OutboxState.CONFIRMED),
            ) as record,
        ):
            outbox = EvidenceApplicationOutbox(lambda: _factory(sessions.pop(0)))
            recovered = await outbox.pending(EVENT.producer)
            await outbox.record(
                identity,
                OutboxEvent.CONFIRM,
                "2026-08-25T12:00:02.000Z",
            )

        # Assert
        self.assertEqual(
            (
                (EVENT,),
                0,
                ["commit", "close"],
                ["commit", "close"],
                (pending_session, EVENT.producer),
                (
                    outcome_session,
                    identity,
                    OutboxState.STAGED,
                    OutboxEvent.CONFIRM,
                    "2026-08-25T12:00:02.000Z",
                ),
            ),
            (
                recovered,
                len(sessions),
                pending_session.calls,
                outcome_session.calls,
                pending.await_args_list[0].args,
                record.await_args_list[0].args,
            ),
        )


if __name__ == "__main__":
    unittest.main()
