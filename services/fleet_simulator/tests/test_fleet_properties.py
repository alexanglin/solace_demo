"""Property-based invariants of the tick fold: determinism, and what the roster order costs.

Determinism is the claim ADR-0078 makes and the one an example cannot carry. Module-level
functions with ``derandomize`` for the reason the domain's property modules give.
"""

from __future__ import annotations

import pytest
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.mission import is_terminal as mission_is_terminal
from aerial_rescue_fleet_simulator.fleet import FleetState, Reading, advance_tick, initial_fleet
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from hypothesis import given, settings
from hypothesis import strategies as st

TICKS = st.integers(min_value=0, max_value=25)
_ROSTER = ("01", "02", "03", "04")
_STEP = 25


@st.composite
def scenarios(draw: st.DrawFn) -> FleetScenario:
    """Draw a small fleet with an arbitrary absence schedule and no reachable bound."""
    size = draw(st.integers(min_value=1, max_value=len(_ROSTER)))
    degraded = draw(st.integers(min_value=1, max_value=3))
    drones = tuple(
        DroneStart(
            drone_id=f"drone-{suffix}",
            sector_id=f"sector-{suffix}",
            latitude_microdegrees=draw(st.integers(min_value=-1_000, max_value=1_000)),
            longitude_microdegrees=draw(st.integers(min_value=-1_000, max_value=1_000)),
            altitude_metres=100,
            heading_degrees=0,
            ground_speed_centimetres_per_second=1,
            battery_permille=1_000,
            north_microdegrees_per_tick=draw(st.integers(min_value=1, max_value=_STEP)),
            east_microdegrees_per_tick=0,
            battery_drain_permille_per_tick=draw(st.integers(min_value=0, max_value=20)),
        )
        for suffix in _ROSTER[:size]
    )
    return FleetScenario(
        mission_id="m-2026-0001",
        drones=drones,
        tick_interval_milliseconds=1_000,
        thresholds=ConnectivityThresholds(degraded, degraded + 1, 2),
        ticks_to_sweep=draw(st.integers(min_value=1, max_value=8)),
        absent_heartbeats={
            drone.drone_id: draw(st.frozensets(st.integers(min_value=0, max_value=25), max_size=8))
            for drone in drones
        },
    )


def _fold(scenario: FleetScenario, ticks: int) -> tuple[FleetState, tuple[Reading, ...]]:
    """Fold up to ``ticks`` ticks, stopping at the ending rather than asking for one more."""
    state = initial_fleet(scenario)
    produced: list[Reading] = []
    for _ in range(ticks):
        if mission_is_terminal(state.mission):
            break
        tick = advance_tick(scenario, state)
        state = tick.state
        produced.extend(tick.readings)
    return state, tuple(produced)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(scenarios(), TICKS)
def test_two_folds_of_one_scenario_reach_the_same_state_and_the_same_readings(
    scenario: FleetScenario, ticks: int
) -> None:
    # Arrange
    first = _fold(scenario, ticks)

    # Act
    second = _fold(scenario, ticks)

    # Assert
    assert first == second


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(scenarios(), TICKS)
def test_reversing_the_roster_changes_neither_the_state_nor_the_readings(
    scenario: FleetScenario, ticks: int
) -> None:
    # Arrange
    reversed_roster = FleetScenario(
        mission_id=scenario.mission_id,
        drones=tuple(reversed(scenario.drones)),
        tick_interval_milliseconds=scenario.tick_interval_milliseconds,
        thresholds=scenario.thresholds,
        ticks_to_sweep=scenario.ticks_to_sweep,
        absent_heartbeats=scenario.absent_heartbeats,
    )

    # Act
    folded = _fold(reversed_roster, ticks)

    # Assert
    assert folded == _fold(scenario, ticks)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(scenarios(), TICKS)
def test_every_tick_reports_each_drone_once_in_ascending_identifier_order(
    scenario: FleetScenario, ticks: int
) -> None:
    # Arrange
    expected = tuple(sorted(drone.drone_id for drone in scenario.drones))

    # Act
    _, readings = _fold(scenario, ticks)

    # Assert
    assert all(
        tuple(reading.drone_id for reading in readings[offset : offset + len(expected)]) == expected
        for offset in range(0, len(readings), len(expected))
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(scenarios(), TICKS)
def test_the_ordinal_counts_exactly_the_ticks_that_were_folded(
    scenario: FleetScenario, ticks: int
) -> None:
    # Arrange
    state, readings = _fold(scenario, ticks)

    # Act
    folded = state.tick

    # Assert
    assert folded == len(readings) // len(scenario.drones) <= ticks
