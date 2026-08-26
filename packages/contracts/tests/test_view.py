"""Normalized dashboard events and the reduced state they fold into.

A dashboard event carries what the operator's browser needs and nothing from the transport
(``docs/adr/0067-normalized-dashboard-events-and-reduced-state.md``). The envelope's ``time``
crosses the boundary because the timeline shows it; ``id``, ``source``, ``sequence``,
``dataschema``, and the trace context do not. An event type with no projection is refused,
which is the same shape as the unbound-type refusal in ADR-0037.

The fold is the replay determinism oracle of ADR-0009, so it is tested for the two properties
that oracle needs: one event sequence always reaches one digest, and two states differing only
in the order their drones arrived are one state.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Final

from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.digest import Context, digest
from aerial_rescue_contracts.envelope import BINDINGS, Envelope
from aerial_rescue_contracts.view import (
    EMPTY_STATE,
    MAX_BUFFERED_EVENTS,
    PROJECTIONS,
    DashboardEvent,
    DashboardState,
    Drone,
    EventClass,
    ViewError,
    ViewRefusal,
    apply,
    droppable,
    project,
    reduce_events,
    state_digest,
    state_document,
)

TELEMETRY_TYPE: Final = "aerial-rescue.v1.drone.telemetry"
TELEMETRY_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
)
MISSION: Final = "m-2026-0001"
DRONE: Final = "drone-vision-01"
TIME: Final = "2026-08-21T09:15:30.250Z"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01"

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


def _payload(**overrides: object) -> dict[str, object]:
    """Return the baseline telemetry payload with the named members replaced."""
    return {**TELEMETRY_PAYLOAD, **overrides}


def _envelope(
    event_type: str = TELEMETRY_TYPE,
    *,
    mission: str = MISSION,
    payload: Mapping[str, object] | None = None,
) -> Envelope:
    """Return a valid telemetry envelope, optionally with another type, mission, or payload."""
    data = dict(TELEMETRY_PAYLOAD if payload is None else payload)
    data["missionId"] = mission
    return Envelope(
        id="e-0000000001",
        source="urn:aerial-rescue:simulator:fleet-01",
        type=event_type,
        subject=mission,
        time=TIME,
        dataschema=TELEMETRY_SCHEMA,
        sequence="000000000000042",
        correlation_id="c-0000000001",
        traceparent=TRACEPARENT,
        data=data,
    )


def _refusal_of(envelope: Envelope) -> tuple[ViewRefusal, str, object]:
    """Return the refusal, member, and value projecting ``envelope`` raises, failing if accepted."""
    try:
        project(envelope)
    except ViewError as error:
        return (error.refusal, error.attribute, error.value)
    message = f"projected: {envelope!r}"
    raise AssertionError(message)


def _apply_refusal_of(
    state: DashboardState, event: DashboardEvent
) -> tuple[ViewRefusal, str, object]:
    """Return the refusal, member, and value folding ``event`` raises, failing if it folds."""
    try:
        apply(state, event)
    except ViewError as error:
        return (error.refusal, error.attribute, error.value)
    message = f"applied: {event!r}"
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


class DroneTests(unittest.TestCase):
    def test_a_drone_carries_every_telemetry_field(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))

        # Act
        drone = state.drones[0]

        # Assert
        self.assertEqual(
            Drone(DRONE, 47123456, -122654321, 87, 412, 270, 850),
            drone,
        )


class FoldTests(unittest.TestCase):
    def test_the_empty_state_carries_no_mission_and_no_drone(self) -> None:
        # Arrange
        state = EMPTY_STATE

        # Act
        observed = (state.mission, state.drones)

        # Assert
        self.assertEqual((None, ()), observed)

    def test_the_first_telemetry_event_adopts_the_mission(self) -> None:
        # Arrange
        event = project(_envelope())

        # Act
        state = apply(EMPTY_STATE, event)

        # Assert
        self.assertEqual(MISSION, state.mission)

    def test_a_second_report_from_one_drone_supersedes_the_first(self) -> None:
        # Arrange
        first = project(_envelope())
        second = project(_envelope(payload=_payload(batteryPercent=61)))

        # Act
        state = reduce_events((first, second))

        # Assert
        self.assertEqual((1, 61), (len(state.drones), state.drones[0].battery_percent))

    def test_drones_are_held_in_ascending_byte_order_of_their_identifier(self) -> None:
        # Arrange
        identifiers = ("drone-sim-20", "drone-comms-03", "drone-vision-01")
        events = tuple(project(_envelope(payload=_payload(droneId=name))) for name in identifiers)

        # Act
        state = reduce_events(events)

        # Assert
        self.assertEqual(tuple(sorted(identifiers)), tuple(d.drone_id for d in state.drones))

    def test_insertion_order_does_not_change_the_state(self) -> None:
        # Arrange
        identifiers = ("drone-sim-20", "drone-comms-03", "drone-vision-01")
        events = tuple(project(_envelope(payload=_payload(droneId=name))) for name in identifiers)

        # Act
        both = (reduce_events(events), reduce_events(tuple(reversed(events))))

        # Assert
        self.assertEqual(both[0], both[1])

    def test_an_event_from_another_mission_is_refused(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))
        other = project(_envelope(mission="m-2026-0002"))

        # Act
        refusal = _apply_refusal_of(state, other)

        # Assert
        self.assertEqual((ViewRefusal.MISSION_MISMATCH, "mission", "m-2026-0002"), refusal)

    def test_a_kind_with_no_state_rule_is_refused(self) -> None:
        # Arrange
        event = DashboardEvent("droneHalo", EventClass.TELEMETRY, MISSION, TIME, {})

        # Act
        refusal = _apply_refusal_of(EMPTY_STATE, event)

        # Assert
        self.assertEqual((ViewRefusal.UNPROJECTED, "kind", "droneHalo"), refusal)

    def test_a_field_outside_its_type_is_refused(self) -> None:
        # Arrange
        event = project(_envelope(payload=_payload(batteryPercent="87")))

        # Act
        refusal = _apply_refusal_of(EMPTY_STATE, event)

        # Assert
        self.assertEqual((ViewRefusal.FIELD_FORM, "batteryPercent", "87"), refusal)

    def test_an_absent_field_is_refused(self) -> None:
        # Arrange
        event = DashboardEvent("droneTelemetry", EventClass.TELEMETRY, MISSION, TIME, {})

        # Act
        refusal = _apply_refusal_of(EMPTY_STATE, event)

        # Assert
        self.assertEqual((ViewRefusal.FIELD_FORM, "droneId", None), refusal)

    def test_a_drone_identifier_of_another_type_reports_the_value_it_carried(self) -> None:
        # Arrange
        event = project(_envelope(payload=_payload(droneId=7)))

        # Act
        refusal = _apply_refusal_of(EMPTY_STATE, event)

        # Assert
        self.assertEqual((ViewRefusal.FIELD_FORM, "droneId", 7), refusal)

    def test_a_boolean_is_not_an_integer_field(self) -> None:
        # Arrange
        event = project(_envelope(payload=_payload(batteryPercent=True)))

        # Act
        refusal = _apply_refusal_of(EMPTY_STATE, event)

        # Assert
        self.assertEqual((ViewRefusal.FIELD_FORM, "batteryPercent", True), refusal)

    def test_reducing_no_events_is_the_empty_state(self) -> None:
        # Arrange
        events: tuple[DashboardEvent, ...] = ()

        # Act
        state = reduce_events(events)

        # Assert
        self.assertEqual(EMPTY_STATE, state)


class StateDocumentTests(unittest.TestCase):
    def test_the_empty_state_omits_the_mission_rather_than_carrying_null(self) -> None:
        # Arrange
        state = EMPTY_STATE

        # Act
        document = state_document(state)

        # Assert
        self.assertEqual({"canonicalizationVersion": 1, "drones": [], "viewVersion": 1}, document)

    def test_a_folded_state_documents_the_mission_and_every_drone(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))

        # Act
        document = state_document(state)

        # Assert
        self.assertEqual(
            {
                "canonicalizationVersion": 1,
                "mission": MISSION,
                "drones": [
                    {
                        "altitudeMetres": 412,
                        "batteryPercent": 87,
                        "droneId": DRONE,
                        "groundSpeedCentimetresPerSecond": 850,
                        "headingDegrees": 270,
                        "latitudeMicrodegrees": 47123456,
                        "longitudeMicrodegrees": -122654321,
                    }
                ],
                "viewVersion": 1,
            },
            document,
        )

    def test_the_document_canonicalizes(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))

        # Act
        encoded = canonical_bytes(state_document(state))

        # Assert
        self.assertIsInstance(encoded, bytes)


class StateDigestTests(unittest.TestCase):
    def test_the_digest_is_lowercase_hexadecimal_sha256(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))

        # Act
        observed = state_digest(state)

        # Assert
        self.assertRegex(observed, "^[0-9a-f]{64}$")

    def test_ten_folds_of_one_event_sequence_agree_on_one_digest(self) -> None:
        # Arrange
        events = tuple(
            project(_envelope(payload=_payload(droneId=f"drone-sim-{index:02d}")))
            for index in range(1, 6)
        )

        # Act
        digests = frozenset(state_digest(reduce_events(events)) for _ in range(10))

        # Assert
        self.assertEqual(1, len(digests))

    def test_states_differing_only_in_insertion_order_agree_on_one_digest(self) -> None:
        # Arrange
        events = tuple(
            project(_envelope(payload=_payload(droneId=name)))
            for name in ("drone-sim-20", "drone-comms-03")
        )

        # Act
        digests = (state_digest(reduce_events(events)), state_digest(reduce_events(events[::-1])))

        # Assert
        self.assertEqual(digests[0], digests[1])

    def test_a_different_battery_reading_changes_the_digest(self) -> None:
        # Arrange
        first = apply(EMPTY_STATE, project(_envelope()))
        second = apply(EMPTY_STATE, project(_envelope(payload=_payload(batteryPercent=61))))

        # Act
        digests = (state_digest(first), state_digest(second))

        # Assert
        self.assertNotEqual(digests[0], digests[1])

    def test_the_digest_is_taken_under_the_replay_state_context(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))

        # Act
        observed = state_digest(state)

        # Assert
        self.assertEqual(digest(Context.REPLAY_STATE, state_document(state)), observed)

    def test_the_replay_state_digest_is_not_the_proposal_digest_of_the_same_bytes(self) -> None:
        # Arrange
        state = apply(EMPTY_STATE, project(_envelope()))

        # Act
        document = state_document(state)

        # Assert
        self.assertNotEqual(digest(Context.PROPOSAL, document), state_digest(state))
