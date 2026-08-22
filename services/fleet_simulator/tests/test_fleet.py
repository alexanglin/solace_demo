"""One tick: one observation per drone, in identifier order, and the edges it implies.

ADR-0078 fixes what a tick is. These tests drive the real domain machines rather than a
copy of their tables: the connectivity fold, the sector table, and the mission table all
come from `aerial_rescue_domain`, so a refused edge here is the domain refusing it.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from enum import Enum
from typing import Final

import pytest
from aerial_rescue_domain.connectivity import ConnectivityState, ConnectivityThresholds
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_domain.sectors import SectorState
from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.bounds import LATITUDE_MICRODEGREES
from aerial_rescue_fleet_simulator.fleet import (
    FleetError,
    FleetRefusal,
    FleetState,
    advance_tick,
    initial_fleet,
)
from aerial_rescue_fleet_simulator.flight import FlightRefusal
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario

pytestmark = [pytest.mark.unit]

THRESHOLDS: Final = ConnectivityThresholds(
    misses_to_degraded=2, misses_to_offline=3, heartbeats_to_recover=2
)

VISION: Final = DroneStart(
    drone_id="drone-vision-01",
    sector_id="sector-north",
    latitude_microdegrees=47_000_000,
    longitude_microdegrees=-122_000_000,
    altitude_metres=400,
    heading_degrees=0,
    ground_speed_centimetres_per_second=850,
    battery_permille=1_000,
    north_microdegrees_per_tick=10,
    east_microdegrees_per_tick=0,
    battery_drain_permille_per_tick=5,
)
THERMAL: Final = replace(
    VISION,
    drone_id="drone-thermal-02",
    sector_id="sector-south",
    heading_degrees=180,
    north_microdegrees_per_tick=-10,
)
GUARD: Final = replace(
    VISION,
    drone_id="drone-audio-03",
    sector_id="sector-east",
    heading_degrees=90,
    north_microdegrees_per_tick=0,
    east_microdegrees_per_tick=10,
)


def _scenario(
    *,
    drones: tuple[DroneStart, ...] = (VISION, THERMAL),
    ticks_to_sweep: int = 4,
    absent_heartbeats: dict[str, frozenset[int]] | None = None,
) -> FleetScenario:
    """Return an accepted two-drone scenario, with any member replaced."""
    return FleetScenario(
        mission_id="m-2026-0001",
        drones=drones,
        tick_interval_milliseconds=1_000,
        thresholds=THRESHOLDS,
        ticks_to_sweep=ticks_to_sweep,
        absent_heartbeats={} if absent_heartbeats is None else absent_heartbeats,
    )


def _run(scenario: FleetScenario, ticks: int) -> FleetState:
    """Fold ``ticks`` ticks and return the state they reach."""
    state = initial_fleet(scenario)
    for _ in range(ticks):
        state = advance_tick(scenario, state).state
    return state


def _refusal(scenario: FleetScenario, state: FleetState) -> tuple[Enum, object]:
    """Return the refusal one tick raises, failing the test if it is accepted."""
    try:
        advanced = advance_tick(scenario, state)
    except FleetError as error:
        return (error.refusal, error.value)
    message = f"accepted: {advanced!r}"
    raise AssertionError(message)


class InitialFleetTests(unittest.TestCase):
    def test_a_run_begins_planned_with_every_sector_unassigned_and_no_tick_folded(self) -> None:
        # Arrange
        scenario = _scenario()

        # Act
        state = initial_fleet(scenario)

        # Assert
        self.assertEqual(
            (0, MissionState.PLANNED, {SectorState.UNASSIGNED}),
            (state.tick, state.mission, {sector.state for sector in state.sectors.values()}),
        )


class FirstTickTests(unittest.TestCase):
    def test_the_first_tick_starts_the_mission_and_assigns_every_sector(self) -> None:
        # Arrange
        scenario = _scenario()

        # Act
        state = _run(scenario, 1)

        # Assert
        self.assertEqual(
            (MissionState.SEARCHING, {SectorState.ASSIGNED}),
            (state.mission, {sector.state for sector in state.sectors.values()}),
        )

    def test_a_tick_advances_the_ordinal_by_one(self) -> None:
        # Arrange
        scenario = _scenario()

        # Act
        state = _run(scenario, 3)

        # Assert
        self.assertEqual(3, state.tick)


class ReadingTests(unittest.TestCase):
    def test_one_reading_per_drone_arrives_in_ascending_identifier_order(self) -> None:
        # Arrange
        scenario = _scenario(drones=(VISION, THERMAL))

        # Act
        tick = advance_tick(scenario, initial_fleet(scenario))

        # Assert
        self.assertEqual(
            ("drone-thermal-02", "drone-vision-01"),
            tuple(reading.drone_id for reading in tick.readings),
        )

    def test_the_roster_order_does_not_change_the_reading_order(self) -> None:
        # Arrange
        reversed_roster = _scenario(drones=(THERMAL, VISION))

        # Act
        tick = advance_tick(reversed_roster, initial_fleet(reversed_roster))

        # Assert
        self.assertEqual(
            ("drone-thermal-02", "drone-vision-01"),
            tuple(reading.drone_id for reading in tick.readings),
        )

    def test_a_reading_carries_the_advanced_position_and_the_floored_battery(self) -> None:
        # Arrange
        scenario = _scenario()

        # Act
        tick = advance_tick(scenario, initial_fleet(scenario))
        reading = next(item for item in tick.readings if item.drone_id == VISION.drone_id)

        # Assert
        self.assertEqual(
            (47_000_010, -122_000_000, 99, 400, 0, 850),
            (
                reading.latitude_microdegrees,
                reading.longitude_microdegrees,
                reading.battery_percent,
                reading.altitude_metres,
                reading.heading_degrees,
                reading.ground_speed_centimetres_per_second,
            ),
        )


class ConnectivityEdgeTests(unittest.TestCase):
    def test_enough_missed_heartbeats_take_a_drone_offline_and_imperil_its_sector(self) -> None:
        # Arrange
        scenario = _scenario(absent_heartbeats={VISION.drone_id: frozenset({0, 1, 2})})

        # Act
        state = _run(scenario, 3)

        # Assert
        self.assertEqual(
            (ConnectivityState.OFFLINE, SectorState.AT_RISK),
            (
                state.drones[VISION.drone_id].connectivity.state,
                state.sectors[VISION.sector_id].state,
            ),
        )

    def test_a_drone_that_only_degrades_leaves_its_sector_assigned(self) -> None:
        # Arrange
        scenario = _scenario(absent_heartbeats={VISION.drone_id: frozenset({0, 1})})

        # Act
        state = _run(scenario, 2)

        # Assert
        self.assertEqual(
            (ConnectivityState.DEGRADED, SectorState.ASSIGNED),
            (
                state.drones[VISION.drone_id].connectivity.state,
                state.sectors[VISION.sector_id].state,
            ),
        )

    def test_a_drone_that_comes_back_recovers_its_sector(self) -> None:
        # Arrange
        scenario = _scenario(
            ticks_to_sweep=99, absent_heartbeats={VISION.drone_id: frozenset({0, 1, 2})}
        )

        # Act
        state = _run(scenario, 5)

        # Assert
        self.assertEqual(
            (ConnectivityState.CONNECTED, SectorState.ASSIGNED),
            (
                state.drones[VISION.drone_id].connectivity.state,
                state.sectors[VISION.sector_id].state,
            ),
        )


class SweepTests(unittest.TestCase):
    def test_a_sector_is_searched_once_its_holder_has_swept_it_for_the_declared_ticks(
        self,
    ) -> None:
        # Arrange
        scenario = _scenario(ticks_to_sweep=3)

        # Act
        states = tuple(_run(scenario, ticks).sectors[VISION.sector_id].state for ticks in (2, 3))

        # Assert
        self.assertEqual((SectorState.ASSIGNED, SectorState.SEARCHED), states)

    def test_an_imperilled_sector_stops_accumulating_and_resumes_where_it_stopped(self) -> None:
        # Arrange
        scenario = _scenario(
            ticks_to_sweep=3,
            absent_heartbeats={VISION.drone_id: frozenset({0, 1, 2, 3, 4})},
        )

        # Act
        during = _run(scenario, 5).sectors[VISION.sector_id]
        after = _run(scenario, 7).sectors[VISION.sector_id]

        # Assert
        self.assertEqual(
            ((2, SectorState.AT_RISK), (3, SectorState.SEARCHED)),
            ((during.swept_ticks, during.state), (after.swept_ticks, after.state)),
        )

    def test_a_sector_already_searched_is_not_imperilled_by_a_later_link_loss(self) -> None:
        # Arrange
        scenario = _scenario(
            drones=(VISION, THERMAL, GUARD),
            ticks_to_sweep=4,
            absent_heartbeats={
                VISION.drone_id: frozenset({4, 5, 6}),
                GUARD.drone_id: frozenset(range(7)),
            },
        )

        # Act
        state = _run(scenario, 7)

        # Assert
        self.assertEqual(
            (ConnectivityState.OFFLINE, SectorState.SEARCHED, MissionState.SEARCHING),
            (
                state.drones[VISION.drone_id].connectivity.state,
                state.sectors[VISION.sector_id].state,
                state.mission,
            ),
        )


class MissionEndingTests(unittest.TestCase):
    def test_the_mission_exhausts_on_the_tick_the_last_sector_is_searched(self) -> None:
        # Arrange
        scenario = _scenario(ticks_to_sweep=2)

        # Act
        missions = tuple(_run(scenario, ticks).mission for ticks in (1, 2))

        # Assert
        self.assertEqual((MissionState.SEARCHING, MissionState.EXHAUSTED), missions)

    def test_a_tick_after_the_mission_ends_is_refused_rather_than_manufacturing_state(
        self,
    ) -> None:
        # Arrange
        scenario = _scenario(ticks_to_sweep=1)
        ended = _run(scenario, 1)

        # Act
        refusal = _refusal(scenario, ended)

        # Assert
        self.assertEqual((FleetRefusal.MISSION_ENDED, MissionState.EXHAUSTED), refusal)


class OutOfRangeTests(unittest.TestCase):
    def test_a_step_off_the_map_stops_the_run_rather_than_publishing_a_false_position(
        self,
    ) -> None:
        # Arrange
        edge = replace(VISION, latitude_microdegrees=LATITUDE_MICRODEGREES.high - 5)
        scenario = _scenario(drones=(edge, THERMAL))

        # Act
        with pytest.raises(FleetSimulatorError) as captured:
            advance_tick(scenario, initial_fleet(scenario))

        # Assert
        self.assertEqual(
            (FlightRefusal.OUT_OF_RANGE, edge.drone_id),
            (captured.value.refusal, captured.value.value),
        )


if __name__ == "__main__":
    unittest.main()
