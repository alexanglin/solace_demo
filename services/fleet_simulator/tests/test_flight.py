"""The integer point-mass advance, and the refusal that keeps a false position off the wire.

ADR-0078 makes motion integer addition of a declared per-tick displacement. There is no
trigonometry here, so the same scenario produces the same track on both supported
platforms rather than depending on the C library's `cos`.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from enum import Enum
from typing import Final

import pytest
from aerial_rescue_fleet_simulator.bounds import (
    BATTERY_PERMILLE,
    LATITUDE_MICRODEGREES,
    LONGITUDE_MICRODEGREES,
    PERMILLE_PER_PERCENT,
)
from aerial_rescue_fleet_simulator.flight import (
    DroneState,
    FlightError,
    FlightRefusal,
    advance,
    battery_percent,
    initial_state,
)
from aerial_rescue_fleet_simulator.scenario import DroneStart

pytestmark = [pytest.mark.unit]

DRONE: Final = DroneStart(
    drone_id="drone-vision-01",
    sector_id="sector-north",
    latitude_microdegrees=47_123_456,
    longitude_microdegrees=-122_654_321,
    altitude_metres=412,
    heading_degrees=270,
    ground_speed_centimetres_per_second=850,
    battery_permille=870,
    north_microdegrees_per_tick=13,
    east_microdegrees_per_tick=-76,
    battery_drain_permille_per_tick=2,
)


def _fold(start: DroneStart, ticks: int) -> DroneState:
    """Advance one drone through ``ticks`` ticks from its starting state."""
    state = initial_state(start)
    for _ in range(ticks):
        state = advance(state, start)
    return state


def _refusal(state: DroneState, start: DroneStart) -> tuple[Enum, object]:
    """Return the refusal an advance raises, failing the test if it is accepted."""
    try:
        advanced = advance(state, start)
    except FlightError as error:
        return (error.refusal, error.value)
    message = f"accepted: {advanced!r}"
    raise AssertionError(message)


class InitialStateTests(unittest.TestCase):
    def test_a_drone_begins_where_and_as_charged_as_its_scenario_entry_says(self) -> None:
        # Arrange
        expected = DroneState(47_123_456, -122_654_321, 870)

        # Act
        state = initial_state(DRONE)

        # Assert
        self.assertEqual(expected, state)


class AdvanceTests(unittest.TestCase):
    def test_each_tick_adds_the_declared_displacement_exactly(self) -> None:
        # Arrange
        ticks = 5

        # Act
        state = _fold(DRONE, ticks)

        # Assert
        self.assertEqual(
            (47_123_456 + 13 * ticks, -122_654_321 - 76 * ticks),
            (state.latitude_microdegrees, state.longitude_microdegrees),
        )

    def test_each_tick_drains_the_declared_rate_from_the_battery(self) -> None:
        # Arrange
        ticks = 7

        # Act
        state = _fold(DRONE, ticks)

        # Assert
        self.assertEqual(870 - 2 * ticks, state.battery_permille)

    def test_a_battery_stops_at_empty_rather_than_going_below_it(self) -> None:
        # Arrange
        thirsty = replace(DRONE, battery_permille=3, battery_drain_permille_per_tick=2)

        # Act
        state = _fold(thirsty, 4)

        # Assert
        self.assertEqual(BATTERY_PERMILLE.low, state.battery_permille)

    def test_a_step_landing_exactly_on_a_bound_is_accepted(self) -> None:
        # Arrange
        edge = replace(
            DRONE,
            latitude_microdegrees=LATITUDE_MICRODEGREES.high - 13,
            longitude_microdegrees=LONGITUDE_MICRODEGREES.low + 76,
            east_microdegrees_per_tick=-76,
        )

        # Act
        state = advance(initial_state(edge), edge)

        # Assert
        self.assertEqual(
            (LATITUDE_MICRODEGREES.high, LONGITUDE_MICRODEGREES.low),
            (state.latitude_microdegrees, state.longitude_microdegrees),
        )


class OutOfRangeTests(unittest.TestCase):
    def test_a_step_past_a_coordinate_bound_is_refused_and_names_the_drone(self) -> None:
        # Arrange
        cases = (
            replace(DRONE, latitude_microdegrees=LATITUDE_MICRODEGREES.high - 12),
            replace(
                DRONE,
                latitude_microdegrees=LATITUDE_MICRODEGREES.low + 11,
                north_microdegrees_per_tick=-13,
            ),
            replace(DRONE, longitude_microdegrees=LONGITUDE_MICRODEGREES.low + 75),
            replace(
                DRONE,
                longitude_microdegrees=LONGITUDE_MICRODEGREES.high - 75,
                east_microdegrees_per_tick=76,
            ),
        )

        # Act
        refusals = tuple(_refusal(initial_state(start), start) for start in cases)

        # Assert
        self.assertEqual(((FlightRefusal.OUT_OF_RANGE, DRONE.drone_id),) * len(cases), refusals)


class BatteryPercentTests(unittest.TestCase):
    def test_the_published_percent_is_the_floor_of_the_permille_reserve(self) -> None:
        # Arrange
        reserves = (0, 9, 10, 999, BATTERY_PERMILLE.high)

        # Act
        published = tuple(battery_percent(DroneState(0, 0, reserve)) for reserve in reserves)

        # Assert
        self.assertEqual((0, 0, 1, 99, BATTERY_PERMILLE.high // PERMILLE_PER_PERCENT), published)


if __name__ == "__main__":
    unittest.main()
