"""Typed persistence, provenance, settlement, and publication seams."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol

from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome
from aerial_rescue_store.evidence import StoredEvidenceDecision, StoredEvidenceItem
from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import StoredSourceEvidenceFact
from aerial_rescue_store.proposals import StoredProposal


@dataclass(frozen=True)
class InboundDelivery:
    """One guaranteed proposal delivery after broker-level canonical validation."""

    topic: str
    payload: bytes
    canonical_digest: str


@dataclass(frozen=True)
class ProvenanceFact:
    """One persisted observation and the complete document its digest covers."""

    evidence_item_id: str
    source_id: str
    origin: ObservationOrigin
    provenance_digest: str
    document: Mapping[str, object]
    observed_at: str


@dataclass(frozen=True)
class SourceEvidence:
    """The durable source event and the observations derived from it."""

    topic: str
    event: bytes
    observations: tuple[ProvenanceFact, ...]


@dataclass(frozen=True)
class DecisionStamp:
    """All identifiers, ordering, time, and tracing minted outside model control."""

    producer_id: str
    decision_id: str
    decision_event_id: str
    audit_record_id: str
    audit_event_id: str
    decided_at: str
    decision_sequence: int
    audit_sequence: int
    traceparent: str


class ProvenancePort(Protocol):
    """Read the exact durable source event without creating a missing claim."""

    async def source_for(
        self,
        mission_id: str,
        source_event_id: str,
    ) -> SourceEvidence | None:
        """Return one mission-bound source fact without writing any store row."""


class EvidenceTransaction(ProvenancePort, Protocol):
    """Every effect that must commit before proposal settlement."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the proposal event or return its exact prior result."""

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Return the immutable authoritative proposal."""

    async def record_item(self, item: StoredEvidenceItem) -> None:
        """Persist one immutable evidence item."""

    async def record_decision(self, decision: StoredEvidenceDecision) -> None:
        """Persist one append-only evidence decision."""

    async def append_audit(self, record: AuditRecord) -> int:
        """Append one authoritative mission-ordered audit row."""

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact application publication."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the proposal inbox claim with its exact durable result."""


class TransactionContext(Protocol):
    """An async transaction that commits only on successful exit."""

    async def __aenter__(self) -> EvidenceTransaction:
        """Return the transaction's typed operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success and roll back on any exception."""


class EvidenceUnitOfWork(Protocol):
    """Construct one processing transaction."""

    def begin(self) -> TransactionContext:
        """Return a fresh transaction context."""

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed-ingress evidence in a separate transaction."""


class SourceTransaction(Protocol):
    """Every effect that must commit before a salient source event is settled."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim the source event or return its exact durable prior result."""

    async def record_source(
        self,
        event: StoredSourceEvent,
        facts: tuple[StoredSourceEvidenceFact, ...],
    ) -> None:
        """Persist the exact source event and complete initial sensor provenance."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the source inbox claim with its exact durable result."""


class SourceTransactionContext(Protocol):
    """An async source transaction that commits only on successful exit."""

    async def __aenter__(self) -> SourceTransaction:
        """Return the transaction's typed source operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success and roll back on any exception."""


class SourceUnitOfWork(Protocol):
    """Construct source-ingestion transactions and durable refusal records."""

    def begin(self) -> SourceTransactionContext:
        """Return a fresh source-ingestion transaction context."""

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed source-ingress evidence independently."""


class SettlementPort(Protocol):
    """Settle one guaranteed delivery after its transaction committed."""

    async def accept(self, event_id: str) -> None:
        """Accept one broker event identity."""

    async def reject(self) -> None:
        """Reject malformed ingress only after its bounded evidence commits."""
