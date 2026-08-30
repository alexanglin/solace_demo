"""Typed recorder translation into the SQLAlchemy-owned durable store boundary.

The service owns orchestration values and the store owns repository values.  This adapter
performs the complete translation at the composition seam so structural similarity never
silently substitutes for an interface contract.  It opens no connection: the injected store
unit of work remains the single transaction and retains commit/rollback ownership.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from enum import Enum
from typing import TYPE_CHECKING, Protocol, cast

from aerial_rescue_contracts import canonical
from aerial_rescue_domain.mission import (
    MissionError,
    MissionRefusal,
    MissionState,
    event_reaching,
    transition,
)
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard.events import BrokerEvent
from aerial_rescue_store.inbox import (
    InboxDecision as StoreInboxDecision,
)
from aerial_rescue_store.inbox import (
    InboxIdentity,
)
from aerial_rescue_store.inbox import (
    InboxOutcome as StoreInboxOutcome,
)
from aerial_rescue_store.processing.source_events import StoredSourceEvent

from aerial_rescue_recorder.capture import (
    AuditFact,
    InboxDecision,
    InboxFact,
    InboxOutcome,
    RecordingUnitOfWork,
    SourceEventFact,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class StoreAdapterRefusal(Enum):
    """Why a durable store result cannot satisfy the recorder service contract."""

    INBOX_RESULT = "the durable recorder inbox result is not one closed positive ordinal"


class StoreAdapterError(ValueError):
    """A store result refused without retaining or rendering its untrusted bytes."""

    refusal: StoreAdapterRefusal
    value: object

    def __init__(self, refusal: StoreAdapterRefusal, value: object) -> None:
        """Retain only the structured refusal and the already validated event identity."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class StoreRecordingTransaction(Protocol):
    """The purpose-specific package-store transaction this adapter translates into."""

    async def claim_inbox(self, identity: InboxIdentity, /) -> StoreInboxOutcome:
        """Claim an exact broker identity or return its committed binary result."""

    async def record_source_event(self, event: StoredSourceEvent, /) -> object:
        """Persist one complete immutable source event."""

    async def mission_lifecycle(self, mission_id: str, /) -> str:
        """Read the recorder-owned lifecycle under an exclusive row lock."""

    async def transition_mission(self, mission_id: str, expected: str, target: str, /) -> None:
        """Move the locked mission row by compare-and-set on the state that was read."""

    async def append_audit(self, record: AuditRecord, /) -> int:
        """Append one audit fact and return the store-issued ordinal."""

    async def link_broker_event(
        self,
        event: BrokerEvent,
        mission_id: str,
        ordinal: int,
        /,
    ) -> object:
        """Link one appended ordinal to the broker identity that produced it."""

    async def complete_inbox(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
        /,
    ) -> None:
        """Complete an inbox claim with its canonical binary outcome."""


class StoreRecordingTransactions(Protocol):
    """Factory for the store-owned atomic unit of work."""

    def open(self) -> AbstractAsyncContextManager[StoreRecordingTransaction]:
        """Return one fresh transaction context."""


class RecordingTransactionAdapter:
    """Translate service facts into exact package-store repository values."""

    def __init__(self, transaction: StoreRecordingTransaction) -> None:
        """Retain the already-open store transaction without taking ownership of it."""
        self._transaction = transaction

    async def claim_inbox(self, fact: InboxFact, /) -> InboxOutcome:
        """Translate a claim and decode only the closed canonical duplicate result."""
        stored = await self._transaction.claim_inbox(_inbox_identity(fact))
        if stored.decision is StoreInboxDecision.CLAIMED:
            if stored.result is not None:
                raise StoreAdapterError(StoreAdapterRefusal.INBOX_RESULT, fact.event_id)
            return InboxOutcome(InboxDecision.CLAIMED, None)
        return InboxOutcome(
            InboxDecision.DUPLICATE,
            _decode_ordinal(stored.result, fact.event_id),
        )

    async def record_source_event(self, fact: SourceEventFact, /) -> None:
        """Persist a complete source event with its exact arriving topic and bytes."""
        await self._transaction.record_source_event(
            StoredSourceEvent(
                source=fact.source,
                event_id=fact.event_id,
                mission_id=fact.mission_id,
                topic=fact.topic,
                canonical_digest=fact.canonical_digest,
                canonical_payload=fact.canonical_event,
                observed_at=fact.observed_at,
            )
        )

    async def apply_mission_lifecycle(self, mission_id: str, target: MissionState, /) -> None:
        """Apply one domain-approved transition inside the caller's recorder transaction.

        The lifecycle column is the recorder's to move, and the deployed composition never
        moved it: the transition applier lived only in the parallel service composition, so
        a mission stayed `PLANNED` however far its fleet swept. A redelivered event finds
        the state already applied and writes nothing, which is why this is safe to run on
        every delivery rather than only the first.
        """
        current_name = await self._transaction.mission_lifecycle(mission_id)
        try:
            current = MissionState[current_name]
        except KeyError as invalid:
            raise MissionError(MissionRefusal.TRANSITION, current_name) from invalid
        if current is target:
            return
        event = event_reaching(target)
        if event is None or transition(current, event) is not target:
            raise MissionError(MissionRefusal.TRANSITION, (current, target))
        await self._transaction.transition_mission(mission_id, current.name, target.name)

    async def append_audit(self, fact: AuditFact, /) -> int:
        """Append the canonical event bytes as the authoritative audit payload."""
        return await self._transaction.append_audit(
            AuditRecord(
                mission_id=fact.mission_id,
                kind=fact.kind,
                occurred_at=fact.occurred_at,
                payload=fact.canonical_event,
                correlation_id=fact.correlation_id,
                causation_id=fact.causation_id,
                traceparent=fact.traceparent,
            )
        )

    async def link_broker_event(
        self,
        event: BrokerEvent,
        mission_id: str,
        ordinal: int,
        /,
    ) -> None:
        """Persist the provenance row the dashboard snapshot watermark joins against."""
        await self._transaction.link_broker_event(event, mission_id, ordinal)

    async def complete_inbox(
        self,
        fact: InboxFact,
        ordinal: int,
        processed_at: str,
        /,
    ) -> None:
        """Encode the issued ordinal as the store's closed canonical duplicate result."""
        await self._transaction.complete_inbox(
            _inbox_identity(fact),
            canonical.canonical_bytes({"auditOrdinal": ordinal}),
            processed_at,
        )


class RecordingTransactionsAdapter:
    """Expose the store transaction as the recorder service's unit-of-work factory."""

    def __init__(self, transactions: StoreRecordingTransactions) -> None:
        """Retain the lazy store transaction factory without opening a session."""
        self._transactions = transactions

    def open(self) -> RecordingUnitOfWork:
        """Return one translating context over one store-owned transaction."""
        return cast("RecordingUnitOfWork", _open(self._transactions))


@asynccontextmanager
async def _open(
    transactions: StoreRecordingTransactions,
) -> AsyncIterator[RecordingTransactionAdapter]:
    """Preserve the store context's exact commit and rollback behavior."""
    async with transactions.open() as transaction:
        yield RecordingTransactionAdapter(transaction)


def _inbox_identity(fact: InboxFact) -> InboxIdentity:
    """Translate the complete service claim without deriving or dropping a field."""
    return InboxIdentity(
        consumer=fact.consumer,
        source=fact.source,
        event_id=fact.event_id,
        mission_id=fact.mission_id,
        canonical_digest=fact.canonical_digest,
    )


def _decode_ordinal(result: bytes | None, event_id: str) -> int:
    """Decode one canonical, closed, positive audit ordinal or refuse it redacted."""
    try:
        document = canonical.decode(result) if isinstance(result, bytes) else None
        canonical_result = canonical.canonical_bytes(document)
    except (TypeError, ValueError) as error:
        raise StoreAdapterError(StoreAdapterRefusal.INBOX_RESULT, event_id) from error
    if canonical_result != result or not isinstance(document, dict):
        raise StoreAdapterError(StoreAdapterRefusal.INBOX_RESULT, event_id)
    if set(document) != {"auditOrdinal"}:
        raise StoreAdapterError(StoreAdapterRefusal.INBOX_RESULT, event_id)
    ordinal = document["auditOrdinal"]
    if type(ordinal) is not int or ordinal <= 0:
        raise StoreAdapterError(StoreAdapterRefusal.INBOX_RESULT, event_id)
    return ordinal
