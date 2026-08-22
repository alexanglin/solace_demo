"""Property-based invariants of the point-mass advance.

Module-level functions with ``derandomize`` for the reason the domain's property modules
give: a flapping example set would turn a coverage or mutation number into a moving one.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from aerial_rescue_fleet_simulator.bounds import (
    BATTERY_PERMILLE,
    LATITUDE_MICRODEGREES,
    LONGITUDE_MICRODEGREES,
    PERCENT,
)
from aerial_rescue_fleet_simulator.flight import (
    DroneState,
    advance,
    battery_percent,
    initial_state,
)
from aerial_rescue_fleet_simulator.scenario import DroneStart
from hypothesis import given, settings
from hypothesis import strategies as st

TICKS = st.integers(min_value=0, max_value=40)
_MAXIMUM_STEP = 1_000


@st.composite
def drones(draw: st.DrawFn) -> DroneStart:
    """Draw a drone whose declared leg cannot reach a coordinate bound in forty ticks."""
    north = draw(st.integers(min_value=-_MAXIMUM_STEP, max_value=_MAXIMUM_STEP))
    east = draw(st.integers(min_value=-_MAXIMUM_STEP, max_value=_MAXIMUM_STEP))
    moving = (north, east) != (0, 0)
    return DroneStart(
        drone_id="drone-vision-01",
        sector_id="sector-north",
        latitude_microdegrees=draw(st.integers(min_value=-1_000_000, max_value=1_000_000)),
        longitude_microdegrees=draw(st.integers(min_value=-1_000_000, max_value=1_000_000)),
        altitude_metres=0,
        heading_degrees=0,
        ground_speed_centimetres_per_second=1 if moving else 0,
        battery_permille=draw(st.integers(min_value=0, max_value=BATTERY_PERMILLE.high)),
        north_microdegrees_per_tick=north,
        east_microdegrees_per_tick=east,
        battery_drain_permille_per_tick=draw(st.integers(min_value=0, max_value=50)),
    )


def _fold(start: DroneStart, ticks: int) -> DroneState:
    """Advance one drone through ``ticks`` ticks from its starting state."""
    state = initial_state(start)
    for _ in range(ticks):
        state = advance(state, start)
    return state


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(drones(), TICKS)
def test_a_fold_of_n_ticks_lands_exactly_n_displacements_from_the_start(
    start: DroneStart, ticks: int
) -> None:
    # Arrange
    expected = (
        start.latitude_microdegrees + start.north_microdegrees_per_tick * ticks,
        start.longitude_microdegrees + start.east_microdegrees_per_tick * ticks,
    )

    # Act
    state = _fold(start, ticks)

    # Assert
    assert (state.latitude_microdegrees, state.longitude_microdegrees) == expected


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(drones(), TICKS)
def test_the_battery_never_rises_and_never_leaves_its_bound(start: DroneStart, ticks: int) -> None:
    # Arrange
    reserves = [initial_state(start).battery_permille]

    # Act
    state = initial_state(start)
    for _ in range(ticks):
        state = advance(state, start)
        reserves.append(state.battery_permille)

    # Assert
    assert all(
        later <= earlier and BATTERY_PERMILLE.holds(later) for earlier, later in pairwise(reserves)
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(drones(), TICKS)
def test_the_published_position_and_percent_stay_inside_the_payload_bounds(
    start: DroneStart, ticks: int
) -> None:
    # Arrange
    state = _fold(start, ticks)

    # Act
    published = (
        state.latitude_microdegrees,
        state.longitude_microdegrees,
        battery_percent(state),
    )

    # Assert
    assert (
        LATITUDE_MICRODEGREES.holds(published[0]),
        LONGITUDE_MICRODEGREES.holds(published[1]),
        PERCENT.holds(published[2]),
    ) == (True, True, True)
