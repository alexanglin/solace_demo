"""Source-ingestion transaction composition over migrated SQLAlchemy repositories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, cast
from unittest.mock import AsyncMock, patch

from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import StoredSourceEvidenceFact
from aerial_rescue_store.processing.source_ingress import SourceProcessingTransactions
from sqlalchemy.ext.asyncio import AsyncSession

IDENTITY: Final = InboxIdentity(
    "evidence-service",
    "urn:aerial-rescue:drone:drone-1",
    "source-event-1",
    "mission-1",
    "1" * 64,
)
EVENT: Final = StoredSourceEvent(
    IDENTITY.source,
    IDENTITY.event_id,
    IDENTITY.mission_id,
    "aerial-rescue/v1/mission-1/drone/drone-1/event/salient",
    "2" * 64,
    b'{"event":"accepted"}',
    "2026-08-25T12:00:00.000Z",
)
FACT: Final = StoredSourceEvidenceFact(
    "evidence-1",
    "drone-1",
    ObservationOrigin.LIVE_SENSOR,
    "3" * 64,
    b'{"fact":"accepted"}',
    {"fact": "accepted"},
    EVENT.observed_at,
)


@dataclass
class _Session:
    """Record transaction completion without opening PostgreSQL."""

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
    """Expose one deterministic fake as the injected SQLAlchemy session."""
    return cast("AsyncSession", session)


async def test_source_inbox_provenance_and_result_share_one_commit_boundary() -> None:
    # Arrange
    session = _Session()
    claimed = InboxOutcome(InboxDecision.CLAIMED, None)

    # Act
    with (
        patch(
            "aerial_rescue_store.processing.source_ingress.claim",
            AsyncMock(return_value=claimed),
        ) as claim,
        patch(
            "aerial_rescue_store.processing.source_ingress.record_source_evidence",
            AsyncMock(),
        ) as record,
        patch(
            "aerial_rescue_store.processing.source_ingress.complete",
            AsyncMock(),
        ) as complete,
    ):
        transactions = SourceProcessingTransactions(lambda: _factory(session))
        async with transactions.open() as transaction:
            outcome = await transaction.claim(IDENTITY)
            await transaction.record_source(EVENT, (FACT,))
            await transaction.complete(IDENTITY, b'{"stored":true}', EVENT.observed_at)
    claim_args = claim.await_args
    record_args = record.await_args
    complete_args = complete.await_args

    # Assert
    assert claim_args is not None
    assert record_args is not None
    assert complete_args is not None
    assert (
        outcome,
        session.calls,
        claim_args.args[0],
        record_args.args[0],
        complete_args.args[0],
    ) == (
        claimed,
        ["commit", "close"],
        session,
        session,
        session,
    )
