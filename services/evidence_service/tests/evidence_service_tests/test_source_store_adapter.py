"""Evidence-service adaptation of source-ingestion SQLAlchemy transactions."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, cast, override

from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_evidence_service.store_adapter import StoreSourceUnitOfWork
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import StoredSourceEvidenceFact
from aerial_rescue_store.processing.source_ingress import SourceProcessingTransactions

if TYPE_CHECKING:
    from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder


@dataclass
class _Transaction:
    """Record every source-store operation."""

    calls: list[str] = field(default_factory=list)

    async def claim(self, _identity: InboxIdentity) -> InboxOutcome:
        """Return one new inbox claim."""
        self.calls.append("claim")
        return InboxOutcome(InboxDecision.CLAIMED, None)

    async def record_source(
        self,
        _event: StoredSourceEvent,
        _facts: tuple[StoredSourceEvidenceFact, ...],
    ) -> None:
        """Record source provenance."""
        self.calls.append("record-source")

    async def complete(
        self,
        _identity: InboxIdentity,
        _result: bytes,
        _processed_at: str,
    ) -> None:
        """Complete source work."""
        self.calls.append("complete")


class _Context(AbstractAsyncContextManager[_Transaction]):
    """Expose one fake source transaction."""

    def __init__(self, transaction: _Transaction, lifecycle: list[str]) -> None:
        """Retain the fake and lifecycle log."""
        self.transaction = transaction
        self.lifecycle = lifecycle

    @override
    async def __aenter__(self) -> _Transaction:
        """Record transaction entry."""
        self.lifecycle.append("begin")
        return self.transaction

    @override
    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Record commit or rollback."""
        self.lifecycle.append("commit" if exception_type is None else "rollback")


class _Transactions:
    """Return one configured store transaction context."""

    def __init__(self, transaction: _Transaction, lifecycle: list[str]) -> None:
        """Retain the fake and lifecycle log."""
        self.transaction = transaction
        self.lifecycle = lifecycle

    def open(self) -> _Context:
        """Return a fresh context wrapper."""
        return _Context(self.transaction, self.lifecycle)


@dataclass
class _Refusals:
    """Persist body-free refusal candidates."""

    candidates: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def record(self, candidate: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return a deterministic stored refusal."""
        self.candidates.append(candidate)
        stored = StoredBrokerRefusal(
            candidate.consumer,
            candidate.source,
            candidate.family,
            candidate.channel,
            candidate.refusal_code,
            candidate.raw_digest,
            "2026-08-25T12:00:00.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)


async def test_source_operations_and_refusals_delegate_without_a_second_transaction() -> None:
    # Arrange
    lifecycle: list[str] = []
    transaction = _Transaction()
    refusals = _Refusals()
    work = StoreSourceUnitOfWork(
        cast("SourceProcessingTransactions", _Transactions(transaction, lifecycle)),
        cast("BrokerRefusalRecorder", refusals),
    )
    identity = InboxIdentity("evidence-service", "source", "event-1", "mission-1", "1" * 64)
    event = StoredSourceEvent(
        "source",
        "event-1",
        "mission-1",
        "aerial-rescue/v1/mission-1/drone/drone-1/event/salient",
        "2" * 64,
        b"{}",
        "2026-08-25T12:00:00.000Z",
    )
    fact = StoredSourceEvidenceFact(
        "evidence-1",
        "drone-1",
        ObservationOrigin.LIVE_SENSOR,
        "3" * 64,
        b"{}",
        {},
        event.observed_at,
    )
    refusal = BrokerRefusalCandidate(
        "evidence-service", None, "drone.event", "source", "invalid", "4" * 64
    )

    # Act
    async with work.begin() as opened:
        claim = await opened.claim(identity)
        await opened.record_source(event, (fact,))
        await opened.complete(identity, b"{}", event.observed_at)
    refused = await work.refuse(refusal)

    # Assert
    assert (
        claim.decision,
        lifecycle,
        transaction.calls,
        refused.decision,
        refusals.candidates,
    ) == (
        InboxDecision.CLAIMED,
        ["begin", "commit"],
        ["claim", "record-source", "complete"],
        BrokerRefusalDecision.STORED,
        [refusal],
    )
