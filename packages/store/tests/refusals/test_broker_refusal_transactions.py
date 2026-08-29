"""One commit-or-rollback transaction per durable broker-ingress refusal."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Final, cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from sqlalchemy.ext.asyncio import AsyncSession

FACT: Final = StoredBrokerRefusal(
    consumer="evidence-service",
    source=None,
    family="agent-proposal",
    channel="evidence-agent-proposal",
    refusal_code="invalid-payload",
    raw_digest="2" * 64,
    observed_at="2026-08-25T12:00:00.000Z",
)
CANDIDATE: Final = BrokerRefusalCandidate(
    consumer=FACT.consumer,
    source=FACT.source,
    family=FACT.family,
    channel=FACT.channel,
    refusal_code=FACT.refusal_code,
    raw_digest=FACT.raw_digest,
)


@dataclass
class _Session:
    """Expose transaction finalization calls without opening PostgreSQL."""

    calls: list[str] = field(default_factory=list)

    async def commit(self) -> None:
        """Record commit."""
        self.calls.append("commit")

    async def rollback(self) -> None:
        """Record rollback."""
        self.calls.append("rollback")

    async def close(self) -> None:
        """Record session release."""
        self.calls.append("close")


def _factory(session: _Session) -> AsyncSession:
    """Expose the transaction fake through the injected SQLAlchemy session type."""
    return cast("AsyncSession", session)


class BrokerRefusalTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_returns_only_after_the_refusal_fact_commits(self) -> None:
        # Arrange
        session = _Session()
        outcome = BrokerRefusalOutcome(BrokerRefusalDecision.STORED, FACT)

        # Act
        with patch(
            "aerial_rescue_store.processing.broker_refusals.record",
            AsyncMock(return_value=outcome),
        ) as persisted:
            actual = await BrokerRefusalRecorder(
                lambda: _factory(session), lambda: FACT.observed_at
            ).record(CANDIDATE)

        # Assert
        self.assertEqual(
            (outcome, ["commit", "close"], FACT, session),
            (
                actual,
                session.calls,
                persisted.await_args_list[0].args[1],
                persisted.await_args_list[0].args[0],
            ),
        )

    async def test_repository_failure_rolls_back_before_caller_settlement(self) -> None:
        # Arrange
        session = _Session()
        failure = RuntimeError("injected refusal persistence failure")

        # Act
        with (
            patch(
                "aerial_rescue_store.processing.broker_refusals.record",
                AsyncMock(side_effect=failure),
            ),
            pytest.raises(RuntimeError) as captured,
        ):
            await BrokerRefusalRecorder(lambda: _factory(session), lambda: FACT.observed_at).record(
                CANDIDATE
            )

        # Assert
        self.assertEqual((failure, ["rollback", "close"]), (captured.value, session.calls))


if __name__ == "__main__":
    unittest.main()
