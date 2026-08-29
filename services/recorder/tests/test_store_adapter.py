"""Recorder-to-store translation over one atomic SQLAlchemy unit of work."""

from __future__ import annotations

import unittest
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import Final

import pytest
from aerial_rescue_recorder.capture import (
    AuditFact,
    InboxDecision,
    InboxFact,
    SourceEventFact,
)
from aerial_rescue_recorder.store import (
    RecordingTransactionsAdapter,
    StoreAdapterError,
    StoreAdapterRefusal,
)
from aerial_rescue_store.audit import AuditRecord
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

MISSION: Final = "mission-1"
EVENT_ID: Final = "event-1"
OBSERVED_AT: Final = "2026-08-25T12:00:00.250Z"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"

INBOX: Final = InboxFact(
    consumer="recorder",
    source="urn:aerial-rescue:fleet:drone-1",
    event_id=EVENT_ID,
    mission_id=MISSION,
    canonical_digest="1" * 64,
)
SOURCE: Final = SourceEventFact(
    source=INBOX.source,
    event_id=EVENT_ID,
    mission_id=MISSION,
    topic="aerial-rescue/v1/mission-1/drone/drone-1/event/salient",
    canonical_digest=INBOX.canonical_digest,
    canonical_event=b'{"event":"complete"}',
    observed_at=OBSERVED_AT,
)
AUDIT: Final = AuditFact(
    mission_id=MISSION,
    kind="aerial-rescue.v1.drone.event.salient",
    occurred_at="2026-08-25T12:00:00.000Z",
    canonical_event=SOURCE.canonical_event,
    correlation_id="correlation-1",
    causation_id=None,
    traceparent=TRACEPARENT,
)


@dataclass
class _StoreTransaction:
    """Record the exact package-store values received by one transaction."""

    outcome: StoreInboxOutcome
    calls: list[str] = field(default_factory=list)
    inboxes: list[InboxIdentity] = field(default_factory=list)
    sources: list[StoredSourceEvent] = field(default_factory=list)
    audits: list[AuditRecord] = field(default_factory=list)
    completions: list[tuple[InboxIdentity, bytes, str]] = field(default_factory=list)

    async def claim_inbox(self, identity: InboxIdentity) -> StoreInboxOutcome:
        """Return the scripted durable claim."""
        self.calls.append("claim")
        self.inboxes.append(identity)
        return self.outcome

    async def record_source_event(self, event: StoredSourceEvent) -> object:
        """Record a complete source event."""
        self.calls.append("source")
        self.sources.append(event)
        return object()

    async def append_audit(self, record: AuditRecord) -> int:
        """Record the audit input and return its authoritative ordinal."""
        self.calls.append("audit")
        self.audits.append(record)
        return 7

    async def complete_inbox(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Record the canonical durable duplicate result."""
        self.calls.append("complete")
        self.completions.append((identity, result, processed_at))


@dataclass
class _StoreUnitOfWork:
    """Expose one fake store transaction and record finalization."""

    transaction: _StoreTransaction

    async def __aenter__(self) -> _StoreTransaction:
        """Open the fake transaction."""
        self.transaction.calls.append("begin")
        return self.transaction

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Record commit or rollback without suppressing a failure."""
        del exception, traceback
        self.transaction.calls.append("commit" if exception_type is None else "rollback")


@dataclass
class _StoreTransactions:
    """Return a fresh context around the scripted store transaction."""

    transaction: _StoreTransaction

    def open(self) -> AbstractAsyncContextManager[_StoreTransaction]:
        """Return the fake unit of work."""
        return _StoreUnitOfWork(self.transaction)


class RecordingStoreAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_service_fact_is_translated_and_committed_in_one_store_transaction(
        self,
    ) -> None:
        # Arrange
        store = _StoreTransaction(StoreInboxOutcome(StoreInboxDecision.CLAIMED, None))
        transactions = RecordingTransactionsAdapter(_StoreTransactions(store))

        # Act
        async with transactions.open() as transaction:
            claimed = await transaction.claim_inbox(INBOX)
            await transaction.record_source_event(SOURCE)
            ordinal = await transaction.append_audit(AUDIT)
            await transaction.complete_inbox(INBOX, ordinal, OBSERVED_AT)

        # Assert
        self.assertEqual(
            (
                InboxDecision.CLAIMED,
                None,
                ["begin", "claim", "source", "audit", "complete", "commit"],
                InboxIdentity(**INBOX.__dict__),
                StoredSourceEvent(
                    source=SOURCE.source,
                    event_id=SOURCE.event_id,
                    mission_id=SOURCE.mission_id,
                    topic=SOURCE.topic,
                    canonical_digest=SOURCE.canonical_digest,
                    canonical_payload=SOURCE.canonical_event,
                    observed_at=SOURCE.observed_at,
                ),
                AuditRecord(
                    mission_id=AUDIT.mission_id,
                    kind=AUDIT.kind,
                    occurred_at=AUDIT.occurred_at,
                    payload=AUDIT.canonical_event,
                    correlation_id=AUDIT.correlation_id,
                    causation_id=AUDIT.causation_id,
                    traceparent=AUDIT.traceparent,
                ),
                b'{"auditOrdinal":7}',
            ),
            (
                claimed.decision,
                claimed.audit_ordinal,
                store.calls,
                store.inboxes[0],
                store.sources[0],
                store.audits[0],
                store.completions[0][1],
            ),
        )

    async def test_an_exact_duplicate_decodes_only_the_closed_positive_ordinal_result(self) -> None:
        # Arrange
        store = _StoreTransaction(
            StoreInboxOutcome(StoreInboxDecision.DUPLICATE, b'{"auditOrdinal":7}')
        )
        transactions = RecordingTransactionsAdapter(_StoreTransactions(store))

        # Act
        async with transactions.open() as transaction:
            outcome = await transaction.claim_inbox(INBOX)

        # Assert
        self.assertEqual(
            (InboxDecision.DUPLICATE, 7, ["begin", "claim", "commit"]),
            (outcome.decision, outcome.audit_ordinal, store.calls),
        )

    async def test_a_claimed_row_with_a_result_is_refused_as_inconsistent_store_output(
        self,
    ) -> None:
        # Arrange
        store = _StoreTransaction(
            StoreInboxOutcome(StoreInboxDecision.CLAIMED, b'{"auditOrdinal":7}')
        )
        transactions = RecordingTransactionsAdapter(_StoreTransactions(store))

        # Act
        with pytest.raises(StoreAdapterError) as captured:
            async with transactions.open() as transaction:
                await transaction.claim_inbox(INBOX)

        # Assert
        self.assertEqual(
            (StoreAdapterRefusal.INBOX_RESULT, ["begin", "claim", "rollback"]),
            (captured.value.refusal, store.calls),
        )

    async def test_a_malformed_duplicate_result_rolls_back_without_exposing_its_bytes(self) -> None:
        # Arrange
        store = _StoreTransaction(
            StoreInboxOutcome(StoreInboxDecision.DUPLICATE, b'{"auditOrdinal":0,"secret":"x"}')
        )
        transactions = RecordingTransactionsAdapter(_StoreTransactions(store))

        # Act
        with pytest.raises(StoreAdapterError) as captured:
            async with transactions.open() as transaction:
                await transaction.claim_inbox(INBOX)

        # Assert
        self.assertEqual(
            (
                StoreAdapterRefusal.INBOX_RESULT,
                EVENT_ID,
                ["begin", "claim", "rollback"],
                False,
            ),
            (
                captured.value.refusal,
                captured.value.value,
                store.calls,
                "secret" in str(captured.value),
            ),
        )

    async def test_every_noncanonical_or_nonpositive_duplicate_result_is_refused_redacted(
        self,
    ) -> None:
        # Arrange
        results = (
            None,
            b"not-json",
            b'{ "auditOrdinal":7}',
            b"7",
            b'{"auditOrdinal":true}',
            b'{"auditOrdinal":0}',
        )

        # Act
        refusals = []
        for result in results:
            with self.subTest(result_type=type(result).__name__):
                store = _StoreTransaction(StoreInboxOutcome(StoreInboxDecision.DUPLICATE, result))
                transactions = RecordingTransactionsAdapter(_StoreTransactions(store))
                with pytest.raises(StoreAdapterError) as captured:
                    async with transactions.open() as transaction:
                        await transaction.claim_inbox(INBOX)
                refusals.append((captured.value.refusal, captured.value.value))

        # Assert
        self.assertEqual(
            [(StoreAdapterRefusal.INBOX_RESULT, EVENT_ID)] * len(results),
            refusals,
        )


if __name__ == "__main__":
    unittest.main()
