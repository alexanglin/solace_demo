"""One tick of the fleet: one observation per drone, in ascending identifier order.

``docs/adr/0078-one-tick-is-one-observation-per-drone.md`` fixes the fold. Every machine
this drives is the real one in ``aerial_rescue_domain``: the connectivity counter, the
sector table, and the mission table. No transition, threshold, or terminal set is copied
here, so a refused edge is the domain refusing it.

The observation comes from the scenario's schedule and never from whether a telemetry event
was published. ``docs/operating-parameters.md`` says why: routine telemetry is droppable, so
absence of telemetry is not absence of the drone.

Two machines are deliberately absent. The command dispatch lifecycle needs the command send
budget and the evidence score needs the band boundaries, and both are open rows in
``docs/operating-parameters.md``; command intake needs a durable queue as well.

This module is pure: it performs no input or output, reads no clock, and consumes no random
source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from aerial_rescue_domain.connectivity import (
    INITIAL_STATUS,
    ConnectivityState,
    ConnectivityStatus,
    heartbeat_missed,
    heartbeat_received,
)
from aerial_rescue_domain.mission import MissionEvent, MissionState
from aerial_rescue_domain.mission import is_terminal as mission_is_terminal
from aerial_rescue_domain.mission import transition as mission_transition
from aerial_rescue_domain.sectors import INITIAL_STATE as SECTOR_INITIAL
from aerial_rescue_domain.sectors import SectorEvent, SectorState
from aerial_rescue_domain.sectors import is_terminal as sector_is_terminal
from aerial_rescue_domain.sectors import transition as sector_transition

from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.flight import DroneState, advance, battery_percent, initial_state
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario, ordered_drones

_NO_SWEPT_TICKS = 0
_FIRST_TICK = 0


class FleetRefusal(Enum):
    """Why the fleet cannot fold another tick."""

    MISSION_ENDED = "the mission has reached an ending and folding one more tick would invent state"


class FleetError(FleetSimulatorError):
    """A tick the fold refuses, carrying the refusal as structured data."""


@dataclass(frozen=True)
class Reading:
    """One drone's telemetry for one tick, in the units the payload carries."""

    drone_id: str
    latitude_microdegrees: int
    longitude_microdegrees: int
    altitude_metres: int
    heading_degrees: int
    ground_speed_centimetres_per_second: int
    battery_percent: int


@dataclass(frozen=True)
class DroneRuntime:
    """One drone's simulated physical state beside the link state it was last observed in."""

    state: DroneState
    connectivity: ConnectivityStatus


@dataclass(frozen=True)
class SectorRuntime:
    """One sector's lifecycle state beside the ticks its holder has swept it for."""

    state: SectorState
    swept_ticks: int


@dataclass(frozen=True)
class FleetState:
    """Everything one run carries between ticks. Authority for nothing durable."""

    tick: int
    mission: MissionState
    drones: Mapping[str, DroneRuntime]
    sectors: Mapping[str, SectorRuntime]


@dataclass(frozen=True)
class Tick:
    """The state one tick reached, and the readings it produced in fold order."""

    state: FleetState
    readings: tuple[Reading, ...]


@dataclass(frozen=True)
class _Stepped:
    """What folding every drone produced, before the sector and mission edges are applied."""

    drones: Mapping[str, DroneRuntime]
    readings: tuple[Reading, ...]
    events: Mapping[str, SectorEvent]


def initial_fleet(scenario: FleetScenario) -> FleetState:
    """Return the state before the first tick: planned, unassigned, and nowhere yet."""
    return FleetState(
        tick=_FIRST_TICK,
        mission=MissionState.PLANNED,
        drones={
            drone.drone_id: DroneRuntime(initial_state(drone), INITIAL_STATUS)
            for drone in scenario.drones
        },
        sectors={
            drone.sector_id: SectorRuntime(SECTOR_INITIAL, _NO_SWEPT_TICKS)
            for drone in scenario.drones
        },
    )


def _reading(start: DroneStart, state: DroneState) -> Reading:
    """Return the telemetry one drone reports for the tick it has just flown."""
    return Reading(
        drone_id=start.drone_id,
        latitude_microdegrees=state.latitude_microdegrees,
        longitude_microdegrees=state.longitude_microdegrees,
        altitude_metres=start.altitude_metres,
        heading_degrees=start.heading_degrees,
        ground_speed_centimetres_per_second=start.ground_speed_centimetres_per_second,
        battery_percent=battery_percent(state),
    )


def _observe(
    status: ConnectivityStatus, scenario: FleetScenario, start: DroneStart, tick: int
) -> ConnectivityStatus:
    """Apply this tick's single heartbeat-or-miss observation to one drone."""
    absent = tick in scenario.absent_heartbeats.get(start.drone_id, frozenset())
    step = heartbeat_missed if absent else heartbeat_received
    return step(status, scenario.thresholds)


def _sector_event(
    before: ConnectivityState, after: ConnectivityState, sector: SectorState
) -> SectorEvent | None:
    """Return the sector edge a connectivity delta implies, or ``None`` for no edge.

    A finished sector receives no edge. Its holder's link may still be lost, but
    ``docs/adr/0073`` makes ``SEARCHED`` absorbing, so imperilling it would be asking the
    table for a transition it does not have.
    """
    if sector_is_terminal(sector):
        return None
    if after is ConnectivityState.OFFLINE and before is not ConnectivityState.OFFLINE:
        return SectorEvent.IMPERIL
    if before is ConnectivityState.OFFLINE and after is not ConnectivityState.OFFLINE:
        return SectorEvent.RECOVER
    return None


def _open_the_search(
    state: FleetState,
) -> tuple[MissionState, Mapping[str, SectorRuntime]]:
    """Return the mission and sectors after the first tick's ``START`` and ``ASSIGN``."""
    if state.mission is not MissionState.PLANNED:
        return state.mission, state.sectors
    assigned = {
        sector_id: SectorRuntime(
            sector_transition(runtime.state, SectorEvent.ASSIGN), runtime.swept_ticks
        )
        for sector_id, runtime in state.sectors.items()
    }
    return mission_transition(state.mission, MissionEvent.START), assigned


def _step_drones(
    scenario: FleetScenario, state: FleetState, sectors: Mapping[str, SectorRuntime]
) -> _Stepped:
    """Fold every drone once, in ascending identifier order, and collect what it implies."""
    drones: dict[str, DroneRuntime] = {}
    readings: list[Reading] = []
    events: dict[str, SectorEvent] = {}
    for start in ordered_drones(scenario):
        was = state.drones[start.drone_id]
        observed = _observe(was.connectivity, scenario, start, state.tick)
        flown = advance(was.state, start)
        drones[start.drone_id] = DroneRuntime(flown, observed)
        readings.append(_reading(start, flown))
        event = _sector_event(
            was.connectivity.state, observed.state, sectors[start.sector_id].state
        )
        if event is not None:
            events[start.sector_id] = event
    return _Stepped(drones, tuple(readings), events)


def _apply_events(
    sectors: Mapping[str, SectorRuntime], events: Mapping[str, SectorEvent]
) -> Mapping[str, SectorRuntime]:
    """Return the sectors after each implied edge, through the domain's own table."""
    updated = dict(sectors)
    for sector_id, event in events.items():
        runtime = updated[sector_id]
        updated[sector_id] = SectorRuntime(
            sector_transition(runtime.state, event), runtime.swept_ticks
        )
    return updated


def _sweep(
    scenario: FleetScenario,
    sectors: Mapping[str, SectorRuntime],
    drones: Mapping[str, DroneRuntime],
) -> Mapping[str, SectorRuntime]:
    """Return the sectors after this tick's uniform sweep accounting.

    A sector accumulates only while it is assigned and its holder is not offline. An
    imperilled sector keeps the ticks it already flew, because discarding them would throw
    away the sweep that actually happened.
    """
    updated = dict(sectors)
    for start in ordered_drones(scenario):
        runtime = updated[start.sector_id]
        offline = drones[start.drone_id].connectivity.state is ConnectivityState.OFFLINE
        if runtime.state is not SectorState.ASSIGNED or offline:
            continue
        swept = runtime.swept_ticks + 1
        finished = swept >= scenario.ticks_to_sweep
        reached = sector_transition(runtime.state, SectorEvent.SWEEP) if finished else runtime.state
        updated[start.sector_id] = SectorRuntime(reached, swept)
    return updated


def _mission_after(mission: MissionState, sectors: Mapping[str, SectorRuntime]) -> MissionState:
    """Return the mission after this tick, exhausting it once every sector is searched."""
    searched = all(sector_is_terminal(runtime.state) for runtime in sectors.values())
    if mission is MissionState.SEARCHING and searched:
        return mission_transition(mission, MissionEvent.EXHAUST)
    return mission


def advance_tick(scenario: FleetScenario, state: FleetState) -> Tick:
    """Fold one tick and return the state it reached with the readings it produced.

    Args:
        scenario: The accepted scenario this run folds.
        state: The state the previous tick reached.

    Returns:
        The next state and its readings, one per drone in ascending identifier order.

    Raises:
        FleetError: With ``MISSION_ENDED`` when the mission has already ended.
        FlightError: When one drone's step would leave the coordinate range. It propagates
            unchanged, so the drone that could not fly is the one the refusal names.
    """
    if mission_is_terminal(state.mission):
        raise FleetError(FleetRefusal.MISSION_ENDED, state.mission)
    mission, opened = _open_the_search(state)
    stepped = _step_drones(scenario, state, opened)
    swept = _sweep(scenario, _apply_events(opened, stepped.events), stepped.drones)
    reached = FleetState(state.tick + 1, _mission_after(mission, swept), stepped.drones, swept)
    return Tick(reached, stepped.readings)
