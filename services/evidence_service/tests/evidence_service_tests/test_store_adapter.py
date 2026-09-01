"""Evidence-service adaptation of the SQLAlchemy store transaction."""

from __future__ import annotations

import unittest
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, cast, override

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_domain.evidence import EvidenceState
from aerial_rescue_domain.scoring import EvidenceBand, ObservationOrigin
from aerial_rescue_evidence_service.store_adapter import StoreEvidenceUnitOfWork
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.evidence import (
    EvidenceDecisionOutcome,
    StoredEvidenceDecision,
    StoredEvidenceItem,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.evidence import EvidenceProcessingTransactions
from aerial_rescue_store.processing.source_evidence import (
    StoredSourceEvidence,
    StoredSourceEvidenceFact,
)
from aerial_rescue_store.proposals import StoredProposal

from .support import (
    BOUND_MISSION,
    BOUND_PROPOSAL,
    SOURCE_EVENT,
    SOURCE_TOPIC,
    provenance_fact,
    source_document,
    stored_proposal,
)

if TYPE_CHECKING:
    from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder


def _stored_source() -> StoredSourceEvidence:
    """Return the package-store representation of one complete provenance fact."""
    fact = provenance_fact(
        "evidence-item-sensor-0001",
        "source-sensor-0001",
        ObservationOrigin.LIVE_SENSOR,
    )
    return StoredSourceEvidence(
        topic=SOURCE_TOPIC,
        canonical_event=canonical.canonical_bytes(source_document()),
        facts=(
            StoredSourceEvidenceFact(
                evidence_item_id=fact.evidence_item_id,
                source_id=fact.source_id,
                origin=fact.origin,
                provenance_digest=fact.provenance_digest,
                canonical_document=canonical.canonical_bytes(fact.document),
                document=fact.document,
                observed_at=fact.observed_at,
            ),
        ),
    )


IDENTITY = InboxIdentity(
    "evidence-service",
    "urn:aerial-rescue:command-gateway:gateway-synthetic-01",
    "event-agent-proposal-bound-0001",
    BOUND_MISSION,
    "9" * 64,
)
ITEM = StoredEvidenceItem(
    "evidence-item-sensor-0001",
    BOUND_MISSION,
    BOUND_PROPOSAL,
    "source-sensor-0001",
    ObservationOrigin.LIVE_SENSOR,
    EvidenceState.CONTRIBUTING,
    "8" * 64,
    b"{}",
    "2026-08-25T12:04:00.000Z",
)
DECISION = StoredEvidenceDecision(
    "decision-bound-0001",
    BOUND_MISSION,
    BOUND_PROPOSAL,
    stored_proposal().proposal_digest,
    "7" * 64,
    1,
    1,
    75,
    EvidenceBand.CORROBORATED,
    EvidenceDecisionOutcome.CONTRIBUTING,
    b"[]",
    b"{}",
    "2026-08-25T12:04:00.000Z",
    6,
)
AUDIT = AuditRecord(
    BOUND_MISSION,
    "evidence-decision",
    DECISION.decided_at,
    b"{}",
    "correlation-bound-0001",
    SOURCE_EVENT,
    "00-4bf92f3577b34da6a3ce929d0e0e4739-b7ad6b7169203335-01",
)
EVENT = StagedApplicationEvent(
    "evidence-service",
    "event-evidence-bound-0001",
    "evidence-decision",
    f"aerial-rescue/v1/{BOUND_MISSION}/evidence/decision/{BOUND_PROPOSAL}",
    b"{}",
    b"{}",
    AUDIT.traceparent,
    None,
    AUDIT.correlation_id,
    AUDIT.causation_id,
    DECISION.decided_at,
)


@dataclass
class FakeStoreTransaction:
    """Record calls made through one store-owned transaction."""

    source: StoredSourceEvidence | None = field(default_factory=_stored_source)
    calls: list[str] = field(default_factory=list)

    async def claim(self, _identity: InboxIdentity) -> InboxOutcome:
        """Claim one new inbox identity."""
        self.calls.append("claim")
        return InboxOutcome(InboxDecision.CLAIMED, None)

    async def load_proposal(self, _proposal_id: str) -> StoredProposal:
        """Return the authoritative proposal."""
        self.calls.append("load-proposal")
        return stored_proposal()

    async def load_source(
        self, _mission_id: str, _source_event_id: str
    ) -> StoredSourceEvidence | None:
        """Return the durable source and provenance facts."""
        self.calls.append("load-source")
        return self.source

    async def record_item(self, _item: StoredEvidenceItem) -> None:
        """Record one evidence item."""
        self.calls.append("record-item")

    async def record_decision(self, _decision: StoredEvidenceDecision) -> None:
        """Record one evidence decision."""
        self.calls.append("record-decision")

    async def append_audit(self, _record: AuditRecord) -> int:
        """Append one audit record."""
        self.calls.append("append-audit")
        return 11

    async def stage(self, _event: StagedApplicationEvent) -> None:
        """Stage one exact application event."""
        self.calls.append("stage")

    async def complete(self, _identity: InboxIdentity, _result: bytes, _processed_at: str) -> None:
        """Complete the inbox claim."""
        self.calls.append("complete")


class FakeStoreContext(AbstractAsyncContextManager[FakeStoreTransaction]):
    """Expose one store transaction and record its context lifecycle."""

    def __init__(self, transaction: FakeStoreTransaction, lifecycle: list[str]) -> None:
        """Retain one fake transaction and shared lifecycle log."""
        self.transaction = transaction
        self.lifecycle = lifecycle

    @override
    async def __aenter__(self) -> FakeStoreTransaction:
        self.lifecycle.append("begin")
        return self.transaction

    @override
    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.lifecycle.append("rollback" if exception_type is not None else "commit")


class FakeStoreTransactions:
    """Construct a context over one scripted store transaction."""

    def __init__(self, transaction: FakeStoreTransaction, lifecycle: list[str]) -> None:
        """Retain one fake transaction and shared lifecycle log."""
        self.transaction = transaction
        self.lifecycle = lifecycle

    def open(self) -> FakeStoreContext:
        return FakeStoreContext(self.transaction, self.lifecycle)


@dataclass
class _Refusals:
    """Record candidates delegated through the evidence store adapter."""

    candidates: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def record(self, candidate: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return one deterministic stored fact."""
        self.candidates.append(candidate)
        fact = StoredBrokerRefusal(
            candidate.consumer,
            candidate.source,
            candidate.family,
            candidate.channel,
            candidate.refusal_code,
            candidate.raw_digest,
            "2026-08-25T12:00:00.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, fact)


REFUSAL = BrokerRefusalCandidate(
    "evidence-service",
    None,
    "agent.proposal",
    "evidence-service-agent-proposal",
    "unreadable",
    "1" * 64,
)


async def _raise_inside(work: StoreEvidenceUnitOfWork, failure: RuntimeError) -> None:
    """Raise one injected failure from inside a fresh adapter transaction."""
    async with work.begin():
        raise failure


class StoreEvidenceUnitOfWorkTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_every_processing_effect_to_one_store_transaction(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        stored = FakeStoreTransaction()
        refusals = _Refusals()
        work = StoreEvidenceUnitOfWork(
            cast("EvidenceProcessingTransactions", FakeStoreTransactions(stored, lifecycle)),
            cast("BrokerRefusalRecorder", refusals),
        )

        # Act
        async with work.begin() as transaction:
            claim = await transaction.claim(IDENTITY)
            proposal = await transaction.load_proposal(BOUND_PROPOSAL)
            source = await transaction.source_for(BOUND_MISSION, SOURCE_EVENT)
            await transaction.record_item(ITEM)
            await transaction.record_decision(DECISION)
            await transaction.stage(EVENT)
            await transaction.complete(IDENTITY, DECISION.payload, DECISION.decided_at)
        refusal = await work.refuse(REFUSAL)

        # Assert
        assert source is not None
        self.assertEqual(
            (
                InboxDecision.CLAIMED,
                stored_proposal(),
                SOURCE_TOPIC,
                "evidence-item-sensor-0001",
                ["begin", "commit"],
                [
                    "claim",
                    "load-proposal",
                    "load-source",
                    "record-item",
                    "record-decision",
                    "stage",
                    "complete",
                ],
                BrokerRefusalDecision.STORED,
                [REFUSAL],
            ),
            (
                claim.decision,
                proposal,
                source.topic,
                source.observations[0].evidence_item_id,
                lifecycle,
                stored.calls,
                refusal.decision,
                refusals.candidates,
            ),
        )

    async def test_absent_provenance_is_preserved_and_exceptional_exit_rolls_back(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        stored = FakeStoreTransaction(source=None)
        work = StoreEvidenceUnitOfWork(
            cast("EvidenceProcessingTransactions", FakeStoreTransactions(stored, lifecycle)),
            cast("BrokerRefusalRecorder", _Refusals()),
        )
        failure = RuntimeError("injected evidence failure")

        # Act
        async with work.begin() as transaction:
            source = await transaction.source_for(BOUND_MISSION, SOURCE_EVENT)
        with pytest.raises(RuntimeError) as captured:
            await _raise_inside(work, failure)

        # Assert
        self.assertEqual(
            (None, failure, ["begin", "commit", "begin", "rollback"], ["load-source"]),
            (source, captured.value, lifecycle, stored.calls),
        )


if __name__ == "__main__":
    unittest.main()
