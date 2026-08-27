"""Receiver-only recorder processing and explicit transport exclusion."""

from __future__ import annotations

import inspect
import unittest
from dataclasses import dataclass, field, fields
from typing import cast

import pytest
from aerial_rescue_contracts.topics import Family
from aerial_rescue_recorder.capture import (
    CaptureDecision,
    CaptureOutcome,
    ReceivedNotification,
)
from aerial_rescue_recorder.processing import (
    ExcludedIngress,
    NotificationIngress,
    ProcessDecision,
    ProcessError,
    ProcessRefusal,
    RecorderRuntime,
    RefusedIngress,
)
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)


@dataclass
class _Receiver:
    """Return scripted receiver-only inputs without a publication operation."""

    messages: list[ExcludedIngress | RefusedIngress]
    closes: int = 0

    async def receive(self) -> ExcludedIngress | RefusedIngress | None:
        """Return the next input or report an idle receive window."""
        if not self.messages:
            return None
        return self.messages.pop(0)

    def close(self) -> None:
        """Close the receiver endpoint."""
        self.closes += 1


@dataclass
class _Capture:
    """Record whether excluded transport ever reached durable capture."""

    calls: int = 0

    async def capture(self, notification: object, settlement: object, /) -> CaptureOutcome:
        """Return a canned durable outcome."""
        del notification, settlement
        self.calls += 1
        return CaptureOutcome(CaptureDecision.RECORDED, 1)


@dataclass
class _Settlement:
    """Record permanent rejection of one exact malformed delivery."""

    order: list[str]

    def reject(self) -> None:
        """Record settlement after durable refusal evidence."""
        self.order.append("settle-rejected")


@dataclass
class _Refusals:
    """Record refusal persistence and allow deterministic commit failure."""

    order: list[str] = field(default_factory=list)
    failure: Exception | None = None

    async def record(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Record commit or raise before settlement."""
        self.order.append("refusal-commit")
        if self.failure is not None:
            raise self.failure
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


class ReceiverOnlyCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_runtime_graph_contains_a_receiver_and_capture_port_but_no_publisher(
        self,
    ) -> None:
        # Arrange
        parameters = tuple(inspect.signature(RecorderRuntime).parameters)
        runtime = RecorderRuntime(_Receiver([]), _Capture(), _Refusals())

        # Act
        outcome = await runtime.process_next()

        # Assert
        self.assertEqual(
            (
                ("receiver", "capture", "refusals"),
                ("receiver", "capture", "refusals"),
                ProcessDecision.IDLE,
            ),
            (parameters, tuple(item.name for item in fields(runtime)), outcome.decision),
        )

    async def test_raw_rpc_and_agent_response_are_excluded_without_opening_capture(self) -> None:
        # Arrange
        receiver = _Receiver(
            [
                ExcludedIngress(Family.GATEWAY_REQUEST),
                ExcludedIngress(Family.GATEWAY_RESPONSE),
                ExcludedIngress(Family.AGENT_RESPONSE),
            ]
        )
        capture = _Capture()
        runtime = RecorderRuntime(receiver, capture, _Refusals())

        # Act
        outcomes = [await runtime.process_next() for _ in range(3)]

        # Assert
        self.assertEqual(
            ([ProcessDecision.EXCLUDED] * 3, 0),
            ([outcome.decision for outcome in outcomes], capture.calls),
        )

    async def test_a_recordable_family_cannot_be_smuggled_through_the_exclusion_path(self) -> None:
        # Arrange
        capture = _Capture()
        runtime = RecorderRuntime(
            _Receiver([ExcludedIngress(Family.DRONE_EVENT)]), capture, _Refusals()
        )

        # Act
        with pytest.raises(ProcessError) as captured:
            await runtime.process_next()

        # Assert
        self.assertEqual(
            (ProcessRefusal.INVALID_EXCLUSION, 0),
            (captured.value.refusal, capture.calls),
        )

    async def test_recorded_and_duplicate_notifications_preserve_the_durable_ordinal(self) -> None:
        # Arrange
        notification = cast("ReceivedNotification", object())

        @dataclass
        class NotificationReceiver:
            messages: list[NotificationIngress]

            async def receive(self) -> NotificationIngress | None:
                return self.messages.pop(0) if self.messages else None

            def close(self) -> None:
                return None

        @dataclass
        class ScriptedCapture:
            outcomes: list[CaptureOutcome]

            async def capture(self, notification: object, settlement: object, /) -> CaptureOutcome:
                del notification, settlement
                return self.outcomes.pop(0)

        runtime = RecorderRuntime(
            NotificationReceiver(
                [NotificationIngress(notification, None), NotificationIngress(notification, None)]
            ),
            ScriptedCapture(
                [
                    CaptureOutcome(CaptureDecision.RECORDED, 7),
                    CaptureOutcome(CaptureDecision.DUPLICATE, 7),
                ]
            ),
            _Refusals(),
        )

        # Act
        outcomes = [await runtime.process_next(), await runtime.process_next()]

        # Assert
        self.assertEqual(
            (
                [ProcessDecision.RECORDED, ProcessDecision.DUPLICATE],
                [7, 7],
            ),
            (
                [outcome.decision for outcome in outcomes],
                [outcome.audit_ordinal for outcome in outcomes],
            ),
        )

    async def test_shutdown_closes_only_the_owned_receiver(self) -> None:
        # Arrange
        receiver = _Receiver([])
        runtime = RecorderRuntime(receiver, _Capture(), _Refusals())

        # Act
        runtime.close()

        # Assert
        self.assertEqual(1, receiver.closes)

    async def test_malformed_guaranteed_input_commits_before_rejected_settlement(self) -> None:
        # Arrange
        order: list[str] = []
        fact = BrokerRefusalCandidate(
            "recorder",
            None,
            "mission.event",
            "recorder-mission-event",
            "invalid-notification",
            "1" * 64,
        )
        settlement = _Settlement(order)
        refusals = _Refusals(order)
        runtime = RecorderRuntime(
            _Receiver([RefusedIngress(fact, settlement)]),
            _Capture(),
            refusals,
        )

        # Act
        outcome = await runtime.process_next()

        # Assert
        self.assertEqual(
            (ProcessDecision.REJECTED, ["refusal-commit", "settle-rejected"]),
            (
                outcome.decision,
                order,
            ),
        )

    async def test_malformed_direct_input_commits_and_is_dropped_without_settlement(self) -> None:
        # Arrange
        order: list[str] = []
        fact = BrokerRefusalCandidate(
            "recorder",
            "urn:aerial-rescue:fleet:drone-01",
            "drone.telemetry",
            "direct",
            "native-trace-refused",
            "6" * 64,
        )
        runtime = RecorderRuntime(
            _Receiver(
                [
                    RefusedIngress(fact, None),
                    ExcludedIngress(Family.GATEWAY_REQUEST),
                ]
            ),
            _Capture(),
            _Refusals(order),
        )

        # Act
        outcomes = [await runtime.process_next(), await runtime.process_next()]

        # Assert
        self.assertEqual(
            ([ProcessDecision.DROPPED, ProcessDecision.EXCLUDED], ["refusal-commit"]),
            ([outcome.decision for outcome in outcomes], order),
        )

    async def test_refusal_commit_failure_leaves_the_message_unsettled(self) -> None:
        # Arrange
        order: list[str] = []
        failure = RuntimeError("injected refusal commit failure")
        fact = BrokerRefusalCandidate(
            "recorder",
            None,
            None,
            "recorder-mission-event",
            "invalid-topic",
            "2" * 64,
        )
        runtime = RecorderRuntime(
            _Receiver([RefusedIngress(fact, _Settlement(order))]),
            _Capture(),
            _Refusals(order, failure),
        )

        # Act
        with pytest.raises(RuntimeError) as captured:
            await runtime.process_next()

        # Assert
        self.assertEqual((failure, ["refusal-commit"]), (captured.value, order))


if __name__ == "__main__":
    unittest.main()
