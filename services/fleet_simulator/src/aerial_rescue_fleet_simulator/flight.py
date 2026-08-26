"""The point-mass advance: integer addition, and one refusal that keeps a lie off the wire.

``docs/adr/0078-one-tick-is-one-observation-per-drone.md`` makes motion the addition of a
declared per-tick displacement, with no trigonometry anywhere. Two reasons, both from
accepted records: ``docs/adr/0027`` makes no floating-point value representable where a
digest can reach, and the last-bit behaviour of ``cos`` and ``sin`` differs between C
libraries, so a derived displacement would make the determinism claim rest on the platform.

``docs/LIMITATIONS.md`` bounds what this models: a simplified point-mass flight with no
wind, no weather effect, and no turn-radius constraint. A constant velocity per leg is
exactly that and nothing more.

A step that would leave the documented coordinate range is refused rather than clamped or
wrapped. Clamping publishes a position the drone is not at, and wrapping models a
circumnavigating search this project does not claim.

This module is pure: it performs no input or output, reads no clock, and consumes no
random source.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.bounds import (
    BATTERY_PERMILLE,
    LATITUDE_MICRODEGREES,
    LONGITUDE_MICRODEGREES,
    PERMILLE_PER_PERCENT,
)
from aerial_rescue_fleet_simulator.scenario import DroneStart


class FlightRefusal(Enum):
    """Why one drone cannot take the step its scenario entry declares."""

    OUT_OF_RANGE = "the step leaves the documented coordinate range"


class FlightError(FleetSimulatorError):
    """A step the model refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class DroneState:
    """One drone's simulated physical state between ticks.

    The battery is carried in permille rather than the percent the payload publishes, so a
    drain slower than one percent per tick is representable without a fraction.
    """

    latitude_microdegrees: int
    longitude_microdegrees: int
    battery_permille: int


def initial_state(start: DroneStart) -> DroneState:
    """Return where a drone begins, taken from its accepted scenario entry."""
    return DroneState(
        start.latitude_microdegrees, start.longitude_microdegrees, start.battery_permille
    )


def advance(state: DroneState, start: DroneStart) -> DroneState:
    """Return the state after one tick of the drone's declared leg.

    Args:
        state: Where the drone was at the end of the previous tick.
        start: Its accepted scenario entry, which declares the leg and the drain.

    Returns:
        The state after the tick, with the battery floored at empty because a battery
        cannot hold less than nothing.

    Raises:
        FlightError: With ``OUT_OF_RANGE``, naming the drone, when the step would leave
            the coordinate range the telemetry payload can carry.
    """
    latitude = state.latitude_microdegrees + start.north_microdegrees_per_tick
    longitude = state.longitude_microdegrees + start.east_microdegrees_per_tick
    if not (LATITUDE_MICRODEGREES.holds(latitude) and LONGITUDE_MICRODEGREES.holds(longitude)):
        raise FlightError(FlightRefusal.OUT_OF_RANGE, start.drone_id)
    drained = state.battery_permille - start.battery_drain_permille_per_tick
    return DroneState(latitude, longitude, max(BATTERY_PERMILLE.low, drained))


def battery_percent(state: DroneState) -> int:
    """Return the battery the telemetry payload carries, rounded down.

    Rounding down is deliberate: an optimistic battery reading is the wrong direction for
    a number an operator uses to decide whether a drone can finish a leg.
    """
    return state.battery_permille // PERMILLE_PER_PERCENT
