"""One reading becomes one schema-bound telemetry CloudEvent, or it becomes a refusal.

The record is read back through the envelope profile and the topic binding before it is
returned, which is the discipline `services/command_gateway/record.py` already uses: a
defect fails here rather than reaching the broker.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Final

import pytest
from aerial_rescue_contracts.envelope import (
    MAX_SEQUENCE,
    Envelope,
    EnvelopeRefusal,
    binding_for,
    check_topic_binding,
    parse_envelope,
)
from aerial_rescue_contracts.topics import (
    RESERVED_REPLY_MISSION,
    Family,
    Topic,
    event_type,
    format_topic,
)
from aerial_rescue_fleet_simulator import event_source
from aerial_rescue_fleet_simulator.fleet import Reading
from aerial_rescue_fleet_simulator.telemetry import (
    TelemetryError,
    TelemetryRefusal,
    TelemetryStamp,
    telemetry_record,
)

pytestmark = [pytest.mark.unit]

MISSION: Final = "m-2026-0001"
READING: Final = Reading(
    drone_id="drone-vision-01",
    latitude_microdegrees=47_123_456,
    longitude_microdegrees=-122_654_321,
    altitude_metres=412,
    heading_degrees=270,
    ground_speed_centimetres_per_second=850,
    battery_percent=87,
)
STAMP: Final = TelemetryStamp(
    event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6b",
    occurred_at="2026-08-20T14:03:07.250Z",
    sequence=42,
    correlation_id="c-2026-0001",
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
)
TOPIC: Final = Topic(Family.DRONE_TELEMETRY, MISSION, {"droneId": READING.drone_id})


class SourceTests(unittest.TestCase):
    def test_the_producer_is_the_drone_rather_than_the_process_that_simulates_it(self) -> None:
        # Arrange
        expected = "urn:aerial-rescue:drone:drone-vision-01"

        # Act
        source = event_source(READING.drone_id)

        # Assert
        self.assertEqual(expected, source)


class TelemetryRecordTests(unittest.TestCase):
    def test_the_record_goes_to_the_drone_telemetry_topic_of_its_mission(self) -> None:
        # Arrange
        expected = format_topic(TOPIC)

        # Act
        topic, _ = telemetry_record(MISSION, READING, STAMP)

        # Assert
        self.assertEqual(expected, topic)

    def test_the_document_satisfies_the_profile_and_binds_to_the_topic_it_is_sent_on(
        self,
    ) -> None:
        # Arrange
        _, document = telemetry_record(MISSION, READING, STAMP)

        # Act
        bound = _binds(parse_envelope(document), TOPIC)

        # Assert
        self.assertTrue(bound)

    def test_the_payload_carries_the_reading_and_names_its_mission_once(self) -> None:
        # Arrange
        expected = {
            "missionId": MISSION,
            "droneId": "drone-vision-01",
            "latitudeMicrodegrees": 47_123_456,
            "longitudeMicrodegrees": -122_654_321,
            "batteryPercent": 87,
            "altitudeMetres": 412,
            "headingDegrees": 270,
            "groundSpeedCentimetresPerSecond": 850,
        }

        # Act
        _, document = telemetry_record(MISSION, READING, STAMP)

        # Assert
        self.assertEqual(expected, document["data"])

    def test_the_context_names_the_drone_the_bound_type_and_the_bound_schema(self) -> None:
        # Arrange
        declared = event_type(TOPIC)

        # Act
        _, document = telemetry_record(MISSION, READING, STAMP)

        # Assert
        self.assertEqual(
            (
                event_source(READING.drone_id),
                declared,
                binding_for(declared).dataschema,
                MISSION,
                "000000000000042",
            ),
            (
                document["source"],
                document["type"],
                document["dataschema"],
                document["subject"],
                document["sequence"],
            ),
        )


class RefusalTests(unittest.TestCase):
    def test_a_sequence_the_envelope_form_cannot_carry_is_refused(self) -> None:
        # Arrange
        stamps = tuple(replace(STAMP, sequence=value) for value in (-1, MAX_SEQUENCE + 1))

        # Act
        refusals = tuple(_refusal(MISSION, READING, stamp) for stamp in stamps)

        # Assert
        self.assertEqual(
            tuple((TelemetryRefusal.SEQUENCE_RANGE, value) for value in (-1, MAX_SEQUENCE + 1)),
            refusals,
        )

    def test_a_record_the_profile_would_reject_is_refused_before_it_reaches_a_broker(
        self,
    ) -> None:
        # Arrange
        prose = EnvelopeRefusal.RESERVED_MISSION.value

        # Act
        refusal, value = _refusal(RESERVED_REPLY_MISSION, READING, STAMP)

        # Assert
        self.assertEqual(
            (TelemetryRefusal.UNPUBLISHABLE, prose), (refusal, str(value)[: len(prose)])
        )


def _binds(envelope: Envelope, topic: Topic) -> bool:
    """Report whether an envelope agrees with the topic it would be published on."""
    try:
        check_topic_binding(envelope, topic)
    except ValueError:
        return False
    return True


def _refusal(mission: str, reading: Reading, stamp: TelemetryStamp) -> tuple[object, object]:
    """Return the refusal building a record raises, failing the test if it is accepted."""
    try:
        built = telemetry_record(mission, reading, stamp)
    except TelemetryError as error:
        return (error.refusal, error.value)
    message = f"accepted: {built!r}"
    raise AssertionError(message)


if __name__ == "__main__":
    unittest.main()
