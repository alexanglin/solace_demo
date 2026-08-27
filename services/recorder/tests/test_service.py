"""Long-running recorder composition, readiness recovery, and exact broker bindings."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from aerial_rescue_broker.messaging import BrokerLifecycle
from aerial_rescue_broker.queues import queues_for
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import Principal
from aerial_rescue_recorder.processing import ProcessDecision, ProcessOutcome
from aerial_rescue_recorder.service import (
    ServiceError,
    ServiceRefusal,
    recorder_bindings,
    serve,
)


def _ticks(count: int) -> Callable[[], bool]:
    """Return a loop condition that holds for exactly ``count`` calls."""
    remaining = iter(range(count))
    return lambda: next(remaining, None) is not None


@dataclass
class _Runtime:
    """Return idle recorder outcomes while counting bounded polls."""

    outcomes: list[ProcessOutcome] = field(default_factory=list)
    calls: int = 0

    async def process_next(self) -> ProcessOutcome:
        """Return the next scripted outcome or an idle window."""
        self.calls += 1
        if self.outcomes:
            return self.outcomes.pop(0)
        return ProcessOutcome(ProcessDecision.IDLE)


class RecorderBindingTests(unittest.TestCase):
    def test_only_recordable_families_are_bound_with_their_required_delivery(self) -> None:
        # Arrange
        role = Principal.RECORDER
        expected_queues = {queue.name: queue.name for queue in queues_for(role, ())}
        expected_direct = (
            subscription_for(Family.DRONE_TELEMETRY),
            subscription_for(Family.GATEWAY_RECORD),
        )

        # Act
        bindings = recorder_bindings()

        # Assert
        self.assertEqual(
            (expected_queues, expected_direct),
            (dict(bindings.queues), tuple(bindings.direct_subscriptions)),
        )


class RecorderServeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_nonpositive_or_boolean_recovery_cycle_bound_is_refused_before_processing(
        self,
    ) -> None:
        # Arrange
        invalid = (0, -1, True)
        runtime = _Runtime()
        lifecycle = BrokerLifecycle()
        lifecycle.connected()

        # Act
        refusals = []
        for value in invalid:
            with self.subTest(value=value):
                with pytest.raises(ServiceError) as captured:
                    await serve(runtime, lifecycle, _ticks(1), value)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            ([ServiceRefusal.INVALID_RECOVERY_CYCLE] * len(invalid), 0),
            (refusals, runtime.calls),
        )

    async def test_readiness_waits_for_one_successful_poll_of_every_bound_receiver(self) -> None:
        # Arrange
        polls = len(recorder_bindings().queues) + 1
        short_runtime = _Runtime()
        short_lifecycle = BrokerLifecycle()
        short_lifecycle.connected()
        complete_runtime = _Runtime()
        complete_lifecycle = BrokerLifecycle()
        complete_lifecycle.connected()

        # Act
        await serve(short_runtime, short_lifecycle, _ticks(polls - 1), polls)
        report = await serve(complete_runtime, complete_lifecycle, _ticks(polls), polls)

        # Assert
        self.assertEqual(
            (False, True, polls, {ProcessDecision.IDLE: polls}, 0),
            (
                short_lifecycle.is_ready(),
                complete_lifecycle.is_ready(),
                complete_runtime.calls,
                report.outcomes,
                report.exit_status,
            ),
        )

    async def test_a_reconnected_session_stays_unready_until_a_complete_receiver_cycle(
        self,
    ) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.mark_ready()
        lifecycle.reconnecting()
        lifecycle.reconnected()
        polls = len(recorder_bindings().queues) + 1

        # Act
        await serve(_Runtime(), lifecycle, _ticks(polls), polls)

        # Assert
        self.assertTrue(lifecycle.is_ready())

    async def test_recovery_exhaustion_exits_nonzero_without_processing_or_claiming_recovery(
        self,
    ) -> None:
        # Arrange
        lifecycle = BrokerLifecycle()
        lifecycle.connected()
        lifecycle.exhausted()
        runtime = _Runtime()

        # Act
        report = await serve(runtime, lifecycle, _ticks(3), 1)

        # Assert
        self.assertEqual(
            (1, 0, False, {}),
            (report.exit_status, runtime.calls, lifecycle.is_ready(), report.outcomes),
        )

    async def test_every_processing_outcome_is_counted_without_changing_its_meaning(self) -> None:
        # Arrange
        outcomes = [
            ProcessOutcome(ProcessDecision.RECORDED, 1),
            ProcessOutcome(ProcessDecision.DUPLICATE, 1),
            ProcessOutcome(ProcessDecision.EXCLUDED),
        ]
        lifecycle = BrokerLifecycle()
        lifecycle.connected()

        # Act
        report = await serve(_Runtime(outcomes), lifecycle, _ticks(3), 1)

        # Assert
        self.assertEqual(
            {
                ProcessDecision.RECORDED: 1,
                ProcessDecision.DUPLICATE: 1,
                ProcessDecision.EXCLUDED: 1,
            },
            report.outcomes,
        )


if __name__ == "__main__":
    unittest.main()
