"""The frozen scenario value the composition root supplies, and every refusal it carries.

``docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md`` fixes this
boundary. Loading, versioning, and delivering a scenario document are the scenario
service's job; this module is only the shape that work must produce, so nothing here reads
a file, an environment variable, a broker message, a clock, or a random source.

The value validates once, at construction, and is an accepted value thereafter -- the
arrangement ``aerial_rescue_contracts`` already uses -- so the fold never re-validates. It
carries no random seed, because nothing in the fold is random.

This module is pure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts.topics import IDENTIFIER_PATTERN
from aerial_rescue_domain.connectivity import ConnectivityThresholds

from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.bounds import (
    ALTITUDE_METRES,
    BATTERY_PERMILLE,
    EAST_MICRODEGREES_PER_TICK,
    GROUND_SPEED_CENTIMETRES_PER_SECOND,
    HEADING_DEGREES,
    LATITUDE_MICRODEGREES,
    LONGITUDE_MICRODEGREES,
    NORTH_MICRODEGREES_PER_TICK,
    Bound,
)

_MINIMUM_COUNT: Final = 1
_FIRST_TICK: Final = 0


class ScenarioRefusal(Enum):
    """Why a scenario cannot be built."""

    EMPTY_ROSTER = "a scenario with no drones folds nothing"
    DUPLICATE_DRONE = "two roster entries name the same drone"
    IDENTIFIER_FORM = "identifier outside the topic identifier form"
    OUT_OF_RANGE = "value outside the telemetry payload bound"
    NON_POSITIVE = "count must be at least one"
    MOTION_DISAGREEMENT = "ground speed and displacement disagree about whether the drone moves"
    UNKNOWN_DRONE = "the heartbeat schedule names a drone the roster does not"
    NEGATIVE_TICK = "a scheduled tick ordinal is before the run starts"


class ScenarioError(FleetSimulatorError):
    """A scenario the boundary refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class DroneStart:
    """One drone's starting state and the constant leg it flies.

    ``heading_degrees`` and ``ground_speed_centimetres_per_second`` are declared rather
    than derived: deriving them from the displacement needs trigonometry, which
    ``docs/adr/0027`` keeps out of values a digest can reach. The only agreement checked is
    whether the two halves say the drone is moving (``docs/adr/0078``).
    """

    drone_id: str
    sector_id: str
    latitude_microdegrees: int
    longitude_microdegrees: int
    altitude_metres: int
    heading_degrees: int
    ground_speed_centimetres_per_second: int
    battery_permille: int
    north_microdegrees_per_tick: int
    east_microdegrees_per_tick: int
    battery_drain_permille_per_tick: int

    def __post_init__(self) -> None:
        """Refuse a drone the telemetry payload could never carry."""
        _check_drone(self)

    def displaces(self) -> bool:
        """Return whether this drone's declared leg moves it at all."""
        return (self.north_microdegrees_per_tick, self.east_microdegrees_per_tick) != (0, 0)


@dataclass(frozen=True)
class FleetScenario:
    """Everything one deterministic run folds, and nothing else."""

    mission_id: str
    drones: tuple[DroneStart, ...]
    tick_interval_milliseconds: int
    thresholds: ConnectivityThresholds
    ticks_to_sweep: int
    absent_heartbeats: Mapping[str, frozenset[int]]

    def __post_init__(self) -> None:
        """Refuse a scenario the fold could not advance."""
        _check_scenario(self)


def _identifier(value: str, member: str) -> None:
    """Raise unless a value obeys the topic grammar's identifier rule."""
    if re.fullmatch(IDENTIFIER_PATTERN, value) is None:
        raise ScenarioError(ScenarioRefusal.IDENTIFIER_FORM, member)


def _bounded(value: int, bound: Bound, member: str) -> None:
    """Raise unless a value lies inside the bound the committed schema declares."""
    if not bound.holds(value):
        raise ScenarioError(ScenarioRefusal.OUT_OF_RANGE, member)


def _positive(value: int, member: str) -> None:
    """Raise unless a count is at least one, so the fold can advance."""
    if value < _MINIMUM_COUNT:
        raise ScenarioError(ScenarioRefusal.NON_POSITIVE, member)


def _bounded_members(start: DroneStart) -> tuple[tuple[str, int, Bound], ...]:
    """Return every member with a committed bound, beside the bound it must obey."""
    return (
        ("latitude_microdegrees", start.latitude_microdegrees, LATITUDE_MICRODEGREES),
        ("longitude_microdegrees", start.longitude_microdegrees, LONGITUDE_MICRODEGREES),
        ("altitude_metres", start.altitude_metres, ALTITUDE_METRES),
        ("heading_degrees", start.heading_degrees, HEADING_DEGREES),
        (
            "ground_speed_centimetres_per_second",
            start.ground_speed_centimetres_per_second,
            GROUND_SPEED_CENTIMETRES_PER_SECOND,
        ),
        ("battery_permille", start.battery_permille, BATTERY_PERMILLE),
        (
            "battery_drain_permille_per_tick",
            start.battery_drain_permille_per_tick,
            BATTERY_PERMILLE,
        ),
        (
            "north_microdegrees_per_tick",
            start.north_microdegrees_per_tick,
            NORTH_MICRODEGREES_PER_TICK,
        ),
        (
            "east_microdegrees_per_tick",
            start.east_microdegrees_per_tick,
            EAST_MICRODEGREES_PER_TICK,
        ),
    )


def _check_drone(start: DroneStart) -> None:
    """Raise on the first defect in one roster entry, in a fixed order."""
    _identifier(start.drone_id, "drone_id")
    _identifier(start.sector_id, "sector_id")
    for member, value, bound in _bounded_members(start):
        _bounded(value, bound, member)
    if start.displaces() != (start.ground_speed_centimetres_per_second > 0):
        raise ScenarioError(ScenarioRefusal.MOTION_DISAGREEMENT, start.drone_id)


def _check_roster(drones: Sequence[DroneStart]) -> frozenset[str]:
    """Return the roster's drone identifiers, refusing an empty roster and a repeat."""
    if not drones:
        raise ScenarioError(ScenarioRefusal.EMPTY_ROSTER, ())
    seen: set[str] = set()
    for drone in drones:
        if drone.drone_id in seen:
            raise ScenarioError(ScenarioRefusal.DUPLICATE_DRONE, drone.drone_id)
        seen.add(drone.drone_id)
    return frozenset(seen)


def _check_schedule(schedule: Mapping[str, frozenset[int]], known: frozenset[str]) -> None:
    """Raise unless every scheduled absence names a roster drone and a tick in the run."""
    for drone_id, ticks in schedule.items():
        if drone_id not in known:
            raise ScenarioError(ScenarioRefusal.UNKNOWN_DRONE, drone_id)
        for tick in sorted(ticks):
            if tick < _FIRST_TICK:
                raise ScenarioError(ScenarioRefusal.NEGATIVE_TICK, tick)


def _check_scenario(scenario: FleetScenario) -> None:
    """Raise on the first defect in a scenario, in a fixed order."""
    _identifier(scenario.mission_id, "mission_id")
    known = _check_roster(scenario.drones)
    _positive(scenario.tick_interval_milliseconds, "tick_interval_milliseconds")
    _positive(scenario.ticks_to_sweep, "ticks_to_sweep")
    _check_schedule(scenario.absent_heartbeats, known)


def ordered_drones(scenario: FleetScenario) -> tuple[DroneStart, ...]:
    """Return the roster in ascending drone-identifier order.

    The order is the contract of ``docs/adr/0078``, matching the rule ``docs/adr/0067``
    fixed for the reduced dashboard state's collections, so the roster's own order carries
    nothing and two scenarios differing only in it fold identically.
    """
    return tuple(sorted(scenario.drones, key=lambda drone: drone.drone_id))


def sectors(scenario: FleetScenario) -> tuple[str, ...]:
    """Return the distinct assigned sectors in ascending identifier order."""
    return tuple(sorted({drone.sector_id for drone in scenario.drones}))
