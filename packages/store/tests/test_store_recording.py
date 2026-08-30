"""Recorder-owned transaction composition over the store's purpose-specific repositories."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Final, cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.recording import RecordingTransactions
from aerial_rescue_store.processing.source_events import SourceEventDecision, StoredSourceEvent
from sqlalchemy.ext.asyncio import AsyncSession

IDENTITY: Final = InboxIdentity(
    consumer="recorder",
    source="urn:aerial-rescue:drone:drone-1",
    event_id="source-event-1",
    mission_id="mission-1",
    canonical_digest="1" * 64,
)
SOURCE_EVENT: Final = StoredSourceEvent(
    source=IDENTITY.source,
    event_id=IDENTITY.event_id,
    mission_id=IDENTITY.mission_id,
    topic="aerial-rescue/v1/mission-1/drone/drone-1/event/salient",
    canonical_digest=IDENTITY.canonical_digest,
    canonical_payload=b'{"event":"complete"}',
    observed_at="2026-08-25T12:00:00.000Z",
)
AUDIT: Final = AuditRecord(
    mission_id=IDENTITY.mission_id,
    kind="aerial-rescue.v1.drone.event.salient",
    occurred_at="2026-08-25T12:00:00.000Z",
    payload=SOURCE_EVENT.canonical_payload,
    correlation_id="correlation-1",
    causation_id=None,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
)


@dataclass
class _Session:
    """Record transaction-finalization calls without opening PostgreSQL."""

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


class RecorderMissionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_locked_read_and_its_compare_and_set_share_the_recorder_session(self) -> None:
        """The recorder owns this column, and its move belongs in the audit's own transaction."""
        # Arrange
        session = _Session()

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.recording.mission_lifecycle_for_update",
                AsyncMock(return_value="PLANNED"),
            ) as locked,
            patch(
                "aerial_rescue_store.processing.recording.transition_mission_row", AsyncMock()
            ) as moved,
        ):
            transactions = RecordingTransactions(lambda: _factory(session))
            async with transactions.open() as transaction:
                observed = await transaction.mission_lifecycle("mission-1")
                await transaction.transition_mission("mission-1", "PLANNED", "SEARCHING")

        # Assert
        self.assertEqual(("PLANNED", ["commit", "close"]), (observed, session.calls))
        self.assertEqual(
            (session, session, ("mission-1", "PLANNED", "SEARCHING")),
            (
                locked.await_args_list[0].args[0],
                moved.await_args_list[0].args[0],
                moved.await_args_list[0].args[1:],
            ),
        )


class RecordingTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_recorder_effects_share_one_session_and_commit_together(self) -> None:
        # Arrange
        session = _Session()
        claimed = InboxOutcome(InboxDecision.CLAIMED, None)

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.recording.claim",
                AsyncMock(return_value=claimed),
            ) as claim,
            patch(
                "aerial_rescue_store.processing.recording.record_source_event",
                AsyncMock(return_value=SourceEventDecision.STORED),
            ) as record_source,
            patch(
                "aerial_rescue_store.processing.recording.append",
                AsyncMock(return_value=7),
            ) as append,
            patch("aerial_rescue_store.processing.recording.complete", AsyncMock()) as complete,
        ):
            transactions = RecordingTransactions(lambda: _factory(session))

            async with transactions.open() as transaction:
                outcome = await transaction.claim_inbox(IDENTITY)
                source_decision = await transaction.record_source_event(SOURCE_EVENT)
                ordinal = await transaction.append_audit(AUDIT)
                await transaction.complete_inbox(
                    IDENTITY,
                    b'{"auditOrdinal":7}',
                    SOURCE_EVENT.observed_at,
                )

        # Assert
        self.assertEqual(
            (
                claimed,
                SourceEventDecision.STORED,
                7,
                ["commit", "close"],
                (session,) * 4,
            ),
            (
                outcome,
                source_decision,
                ordinal,
                session.calls,
                (
                    claim.await_args_list[0].args[0],
                    record_source.await_args_list[0].args[0],
                    append.await_args_list[0].args[0],
                    complete.await_args_list[0].args[0],
                ),
            ),
        )

    async def test_repository_failure_rolls_back_and_never_commits(self) -> None:
        # Arrange
        session = _Session()
        failure = RuntimeError("injected append failure")

        # Act
        with patch(
            "aerial_rescue_store.processing.recording.append",
            AsyncMock(side_effect=failure),
        ):
            transactions = RecordingTransactions(lambda: _factory(session))

            with pytest.raises(RuntimeError) as captured:
                async with transactions.open() as transaction:
                    await transaction.append_audit(AUDIT)

        # Assert
        self.assertEqual((failure, ["rollback", "close"]), (captured.value, session.calls))


if __name__ == "__main__":
    unittest.main()
