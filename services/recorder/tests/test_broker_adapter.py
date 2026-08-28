"""Receiver-only broker admission for the durable recorder runtime."""

from __future__ import annotations

import hashlib
import unittest
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, cast

import pytest
from aerial_rescue_broker.messaging import (
    GuaranteedMessage,
    InboundMessage,
    InvalidDirectMessageError,
    MessageSettlement,
    MessagingRefusal,
    UnsettledMessageError,
    UnsettledMessageMetadata,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_recorder.broker import (
    BrokerIngressError,
    BrokerIngressRefusal,
    RecorderBrokerReceiver,
)
from aerial_rescue_recorder.processing import ExcludedIngress, NotificationIngress, RefusedIngress
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate

MISSION: Final = "mission-1"
OBSERVED_AT: Final = "2026-08-25T12:00:00.250Z"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"
MISSION_TOPIC: Final = "aerial-rescue/v1/mission-1/mission/event/lifecycle"
AGENT_TOPIC: Final = "aerial-rescue/v1/mission-1/agent/response/MissionCoordinator"
MISSION_EVENT: Final = canonical.canonical_bytes(
    {
        "specversion": "1.0",
        "id": "event-1",
        "source": "urn:aerial-rescue:mission-lifecycle:run-1",
        "type": "aerial-rescue.v1.mission.event.lifecycle",
        "subject": MISSION,
        "time": "2026-08-25T12:00:00.000Z",
        "datacontenttype": "application/json",
        "dataschema": (
            "https://aerial-rescue.invalid/schemas/v1/payload/mission-event-lifecycle.schema.json"
        ),
        "sequence": "000000000000001",
        "correlationid": "correlation-1",
        "traceparent": TRACEPARENT,
        "data": {"missionId": MISSION, "lifecycle": "SEARCHING"},
    }
)


@dataclass(frozen=True)
class _Message:
    """One broker message carrying only the receiver port's required members."""

    payload: bytes | None
    destination: str | None

    def get_payload_as_bytes(self) -> bytes | None:
        """Return the scripted body."""
        return self.payload

    def get_destination_name(self) -> str | None:
        """Return the scripted concrete topic."""
        return self.destination

    def get_properties(self) -> Mapping[str, object]:
        """Return no user properties."""
        return {}


@dataclass(frozen=True)
class _ByteArrayMessage:
    """One broker message whose body arrives as the pinned SDK delivers it: a ``bytearray``."""

    payload: bytes
    destination: str

    def get_payload_as_bytes(self) -> bytearray:
        """Return the mutable body the SDK hands over."""
        return bytearray(self.payload)

    def get_destination_name(self) -> str | None:
        """Return the scripted concrete topic."""
        return self.destination

    def get_properties(self) -> Mapping[str, object]:
        """Return no user properties."""
        return {}


@dataclass
class _Settlement:
    """Record the one settlement decision made by downstream processing."""

    calls: list[str] = field(default_factory=list)

    def accept(self) -> None:
        """Record acceptance."""
        self.calls.append("accept")

    def fail(self) -> None:
        """Record transient failure."""
        self.calls.append("fail")

    def reject(self) -> None:
        """Record durable rejection."""
        self.calls.append("reject")


@dataclass
class _ReceiverSession:
    """Script one direct channel and stable named Guaranteed channels."""

    direct: list[InboundMessage | None]
    guaranteed: dict[str, list[GuaranteedMessage | None]]
    calls: list[tuple[str, int]] = field(default_factory=list)
    closed: int = 0

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return stable queue names."""
        return tuple(sorted(self.guaranteed))

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return the next scripted Direct input."""
        self.calls.append(("direct", timeout_milliseconds))
        return self.direct.pop(0) if self.direct else None

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return the next scripted input for one named queue."""
        self.calls.append((receiver_name, timeout_milliseconds))
        scripted = self.guaranteed[receiver_name]
        return scripted.pop(0) if scripted else None

    def close(self) -> None:
        """Record receiver-only shutdown."""
        self.closed += 1


class _NativeTracePoisonSession:
    """Raise one message-bound native trace refusal from a durable channel."""

    receiver_names = ("mission",)

    def __init__(self, error: UnsettledMessageError) -> None:
        """Retain the exact unsettled delivery and call log."""
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return one idle Direct turn."""
        self.calls.append(("direct", timeout_milliseconds))
        return None

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Raise the native refusal after recording its poll."""
        self.calls.append((receiver_name, timeout_milliseconds))
        raise self.error

    def close(self) -> None:
        """Expose the receiver-only close capability."""


class _DirectTracePoisonSession:
    """Raise one body-free native trace refusal from Direct ingress."""

    receiver_names: tuple[str, ...] = ()

    def __init__(self, error: InvalidDirectMessageError) -> None:
        """Retain the exact refusal."""
        self.error = error

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Raise the configured Direct refusal."""
        del timeout_milliseconds
        raise self.error

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Refuse an impossible Guaranteed poll in this graph."""
        raise AssertionError((receiver_name, timeout_milliseconds))

    def close(self) -> None:
        """Expose the receiver-only close capability."""


@dataclass
class _Schemas:
    """Record the payload schema selected after envelope and topic binding."""

    calls: list[tuple[str, Mapping[str, object]]] = field(default_factory=list)

    def validate(self, schema_id: str, payload: Mapping[str, object], /) -> None:
        """Accept and record one schema execution."""
        self.calls.append((schema_id, payload))


class RecorderBrokerReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_trace_refusal_becomes_body_free_dropped_ingress(self) -> None:
        # Arrange
        error = InvalidDirectMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            UnsettledMessageMetadata(
                source="urn:aerial-rescue:fleet:drone-01",
                family="drone.telemetry",
                raw_digest="6" * 64,
            ),
        )
        receiver = RecorderBrokerReceiver(
            _DirectTracePoisonSession(error),
            _Schemas(),
            lambda: OBSERVED_AT,
            100,
        )

        # Act
        ingress = await receiver.receive()

        # Assert
        self.assertEqual(
            (
                BrokerRefusalCandidate(
                    consumer="recorder",
                    source="urn:aerial-rescue:fleet:drone-01",
                    family="drone.telemetry",
                    channel="direct",
                    refusal_code="native-trace-refused",
                    raw_digest="6" * 64,
                ),
                None,
            ),
            (
                ingress.fact if isinstance(ingress, RefusedIngress) else None,
                ingress.settlement if isinstance(ingress, RefusedIngress) else object(),
            ),
        )

    async def test_native_trace_refusal_becomes_body_free_message_bound_ingress(self) -> None:
        # Arrange
        settlement = _Settlement()
        error = UnsettledMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            cast("MessageSettlement", settlement),
            UnsettledMessageMetadata(
                source="urn:aerial-rescue:fleet:drone-01",
                family="mission.event",
                raw_digest="3" * 64,
            ),
        )
        session = _NativeTracePoisonSession(error)
        receiver = RecorderBrokerReceiver(session, _Schemas(), lambda: OBSERVED_AT, 100)

        # Act
        first = await receiver.receive()
        second = await receiver.receive()

        # Assert
        fact = second.fact if isinstance(second, RefusedIngress) else None
        self.assertEqual(
            (
                None,
                BrokerRefusalCandidate(
                    consumer="recorder",
                    source="urn:aerial-rescue:fleet:drone-01",
                    family="mission.event",
                    channel="mission",
                    refusal_code="native-trace-refused",
                    raw_digest="3" * 64,
                ),
                settlement,
                [],
            ),
            (
                first,
                fact,
                second.settlement if isinstance(second, RefusedIngress) else None,
                settlement.calls,
            ),
        )

    async def test_nonpositive_or_boolean_receive_bounds_are_refused_before_session_io(
        self,
    ) -> None:
        # Arrange
        session = _ReceiverSession([], {})
        values = (0, -1, True)

        # Act
        refusals = []
        for value in values:
            with self.subTest(value=value):
                with pytest.raises(BrokerIngressError) as captured:
                    RecorderBrokerReceiver(session, _Schemas(), lambda: OBSERVED_AT, value)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([BrokerIngressRefusal.INVALID_TIMEOUT] * len(values), refusals)

    async def test_stable_round_robin_admits_a_guaranteed_notification_with_its_settlement(
        self,
    ) -> None:
        # Arrange
        settlement = _Settlement()
        message = _Message(MISSION_EVENT, MISSION_TOPIC)
        session = _ReceiverSession(
            direct=[None],
            guaranteed={
                "audit": [None],
                "mission": [GuaranteedMessage(message, cast("MessageSettlement", settlement))],
            },
        )
        schemas = _Schemas()
        receiver = RecorderBrokerReceiver(session, schemas, lambda: OBSERVED_AT, 100)

        # Act
        first = await receiver.receive()
        second = await receiver.receive()
        third = await receiver.receive()

        # Assert
        self.assertEqual(
            (
                None,
                None,
                MISSION,
                OBSERVED_AT,
                settlement,
                [("direct", 100), ("audit", 100), ("mission", 100)],
                1,
            ),
            (
                first,
                second,
                third.notification.envelope.subject
                if isinstance(third, NotificationIngress)
                else None,
                third.notification.observed_at if isinstance(third, NotificationIngress) else None,
                third.settlement if isinstance(third, NotificationIngress) else None,
                session.calls,
                len(schemas.calls),
            ),
        )

    async def test_a_guaranteed_notification_with_an_sdk_bytearray_body_is_admitted(self) -> None:
        # Arrange
        settlement = _Settlement()
        message = _ByteArrayMessage(MISSION_EVENT, MISSION_TOPIC)
        session = _ReceiverSession(
            direct=[None],
            guaranteed={
                "audit": [None],
                "mission": [GuaranteedMessage(message, cast("MessageSettlement", settlement))],
            },
        )
        receiver = RecorderBrokerReceiver(session, _Schemas(), lambda: OBSERVED_AT, 100)

        # Act
        await receiver.receive()
        await receiver.receive()
        third = await receiver.receive()

        # Assert
        self.assertEqual(
            (True, MISSION, settlement),
            (
                isinstance(third, NotificationIngress),
                third.notification.envelope.subject
                if isinstance(third, NotificationIngress)
                else None,
                third.settlement if isinstance(third, NotificationIngress) else None,
            ),
        )

    async def test_an_excluded_integration_body_is_classified_without_schema_or_body_decode(
        self,
    ) -> None:
        # Arrange
        hostile = b'{"repeated":1,"repeated":"secret"}'
        session = _ReceiverSession([_Message(hostile, AGENT_TOPIC)], {})
        schemas = _Schemas()
        receiver = RecorderBrokerReceiver(session, schemas, lambda: OBSERVED_AT, 50)

        # Act
        ingress = await receiver.receive()

        # Assert
        self.assertEqual(
            ("AGENT_RESPONSE", [], [("direct", 50)]),
            (
                ingress.family.name if isinstance(ingress, ExcludedIngress) else None,
                schemas.calls,
                session.calls,
            ),
        )

    async def test_malformed_guaranteed_input_becomes_body_free_message_bound_refusal(
        self,
    ) -> None:
        # Arrange
        settlement = _Settlement()
        secret = b'{"authorization":"Bearer should-not-render"}'
        message = _Message(secret, MISSION_TOPIC)
        session = _ReceiverSession(
            [],
            {"mission": [GuaranteedMessage(message, cast("MessageSettlement", settlement))]},
        )
        receiver = RecorderBrokerReceiver(session, _Schemas(), lambda: OBSERVED_AT, 100)

        # Act
        await receiver.receive()
        ingress = await receiver.receive()

        # Assert
        fact = ingress.fact if isinstance(ingress, RefusedIngress) else None
        self.assertEqual(
            (
                True,
                "recorder",
                "mission.event",
                "mission",
                "invalid-notification",
                hashlib.sha256(secret).hexdigest(),
                settlement,
                False,
                [],
            ),
            (
                isinstance(ingress, RefusedIngress),
                fact.consumer if fact is not None else None,
                fact.family if fact is not None else None,
                fact.channel if fact is not None else None,
                fact.refusal_code if fact is not None else None,
                fact.raw_digest if fact is not None else None,
                ingress.settlement if isinstance(ingress, RefusedIngress) else None,
                "Bearer" in repr(ingress),
                settlement.calls,
            ),
        )

    async def test_guaranteed_input_without_a_destination_retains_no_topic_but_can_be_refused(
        self,
    ) -> None:
        # Arrange
        settlement = _Settlement()
        session = _ReceiverSession(
            [],
            {
                "mission": [
                    GuaranteedMessage(
                        _Message(MISSION_EVENT, None),
                        cast("MessageSettlement", settlement),
                    )
                ]
            },
        )
        receiver = RecorderBrokerReceiver(session, _Schemas(), lambda: OBSERVED_AT, 100)

        # Act
        await receiver.receive()
        ingress = await receiver.receive()

        # Assert
        fact = ingress.fact if isinstance(ingress, RefusedIngress) else None
        self.assertEqual(
            (
                True,
                None,
                "mission",
                "invalid-topic",
                hashlib.sha256(MISSION_EVENT).hexdigest(),
                [],
            ),
            (
                isinstance(ingress, RefusedIngress),
                fact.family if fact is not None else None,
                fact.channel if fact is not None else None,
                fact.refusal_code if fact is not None else None,
                fact.raw_digest if fact is not None else None,
                settlement.calls,
            ),
        )

    async def test_absent_invalid_topic_and_absent_payload_members_are_refused_redacted(
        self,
    ) -> None:
        # Arrange
        messages = (
            _Message(MISSION_EVENT, None),
            _Message(MISSION_EVENT, "outside/v1/mission-1/mission/event/lifecycle"),
            _Message(None, MISSION_TOPIC),
        )

        # Act
        refusals = []
        values = []
        for message in messages:
            with self.subTest(destination=message.destination):
                receiver = RecorderBrokerReceiver(
                    _ReceiverSession([message], {}),
                    _Schemas(),
                    lambda: OBSERVED_AT,
                    100,
                )
                with pytest.raises(BrokerIngressError) as captured:
                    await receiver.receive()
                refusals.append(captured.value.refusal)
                values.append(captured.value.value)

        # Assert
        self.assertEqual(
            (
                [
                    BrokerIngressRefusal.INVALID_TOPIC,
                    BrokerIngressRefusal.INVALID_TOPIC,
                    BrokerIngressRefusal.INVALID_NOTIFICATION,
                ],
                ["redacted-topic", "redacted-topic", "MISSION_EVENT"],
            ),
            (refusals, values),
        )

    async def test_shutdown_closes_the_receiver_only_session_once(self) -> None:
        # Arrange
        session = _ReceiverSession([], {})
        receiver = RecorderBrokerReceiver(session, _Schemas(), lambda: OBSERVED_AT, 100)

        # Act
        receiver.close()

        # Assert
        self.assertEqual(1, session.closed)


if __name__ == "__main__":
    unittest.main()
