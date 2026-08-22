"""The CloudEvent record the command gateway publishes for every answer it sends.

The record is built and then read back through ``parse_envelope`` and
``check_topic_binding`` before it is returned, so a defect produces a refusal here rather
than an unparsable event on the broker. These tests supply the clock, identifier, and
sequence rather than reading them, which is what keeps the module pure.
"""

from __future__ import annotations

import unittest
from enum import Enum
from typing import Final, cast

import pytest
from aerial_rescue_command_gateway import event_source
from aerial_rescue_command_gateway.record import (
    MAX_SEQUENCE,
    RecordError,
    RecordRefusal,
    RecordStamp,
    response_record,
)
from aerial_rescue_contracts.envelope import EnvelopeRefusal, parse_envelope
from aerial_rescue_contracts.rpc import GatewayResponse, Outcome
from aerial_rescue_contracts.topics import RESERVED_REPLY_MISSION

REQUEST_ID: Final = "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e"
MISSION: Final = "m-2026-0001"
STAMP: Final = RecordStamp(
    event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6d",
    occurred_at="2026-08-22T09:14:52.310Z",
    sequence=1,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4737-b7ad6b7169203333-01",
)


def _answered() -> GatewayResponse:
    """Return one answered response, the shape the egress spike produces."""
    return GatewayResponse(
        mission_id=MISSION,
        request_id=REQUEST_ID,
        operation="command-authority",
        command_type="escalate-rescue",
        outcome=Outcome.ANSWERED,
        actuated=False,
        authority="operator-approval",
    )


def _refusal_of(stamp: RecordStamp) -> tuple[Enum, object]:
    """Return the refusal building a record raises, failing the test if it is accepted."""
    try:
        response_record(_answered(), stamp)
    except RecordError as error:
        return (error.refusal, error.value)
    message = f"accepted: {stamp!r}"
    raise AssertionError(message)


class RecordTests(unittest.TestCase):
    def test_the_record_is_published_on_the_request_s_own_response_topic(self) -> None:
        # Arrange
        response = _answered()

        # Act
        topic, _document = response_record(response, STAMP)

        # Assert
        self.assertEqual(f"aerial-rescue/v1/{MISSION}/gateway/response/{REQUEST_ID}", topic)

    def test_the_record_carries_the_reply_body_verbatim_as_its_payload(self) -> None:
        # Arrange
        response = _answered()

        # Act
        _topic, document = response_record(response, STAMP)

        # Assert
        self.assertEqual(
            {
                "rpcVersion": 1,
                "missionId": MISSION,
                "requestId": REQUEST_ID,
                "operation": "command-authority",
                "commandType": "escalate-rescue",
                "outcome": "answered",
                "actuated": False,
                "authority": "operator-approval",
            },
            document["data"],
        )

    def test_the_command_gateway_is_the_producer_and_the_request_is_the_correlation(
        self,
    ) -> None:
        # Arrange
        response = _answered()

        # Act
        _topic, document = response_record(response, STAMP)

        # Assert
        self.assertEqual(
            ("urn:aerial-rescue:service:command-gateway", REQUEST_ID, event_source()),
            (document["source"], document["correlationid"], document["source"]),
        )

    def test_the_stamp_supplies_the_identifier_instant_sequence_and_trace(self) -> None:
        # Arrange
        response = _answered()

        # Act
        _topic, document = response_record(response, STAMP)

        # Assert
        self.assertEqual(
            (
                "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6d",
                "2026-08-22T09:14:52.310Z",
                "000000000000001",
                "00-4bf92f3577b34da6a3ce929d0e0e4737-b7ad6b7169203333-01",
            ),
            (
                document["id"],
                document["time"],
                document["sequence"],
                document["traceparent"],
            ),
        )

    def test_the_record_it_returns_is_one_the_envelope_profile_accepts(self) -> None:
        # Arrange
        response = _answered()

        # Act
        _topic, document = response_record(response, STAMP)

        # Assert
        self.assertEqual("aerial-rescue.v1.gateway.response", parse_envelope(document).type)

    def test_a_refused_answer_is_recorded_the_same_way(self) -> None:
        # Arrange
        response = GatewayResponse(
            mission_id=MISSION,
            request_id=REQUEST_ID,
            operation="propose-command",
            command_type="launch-strike",
            outcome=Outcome.REFUSED,
            actuated=False,
            refusal="unknown-operation",
        )

        # Act
        _topic, document = response_record(response, STAMP)

        # Assert
        self.assertEqual(
            "unknown-operation", cast("dict[str, object]", document["data"])["refusal"]
        )


class UnpublishableRecordTests(unittest.TestCase):
    def test_an_answer_claiming_the_reserved_reply_identifier_cannot_be_recorded(self) -> None:
        # Arrange
        response = GatewayResponse(
            mission_id=RESERVED_REPLY_MISSION,
            request_id=REQUEST_ID,
            operation="command-authority",
            command_type="escalate-rescue",
            outcome=Outcome.ANSWERED,
            actuated=False,
            authority="operator-approval",
        )

        prose = EnvelopeRefusal.RESERVED_MISSION.value

        # Act
        with pytest.raises(RecordError) as captured:
            response_record(response, STAMP)

        # Assert
        self.assertEqual(
            (RecordRefusal.UNPUBLISHABLE, prose),
            (captured.value.refusal, str(captured.value.value)[: len(prose)]),
        )


class SequenceTests(unittest.TestCase):
    def test_a_sequence_is_zero_padded_to_fifteen_digits(self) -> None:
        # Arrange
        stamps = tuple(
            RecordStamp(
                event_id=STAMP.event_id,
                occurred_at=STAMP.occurred_at,
                sequence=value,
                traceparent=STAMP.traceparent,
            )
            for value in (0, 42, MAX_SEQUENCE)
        )

        # Act
        rendered = tuple(response_record(_answered(), stamp)[1]["sequence"] for stamp in stamps)

        # Assert
        self.assertEqual(("000000000000000", "000000000000042", "999999999999999"), rendered)

    def test_a_sequence_outside_the_representable_range_is_refused(self) -> None:
        # Arrange
        values = (-1, MAX_SEQUENCE + 1)

        # Act
        refusals = tuple(
            _refusal_of(
                RecordStamp(
                    event_id=STAMP.event_id,
                    occurred_at=STAMP.occurred_at,
                    sequence=value,
                    traceparent=STAMP.traceparent,
                )
            )
            for value in values
        )

        # Assert
        self.assertEqual(tuple((RecordRefusal.SEQUENCE_RANGE, value) for value in values), refusals)


class ProducerIdentityTests(unittest.TestCase):
    def test_the_source_is_the_service_urn_the_envelope_profile_accepts(self) -> None:
        # Arrange
        expected = "urn:aerial-rescue:service:command-gateway"

        # Act
        source = event_source()

        # Assert
        self.assertEqual(expected, source)


if __name__ == "__main__":
    unittest.main()
