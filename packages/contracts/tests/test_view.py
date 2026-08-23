"""Projection of validated envelopes into normalized dashboard events.

A dashboard event carries what the operator's browser needs and nothing from the transport
(``docs/adr/0067-normalized-dashboard-events-and-reduced-state.md``). The envelope's ``time``
crosses the boundary because the timeline shows it; ``id``, ``source``, ``sequence``,
``dataschema``, and the trace context do not. An event type with no projection is refused,
which is the same shape as the unbound-type refusal in ADR-0037.
"""

from __future__ import annotations

import unittest
from typing import Final

from aerial_rescue_contracts.envelope import BINDINGS, Envelope
from aerial_rescue_contracts.view import (
    MAX_BUFFERED_EVENTS,
    PROJECTIONS,
    DashboardEvent,
    EventClass,
    ViewError,
    ViewRefusal,
    droppable,
    project,
)

TELEMETRY_TYPE: Final = "aerial-rescue.v1.drone.telemetry"
TELEMETRY_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
)
MISSION: Final = "m-2026-0001"
DRONE: Final = "drone-vision-01"
TIME: Final = "2026-08-21T09:15:30.250Z"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

TELEMETRY_PAYLOAD: Final[dict[str, object]] = {
    "missionId": MISSION,
    "droneId": DRONE,
    "latitudeMicrodegrees": 47123456,
    "longitudeMicrodegrees": -122654321,
    "batteryPercent": 87,
    "altitudeMetres": 412,
    "headingDegrees": 270,
    "groundSpeedCentimetresPerSecond": 850,
}

PROJECTED_TELEMETRY: Final[dict[str, object]] = {
    "droneId": DRONE,
    "latitudeMicrodegrees": 47123456,
    "longitudeMicrodegrees": -122654321,
    "batteryPercent": 87,
    "altitudeMetres": 412,
    "headingDegrees": 270,
    "groundSpeedCentimetresPerSecond": 850,
}

TRANSPORT_MEMBERS: Final = ("id", "source", "sequence", "dataschema", "traceparent", "tracestate")


def _envelope(event_type: str = TELEMETRY_TYPE) -> Envelope:
    """Return a valid telemetry envelope, optionally carrying a different event type."""
    return Envelope(
        id="e-0000000001",
        source="urn:aerial-rescue:simulator:fleet-01",
        type=event_type,
        subject=MISSION,
        time=TIME,
        dataschema=TELEMETRY_SCHEMA,
        sequence="000000000000042",
        correlation_id="c-0000000001",
        traceparent=TRACEPARENT,
        data=dict(TELEMETRY_PAYLOAD),
    )


def _refusal_of(envelope: Envelope) -> tuple[ViewRefusal, str, object]:
    """Return the refusal, member, and value projecting ``envelope`` raises, failing if accepted."""
    try:
        project(envelope)
    except ViewError as error:
        return (error.refusal, error.attribute, error.value)
    message = f"projected: {envelope!r}"
    raise AssertionError(message)


class ProjectionTests(unittest.TestCase):
    def test_a_telemetry_envelope_projects_to_a_dashboard_event(self) -> None:
        # Arrange
        envelope = _envelope()

        # Act
        event = project(envelope)

        # Assert
        self.assertEqual(
            DashboardEvent(
                "droneTelemetry", EventClass.TELEMETRY, MISSION, TIME, PROJECTED_TELEMETRY
            ),
            event,
        )

    def test_the_projection_carries_the_envelope_instant_for_the_timeline(self) -> None:
        # Arrange
        envelope = _envelope()

        # Act
        event = project(envelope)

        # Assert
        self.assertEqual(TIME, event.time)

    def test_the_projection_carries_the_mission_once_and_drops_it_from_the_payload(self) -> None:
        # Arrange
        envelope = _envelope()

        # Act
        event = project(envelope)

        # Assert
        self.assertEqual((MISSION, False), (event.mission, "missionId" in event.data))

    def test_the_projection_carries_no_transport_member(self) -> None:
        # Arrange
        envelope = _envelope()

        # Act
        event = project(envelope)

        # Assert
        self.assertEqual((), tuple(name for name in TRANSPORT_MEMBERS if name in event.data))

    def test_an_event_type_with_no_projection_is_refused(self) -> None:
        # Arrange
        envelope = _envelope("aerial-rescue.v1.drone.event")

        # Act
        refusal = _refusal_of(envelope)

        # Assert
        self.assertEqual((ViewRefusal.UNPROJECTED, "type", "aerial-rescue.v1.drone.event"), refusal)


class ProjectionTableTests(unittest.TestCase):
    def test_every_projection_names_an_event_type_with_a_bound_payload_schema(self) -> None:
        # Arrange
        projected = frozenset(PROJECTIONS)

        # Act
        unbound = projected - frozenset(BINDINGS)

        # Assert
        self.assertEqual(frozenset(), unbound)

    def test_telemetry_is_projected(self) -> None:
        # Arrange
        table = PROJECTIONS

        # Act
        projection = table[TELEMETRY_TYPE]

        # Assert
        self.assertEqual(
            ("droneTelemetry", EventClass.TELEMETRY), (projection.kind, projection.event_class)
        )


class DroppabilityTests(unittest.TestCase):
    def test_telemetry_is_droppable(self) -> None:
        # Arrange
        event_class = EventClass.TELEMETRY

        # Act
        verdict = droppable(event_class)

        # Assert
        self.assertIs(True, verdict)

    def test_every_class_other_than_telemetry_is_never_dropped(self) -> None:
        # Arrange
        others = tuple(member for member in EventClass if member is not EventClass.TELEMETRY)

        # Act
        verdicts = tuple(droppable(member) for member in others)

        # Assert
        self.assertEqual(tuple(False for _ in others), verdicts)

    def test_the_approval_evidence_and_audit_classes_exist_and_are_never_dropped(self) -> None:
        # Arrange
        protected = (EventClass.APPROVAL, EventClass.EVIDENCE, EventClass.AUDIT)

        # Act
        verdicts = tuple(droppable(member) for member in protected)

        # Assert
        self.assertEqual((False, False, False), verdicts)

    def test_the_per_client_buffer_holds_two_hundred_and_fifty_six_events(self) -> None:
        # Arrange
        bound = MAX_BUFFERED_EVENTS

        # Act
        observed = int(bound)

        # Assert
        self.assertEqual(256, observed)


class ViewErrorTests(unittest.TestCase):
    def test_the_message_names_refusal_attribute_and_value(self) -> None:
        # Arrange
        error = ViewError(ViewRefusal.UNPROJECTED, "type", "aerial-rescue.v1.drone.event")

        # Act
        message = str(error)

        # Assert
        self.assertEqual(
            "event type has no dashboard projection: type='aerial-rescue.v1.drone.event'",
            message,
        )
