"""Durable refusal before REJECTED settlement for every command-gateway Guaranteed route."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest
from aerial_rescue_command_gateway.authorization import handle_operator_command
from aerial_rescue_command_gateway.ingress import IngressError
from aerial_rescue_command_gateway.operator_approval import handle_operator_approval
from aerial_rescue_command_gateway.ports import GuaranteedDelivery
from aerial_rescue_command_gateway.progression import handle_command_result
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate

if TYPE_CHECKING:
    from aerial_rescue_command_gateway.authorization import AuthorizationClock
    from aerial_rescue_command_gateway.command_artifacts import AuthorizationStamp
    from aerial_rescue_command_gateway.ports import (
        ApprovalIngressUnitOfWork,
        AuthorizationUnitOfWork,
        ResultUnitOfWork,
    )

MISSION = "mission-synthetic-0001"
MALFORMED = b'{"authorization":"Bearer must-never-be-logged"'


@dataclass
class _UnitOfWork:
    """Record refusal facts and forbid normal transaction entry for malformed input."""

    order: list[str]
    failure: Exception | None = None
    facts: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def refuse(self, fact: BrokerRefusalCandidate) -> object:
        """Persist the candidate or inject a pre-settlement store failure."""
        self.order.append("refusal-commit")
        if self.failure is not None:
            raise self.failure
        self.facts.append(fact)
        return object()

    def begin(self) -> object:
        """Fail if malformed ingress reaches an application transaction."""
        message = "malformed delivery opened an application transaction"
        raise AssertionError(message)


@dataclass
class _Settlement:
    """Record message-bound acceptance or permanent rejection."""

    order: list[str]

    async def accept(self, event_id: str) -> None:
        """Reject any attempt to accept malformed ingress."""
        self.order.append(f"accept:{event_id}")

    async def reject(self) -> None:
        """Record movement through the source queue's isolated DMQ policy."""
        self.order.append("settle-rejected")


async def _handle(
    route: str,
    delivery: GuaranteedDelivery,
    unit: _UnitOfWork,
    settlement: _Settlement,
) -> None:
    """Invoke one Guaranteed handler with dependencies malformed input never inspects."""
    if route == "authorization":
        await handle_operator_command(
            delivery,
            cast("AuthorizationStamp", object()),
            cast("AuthorizationClock", object()),
            cast("AuthorizationUnitOfWork", unit),
            settlement,
        )
    elif route == "approval":
        await handle_operator_approval(
            delivery,
            cast("AuthorizationClock", object()),
            cast("ApprovalIngressUnitOfWork", unit),
            settlement,
        )
    else:
        await handle_command_result(
            delivery,
            cast("ResultUnitOfWork", unit),
            settlement,
        )


class GuaranteedRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_three_guaranteed_routes_commit_body_free_evidence_before_rejection(
        self,
    ) -> None:
        # Arrange
        cases = (
            (
                "aerial-rescue/v1/mission-synthetic-0001/operator/command/assign-sector",
                "operator.command",
                "command-gateway-operator-command",
                "authorization",
            ),
            (
                "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve",
                "operator.approval",
                "command-gateway-operator-approval",
                "approval",
            ),
            (
                "aerial-rescue/v1/mission-synthetic-0001/drone/drone-01/command-result/command-01",
                "drone.command-result",
                "command-gateway-command-result",
                "result",
            ),
        )

        # Act
        observed: list[tuple[str, list[str], BrokerRefusalCandidate, bool]] = []
        for topic, family, _channel, route in cases:
            with self.subTest(route=route):
                order: list[str] = []
                unit = _UnitOfWork(order)
                settlement = _Settlement(order)
                delivery = GuaranteedDelivery(topic, MALFORMED)
                with pytest.raises(IngressError) as captured:
                    await _handle(route, delivery, unit, settlement)
                fact = unit.facts[0]
                observed.append((family, order, fact, "Bearer" in str(captured.value)))

        # Assert
        self.assertEqual(
            [
                (
                    family,
                    ["refusal-commit", "settle-rejected"],
                    "command-gateway",
                    channel,
                    "envelope",
                    hashlib.sha256(MALFORMED).hexdigest(),
                    False,
                    False,
                )
                for _topic, family, channel, _route in cases
            ],
            [
                (
                    family,
                    order,
                    fact.consumer,
                    fact.channel,
                    fact.refusal_code,
                    fact.raw_digest,
                    hasattr(fact, "payload"),
                    leaked,
                )
                for family, order, fact, leaked in observed
            ],
        )

    async def test_refusal_commit_failure_leaves_the_exact_message_unsettled(self) -> None:
        # Arrange
        order: list[str] = []
        failure = RuntimeError("injected refusal store failure")
        unit = _UnitOfWork(order, failure)
        settlement = _Settlement(order)
        delivery = GuaranteedDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve",
            MALFORMED,
        )

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_operator_approval(
                delivery,
                cast("AuthorizationClock", object()),
                cast("ApprovalIngressUnitOfWork", unit),
                settlement,
            )

        # Assert
        self.assertEqual((failure, ["refusal-commit"]), (captured.value, order))


if __name__ == "__main__":
    unittest.main()
