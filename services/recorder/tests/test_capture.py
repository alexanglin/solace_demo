from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import override

import pytest
from aerial_rescue_broker.messaging import InboundMessage, Outcome
from aerial_rescue_domain.mission import MissionError, MissionRefusal
from aerial_rescue_recorder.capture import CaptureProcessor
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard_events import (
    BrokerEvent,
    DashboardEventError,
    DashboardEventRefusal,
)

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MISSION_EVENT = (
    REPOSITORY_ROOT / "fixtures/golden/v1/event/mission-event-lifecycle/baseline.json"
).read_bytes()
MISSION_TOPIC = "aerial-rescue/v1/mission-01/mission/event/lifecycle"


@dataclass(frozen=True)
class _Message(InboundMessage):
    payload: bytes | None = MISSION_EVENT
    destination: str | None = MISSION_TOPIC

    @override
    def get_payload_as_bytes(self) -> bytes | None:
        return self.payload

    @override
    def get_destination_name(self) -> str | None:
        return self.destination

    @override
    def get_properties(self) -> Mapping[str, object]:
        return {}


@dataclass
class _Receiver:
    calls: list[str]

    def receive(self, _timeout_milliseconds: int, /) -> InboundMessage | None:
        return None

    def settle(self, _message: InboundMessage, outcome: Outcome, /) -> None:
        self.calls.append(f"settle:{outcome.name}")


@dataclass
class _Appender:
    calls: list[str]
    refusal: DashboardEventRefusal | None = None
    unexpected: Exception | None = None
    payloads: list[bytes] = field(default_factory=list)

    async def append(self, _event: BrokerEvent, record: AuditRecord) -> None:
        self.calls.append("append")
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise DashboardEventError(self.refusal, "redacted")
        self.payloads.append(record.payload)
        self.calls.append("commit")


class GuaranteedCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_guaranteed_message_is_acknowledged_after_append_returns(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls)
        processor = CaptureProcessor(appender)

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "commit", "settle:ACCEPTED"], calls)
        self.assertEqual(1, len(appender.payloads))
        self.assertNotIn(b"traceparent", appender.payloads[0])

    async def test_invalid_topic_envelope_binding_or_payload_is_permanently_rejected(self) -> None:
        # Arrange
        calls: list[str] = []
        appender = _Appender(calls)
        processor = CaptureProcessor(appender)
        malformed_payload = MISSION_EVENT.replace(b'"SEARCHING"', b'"UNKNOWN"')
        cases = (
            _Message(destination="foreign/topic"),
            _Message(payload=None),
            _Message(payload=b'{"id":"duplicate","id":"duplicate"}'),
            _Message(destination=MISSION_TOPIC.replace("mission-01", "other-mission")),
            _Message(payload=malformed_payload),
        )

        # Act
        for message in cases:
            receiver = _Receiver(calls)
            await processor.process_guaranteed(receiver, message)

        # Assert
        self.assertEqual([], appender.payloads)
        self.assertEqual(["settle:REJECTED"] * len(cases), calls)

    async def test_transient_store_refusal_leaves_message_recoverable(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls, refusal=DashboardEventRefusal.SOURCE_MOVED)
        processor = CaptureProcessor(appender)

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:FAILED"], calls)

    async def test_divergent_duplicate_is_permanently_rejected(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls, refusal=DashboardEventRefusal.DIVERGENT_DUPLICATE)
        processor = CaptureProcessor(appender)

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:REJECTED"], calls)

    async def test_domain_refused_mission_transition_is_permanently_rejected(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        refused = MissionError(MissionRefusal.TRANSITION, "synthetic-regression")
        processor = CaptureProcessor(_Appender(calls, unexpected=refused))

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:REJECTED"], calls)

    async def test_unexpected_append_failure_is_left_recoverable_and_propagated(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls, unexpected=RuntimeError("synthetic failure"))
        processor = CaptureProcessor(appender)

        # Act
        with pytest.raises(RuntimeError, match="synthetic failure"):
            await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:FAILED"], calls)


class BestEffortCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_telemetry_has_no_acknowledgement_claim(self) -> None:
        # Arrange
        calls: list[str] = []
        appender = _Appender(calls)
        processor = CaptureProcessor(appender)
        telemetry = (
            REPOSITORY_ROOT / "fixtures/golden/v1/event/drone-telemetry/baseline.json"
        ).read_bytes()
        message = _Message(
            payload=telemetry,
            destination="aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry",
        )

        # Act
        await processor.process_best_effort(message)

        # Assert
        self.assertEqual(["append", "commit"], calls)

    async def test_direct_store_outcomes_have_no_discarded_result_or_acknowledgement(self) -> None:
        # Arrange
        cases = (
            _Appender([]),
            _Appender([], refusal=DashboardEventRefusal.DIVERGENT_DUPLICATE),
            _Appender([], refusal=DashboardEventRefusal.SOURCE_MOVED),
        )

        # Act
        for appender in cases:
            await CaptureProcessor(appender).process_best_effort(_Message())

        # Assert
        self.assertEqual(
            [["append", "commit"], ["append"], ["append"]],
            [case.calls for case in cases],
        )

    async def test_direct_invalid_input_is_rejected_without_calling_the_store(self) -> None:
        # Arrange
        calls: list[str] = []
        processor = CaptureProcessor(_Appender(calls))

        # Act
        await processor.process_best_effort(_Message(payload=None))

        # Assert
        self.assertEqual([], calls)
