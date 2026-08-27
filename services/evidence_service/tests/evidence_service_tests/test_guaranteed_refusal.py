"""Malformed proposal refusal commits before evidence-service REJECTED settlement."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest
from aerial_rescue_evidence_service.ports import DecisionStamp, InboundDelivery
from aerial_rescue_evidence_service.processing import handle_delivery
from aerial_rescue_evidence_service.wire import IngressError
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)

if TYPE_CHECKING:
    from aerial_rescue_evidence_service.ports import EvidenceUnitOfWork

MALFORMED = b'{"prompt":"hostile body must-never-be-logged"'
TOPIC = "aerial-rescue/v1/mission-synthetic-0001/agent/proposal/VisionAgent/candidate-location"


@dataclass
class _UnitOfWork:
    """Persist refusal candidates without allowing the evidence transaction to open."""

    order: list[str]
    failure: Exception | None = None
    facts: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Record the durable boundary or inject its failure."""
        self.order.append("refusal-commit")
        if self.failure is not None:
            raise self.failure
        self.facts.append(fact)
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

    def begin(self) -> object:
        """Fail if malformed input reaches evidence persistence."""
        message = "malformed proposal opened evidence processing"
        raise AssertionError(message)


@dataclass
class _Settlement:
    """Record the exact message's settlement decision."""

    order: list[str]

    async def accept(self, event_id: str) -> None:
        """Record an invalid acceptance if one occurs."""
        self.order.append(f"accept:{event_id}")

    async def reject(self) -> None:
        """Record permanent rejection after durable evidence."""
        self.order.append("settle-rejected")


def _stamp() -> DecisionStamp:
    """Return a value that malformed ingress must never inspect."""
    return cast("DecisionStamp", object())


class EvidenceGuaranteedRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_proposal_commits_digest_only_evidence_then_rejects(self) -> None:
        # Arrange
        order: list[str] = []
        unit = _UnitOfWork(order)
        settlement = _Settlement(order)
        delivery = InboundDelivery(TOPIC, MALFORMED, "1" * 64)

        # Act
        with pytest.raises(IngressError) as captured:
            await handle_delivery(
                delivery,
                _stamp(),
                cast("EvidenceUnitOfWork", unit),
                settlement,
            )
        fact = unit.facts[0]

        # Assert
        self.assertEqual(
            (
                ["refusal-commit", "settle-rejected"],
                "evidence-service",
                "agent.proposal",
                "evidence-service-agent-proposal",
                "unreadable",
                hashlib.sha256(MALFORMED).hexdigest(),
                False,
                False,
            ),
            (
                order,
                fact.consumer,
                fact.family,
                fact.channel,
                fact.refusal_code,
                fact.raw_digest,
                hasattr(fact, "payload"),
                "hostile" in str(captured.value),
            ),
        )

    async def test_refusal_store_failure_leaves_the_proposal_unsettled(self) -> None:
        # Arrange
        order: list[str] = []
        failure = RuntimeError("injected refusal commit failure")
        unit = _UnitOfWork(order, failure)
        settlement = _Settlement(order)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_delivery(
                InboundDelivery(TOPIC, MALFORMED, "1" * 64),
                _stamp(),
                cast("EvidenceUnitOfWork", unit),
                settlement,
            )

        # Assert
        self.assertEqual((failure, ["refusal-commit"]), (captured.value, order))


if __name__ == "__main__":
    unittest.main()
