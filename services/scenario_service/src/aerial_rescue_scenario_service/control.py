"""Scenario lifecycle coordination over injected catalog and fleet-control ports."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, Protocol

from aerial_rescue_contracts import canonical
from pydantic import ValidationError

from aerial_rescue_scenario_service.wire import (
    DeclaredOnlyMember,
    FleetControlCancelRequest,
    FleetControlDroneStart,
    FleetControlRunStatus,
    FleetControlScenario,
    FleetControlStartRequest,
    ScenarioCatalogResponse,
    ScenarioControlCancelRequest,
    ScenarioControlRecoveryRequest,
    ScenarioControlRunStatus,
    ScenarioControlStartRequest,
    ScenarioDefinition,
    SimulatedMember,
)

from .http_runtime import ControlError, ControlRefusal

_SIMULATED_COUNT: Final = 20
_DECLARED_ONLY_COUNT: Final = 3
_TERMINAL_STATES: Final = frozenset({"EXHAUSTED", "ABORTED"})
MAXIMUM_BINDINGS: Final = 32


class FleetControlRefusal(StrEnum):
    """Closed refusals a fleet-control caller can receive."""

    HOST_INVALID = "HOST_INVALID"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    CANONICAL_JSON_INVALID = "CANONICAL_JSON_INVALID"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PATH_BODY_MISMATCH = "PATH_BODY_MISMATCH"
    RUN_CONFLICT = "RUN_CONFLICT"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    CANCELLATION_NOT_ESTABLISHED = "CANCELLATION_NOT_ESTABLISHED"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    RUN_FAILED = "RUN_FAILED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class FleetControlError(RuntimeError):
    """One typed, redacted failure at the fleet-control client boundary."""

    def __init__(self, refusal: FleetControlRefusal) -> None:
        """Record only the closed refusal, never transport response content."""
        super().__init__(refusal.value)
        self.refusal = refusal


class ScenarioDefinitions(Protocol):
    """A strict scenario-definition source owned outside lifecycle coordination."""

    @property
    def ready(self) -> bool:
        """Whether the source can resolve accepted definitions."""
        ...

    async def startup(self) -> None:
        """Validate and acquire the definition source."""
        ...

    async def shutdown(self) -> None:
        """Release source resources."""
        ...

    async def load(self, scenario_id: str, revision: int) -> ScenarioDefinition:
        """Resolve an exact catalog identity and revision."""
        ...

    def catalog_response(self) -> ScenarioCatalogResponse:
        """Return the dashboard catalog projection validated at startup."""
        ...


class FleetControl(Protocol):
    """The distinct authenticated private fleet-control caller capability."""

    @property
    def ready(self) -> bool:
        """Whether the private caller can accept work."""
        ...

    async def startup(self) -> None:
        """Acquire the caller transport."""
        ...

    async def shutdown(self) -> None:
        """Close the caller transport."""
        ...

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start or reconcile one stable fleet run."""
        ...

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Return one stable fleet run status."""
        ...

    async def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        """Cancel within the scenario caller's remaining shared budget."""
        ...


@dataclass(frozen=True, slots=True)
class _RunBinding:
    request_bytes: bytes
    scenario_id: str
    scenario_revision: int
    mission_id: str
    run_id: str
    terminal_state: str | None = None


_STATE_BY_FLEET_STATE = {
    "ACCEPTED": "PLANNED",
    "RUNNING": "SEARCHING",
    "EXHAUSTED": "EXHAUSTED",
    "CANCELLED": "ABORTED",
    "FAILED": "ABORTED",
}


class ScenarioCoordinator:
    """Coordinate one process epoch without claiming durable mission authority."""

    def __init__(
        self,
        definitions: ScenarioDefinitions,
        fleet: FleetControl,
        *,
        maximum_bindings: int = MAXIMUM_BINDINGS,
    ) -> None:
        """Bind the distinct definition and private fleet-control capabilities."""
        if maximum_bindings < 1:
            message = "maximum_bindings must be positive"
            raise ValueError(message)
        self._definitions = definitions
        self._fleet = fleet
        self._maximum_bindings = maximum_bindings
        self._bindings: dict[str, _RunBinding] = {}
        self._lock = asyncio.Lock()
        self._started = False

    @property
    def ready(self) -> bool:
        """Require the process epoch plus both explicit dependencies."""
        return self._started and self._definitions.ready and self._fleet.ready

    async def startup(self) -> None:
        """Start the definition source before the outbound private caller."""
        await self._definitions.startup()
        try:
            await self._fleet.startup()
        except Exception:
            await self._definitions.shutdown()
            raise
        self._started = True

    async def shutdown(self) -> None:
        """Stop accepting before closing the caller and definition source."""
        self._started = False
        try:
            await self._fleet.shutdown()
        finally:
            await self._definitions.shutdown()

    async def catalog(self) -> ScenarioCatalogResponse:
        """Serve the startup-validated catalog projection; an unready source refuses."""
        return self._definitions.catalog_response()

    async def start(self, request: ScenarioControlStartRequest) -> ScenarioControlRunStatus:
        """Project and start once, or reconcile the same stable request by status."""
        request_bytes = canonical.canonical_bytes(request.model_dump(mode="json", by_alias=True))
        async with self._lock:
            binding = self._bindings.get(request.run_id)
            if binding is not None:
                if not hmac_bytes_equal(binding.request_bytes, request_bytes):
                    raise ControlError(ControlRefusal.RUN_CONFLICT)
                if binding.terminal_state is not None:
                    return _terminal_status(binding)
                fleet_status = await self._fleet_status(request.run_id)
                return self._remember(binding, fleet_status)

            self._admit_capacity()
            definition = await self._definitions.load(
                request.scenario_id, request.scenario_revision
            )
            fleet_request = _fleet_start_request(request, definition)
            try:
                fleet_status = await self._fleet.start(fleet_request)
            except FleetControlError as error:
                raise _translate_fleet_error(error) from error
            binding = _RunBinding(
                request_bytes=request_bytes,
                scenario_id=request.scenario_id,
                scenario_revision=request.scenario_revision,
                mission_id=request.mission_id,
                run_id=request.run_id,
            )
            _validate_fleet_binding(binding, fleet_status)
            self._bindings[request.run_id] = binding
            return self._remember(binding, fleet_status)

    async def status(self, run_id: str) -> ScenarioControlRunStatus:
        """Return status only for a run bound during this process epoch."""
        async with self._lock:
            binding = self._binding(run_id)
            if binding.terminal_state is not None:
                return _terminal_status(binding)
            fleet_status = await self._fleet_status(run_id)
            return self._remember(binding, fleet_status)

    async def recover(self, request: ScenarioControlRecoveryRequest) -> ScenarioControlRunStatus:
        """Reconcile a durable run against the fleet; a run the fleet lost is ABORTED for good."""
        request_bytes = canonical.canonical_bytes(request.model_dump(mode="json", by_alias=True))
        identity = (
            request.scenario_id,
            request.scenario_revision,
            request.mission_id,
            request.run_id,
        )
        async with self._lock:
            binding = self._bindings.get(request.run_id)
            if binding is not None:
                if _identity(binding) != identity:
                    raise ControlError(ControlRefusal.RUN_CONFLICT)
                if binding.terminal_state is not None:
                    return _terminal_status(binding)
            else:
                self._admit_capacity()
                await self._definitions.load(request.scenario_id, request.scenario_revision)
                binding = _RunBinding(
                    request_bytes=request_bytes,
                    scenario_id=request.scenario_id,
                    scenario_revision=request.scenario_revision,
                    mission_id=request.mission_id,
                    run_id=request.run_id,
                )
            try:
                fleet_status = await self._fleet.status(request.run_id)
            except FleetControlError as error:
                if error.refusal is not FleetControlRefusal.RUN_NOT_FOUND:
                    raise _translate_fleet_error(error) from error
                lost = replace(binding, terminal_state="ABORTED")
                self._bindings[request.run_id] = lost
                return _terminal_status(lost)
            _validate_fleet_binding(binding, fleet_status)
            self._bindings[request.run_id] = binding
            return self._remember(binding, fleet_status)

    async def cancel(
        self, request: ScenarioControlCancelRequest, remaining_seconds: float
    ) -> ScenarioControlRunStatus:
        """Cancel and report success only once the fleet state is terminal."""
        async with self._lock:
            binding = self._binding(request.run_id)
            if binding.mission_id != request.mission_id:
                raise ControlError(ControlRefusal.RUN_CONFLICT)
            if remaining_seconds <= 0:
                raise ControlError(ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
            fleet_request = FleetControlCancelRequest.model_validate(
                {
                    "controlVersion": 1,
                    "missionId": request.mission_id,
                    "runId": request.run_id,
                }
            )
            try:
                fleet_status = await self._fleet.cancel(fleet_request, remaining_seconds)
            except FleetControlError as error:
                if binding.terminal_state is not None:
                    return _terminal_status(binding)
                raise _translate_fleet_error(error) from error
            _validate_fleet_binding(binding, fleet_status)
            if fleet_status.state in {"ACCEPTED", "RUNNING"}:
                raise ControlError(ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
            return self._remember(binding, fleet_status)

    def snapshot(self) -> dict[str, int | bool]:
        """Return non-sensitive process-epoch diagnostics for deterministic probes."""
        return {"runCount": len(self._bindings), "started": self._started}

    def _remember(
        self, binding: _RunBinding, fleet_status: FleetControlRunStatus
    ) -> ScenarioControlRunStatus:
        """Map one fleet status and pin a terminal state so the fleet is never asked again."""
        status = _scenario_status(binding, fleet_status)
        if status.state in _TERMINAL_STATES:
            self._bindings[binding.run_id] = replace(binding, terminal_state=status.state)
        return status

    def _admit_capacity(self) -> None:
        """Refuse a new binding once the process epoch holds its bounded number of runs."""
        if len(self._bindings) >= self._maximum_bindings:
            raise ControlError(ControlRefusal.INTERNAL_FAILURE, "binding capacity")

    def _binding(self, run_id: str) -> _RunBinding:
        try:
            return self._bindings[run_id]
        except KeyError as error:
            raise ControlError(ControlRefusal.RUN_NOT_FOUND) from error

    async def _fleet_status(self, run_id: str) -> FleetControlRunStatus:
        try:
            return await self._fleet.status(run_id)
        except FleetControlError as error:
            raise _translate_fleet_error(error) from error


def hmac_bytes_equal(left: bytes, right: bytes) -> bool:
    """Compare canonical request identities without content-dependent early exit."""
    return hmac.compare_digest(left, right)


def _fleet_start_request(
    request: ScenarioControlStartRequest, definition: ScenarioDefinition
) -> FleetControlStartRequest:
    if (
        definition.identifier != request.scenario_id
        or definition.revision != request.scenario_revision
    ):
        raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
    simulated = tuple(
        member for member in definition.members if isinstance(member, SimulatedMember)
    )
    declared = tuple(
        member for member in definition.members if isinstance(member, DeclaredOnlyMember)
    )
    if len(simulated) != _SIMULATED_COUNT or len(declared) != _DECLARED_ONLY_COUNT:
        raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
    drones = [_drone(member) for member in simulated]
    try:
        scenario = FleetControlScenario.model_validate(
            {
                "missionId": request.mission_id,
                "drones": [drone.model_dump(mode="json", by_alias=True) for drone in drones],
                "tickIntervalMilliseconds": definition.tick_interval_milliseconds,
                "connectivityThresholds": definition.connectivity_thresholds.model_dump(
                    mode="json", by_alias=True
                ),
                "ticksToSweep": definition.ticks_to_sweep,
                "absentHeartbeats": [
                    absence.model_dump(mode="json", by_alias=True)
                    for absence in definition.absent_heartbeats
                ],
            }
        )
        return FleetControlStartRequest.model_validate(
            {
                "controlVersion": 1,
                "runId": request.run_id,
                "scenario": scenario.model_dump(mode="json", by_alias=True),
            }
        )
    except ValidationError as error:
        raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH) from error


def _drone(member: SimulatedMember) -> FleetControlDroneStart:
    return FleetControlDroneStart.model_validate(
        {
            "droneId": member.identifier,
            "sectorId": member.sector_id,
            "latitudeMicrodegrees": member.latitude_microdegrees,
            "longitudeMicrodegrees": member.longitude_microdegrees,
            "altitudeMetres": member.altitude_metres,
            "headingDegrees": member.heading_degrees,
            "groundSpeedCentimetresPerSecond": member.ground_speed_centimetres_per_second,
            "batteryPermille": member.battery_permille,
            "northMicrodegreesPerTick": member.north_microdegrees_per_tick,
            "eastMicrodegreesPerTick": member.east_microdegrees_per_tick,
            "batteryDrainPermillePerTick": member.battery_drain_permille_per_tick,
        }
    )


def _validate_fleet_binding(binding: _RunBinding, status: FleetControlRunStatus) -> None:
    if status.run_id != binding.run_id or status.mission_id != binding.mission_id:
        raise ControlError(ControlRefusal.FLEET_UNAVAILABLE)


def _identity(binding: _RunBinding) -> tuple[str, int, str, str]:
    return (binding.scenario_id, binding.scenario_revision, binding.mission_id, binding.run_id)


def _terminal_status(binding: _RunBinding) -> ScenarioControlRunStatus:
    return ScenarioControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "scenarioId": binding.scenario_id,
            "scenarioRevision": binding.scenario_revision,
            "missionId": binding.mission_id,
            "runId": binding.run_id,
            "state": binding.terminal_state,
        }
    )


def _scenario_status(
    binding: _RunBinding, fleet_status: FleetControlRunStatus
) -> ScenarioControlRunStatus:
    _validate_fleet_binding(binding, fleet_status)
    return ScenarioControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "scenarioId": binding.scenario_id,
            "scenarioRevision": binding.scenario_revision,
            "missionId": binding.mission_id,
            "runId": binding.run_id,
            "state": _STATE_BY_FLEET_STATE[fleet_status.state],
        }
    )


def _translate_fleet_error(error: FleetControlError) -> ControlError:
    if error.refusal is FleetControlRefusal.RUN_CONFLICT:
        return ControlError(ControlRefusal.RUN_CONFLICT)
    if error.refusal is FleetControlRefusal.RUN_NOT_FOUND:
        return ControlError(ControlRefusal.RUN_NOT_FOUND)
    if error.refusal is FleetControlRefusal.CANCELLATION_NOT_ESTABLISHED:
        return ControlError(ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
    return ControlError(ControlRefusal.FLEET_UNAVAILABLE)
