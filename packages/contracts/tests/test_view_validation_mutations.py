"""Focused boundary witnesses for normalized dashboard validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import override

from aerial_rescue_contracts.envelope import Envelope
from aerial_rescue_contracts.view import (
    CheckpointAccepted,
    DashboardEvent,
    EventClass,
    FoldRefused,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerRefusal,
    ViewError,
    ViewRefusal,
    _event_identifier,
    checkpoint_from_snapshot,
    fold_ordered_event,
    prepare_checkpoint,
    project,
)

MISSION = "mission-synthetic-0001"
DRONE = "drone-sim-01"
TIME = "2026-08-24T12:00:01.000Z"
TELEMETRY_TYPE = "aerial-rescue.v1.drone.telemetry"
TELEMETRY_SCHEMA = "https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
BASE_TELEMETRY: dict[str, object] = {
    "missionId": MISSION,
    "droneId": DRONE,
    "latitudeMicrodegrees": 44_475_000,
    "longitudeMicrodegrees": -79_245_000,
    "batteryPercent": 96,
    "altitudeMetres": 83,
    "headingDegrees": 45,
    "groundSpeedCentimetresPerSecond": 950,
}
TELEMETRY_BOUNDARIES = (
    ("latitudeMicrodegrees", -90_000_000),
    ("latitudeMicrodegrees", 90_000_000),
    ("longitudeMicrodegrees", -180_000_000),
    ("longitudeMicrodegrees", 180_000_000),
    ("batteryPercent", 0),
    ("batteryPercent", 100),
    ("altitudeMetres", -500),
    ("altitudeMetres", 20_000),
    ("headingDegrees", 0),
    ("headingDegrees", 359),
    ("groundSpeedCentimetresPerSecond", 0),
    ("groundSpeedCentimetresPerSecond", 10_000),
)
TELEMETRY_OUTSIDE_BOUNDARIES = (
    ("latitudeMicrodegrees", -90_000_001),
    ("latitudeMicrodegrees", 90_000_001),
    ("longitudeMicrodegrees", -180_000_001),
    ("longitudeMicrodegrees", 180_000_001),
    ("batteryPercent", -1),
    ("batteryPercent", 101),
    ("altitudeMetres", -501),
    ("altitudeMetres", 20_001),
    ("headingDegrees", -1),
    ("headingDegrees", 360),
    ("groundSpeedCentimetresPerSecond", -1),
    ("groundSpeedCentimetresPerSecond", 10_001),
)


class _ReverseText(str):
    """A string whose rich comparison cannot substitute for its UTF-8 byte key."""

    @override
    def __lt__(self, other: str) -> bool:
        return str(self) > str(other)


def _telemetry_envelope(data: Mapping[str, object]) -> Envelope:
    """Return a locally constructed envelope at the already-validated projection boundary."""
    return Envelope(
        id="event-synthetic-0001",
        source="urn:aerial-rescue:simulator:fleet-synthetic-01",
        type=TELEMETRY_TYPE,
        subject=MISSION,
        time=TIME,
        dataschema=TELEMETRY_SCHEMA,
        sequence="000000000000001",
        correlation_id="correlation-synthetic-0001",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        data=data,
    )


def _telemetry_with(name: str, value: int) -> dict[str, object]:
    """Return one telemetry payload differing in exactly one integer member."""
    return {**BASE_TELEMETRY, name: value}


def _projection_refusal(data: Mapping[str, object]) -> tuple[ViewRefusal, str, object]:
    """Project one payload and return its structured refusal."""
    try:
        project(_telemetry_envelope(data))
    except ViewError as error:
        return (error.refusal, error.attribute, error.value)
    message = f"projected malformed telemetry: {data!r}"
    raise AssertionError(message)


def _prepared(*, sectors: tuple[str, ...] = ("sector-01",)) -> PreparedMission:
    """Return a member-local prepared mission for reducer-boundary tests."""
    return PreparedMission(MISSION, None, (DRONE,), (), sectors)


def _normalized_telemetry_with(name: str, value: int) -> dict[str, object]:
    """Return one normalized telemetry event payload with one changed integer."""
    return {
        key: (value if key == name else member)
        for key, member in BASE_TELEMETRY.items()
        if key != "missionId"
    }


def _reducer_refusal(data: Mapping[str, object]) -> tuple[ReducerRefusal, str, object]:
    """Fold one normalized telemetry payload and return its structured refusal."""
    outcome = fold_ordered_event(
        prepare_checkpoint(_prepared()),
        OrderedDashboardEvent(
            1,
            DashboardEvent("droneTelemetry", EventClass.TELEMETRY, MISSION, TIME, data),
        ),
    )
    if isinstance(outcome, FoldRefused):
        return (outcome.refusal, outcome.attribute, outcome.value)
    message = f"reduced malformed telemetry: {data!r}"
    raise AssertionError(message)


def test_projection_accepts_every_inclusive_telemetry_integer_boundary() -> None:
    # Arrange
    boundaries = TELEMETRY_BOUNDARIES

    # Act
    observed = tuple(
        project(_telemetry_envelope(_telemetry_with(name, value))).data[name]
        for name, value in boundaries
    )

    # Assert
    assert observed == tuple(value for _, value in boundaries)


def test_projection_refuses_each_value_one_step_outside_a_telemetry_integer_boundary() -> None:
    # Arrange
    outside = TELEMETRY_OUTSIDE_BOUNDARIES

    # Act
    observed = tuple(_projection_refusal(_telemetry_with(name, value)) for name, value in outside)

    # Assert
    assert observed == tuple(
        (ViewRefusal.MALFORMED_PAYLOAD, name, value) for name, value in outside
    )


def test_reducer_refuses_each_value_one_step_outside_a_telemetry_integer_boundary() -> None:
    # Arrange
    outside = TELEMETRY_OUTSIDE_BOUNDARIES

    # Act
    observed = tuple(
        _reducer_refusal(_normalized_telemetry_with(name, value)) for name, value in outside
    )

    # Assert
    assert observed == tuple((ReducerRefusal.EVENT_DATA, name, value) for name, value in outside)


def test_projection_orders_unknown_members_by_their_utf8_bytes() -> None:
    # Arrange
    first = _ReverseText("extra-a")
    second = _ReverseText("extra-b")
    data = {**BASE_TELEMETRY, second: "second", first: "first"}

    # Act
    refusal = _projection_refusal(data)

    # Assert
    assert refusal == (ViewRefusal.MALFORMED_PAYLOAD, first, "first")


def test_prepare_checkpoint_orders_sector_identifiers_by_their_utf8_bytes() -> None:
    # Arrange
    first = _ReverseText("sector-a")
    second = _ReverseText("sector-b")
    prepared = _prepared(sectors=(second, first))

    # Act
    checkpoint = prepare_checkpoint(prepared)

    # Assert
    assert tuple(sector.identifier for sector in checkpoint.state.sectors) == (first, second)


def test_snapshot_anchor_accepts_identifiers_in_utf8_byte_order() -> None:
    # Arrange
    first = _ReverseText("sector-a")
    second = _ReverseText("sector-b")
    checkpoint = prepare_checkpoint(_prepared(sectors=(second, first)))

    # Act
    outcome = checkpoint_from_snapshot(checkpoint.state, checkpoint.latest_event_digest)

    # Assert
    assert isinstance(outcome, CheckpointAccepted)


def test_ordered_event_refusal_selects_the_first_unknown_utf8_member() -> None:
    # Arrange
    first = _ReverseText("extra-a")
    second = _ReverseText("extra-b")
    checkpoint = prepare_checkpoint(_prepared())
    event = OrderedDashboardEvent(
        1,
        DashboardEvent(
            "missionLifecycle",
            EventClass.MISSION,
            MISSION,
            TIME,
            {"lifecycle": "SEARCHING", second: "second", first: "first"},
        ),
    )

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert (outcome.refusal, outcome.attribute, outcome.value) == (
        ReducerRefusal.EVENT_DATA,
        first,
        "first",
    )


def test_sector_event_validates_identifier_before_state_and_assignment() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = OrderedDashboardEvent(
        1,
        DashboardEvent(
            "sectorLifecycle",
            EventClass.MISSION,
            MISSION,
            TIME,
            {"sectorId": "", "state": "INVALID", "assignedMemberId": None},
        ),
    )

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert (outcome.refusal, outcome.attribute, outcome.value) == (
        ReducerRefusal.EVENT_DATA,
        "sectorId",
        "",
    )


def test_event_identifier_returns_the_exact_validated_string_object() -> None:
    # Arrange
    identifier = "drone-sim-01"

    # Act
    observed = _event_identifier({"droneId": identifier}, "droneId")

    # Assert
    assert observed is identifier
