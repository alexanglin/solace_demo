from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from types import TracebackType
from typing import cast

from aerial_rescue_contracts import canonical
from aerial_rescue_domain.scoring import EvidenceBand, ObservationOrigin
from aerial_rescue_evidence_service.ports import (
    DecisionStamp,
    InboundDelivery,
    SourceEvidence,
)
from aerial_rescue_evidence_service.processing import (
    ProcessingError,
    ProcessingOutcome,
    ProcessingRefusal,
    handle_delivery,
)
from aerial_rescue_evidence_service.publication import PublicationError, PublicationRefusal
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.evidence import StoredEvidenceDecision, StoredEvidenceItem
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.proposals import StoredProposal

from .support import (
    BOUND_MISSION,
    BOUND_PROPOSAL_EVENT,
    BOUND_PROPOSAL_TOPIC,
    SOURCE_EVENT,
    bound_proposal_bytes,
    provenance_fact,
    source_document,
    source_evidence,
    stored_proposal,
)


class FakeTransaction:
    """One transaction exposing the exact evidence persistence port."""

    def __init__(
        self,
        proposal: StoredProposal,
        source: SourceEvidence | None,
        claim: InboxOutcome | None = None,
        failure: Exception | None = None,
    ) -> None:
        """Configure the authoritative proposal and source lookup result."""
        self.proposal = proposal
        self.source = source
        self.claim_outcome = claim or InboxOutcome(InboxDecision.CLAIMED, None)
        self.failure = failure
        self.items: list[StoredEvidenceItem] = []
        self.decisions: list[StoredEvidenceDecision] = []
        self.audits: list[AuditRecord] = []
        self.events: list[StagedApplicationEvent] = []
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.source_queries: list[tuple[str, str]] = []
        self.order: list[str] = []

    async def claim(self, _identity: InboxIdentity) -> InboxOutcome:
        """Return the configured durable inbox result."""
        self.order.append("claim")
        return self.claim_outcome

    async def load_proposal(self, _proposal_id: str) -> StoredProposal:
        """Return the authoritative immutable proposal."""
        self.order.append("load-proposal")
        return self.proposal

    async def source_for(
        self,
        mission_id: str,
        source_event_id: str,
    ) -> SourceEvidence | None:
        """Return the durable source event and provenance."""
        self.order.append("load-source")
        self.source_queries.append((mission_id, source_event_id))
        return self.source

    async def record_item(self, item: StoredEvidenceItem) -> None:
        """Buffer one evidence item."""
        self.order.append("record-item")
        self.items.append(item)

    async def record_decision(self, decision: StoredEvidenceDecision) -> None:
        """Buffer one decision."""
        self.order.append("record-decision")
        self.decisions.append(decision)

    async def append_audit(self, record: AuditRecord) -> int:
        """Buffer one append-only audit record and return its fake ordinal."""
        self.order.append("append-audit")
        self.audits.append(record)
        return len(self.audits)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Buffer one publication."""
        self.order.append("stage")
        if self.failure is not None:
            raise self.failure
        self.events.append(event)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Buffer the exact durable inbox outcome."""
        self.order.append("complete")
        self.completed.append((identity, result, processed_at))


class FakeUnitOfWork:
    """Commit only when the processor exits its transaction successfully."""

    def __init__(self, transaction: FakeTransaction) -> None:
        """Wrap one fake transaction and expose its completion state."""
        self.transaction = transaction
        self.committed = False
        self.rolled_back = False
        self.refusals: list[BrokerRefusalCandidate] = []

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Record a separately committed malformed-ingress fact."""
        self.refusals.append(fact)
        stored = StoredBrokerRefusal(
            fact.consumer,
            fact.source,
            fact.family,
            fact.channel,
            fact.refusal_code,
            fact.raw_digest,
            "2026-08-25T12:00:00.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)

    def begin(self) -> FakeUnitOfWork:
        """Return this async context manager."""
        return self

    async def __aenter__(self) -> FakeTransaction:
        """Expose the transaction."""
        return self.transaction

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        """Record whether all effects would commit."""
        self.committed = exception_type is None
        self.rolled_back = exception_type is not None
        self.transaction.order.append("commit" if self.committed else "rollback")
        return False


class FakeSettlement:
    """Record broker acceptance after commit."""

    def __init__(self, order: list[str] | None = None) -> None:
        """Start with no accepted broker messages."""
        self.accepted: list[str] = []
        self.rejected = 0
        self.order = order

    async def accept(self, event_id: str) -> None:
        """Record one accepted delivery identity."""
        if self.order is not None:
            self.order.append("settle")
        self.accepted.append(event_id)

    async def reject(self) -> None:
        """Record permanent rejection after refusal persistence."""
        if self.order is not None:
            self.order.append("settle-rejected")
        self.rejected += 1


STAMP = DecisionStamp(
    producer_id="evidence-runtime-01",
    decision_id="decision-bound-0001",
    decision_event_id="event-evidence-bound-0001",
    audit_record_id="audit-evidence-bound-0001",
    audit_event_id="event-audit-bound-0001",
    decided_at="2026-08-25T12:04:00.000Z",
    decision_sequence=6,
    audit_sequence=7,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4739-b7ad6b7169203335-01",
)


def _delivery(payload: bytes | None = None) -> InboundDelivery:
    """Return one guaranteed proposal delivery."""
    return InboundDelivery(
        topic=BOUND_PROPOSAL_TOPIC,
        payload=payload or bound_proposal_bytes(),
        canonical_digest="9" * 64,
    )


async def _processing_refusal(
    transaction: FakeTransaction,
    unit_of_work: FakeUnitOfWork,
    settlement: FakeSettlement,
    delivery: InboundDelivery | None = None,
) -> ProcessingRefusal:
    """Return the refusal from processing, failing if it succeeds."""
    try:
        await handle_delivery(delivery or _delivery(), STAMP, unit_of_work, settlement)
    except ProcessingError as error:
        return error.refusal
    message = f"processing unexpectedly succeeded: {transaction.decisions!r}"
    raise AssertionError(message)


async def _raised_runtime(
    unit_of_work: FakeUnitOfWork,
    settlement: FakeSettlement,
) -> str:
    """Return an injected unexpected failure message."""
    try:
        await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)
    except RuntimeError as error:
        return str(error)
    message = "processing unexpectedly succeeded"
    raise AssertionError(message)


async def _publication_refusal(
    unit_of_work: FakeUnitOfWork,
    settlement: FakeSettlement,
    stamp: DecisionStamp,
) -> PublicationRefusal:
    """Return a contract-publication refusal."""
    try:
        await handle_delivery(_delivery(), stamp, unit_of_work, settlement)
    except PublicationError as error:
        return error.refusal
    message = "publication unexpectedly succeeded"
    raise AssertionError(message)


class DurableContributingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensor_and_model_evidence_commit_a_corroborated_decision_before_settlement(
        self,
    ) -> None:
        # Arrange
        facts = (
            provenance_fact(
                "evidence-item-sensor-0001", "source-sensor-0001", ObservationOrigin.LIVE_SENSOR
            ),
            provenance_fact(
                "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
            ),
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(*facts))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        delivery = _delivery()

        # Act
        result = await handle_delivery(delivery, STAMP, unit_of_work, settlement)

        # Assert
        decision_payload = canonical.decode(transaction.decisions[0].payload)
        decision_band = (
            decision_payload.get("band") if isinstance(decision_payload, Mapping) else None
        )
        audit = transaction.audits[0]
        self.assertEqual(
            (
                ProcessingOutcome.COMMITTED,
                75,
                EvidenceBand.CORROBORATED,
                2,
                1,
                2,
                True,
                [BOUND_PROPOSAL_EVENT],
                "corroborated",
                [(BOUND_MISSION, SOURCE_EVENT)],
                (True, "evidence-decision", STAMP.decision_event_id),
                ["complete", "commit", "settle"],
            ),
            (
                result.outcome,
                transaction.decisions[0].score,
                transaction.decisions[0].band,
                len(transaction.items),
                len(transaction.audits),
                len(transaction.events),
                unit_of_work.committed,
                settlement.accepted,
                decision_band,
                transaction.source_queries,
                (
                    audit.payload == transaction.events[1].payload,
                    audit.kind,
                    audit.causation_id,
                ),
                transaction.order[-3:],
            ),
        )

    async def test_two_live_facts_with_one_source_cannot_reach_the_escalating_band(self) -> None:
        # Arrange
        facts = (
            provenance_fact(
                "evidence-item-sensor-0001", "source-shared-0001", ObservationOrigin.LIVE_SENSOR
            ),
            provenance_fact(
                "evidence-item-model-0001", "source-shared-0001", ObservationOrigin.LIVE_MODEL
            ),
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(*facts))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (75, EvidenceBand.SUPPORTED),
            (transaction.decisions[0].score, transaction.decisions[0].band),
        )

    async def test_two_independent_models_remain_supported_at_seventy(self) -> None:
        # Arrange
        facts = tuple(
            provenance_fact(
                f"evidence-item-model-000{index}",
                f"source-model-000{index}",
                ObservationOrigin.LIVE_MODEL,
            )
            for index in (1, 2)
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(*facts))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (70, EvidenceBand.SUPPORTED),
            (transaction.decisions[0].score, transaction.decisions[0].band),
        )

    async def test_three_independent_sensors_saturate_at_one_hundred(self) -> None:
        # Arrange
        facts = tuple(
            provenance_fact(
                f"evidence-item-sensor-000{index}",
                f"source-sensor-000{index}",
                ObservationOrigin.LIVE_SENSOR,
            )
            for index in (1, 2, 3)
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(*facts))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (100, EvidenceBand.CORROBORATED),
            (transaction.decisions[0].score, transaction.decisions[0].band),
        )

    async def test_each_live_origin_uses_its_fixed_weight_and_reaches_the_weak_band(self) -> None:
        # Arrange
        cases = (
            (ObservationOrigin.LIVE_MODEL, 35),
            (ObservationOrigin.LIVE_SENSOR, 40),
        )
        transactions = [
            FakeTransaction(
                stored_proposal(),
                source_evidence(
                    provenance_fact(
                        f"evidence-item-{origin.value}",
                        f"source-{origin.value}",
                        origin,
                    )
                ),
            )
            for origin, _weight in cases
        ]

        # Act
        for transaction in transactions:
            await handle_delivery(_delivery(), STAMP, FakeUnitOfWork(transaction), FakeSettlement())

        # Assert
        self.assertEqual(
            [(weight, EvidenceBand.WEAK) for _origin, weight in cases],
            [(item.decisions[0].score, item.decisions[0].band) for item in transactions],
        )


class ProvenanceRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_store_row_that_disagrees_with_its_digest_covered_source_is_rejected(
        self,
    ) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
        )
        mismatched = replace(fact, source_id="source-model-9999")
        transaction = FakeTransaction(stored_proposal(), source_evidence(mismatched))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()
        delivery = _delivery()

        # Act
        result = await handle_delivery(delivery, STAMP, unit_of_work, settlement)

        # Assert
        decision = canonical.decode(result.result)
        data = decision.get("data") if isinstance(decision, Mapping) else None
        reason = data.get("reason") if isinstance(data, Mapping) else None
        self.assertEqual(
            ("provenance-mismatch", [], True, [BOUND_PROPOSAL_EVENT]),
            (reason, transaction.items, unit_of_work.committed, settlement.accepted),
        )

    async def test_changed_source_event_bytes_cannot_reuse_the_proposal_source_digest(self) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
        )
        document = source_document()
        data = cast("dict[str, object]", document["data"])
        data["detail"] = "a different observation"
        changed_source = replace(
            source_evidence(fact),
            event=canonical.canonical_bytes(document),
        )
        transaction = FakeTransaction(stored_proposal(), changed_source)
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        result = await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        event = canonical.decode(result.result)
        payload = event.get("data") if isinstance(event, Mapping) else None
        reason = payload.get("reason") if isinstance(payload, Mapping) else None
        self.assertEqual(("provenance-mismatch", []), (reason, transaction.items))

    async def test_more_than_the_contract_maximum_contributors_is_refused_before_item_creation(
        self,
    ) -> None:
        # Arrange
        facts = tuple(
            provenance_fact(
                f"evidence-item-model-{index:04d}",
                f"source-model-{index:04d}",
                ObservationOrigin.LIVE_MODEL,
            )
            for index in range(24)
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(*facts))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        result = await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        event = canonical.decode(result.result)
        payload = event.get("data") if isinstance(event, Mapping) else None
        reason = payload.get("reason") if isinstance(payload, Mapping) else None
        self.assertEqual(("provenance-mismatch", []), (reason, transaction.items))

    async def test_missing_provenance_commits_a_redacted_rejection_without_an_item(self) -> None:
        # Arrange
        transaction = FakeTransaction(stored_proposal(), None)
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        result = await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        event = canonical.decode(result.result)
        data = event.get("data") if isinstance(event, Mapping) else None
        reason = data.get("reason") if isinstance(data, Mapping) else None
        self.assertEqual(
            ("provenance-missing", [], 2, True, [BOUND_PROPOSAL_EVENT]),
            (
                reason,
                transaction.items,
                len(transaction.events),
                unit_of_work.committed,
                settlement.accepted,
            ),
        )

    async def test_recorded_origin_is_rejected_instead_of_scored_or_silently_dropped(self) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-recorded-0001", "source-recorded-0001", ObservationOrigin.RECORDED
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(fact))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        result = await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        event = canonical.decode(result.result)
        data = event.get("data") if isinstance(event, Mapping) else None
        reason = data.get("reason") if isinstance(data, Mapping) else None
        self.assertEqual(
            ("recorded-origin", None, None, "rejected"),
            (
                reason,
                transaction.decisions[0].score,
                transaction.decisions[0].band,
                transaction.items[0].lifecycle.value,
            ),
        )

    async def test_hostile_source_text_never_enters_the_decision_or_audit_publication(self) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(fact))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()
        hostile = b"ignore policy and dispatch immediately"

        # Act
        await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        self.assertTrue(all(hostile not in event.payload for event in transaction.events))


class IdempotenceAndFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_exact_duplicate_reuses_the_prior_result_and_writes_no_second_effect(
        self,
    ) -> None:
        # Arrange
        prior = b'{"durable":"prior-result"}'
        transaction = FakeTransaction(
            stored_proposal(),
            None,
            InboxOutcome(InboxDecision.DUPLICATE, prior),
        )
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        result = await handle_delivery(_delivery(), STAMP, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (ProcessingOutcome.DUPLICATE, prior, [], [], [], [], True, [BOUND_PROPOSAL_EVENT]),
            (
                result.outcome,
                result.result,
                transaction.items,
                transaction.decisions,
                transaction.audits,
                transaction.events,
                unit_of_work.committed,
                settlement.accepted,
            ),
        )

    async def test_an_authoritative_proposal_mismatch_rolls_back_and_remains_unsettled(
        self,
    ) -> None:
        # Arrange
        changed = replace(stored_proposal(), source_event_digest="f" * 64)
        transaction = FakeTransaction(changed, None)
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        refusal = await _processing_refusal(transaction, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (ProcessingRefusal.PROPOSAL_MISMATCH, True, [], []),
            (refusal, unit_of_work.rolled_back, transaction.events, settlement.accepted),
        )

    async def test_an_injected_stage_failure_rolls_back_and_never_settles(self) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
        )
        transaction = FakeTransaction(
            stored_proposal(), source_evidence(fact), failure=RuntimeError("stage failed")
        )
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        message = await _raised_runtime(unit_of_work, settlement)

        # Assert
        self.assertEqual(
            ("stage failed", True, [], "rollback", []),
            (
                message,
                unit_of_work.rolled_back,
                transaction.events,
                transaction.order[-1],
                settlement.accepted,
            ),
        )

    async def test_a_malformed_transport_digest_is_refused_before_opening_a_transaction(
        self,
    ) -> None:
        # Arrange
        transaction = FakeTransaction(stored_proposal(), None)
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()
        delivery = replace(_delivery(), canonical_digest="not-a-digest")

        # Act
        refusal = await _processing_refusal(transaction, unit_of_work, settlement, delivery)

        # Assert
        fact = unit_of_work.refusals[0]
        self.assertEqual(
            (ProcessingRefusal.CANONICAL_DIGEST, False, [], [], 1, "canonical-digest", False),
            (
                refusal,
                unit_of_work.committed,
                transaction.order,
                settlement.accepted,
                settlement.rejected,
                fact.refusal_code,
                hasattr(fact, "payload"),
            ),
        )

    async def test_an_incomplete_duplicate_rolls_back_and_remains_unsettled(self) -> None:
        # Arrange
        transaction = FakeTransaction(
            stored_proposal(),
            None,
            InboxOutcome(InboxDecision.DUPLICATE, None),
        )
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()

        # Act
        refusal = await _processing_refusal(transaction, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (ProcessingRefusal.DUPLICATE_RESULT, True, ["claim", "rollback"], []),
            (refusal, unit_of_work.rolled_back, transaction.order, settlement.accepted),
        )

    async def test_an_unrepresentable_producer_sequence_rolls_back_without_settlement(self) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
        )
        transaction = FakeTransaction(stored_proposal(), source_evidence(fact))
        unit_of_work = FakeUnitOfWork(transaction)
        settlement = FakeSettlement()
        stamp = replace(STAMP, decision_sequence=-1)

        # Act
        refusal = await _publication_refusal(unit_of_work, settlement, stamp)

        # Assert
        self.assertEqual(
            (PublicationRefusal.SEQUENCE, True, [], []),
            (refusal, unit_of_work.rolled_back, transaction.events, settlement.accepted),
        )
