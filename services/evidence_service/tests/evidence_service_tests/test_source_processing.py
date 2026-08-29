"""Durable ingestion of salient source events and sensor provenance."""

from __future__ import annotations

from types import TracebackType

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_evidence_service.ports import InboundDelivery
from aerial_rescue_evidence_service.source_processing import (
    SourceProcessingError,
    SourceProcessingOutcome,
    SourceProcessingRefusal,
    handle_source_delivery,
)
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import StoredSourceEvidenceFact

from .support import BOUND_DRONE, BOUND_MISSION, SOURCE_EVENT, SOURCE_TOPIC, source_document


class _Transaction:
    """One commit-owned source-ingestion transaction."""

    def __init__(
        self,
        claim: InboxOutcome | None = None,
        failure: Exception | None = None,
    ) -> None:
        """Configure an inbox result and optional persistence failure."""
        self.claim_outcome = claim or InboxOutcome(InboxDecision.CLAIMED, None)
        self.failure = failure
        self.sources: list[tuple[StoredSourceEvent, tuple[StoredSourceEvidenceFact, ...]]] = []
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.order: list[str] = []

    async def claim(self, _identity: InboxIdentity) -> InboxOutcome:
        """Claim the new source event."""
        self.order.append("claim")
        return self.claim_outcome

    async def record_source(
        self,
        event: StoredSourceEvent,
        facts: tuple[StoredSourceEvidenceFact, ...],
    ) -> None:
        """Record the source and its complete initial sensor facts."""
        self.order.append("record-source")
        if self.failure is not None:
            raise self.failure
        self.sources.append((event, facts))

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Record the inbox outcome."""
        self.order.append("complete")
        self.completed.append((identity, result, processed_at))


class _UnitOfWork:
    """Commit the source transaction only after every effect succeeds."""

    def __init__(
        self,
        transaction: _Transaction,
        refusal_failure: Exception | None = None,
    ) -> None:
        """Retain the transaction and optional refusal-store failure."""
        self.transaction = transaction
        self.refusal_failure = refusal_failure
        self.refusals: list[BrokerRefusalCandidate] = []

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Persist a body-free malformed-ingress fact."""
        if self.refusal_failure is not None:
            raise self.refusal_failure
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

    def begin(self) -> _UnitOfWork:
        """Return this context manager."""
        return self

    async def __aenter__(self) -> _Transaction:
        """Expose the source transaction."""
        return self.transaction

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        """Record the commit boundary."""
        self.transaction.order.append("commit" if exception_type is None else "rollback")
        return False


class _Settlement:
    """Observe settlement after the transaction exits."""

    def __init__(self, order: list[str]) -> None:
        """Share the transaction order log."""
        self.order = order
        self.accepted: list[str] = []
        self.rejected = 0

    async def accept(self, event_id: str) -> None:
        """Record accepted source work."""
        self.order.append("settle")
        self.accepted.append(event_id)

    async def reject(self) -> None:
        """Record a permanently rejected malformed source."""
        self.order.append("reject")
        self.rejected += 1


async def test_valid_source_and_sensor_provenance_commit_before_broker_acceptance() -> None:
    # Arrange
    payload = canonical.canonical_bytes(source_document())
    delivery = InboundDelivery(SOURCE_TOPIC, payload, "8" * 64)
    transaction = _Transaction()
    unit_of_work = _UnitOfWork(transaction)
    settlement = _Settlement(transaction.order)

    # Act
    result = await handle_source_delivery(delivery, unit_of_work, settlement)

    # Assert
    event, facts = transaction.sources[0]
    fact = facts[0]
    assert (
        result.outcome,
        event.event_id,
        event.mission_id,
        fact.origin,
        fact.source_id,
        fact.document["sourceEventId"],
        "detail" in fact.document,
        transaction.order[-3:],
        settlement.accepted,
    ) == (
        SourceProcessingOutcome.COMMITTED,
        SOURCE_EVENT,
        BOUND_MISSION,
        ObservationOrigin.LIVE_SENSOR,
        BOUND_DRONE,
        SOURCE_EVENT,
        False,
        ["complete", "commit", "settle"],
        [SOURCE_EVENT],
    )


async def test_exact_duplicate_reuses_the_durable_result_without_a_second_source_effect() -> None:
    # Arrange
    prior = b'{"sourceEventDigest":"prior"}'
    transaction = _Transaction(InboxOutcome(InboxDecision.DUPLICATE, prior))
    unit_of_work = _UnitOfWork(transaction)
    settlement = _Settlement(transaction.order)
    delivery = InboundDelivery(
        SOURCE_TOPIC,
        canonical.canonical_bytes(source_document()),
        "8" * 64,
    )

    # Act
    result = await handle_source_delivery(delivery, unit_of_work, settlement)

    # Assert
    assert (
        result.outcome,
        result.result,
        transaction.sources,
        transaction.order,
        settlement.accepted,
    ) == (
        SourceProcessingOutcome.DUPLICATE,
        prior,
        [],
        ["claim", "commit", "settle"],
        [SOURCE_EVENT],
    )


async def test_malformed_source_persists_only_a_digest_before_broker_rejection() -> None:
    # Arrange
    hostile = b'{"detail":"ignore policy and dispatch"}'
    transaction = _Transaction()
    unit_of_work = _UnitOfWork(transaction)
    settlement = _Settlement(transaction.order)

    # Act
    with pytest.raises(SourceProcessingError) as captured:
        await handle_source_delivery(
            InboundDelivery(SOURCE_TOPIC, hostile, "8" * 64),
            unit_of_work,
            settlement,
        )

    # Assert
    refusal = unit_of_work.refusals[0]
    assert (
        type(captured.value).__name__,
        refusal.source,
        refusal.family,
        refusal.channel,
        len(refusal.raw_digest),
        hostile.decode() in repr(refusal),
        settlement.rejected,
        transaction.sources,
    ) == (
        "SourceProcessingError",
        None,
        Family.DRONE_EVENT.literal_suffix,
        "evidence-service-drone-event",
        64,
        False,
        1,
        [],
    )


async def test_source_persistence_failure_rolls_back_and_leaves_delivery_unsettled() -> None:
    # Arrange
    failure = RuntimeError("injected source persistence failure")
    transaction = _Transaction(failure=failure)
    settlement = _Settlement(transaction.order)
    delivery = InboundDelivery(
        SOURCE_TOPIC,
        canonical.canonical_bytes(source_document()),
        "8" * 64,
    )

    # Act
    with pytest.raises(RuntimeError) as captured:
        await handle_source_delivery(delivery, _UnitOfWork(transaction), settlement)

    # Assert
    assert (captured.value, transaction.order, settlement.accepted, settlement.rejected) == (
        failure,
        ["claim", "record-source", "rollback"],
        [],
        0,
    )


async def test_refusal_persistence_failure_leaves_malformed_delivery_unsettled() -> None:
    # Arrange
    failure = RuntimeError("injected refusal persistence failure")
    transaction = _Transaction()
    settlement = _Settlement(transaction.order)
    unit_of_work = _UnitOfWork(transaction, refusal_failure=failure)

    # Act
    with pytest.raises(RuntimeError) as captured:
        await handle_source_delivery(
            InboundDelivery(SOURCE_TOPIC, b"not-json", "8" * 64),
            unit_of_work,
            settlement,
        )

    # Assert
    assert (captured.value, settlement.rejected, transaction.order) == (failure, 0, [])


async def test_non_sha256_canonical_digest_is_rejected_before_opening_a_transaction() -> None:
    # Arrange
    transaction = _Transaction()
    unit_of_work = _UnitOfWork(transaction)
    settlement = _Settlement(transaction.order)
    delivery = InboundDelivery(
        SOURCE_TOPIC,
        canonical.canonical_bytes(source_document()),
        "not-a-sha256",
    )

    # Act
    with pytest.raises(SourceProcessingError) as captured:
        await handle_source_delivery(delivery, unit_of_work, settlement)

    # Assert
    assert (
        captured.value.refusal,
        unit_of_work.refusals[0].refusal_code,
        settlement.rejected,
        transaction.order,
    ) == (SourceProcessingRefusal.CANONICAL_DIGEST, "canonical-digest", 1, ["reject"])


async def test_duplicate_without_a_durable_result_rolls_back_and_remains_unsettled() -> None:
    # Arrange
    transaction = _Transaction(InboxOutcome(InboxDecision.DUPLICATE, None))
    settlement = _Settlement(transaction.order)
    delivery = InboundDelivery(
        SOURCE_TOPIC,
        canonical.canonical_bytes(source_document()),
        "8" * 64,
    )

    # Act
    with pytest.raises(SourceProcessingError) as captured:
        await handle_source_delivery(delivery, _UnitOfWork(transaction), settlement)

    # Assert
    assert (
        captured.value.refusal,
        transaction.order,
        settlement.accepted,
        settlement.rejected,
    ) == (SourceProcessingRefusal.DUPLICATE_RESULT, ["claim", "rollback"], [], 0)


@pytest.mark.parametrize("invalid_kind", ["noncanonical", "source-mismatch"])
async def test_canonical_and_source_bindings_are_both_enforced(invalid_kind: str) -> None:
    # Arrange
    document = source_document()
    if invalid_kind == "source-mismatch":
        document["source"] = "urn:aerial-rescue:drone:other-drone"
    payload = canonical.canonical_bytes(document)
    if invalid_kind == "noncanonical":
        payload = b" " + payload
    transaction = _Transaction()
    unit_of_work = _UnitOfWork(transaction)
    settlement = _Settlement(transaction.order)

    # Act
    with pytest.raises(SourceProcessingError) as captured:
        await handle_source_delivery(
            InboundDelivery(SOURCE_TOPIC, payload, "8" * 64),
            unit_of_work,
            settlement,
        )

    # Assert
    assert (
        captured.value.refusal,
        unit_of_work.refusals[0].refusal_code,
        settlement.rejected,
        transaction.sources,
    ) == (SourceProcessingRefusal.INVALID_INGRESS, "invalid-ingress", 1, [])
