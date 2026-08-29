from __future__ import annotations

import unittest
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import cast, override

import pytest
from aerial_rescue_broker.messaging import (
    AcknowledgingReceiver,
    InboundMessage,
    MessageReceiver,
    Outcome,
)
from aerial_rescue_domain.mission import MissionError, MissionRefusal
from aerial_rescue_recorder.service import CaptureLoop, DashboardAppender, _mission_lifecycle
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard.events import (
    BrokerEvent,
    BrokerEventOutcome,
    BrokerEventReceipt,
    EventSession,
)

pytestmark = [pytest.mark.unit]


@dataclass(frozen=True)
class _Message(InboundMessage):
    identifier: str

    @override
    def get_payload_as_bytes(self) -> bytes:
        return b"{}"

    @override
    def get_destination_name(self) -> str:
        return self.identifier

    @override
    def get_properties(self) -> Mapping[str, object]:
        return {}


@dataclass
class _DirectReceiver(MessageReceiver):
    scripted: list[InboundMessage]
    timeouts: list[int] = field(default_factory=list)

    @override
    def receive(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        self.timeouts.append(timeout_milliseconds)
        return self.scripted.pop(0) if self.scripted else None


@dataclass
class _GuaranteedReceiver(_DirectReceiver, AcknowledgingReceiver):
    settlements: list[Outcome] = field(default_factory=list)

    @override
    def settle(self, _message: InboundMessage, outcome: Outcome, /) -> None:
        self.settlements.append(outcome)


@dataclass
class _Processor:
    calls: list[str] = field(default_factory=list)

    async def process_best_effort(self, message: InboundMessage) -> None:
        self.calls.append(f"direct:{message.get_destination_name()}")

    async def process_guaranteed(
        self,
        _receiver: AcknowledgingReceiver,
        message: InboundMessage,
    ) -> None:
        self.calls.append(f"guaranteed:{message.get_destination_name()}")


class CaptureLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_negative_receive_timeout_is_refused_before_polling(self) -> None:
        # Arrange
        direct = _DirectReceiver([])
        processor = _Processor()

        # Act
        with pytest.raises(ValueError, match="bounds"):
            CaptureLoop(direct, (), processor, receive_timeout_milliseconds=-1)

        # Assert
        self.assertEqual([], direct.timeouts)
        self.assertEqual([], processor.calls)

    async def test_nonpositive_batch_bound_is_refused_before_polling(self) -> None:
        # Arrange
        direct = _DirectReceiver([])
        processor = _Processor()

        # Act
        with pytest.raises(ValueError, match="bounds"):
            CaptureLoop(
                direct,
                (),
                processor,
                receive_timeout_milliseconds=1,
                maximum_batch_messages=0,
            )

        # Assert
        self.assertEqual([], direct.timeouts)
        self.assertEqual([], processor.calls)

    async def test_one_poll_is_fair_and_bounded_across_direct_and_guaranteed_inputs(self) -> None:
        # Arrange
        direct = _DirectReceiver([_Message("telemetry")])
        guaranteed = (
            _GuaranteedReceiver([_Message("mission")]),
            _GuaranteedReceiver([_Message("sector")]),
        )
        processor = _Processor()
        loop = CaptureLoop(
            direct,
            guaranteed,
            processor,
            receive_timeout_milliseconds=25,
            maximum_batch_messages=3,
        )

        # Act
        await loop.poll_once()

        # Assert
        self.assertEqual([25], direct.timeouts)
        self.assertEqual([[0], [0]], [receiver.timeouts for receiver in guaranteed])
        self.assertEqual(
            ["direct:telemetry", "guaranteed:mission", "guaranteed:sector"],
            processor.calls,
        )

    async def test_empty_poll_returns_without_inventing_capture(self) -> None:
        # Arrange
        direct = _DirectReceiver([])
        guaranteed = (_GuaranteedReceiver([]),)
        processor = _Processor()
        loop = CaptureLoop(
            direct,
            guaranteed,
            processor,
            receive_timeout_milliseconds=1,
            maximum_batch_messages=8,
        )

        # Act
        await loop.poll_once()

        # Assert
        self.assertEqual([], processor.calls)
        self.assertEqual([1], direct.timeouts)
        self.assertEqual([0], guaranteed[0].timeouts)

    async def test_bounded_batches_interleave_280_telemetry_with_ordered_lifecycle(self) -> None:
        # Arrange
        direct = _DirectReceiver([_Message(f"telemetry-{index:03d}") for index in range(280)])
        lifecycle = _GuaranteedReceiver([_Message(f"lifecycle-{index:03d}") for index in range(48)])
        processor = _Processor()
        loop = CaptureLoop(
            direct,
            (lifecycle,),
            processor,
            receive_timeout_milliseconds=25,
            maximum_batch_messages=32,
        )

        # Act
        batch_sizes: list[int] = []
        for _index in range(12):
            before = len(processor.calls)
            await loop.poll_once()
            batch_sizes.append(len(processor.calls) - before)

        # Assert
        self.assertEqual(328, sum(batch_sizes))
        self.assertLessEqual(max(batch_sizes), 32)
        self.assertEqual(0, batch_sizes[-1])
        self.assertEqual(
            [f"guaranteed:lifecycle-{index:03d}" for index in range(48)],
            [call for call in processor.calls if call.startswith("guaranteed:")],
        )
        self.assertEqual(
            ["direct:telemetry-000", "guaranteed:lifecycle-000"],
            processor.calls[:2],
        )


class DashboardAppenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_mission_lifecycle_projection_refuses_each_malformed_shape(self) -> None:
        # Arrange
        payloads = (
            b"[]",
            b'{"data":[]}',
            b'{"data":{"lifecycle":1}}',
        )
        records = tuple(
            AuditRecord("mission-01", "missionLifecycle", "time", payload, "c", None, "t")
            for payload in payloads
        )
        refusals: list[MissionRefusal] = []

        # Act
        for record in records:
            with pytest.raises(MissionError) as captured:
                _mission_lifecycle(record)
            refusals.append(cast("MissionRefusal", captured.value.refusal))

        # Assert
        self.assertEqual([MissionRefusal.TRANSITION] * len(records), refusals)

    async def test_appender_returns_only_after_the_transaction_commits(self) -> None:
        # Arrange
        calls: list[str] = []
        session = cast("EventSession", object())

        @asynccontextmanager
        async def transactions() -> AsyncIterator[EventSession]:
            calls.append("begin")
            yield session
            calls.append("commit")

        async def persist(
            actual_session: EventSession,
            _event: BrokerEvent,
            _record: AuditRecord,
        ) -> BrokerEventReceipt:
            self.assertIs(session, actual_session)
            calls.append("persist")
            return BrokerEventReceipt(BrokerEventOutcome.ACCEPTED, "mission-01", 1)

        appender = DashboardAppender(transactions, persist=persist)
        event = BrokerEvent("source", "event", 1, "0" * 64)
        record = AuditRecord("mission-01", "telemetry", "time", b"{}", "c", None, "t")

        # Act
        await appender.append(event, record)

        # Assert
        self.assertEqual(["begin", "persist", "commit"], calls)

    async def test_mission_lifecycle_changes_the_authoritative_row_in_the_audit_transaction(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []
        session = cast("EventSession", object())

        @asynccontextmanager
        async def transactions() -> AsyncIterator[EventSession]:
            calls.append("begin")
            yield session
            calls.append("commit")

        async def lifecycle(_session: object, mission_id: str) -> str:
            calls.append(f"read:{mission_id}")
            return "PLANNED"

        async def transition(
            _session: object,
            mission_id: str,
            expected: str,
            target: str,
        ) -> None:
            calls.append(f"transition:{mission_id}:{expected}:{target}")

        async def persist(
            _session: EventSession,
            _event: BrokerEvent,
            record: AuditRecord,
        ) -> BrokerEventReceipt:
            calls.append("persist")
            return BrokerEventReceipt(BrokerEventOutcome.ACCEPTED, record.mission_id, 1)

        appender = DashboardAppender(
            transactions,
            persist=persist,
            lifecycle=lifecycle,
            transition_lifecycle=transition,
        )
        event = BrokerEvent("source", "event", 1, "0" * 64)
        record = AuditRecord(
            "mission-01",
            "missionLifecycle",
            "time",
            b'{"data":{"lifecycle":"SEARCHING"},"eventClass":"MISSION",'
            b'"kind":"missionLifecycle","mission":"mission-01","time":"time"}',
            "c",
            None,
            "t",
        )

        # Act
        await appender.append(event, record)

        # Assert
        self.assertEqual(
            [
                "begin",
                "read:mission-01",
                "transition:mission-01:PLANNED:SEARCHING",
                "persist",
                "commit",
            ],
            calls,
        )

    async def test_equal_mission_lifecycle_is_idempotent_and_regression_is_refused(self) -> None:
        # Arrange
        session = cast("EventSession", object())

        @asynccontextmanager
        async def transactions() -> AsyncIterator[EventSession]:
            yield session

        async def persist(
            _session: EventSession,
            _event: BrokerEvent,
            record: AuditRecord,
        ) -> BrokerEventReceipt:
            return BrokerEventReceipt(BrokerEventOutcome.ACCEPTED, record.mission_id, 1)

        transitions: list[str] = []

        async def transition(
            _session: object,
            _mission_id: str,
            _expected: str,
            target: str,
        ) -> None:
            transitions.append(target)

        event = BrokerEvent("source", "event", 1, "0" * 64)
        record = AuditRecord(
            "mission-01",
            "missionLifecycle",
            "time",
            b'{"data":{"lifecycle":"SEARCHING"},"eventClass":"MISSION",'
            b'"kind":"missionLifecycle","mission":"mission-01","time":"time"}',
            "c",
            None,
            "t",
        )

        async def equal(_session: object, _mission_id: str) -> str:
            return "SEARCHING"

        async def terminal(_session: object, _mission_id: str) -> str:
            return "EXHAUSTED"

        # Act
        await DashboardAppender(
            transactions,
            persist=persist,
            lifecycle=equal,
            transition_lifecycle=transition,
        ).append(event, record)
        with pytest.raises(MissionError) as refused:
            await DashboardAppender(
                transactions,
                persist=persist,
                lifecycle=terminal,
                transition_lifecycle=transition,
            ).append(event, record)

        # Assert
        self.assertEqual(MissionRefusal.TRANSITION, refused.value.refusal)
        self.assertEqual([], transitions)

    async def test_planned_regression_is_refused_before_store_transition_or_audit(self) -> None:
        # Arrange
        session = cast("EventSession", object())
        calls: list[str] = []

        @asynccontextmanager
        async def transactions() -> AsyncIterator[EventSession]:
            calls.append("begin")
            yield session

        async def lifecycle(_session: object, _mission_id: str) -> str:
            calls.append("read")
            return "SEARCHING"

        async def transition(
            _session: object,
            _mission_id: str,
            _expected: str,
            _target: str,
        ) -> None:
            calls.append("transition")

        async def persist(
            _session: EventSession,
            _event: BrokerEvent,
            _record: AuditRecord,
        ) -> BrokerEventReceipt:
            calls.append("persist")
            return BrokerEventReceipt(BrokerEventOutcome.ACCEPTED, "mission-01", 1)

        appender = DashboardAppender(
            transactions,
            persist=persist,
            lifecycle=lifecycle,
            transition_lifecycle=transition,
        )
        event = BrokerEvent("source", "event", 1, "0" * 64)
        record = AuditRecord(
            "mission-01",
            "missionLifecycle",
            "time",
            b'{"data":{"lifecycle":"PLANNED"}}',
            "c",
            None,
            "t",
        )

        # Act
        with pytest.raises(MissionError) as refused:
            await appender.append(event, record)

        # Assert
        self.assertEqual(MissionRefusal.TRANSITION, refused.value.refusal)
        self.assertEqual(["begin", "read"], calls)
