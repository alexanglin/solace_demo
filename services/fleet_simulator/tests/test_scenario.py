"""The frozen scenario value the composition root supplies, and every refusal it carries.

ADR-0077 fixes this boundary: the member reads no file, no environment variable, no broker
message, no clock, and no random source to obtain a scenario. These tests therefore need
none of those either -- a scenario is a literal.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import Enum
from typing import Final

import pytest
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_fleet_simulator.bounds import (
    ALTITUDE_METRES,
    BATTERY_PERMILLE,
    EAST_MICRODEGREES_PER_TICK,
    GROUND_SPEED_CENTIMETRES_PER_SECOND,
    HEADING_DEGREES,
    LATITUDE_MICRODEGREES,
    LONGITUDE_MICRODEGREES,
    NORTH_MICRODEGREES_PER_TICK,
)
from aerial_rescue_fleet_simulator.scenario import (
    DroneStart,
    FleetScenario,
    ScenarioError,
    ScenarioRefusal,
    ordered_drones,
    sectors,
)

pytestmark = [pytest.mark.unit]

THRESHOLDS: Final = ConnectivityThresholds(
    misses_to_degraded=3, misses_to_offline=6, heartbeats_to_recover=2
)

DRONE: Final = DroneStart(
    drone_id="drone-vision-01",
    sector_id="sector-north",
    latitude_microdegrees=47_123_456,
    longitude_microdegrees=-122_654_321,
    altitude_metres=412,
    heading_degrees=270,
    ground_speed_centimetres_per_second=850,
    battery_permille=870,
    north_microdegrees_per_tick=0,
    east_microdegrees_per_tick=-76,
    battery_drain_permille_per_tick=2,
)
OTHER: Final = replace(
    DRONE, drone_id="drone-thermal-02", sector_id="sector-east", heading_degrees=90
)


def _scenario(
    *,
    mission_id: str = "m-2026-0001",
    drones: tuple[DroneStart, ...] = (DRONE, OTHER),
    tick_interval_milliseconds: int = 1_000,
    ticks_to_sweep: int = 4,
    absent_heartbeats: Mapping[str, frozenset[int]] | None = None,
) -> FleetScenario:
    """Return an accepted scenario, with any member replaced."""
    return FleetScenario(
        mission_id=mission_id,
        drones=drones,
        tick_interval_milliseconds=tick_interval_milliseconds,
        thresholds=THRESHOLDS,
        ticks_to_sweep=ticks_to_sweep,
        absent_heartbeats={} if absent_heartbeats is None else absent_heartbeats,
    )


def _refusal(build: Callable[[], object]) -> tuple[Enum, object]:
    """Return the refusal a construction raises, failing the test if it is accepted."""
    try:
        built = build()
    except ScenarioError as error:
        return (error.refusal, error.value)
    message = f"accepted: {built!r}"
    raise AssertionError(message)


class AcceptedScenarioTests(unittest.TestCase):
    def test_the_roster_order_does_not_decide_the_fold_order(self) -> None:
        # Arrange
        reversed_roster = _scenario(drones=(OTHER, DRONE))

        # Act
        order = tuple(drone.drone_id for drone in ordered_drones(reversed_roster))

        # Assert
        self.assertEqual(("drone-thermal-02", "drone-vision-01"), order)

    def test_the_sectors_are_the_distinct_assignments_in_ascending_order(self) -> None:
        # Arrange
        third = replace(DRONE, drone_id="drone-audio-03", sector_id="sector-east")

        # Act
        assigned = sectors(_scenario(drones=(DRONE, OTHER, third)))

        # Assert
        self.assertEqual(("sector-east", "sector-north"), assigned)

    def test_a_stationary_drone_declaring_no_ground_speed_is_accepted(self) -> None:
        # Arrange
        parked = replace(
            DRONE,
            ground_speed_centimetres_per_second=0,
            north_microdegrees_per_tick=0,
            east_microdegrees_per_tick=0,
        )

        # Act
        accepted = _scenario(drones=(parked,))

        # Assert
        self.assertEqual((parked,), accepted.drones)

    def test_a_schedule_of_absent_heartbeats_is_kept_exactly_as_given(self) -> None:
        # Arrange
        schedule = {DRONE.drone_id: frozenset({3, 7})}

        # Act
        accepted = _scenario(absent_heartbeats=schedule)

        # Assert
        self.assertEqual(schedule, dict(accepted.absent_heartbeats))


class DroneRefusalTests(unittest.TestCase):
    def test_a_value_one_step_outside_its_bound_is_refused_and_names_the_member(self) -> None:
        # Arrange
        cases: tuple[tuple[str, Callable[[], DroneStart]], ...] = (
            (
                "latitude_microdegrees",
                lambda: replace(DRONE, latitude_microdegrees=LATITUDE_MICRODEGREES.high + 1),
            ),
            (
                "latitude_microdegrees",
                lambda: replace(DRONE, latitude_microdegrees=LATITUDE_MICRODEGREES.low - 1),
            ),
            (
                "longitude_microdegrees",
                lambda: replace(DRONE, longitude_microdegrees=LONGITUDE_MICRODEGREES.high + 1),
            ),
            (
                "longitude_microdegrees",
                lambda: replace(DRONE, longitude_microdegrees=LONGITUDE_MICRODEGREES.low - 1),
            ),
            ("altitude_metres", lambda: replace(DRONE, altitude_metres=ALTITUDE_METRES.high + 1)),
            ("altitude_metres", lambda: replace(DRONE, altitude_metres=ALTITUDE_METRES.low - 1)),
            ("heading_degrees", lambda: replace(DRONE, heading_degrees=HEADING_DEGREES.high + 1)),
            ("heading_degrees", lambda: replace(DRONE, heading_degrees=HEADING_DEGREES.low - 1)),
            (
                "ground_speed_centimetres_per_second",
                lambda: replace(
                    DRONE,
                    ground_speed_centimetres_per_second=GROUND_SPEED_CENTIMETRES_PER_SECOND.high
                    + 1,
                ),
            ),
            (
                "battery_permille",
                lambda: replace(DRONE, battery_permille=BATTERY_PERMILLE.high + 1),
            ),
            ("battery_permille", lambda: replace(DRONE, battery_permille=BATTERY_PERMILLE.low - 1)),
            (
                "battery_drain_permille_per_tick",
                lambda: replace(DRONE, battery_drain_permille_per_tick=BATTERY_PERMILLE.low - 1),
            ),
            (
                "north_microdegrees_per_tick",
                lambda: replace(
                    DRONE, north_microdegrees_per_tick=NORTH_MICRODEGREES_PER_TICK.high + 1
                ),
            ),
            (
                "east_microdegrees_per_tick",
                lambda: replace(
                    DRONE, east_microdegrees_per_tick=EAST_MICRODEGREES_PER_TICK.low - 1
                ),
            ),
        )

        # Act
        refusals = tuple((member, _refusal(build)) for member, build in cases)

        # Assert
        self.assertEqual(
            tuple((member, (ScenarioRefusal.OUT_OF_RANGE, member)) for member, _ in cases),
            refusals,
        )

    def test_an_identifier_outside_the_topic_form_is_refused(self) -> None:
        # Arrange
        cases: tuple[Callable[[], DroneStart], ...] = (
            lambda: replace(DRONE, drone_id="Drone-01"),
            lambda: replace(DRONE, drone_id="-drone"),
            lambda: replace(DRONE, drone_id=""),
            lambda: replace(DRONE, sector_id="sector north"),
        )

        # Act
        refusals = tuple(_refusal(build)[0] for build in cases)

        # Assert
        self.assertEqual((ScenarioRefusal.IDENTIFIER_FORM,) * len(cases), refusals)

    def test_a_declared_ground_speed_must_agree_with_the_displacement_about_moving(self) -> None:
        # Arrange
        cases: tuple[Callable[[], DroneStart], ...] = (
            lambda: replace(DRONE, north_microdegrees_per_tick=0, east_microdegrees_per_tick=0),
            lambda: replace(DRONE, ground_speed_centimetres_per_second=0),
        )

        # Act
        refusals = tuple(_refusal(build)[0] for build in cases)

        # Assert
        self.assertEqual((ScenarioRefusal.MOTION_DISAGREEMENT,) * len(cases), refusals)


class ScenarioRefusalTests(unittest.TestCase):
    def test_a_scenario_with_no_drones_folds_nothing_and_is_refused(self) -> None:
        # Arrange
        expected = (ScenarioRefusal.EMPTY_ROSTER, ())

        # Act
        refusal = _refusal(lambda: _scenario(drones=()))

        # Assert
        self.assertEqual(expected, refusal)

    def test_two_roster_entries_naming_one_drone_are_refused(self) -> None:
        # Arrange
        repeated = (DRONE, replace(OTHER, drone_id=DRONE.drone_id))

        # Act
        refusal = _refusal(lambda: _scenario(drones=repeated))

        # Assert
        self.assertEqual((ScenarioRefusal.DUPLICATE_DRONE, DRONE.drone_id), refusal)

    def test_a_mission_identifier_outside_the_topic_form_is_refused(self) -> None:
        # Arrange
        expected = (ScenarioRefusal.IDENTIFIER_FORM, "mission_id")

        # Act
        refusal = _refusal(lambda: _scenario(mission_id="M-2026-0001"))

        # Assert
        self.assertEqual(expected, refusal)

    def test_a_count_below_one_cannot_advance_the_fold_and_is_refused(self) -> None:
        # Arrange
        cases: tuple[tuple[str, Callable[[], FleetScenario]], ...] = (
            ("tick_interval_milliseconds", lambda: _scenario(tick_interval_milliseconds=0)),
            ("ticks_to_sweep", lambda: _scenario(ticks_to_sweep=0)),
        )

        # Act
        refusals = tuple(_refusal(build) for _, build in cases)

        # Assert
        self.assertEqual(
            tuple((ScenarioRefusal.NON_POSITIVE, member) for member, _ in cases), refusals
        )

    def test_a_schedule_naming_a_drone_the_roster_does_not_is_refused(self) -> None:
        # Arrange
        schedule = {"drone-ghost-99": frozenset({1})}

        # Act
        refusal = _refusal(lambda: _scenario(absent_heartbeats=schedule))

        # Assert
        self.assertEqual((ScenarioRefusal.UNKNOWN_DRONE, "drone-ghost-99"), refusal)

    def test_a_schedule_naming_a_tick_before_the_run_starts_is_refused(self) -> None:
        # Arrange
        schedule = {DRONE.drone_id: frozenset({-1})}

        # Act
        refusal = _refusal(lambda: _scenario(absent_heartbeats=schedule))

        # Assert
        self.assertEqual((ScenarioRefusal.NEGATIVE_TICK, -1), refusal)


if __name__ == "__main__":
    unittest.main()
