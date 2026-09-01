"""Adapt package-store evidence transactions to the service processing port."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType

from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome
from aerial_rescue_store.evidence import StoredEvidenceDecision, StoredEvidenceItem
from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.evidence import (
    EvidenceProcessingTransaction,
    EvidenceProcessingTransactions,
)
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import (
    StoredSourceEvidence,
    StoredSourceEvidenceFact,
)
from aerial_rescue_store.processing.source_ingress import (
    SourceProcessingTransaction,
    SourceProcessingTransactions,
)
from aerial_rescue_store.proposals import StoredProposal

from aerial_rescue_evidence_service.ports import ProvenanceFact, SourceEvidence


class StoreEvidenceTransaction:
    """Map one store transaction without adding another persistence boundary."""

    def __init__(self, transaction: EvidenceProcessingTransaction) -> None:
        """Retain the package-owned typed repository transaction."""
        self._transaction = transaction

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Delegate broker inbox identity and digest handling to the store."""
        return await self._transaction.claim(identity)

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Delegate immutable proposal loading to the store."""
        return await self._transaction.load_proposal(proposal_id)

    async def source_for(
        self,
        mission_id: str,
        source_event_id: str,
    ) -> SourceEvidence | None:
        """Map the exact durable source representation into the service boundary."""
        source = await self._transaction.load_source(mission_id, source_event_id)
        return _source(source)

    async def record_item(self, item: StoredEvidenceItem) -> None:
        """Delegate immutable item persistence to the store."""
        await self._transaction.record_item(item)

    async def record_decision(self, decision: StoredEvidenceDecision) -> None:
        """Delegate append-only decision persistence to the store."""
        await self._transaction.record_decision(decision)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Delegate exact application-outbox staging to the store."""
        await self._transaction.stage(event)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Delegate exact inbox-result completion to the store."""
        await self._transaction.complete(identity, result, processed_at)


class StoreEvidenceContext:
    """Preserve the package-store context's commit and rollback behavior."""

    def __init__(
        self,
        context: AbstractAsyncContextManager[EvidenceProcessingTransaction],
    ) -> None:
        """Retain one not-yet-entered store transaction context."""
        self._context = context

    async def __aenter__(self) -> StoreEvidenceTransaction:
        """Enter the store transaction and expose only evidence operations."""
        transaction = await self._context.__aenter__()
        return StoreEvidenceTransaction(transaction)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Forward clean or exceptional exit to the store's transaction boundary."""
        return await self._context.__aexit__(exception_type, exception, traceback)


class StoreEvidenceUnitOfWork:
    """Build service transactions over the injected SQLAlchemy-store composition."""

    def __init__(
        self,
        transactions: EvidenceProcessingTransactions,
        refusals: BrokerRefusalRecorder,
    ) -> None:
        """Retain a lazy package-store transaction factory."""
        self._transactions = transactions
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed proposal evidence in its independent transaction."""
        return await self._refusals.record(fact)

    def begin(self) -> StoreEvidenceContext:
        """Return a fresh evidence transaction without opening it early."""
        return StoreEvidenceContext(self._transactions.open())


class StoreSourceTransaction:
    """Map one package-store source transaction without another boundary."""

    def __init__(self, transaction: SourceProcessingTransaction) -> None:
        """Retain the package-owned source transaction."""
        self._transaction = transaction

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Delegate source inbox identity handling to the store."""
        return await self._transaction.claim(identity)

    async def record_source(
        self,
        event: StoredSourceEvent,
        facts: tuple[StoredSourceEvidenceFact, ...],
    ) -> None:
        """Delegate exact event and immutable sensor provenance persistence."""
        await self._transaction.record_source(event, facts)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Delegate exact source inbox completion."""
        await self._transaction.complete(identity, result, processed_at)


class StoreSourceContext:
    """Preserve the package-store source context's commit and rollback behavior."""

    def __init__(
        self,
        context: AbstractAsyncContextManager[SourceProcessingTransaction],
    ) -> None:
        """Retain one not-yet-entered store source transaction."""
        self._context = context

    async def __aenter__(self) -> StoreSourceTransaction:
        """Enter the store transaction and expose only source operations."""
        transaction = await self._context.__aenter__()
        return StoreSourceTransaction(transaction)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Forward clean or exceptional exit to the store transaction boundary."""
        return await self._context.__aexit__(exception_type, exception, traceback)


class StoreSourceUnitOfWork:
    """Build source-ingestion transactions over the SQLAlchemy composition."""

    def __init__(
        self,
        transactions: SourceProcessingTransactions,
        refusals: BrokerRefusalRecorder,
    ) -> None:
        """Retain lazy source transactions and the independent refusal recorder."""
        self._transactions = transactions
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed source-ingress evidence independently."""
        return await self._refusals.record(fact)

    def begin(self) -> StoreSourceContext:
        """Return a fresh source transaction without opening it early."""
        return StoreSourceContext(self._transactions.open())


def _source(source: StoredSourceEvidence | None) -> SourceEvidence | None:
    """Preserve canonical source bytes and every verified provenance member."""
    if source is None:
        return None
    observations = tuple(
        ProvenanceFact(
            evidence_item_id=fact.evidence_item_id,
            source_id=fact.source_id,
            origin=fact.origin,
            provenance_digest=fact.provenance_digest,
            document=fact.document,
            observed_at=fact.observed_at,
        )
        for fact in source.facts
    )
    return SourceEvidence(source.topic, source.canonical_event, observations)
