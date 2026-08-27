"""One request in, one reply and one record out, against injected ports.

The loop is where the three pure modules meet a broker, so it is tested the way the rest of
the member is: with fakes standing in for the publisher and the message, and with the
clock, identifier, and sequence supplied rather than read. Nothing here opens a socket.

The order matters and is asserted: the reply is published before the record, because
``docs/adr/0068`` makes the record the weaker of the two -- losing it costs an audit line,
never an answer.
"""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Final

import pytest
from aerial_rescue_broker.messaging import MessagingError, MessagingRefusal
from aerial_rescue_command_gateway.exchange import Exchange, ExchangeOutcome, handle_message
from aerial_rescue_command_gateway.record import RecordRefusal, RecordStamp
from aerial_rescue_command_gateway.reply import (
    REPLY_METADATA_KEY,
    REPLY_TOPIC_KEY,
    ReplyRefusal,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.rpc import GatewayRequest, gateway_request_document
from aerial_rescue_contracts.topics import RESERVED_REPLY_MISSION

REQUESTOR: Final = "a9cfb2dc-ebc9-433b-9b35-45c2ca5c43cd"
REQUEST_ID: Final = "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e"
MISSION: Final = "m-2026-0001"
REPLY_TOPIC: Final = f"aerial-rescue/v1/reply/gateway/response/{REQUESTOR}"
REQUEST_TOPIC: Final = f"aerial-rescue/v1/{MISSION}/gateway/request/command-authority"
RECORD_TOPIC: Final = f"aerial-rescue/v1/{MISSION}/gateway/record/{REQUEST_ID}"
METADATA: Final = json.dumps([{"request_id": REQUEST_ID, "response_topic": REPLY_TOPIC}])
STAMP: Final = RecordStamp(
    event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6d",
    occurred_at="2026-08-22T09:14:52.310Z",
    sequence=1,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4737-b7ad6b7169203333-01",
)


class FakeMessage:
    """One inbound message, in the shape the typed receiver port yields."""

    def __init__(
        self,
        payload: bytes | None,
        topic: str | None = REQUEST_TOPIC,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        """Record what this message will report when the loop reads it."""
        self._payload = payload
        self._topic = topic
        self._properties: Mapping[str, object] = (
            {REPLY_TOPIC_KEY: REPLY_TOPIC, REPLY_METADATA_KEY: METADATA}
            if properties is None
            else properties
        )

    def get_payload_as_bytes(self) -> bytes | None:
        """Return the payload as it arrived."""
        return self._payload

    def get_destination_name(self) -> str | None:
        """Return the topic the message arrived on."""
        return self._topic

    def get_properties(self) -> Mapping[str, object]:
        """Return the user properties the requestor set."""
        return self._properties


class FakePublisher:
    """A publisher that records what it was asked to send, in order."""

    def __init__(self, failing_topic: str | None = None) -> None:
        """Record which topic, if any, this publisher refuses."""
        self.sent: list[tuple[str, bytes, Mapping[str, object]]] = []
        self._failing_topic = failing_topic

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Record one publication, or fail if this is the topic under test."""
        if topic == self._failing_topic:
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)
        self.sent.append((topic, payload, properties))


def _request_bytes(
    operation: str = "command-authority",
    command_type: str = "escalate-rescue",
    mission_id: str = MISSION,
) -> bytes:
    """Return the canonical bytes of one gateway request."""
    request = GatewayRequest(mission_id=mission_id, operation=operation, command_type=command_type)
    return canonical.canonical_bytes(gateway_request_document(request))


def _body(publisher: FakePublisher, index: int) -> Mapping[str, object]:
    """Return the decoded payload of one recorded publication."""
    decoded = canonical.decode(publisher.sent[index][1])
    assert isinstance(decoded, Mapping)
    return decoded


class AnsweredExchangeTests(unittest.TestCase):
    def test_one_request_produces_one_reply_and_one_record_in_that_order(self) -> None:
        # Arrange
        publisher = FakePublisher()

        # Act
        exchange = handle_message(FakeMessage(_request_bytes()), publisher, STAMP)

        # Assert
        self.assertEqual(
            (
                Exchange(ExchangeOutcome.REPLIED, REPLY_TOPIC, REQUEST_ID, None),
                [REPLY_TOPIC, RECORD_TOPIC],
            ),
            (exchange, [topic for topic, _payload, _properties in publisher.sent]),
        )

    def test_the_reply_carries_the_answer_and_echoes_the_correlation_metadata(self) -> None:
        # Arrange
        publisher = FakePublisher()

        # Act
        handle_message(FakeMessage(_request_bytes()), publisher, STAMP)

        # Assert
        self.assertEqual(
            ("answered", "operator-approval", False, METADATA),
            (
                _body(publisher, 0)["outcome"],
                _body(publisher, 0)["authority"],
                _body(publisher, 0)["actuated"],
                publisher.sent[0][2][REPLY_METADATA_KEY],
            ),
        )

    def test_the_record_is_a_cloud_event_carrying_the_same_answer(self) -> None:
        # Arrange
        publisher = FakePublisher()

        # Act
        handle_message(FakeMessage(_request_bytes()), publisher, STAMP)

        # Assert
        record = _body(publisher, 1)
        self.assertEqual(
            ("aerial-rescue.v1.gateway.record", _body(publisher, 0)),
            (record["type"], record["data"]),
        )

    def test_the_record_carries_no_reply_metadata(self) -> None:
        # Arrange
        publisher = FakePublisher()

        # Act
        handle_message(FakeMessage(_request_bytes()), publisher, STAMP)

        # Assert
        self.assertEqual({}, dict(publisher.sent[1][2]))

    def test_a_policy_refusal_is_still_replied_to_and_still_recorded(self) -> None:
        # Arrange
        publisher = FakePublisher()
        payload = _request_bytes(operation="propose-command")
        topic = f"aerial-rescue/v1/{MISSION}/gateway/request/propose-command"

        # Act
        exchange = handle_message(FakeMessage(payload, topic), publisher, STAMP)

        # Assert
        self.assertEqual(
            (ExchangeOutcome.REPLIED, "refused", "unknown-operation", 2),
            (
                exchange.outcome,
                _body(publisher, 0)["outcome"],
                _body(publisher, 0)["refusal"],
                len(publisher.sent),
            ),
        )


class UndeliverableTests(unittest.TestCase):
    def test_a_message_with_no_usable_reply_target_publishes_nothing(self) -> None:
        # Arrange
        publisher = FakePublisher()
        message = FakeMessage(_request_bytes(), REQUEST_TOPIC, {})

        # Act
        exchange = handle_message(message, publisher, STAMP)

        # Assert
        self.assertEqual(
            (ExchangeOutcome.UNDELIVERABLE, None, None, ReplyRefusal.MISSING_TOPIC.value, []),
            (
                exchange.outcome,
                exchange.topic,
                exchange.request_id,
                (exchange.detail or "").split(":")[0],
                publisher.sent,
            ),
        )

    def test_a_reply_topic_on_a_real_mission_publishes_nothing(self) -> None:
        # Arrange
        publisher = FakePublisher()
        properties = {
            REPLY_TOPIC_KEY: f"aerial-rescue/v1/{MISSION}/gateway/response/{REQUESTOR}",
            REPLY_METADATA_KEY: METADATA,
        }

        # Act
        exchange = handle_message(
            FakeMessage(_request_bytes(), REQUEST_TOPIC, properties), publisher, STAMP
        )

        # Assert
        self.assertEqual(
            (ExchangeOutcome.UNDELIVERABLE, ReplyRefusal.TOPIC_MISSION.value, []),
            (exchange.outcome, (exchange.detail or "").split(":")[0], publisher.sent),
        )


class UnreadableTests(unittest.TestCase):
    def test_a_payload_that_is_not_a_gateway_request_publishes_nothing(self) -> None:
        # Arrange
        publisher = FakePublisher()
        payloads = (b"this is not the JSON the gateway declares", b'{"rpcVersion":2}', None)

        # Act
        exchanges = tuple(
            handle_message(FakeMessage(payload), publisher, STAMP) for payload in payloads
        )

        # Assert
        self.assertEqual(
            (
                tuple((ExchangeOutcome.UNREADABLE, REPLY_TOPIC, REQUEST_ID) for _ in payloads),
                [],
            ),
            (
                tuple(
                    (exchange.outcome, exchange.topic, exchange.request_id)
                    for exchange in exchanges
                ),
                publisher.sent,
            ),
        )

    def test_a_body_that_disagrees_with_the_topic_it_arrived_on_publishes_nothing(self) -> None:
        # Arrange
        publisher = FakePublisher()
        cases = (
            (_request_bytes(mission_id="m-2026-0002"), REQUEST_TOPIC),
            (_request_bytes(), f"aerial-rescue/v1/{MISSION}/gateway/request/propose-command"),
            (_request_bytes(), f"aerial-rescue/v1/{MISSION}/audit/decision"),
            (_request_bytes(), "not-a-topic"),
            (_request_bytes(), None),
        )

        # Act
        exchanges = tuple(
            handle_message(FakeMessage(payload, topic), publisher, STAMP)
            for payload, topic in cases
        )

        # Assert
        self.assertEqual(
            (tuple((ExchangeOutcome.UNREADABLE, REPLY_TOPIC, REQUEST_ID) for _ in cases), []),
            (
                tuple(
                    (exchange.outcome, exchange.topic, exchange.request_id)
                    for exchange in exchanges
                ),
                publisher.sent,
            ),
        )


class RecordFailureTests(unittest.TestCase):
    def test_a_reply_that_cannot_be_recorded_is_reported_but_still_sent(self) -> None:
        # Arrange
        publisher = FakePublisher(failing_topic=RECORD_TOPIC)

        # Act
        exchange = handle_message(FakeMessage(_request_bytes()), publisher, STAMP)

        # Assert
        self.assertEqual(
            (
                ExchangeOutcome.RECORD_FAILED,
                REPLY_TOPIC,
                REQUEST_ID,
                MessagingRefusal.PUBLISH_REFUSED.value,
                [REPLY_TOPIC],
            ),
            (
                exchange.outcome,
                exchange.topic,
                exchange.request_id,
                (exchange.detail or "").split(":")[0],
                [topic for topic, _payload, _properties in publisher.sent],
            ),
        )

    def test_a_request_claiming_the_reserved_identifier_is_answered_but_not_recorded(
        self,
    ) -> None:
        # Arrange
        publisher = FakePublisher()
        payload = _request_bytes(mission_id=RESERVED_REPLY_MISSION)
        topic = f"aerial-rescue/v1/{RESERVED_REPLY_MISSION}/gateway/request/command-authority"

        # Act
        exchange = handle_message(FakeMessage(payload, topic), publisher, STAMP)

        # Assert
        self.assertEqual(
            (
                ExchangeOutcome.RECORD_FAILED,
                RecordRefusal.UNPUBLISHABLE.value,
                [REPLY_TOPIC],
            ),
            (
                exchange.outcome,
                (exchange.detail or "").split(":")[0],
                [sent for sent, _payload, _properties in publisher.sent],
            ),
        )

    def test_a_reply_that_cannot_be_published_at_all_propagates(self) -> None:
        # Arrange
        publisher = FakePublisher(failing_topic=REPLY_TOPIC)

        # Act
        with pytest.raises(MessagingError) as captured:
            handle_message(FakeMessage(_request_bytes()), publisher, STAMP)

        # Assert
        self.assertEqual(
            (MessagingRefusal.PUBLISH_REFUSED, REPLY_TOPIC, []),
            (captured.value.refusal, captured.value.value, publisher.sent),
        )


if __name__ == "__main__":
    unittest.main()
