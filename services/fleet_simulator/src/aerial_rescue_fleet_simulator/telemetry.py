"""One reading becomes one schema-bound telemetry CloudEvent, or it becomes a refusal.

The four members a physics fold could not have produced -- the identifier, the instant, the
producer sequence, and the trace parent -- arrive as a :class:`TelemetryStamp` from the
composition root, so this module reads no clock and consumes no random source. The record
is read back through the envelope profile and the topic binding before it is returned, so a
defect fails here rather than on the broker. That is the discipline
``services/command_gateway/record.py`` already uses.

Nothing here decides delivery. ``docs/CONTRACTS.md`` puts routine telemetry on direct
delivery; who publishes it, and with which guarantee, belongs to the composition root.

This module is pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic

from aerial_rescue_fleet_simulator import FleetSimulatorError, event_source
from aerial_rescue_fleet_simulator.fleet import Reading

_DRONE_PARAMETER = "droneId"


class TelemetryRefusal(Enum):
    """Why a reading cannot become a record."""

    SEQUENCE_RANGE = "producer sequence outside the representable range"
    UNPUBLISHABLE = "record does not satisfy the envelope profile it was built for"


class TelemetryError(FleetSimulatorError):
    """A record the profile refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class TelemetryStamp:
    """What the composition root supplies that this module may not read for itself."""

    event_id: str
    occurred_at: str
    sequence: int
    correlation_id: str
    traceparent: str


def _payload(mission_id: str, reading: Reading) -> dict[str, object]:
    """Return the telemetry payload one reading carries, in the schema's own member names."""
    return {
        "missionId": mission_id,
        "droneId": reading.drone_id,
        "latitudeMicrodegrees": reading.latitude_microdegrees,
        "longitudeMicrodegrees": reading.longitude_microdegrees,
        "batteryPercent": reading.battery_percent,
        "altitudeMetres": reading.altitude_metres,
        "headingDegrees": reading.heading_degrees,
        "groundSpeedCentimetresPerSecond": reading.ground_speed_centimetres_per_second,
    }


def telemetry_record(
    mission_id: str, reading: Reading, stamp: TelemetryStamp
) -> tuple[str, dict[str, object]]:
    """Return the topic and envelope document one reading is published as.

    Args:
        mission_id: The mission every event of this run names.
        reading: What one drone reported for one tick.
        stamp: The identifier, instant, sequence, correlation, and trace parent to send it
            under.

    Returns:
        The topic text and the envelope document, in that order.

    Raises:
        TelemetryError: With ``SEQUENCE_RANGE`` for a sequence the envelope form cannot
            carry, or ``UNPUBLISHABLE`` when the built record does not satisfy the profile
            it claims.
    """
    rendered = sequence_text(stamp.sequence)
    if rendered is None:
        raise TelemetryError(TelemetryRefusal.SEQUENCE_RANGE, stamp.sequence)
    topic = Topic(Family.DRONE_TELEMETRY, mission_id, {_DRONE_PARAMETER: reading.drone_id})
    declared = event_type(topic)
    document = envelope_document(
        Envelope(
            id=stamp.event_id,
            source=event_source(reading.drone_id),
            type=declared,
            subject=mission_id,
            time=stamp.occurred_at,
            dataschema=binding_for(declared).dataschema,
            sequence=rendered,
            correlation_id=stamp.correlation_id,
            traceparent=stamp.traceparent,
            data=_payload(mission_id, reading),
        )
    )
    try:
        check_topic_binding(parse_envelope(document), topic)
    except ValueError as refusal:
        raise TelemetryError(TelemetryRefusal.UNPUBLISHABLE, str(refusal)) from refusal
    return format_topic(topic), document
