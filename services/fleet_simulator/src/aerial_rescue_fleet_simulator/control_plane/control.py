"""Stable private fleet-run coordination over an injected executable runtime."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Literal, Protocol, override

from aerial_rescue_broker.messaging import BrokerEndpoint
from aerial_rescue_broker.queues import drone_queue_name
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_domain.principals import Principal

from aerial_rescue_fleet_simulator.control_plane.runtime import ControlError, ControlRefusal
from aerial_rescue_fleet_simulator.control_plane.wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
    FleetScenarioDocument,
)
from aerial_rescue_fleet_simulator.lifecycle import BrokerFleetLifecycle
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.service import (
    FleetSessionPort,
    IntakeBounds,
    Pacer,
    PublishOutcome,
    Runtime,
    ServeReport,
    SessionOpener,
    StampSource,
    serve,
)

_ACTIVE_STATES = frozenset({"ACCEPTED", "RUNNING"})


class RunExecutor(Protocol):
    """The broker/store-backed runtime the coordinator owns but does not implement."""

    @property
    def ready(self) -> bool:
        """Return whether queues, store, broker, bindings, and outboxes are ready."""

    async def startup(self) -> None:
        """Validate queues and acquire all bounded dependencies."""

    async def shutdown(self) -> None:
        """Stop intake and release consumers, publishers, and store resources."""

    async def execute(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
    ) -> FleetControlRunStatus:
        """Run one accepted scenario until terminal state or cancellation."""


@dataclass(slots=True)
class _RunRecord:
    """One stable request binding and its current task-owned status."""

    digest: str
    status: FleetControlRunStatus
    cancelled: asyncio.Event
    task: asyncio.Task[None] | None = None


def _status(
    request: FleetControlStartRequest,
    state: str,
    ticks: int = 0,
    telemetry: int = 0,
) -> FleetControlRunStatus:
    """Build one strict status from coordinator-owned counters."""
    return FleetControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "missionId": request.scenario.mission_id,
            "runId": request.run_id,
            "state": state,
            "completedTickCount": ticks,
            "telemetryPublicationCount": telemetry,
        }
    )


def _digest(request: FleetControlStartRequest) -> str:
    """Return the stable digest binding one run identifier to its exact request."""
    document = request.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(canonical.canonical_bytes(document)).hexdigest()


class FleetCoordinator:
    """Coordinate bounded concurrent runs without owning broker or SQL implementations."""

    def __init__(
        self,
        executor: RunExecutor,
        *,
        cancellation_timeout_seconds: float,
        capacity: int,
    ) -> None:
        """Bind explicit cancellation and run-capacity limits."""
        if cancellation_timeout_seconds <= 0 or capacity < 1:
            message = "fleet coordinator bounds must be positive"
            raise ValueError(message)
        self._executor = executor
        self._cancellation_timeout = cancellation_timeout_seconds
        self._capacity = capacity
        self._runs: dict[str, _RunRecord] = {}
        self._lock = asyncio.Lock()
        self._started = False

    @property
    def ready(self) -> bool:
        """Require successful lifecycle startup and executor recovery readiness."""
        return self._started and self._executor.ready

    async def startup(self) -> None:
        """Validate and acquire the executor dependencies before accepting work."""
        await self._executor.startup()
        self._started = True

    async def shutdown(self) -> None:
        """Signal every run, bound their completion, then close the executor."""
        self._started = False
        async with self._lock:
            records = tuple(self._runs.values())
            for record in records:
                record.cancelled.set()
            tasks = tuple(record.task for record in records if record.task is not None)
        try:
            async with asyncio.timeout(self._cancellation_timeout):
                await asyncio.gather(*tasks)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._executor.shutdown()

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start one stable request, reconcile an exact repeat, or fail closed."""
        digest = _digest(request)
        async with self._lock:
            known = self._runs.get(request.run_id)
            if known is not None:
                if known.digest != digest:
                    raise ControlError(ControlRefusal.RUN_CONFLICT)
                return known.status
            active = sum(record.status.state in _ACTIVE_STATES for record in self._runs.values())
            if active >= self._capacity:
                raise ControlError(ControlRefusal.CAPACITY_EXCEEDED)
            record = _RunRecord(digest, _status(request, "ACCEPTED"), asyncio.Event())
            self._runs[request.run_id] = record
            record.task = asyncio.create_task(self._execute(request, record))
            return record.status

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Return one stable run status or a closed not-found refusal."""
        async with self._lock:
            try:
                return self._runs[run_id].status
            except KeyError as error:
                raise ControlError(ControlRefusal.RUN_NOT_FOUND) from error

    async def cancel(self, request: FleetControlCancelRequest) -> FleetControlRunStatus:
        """Signal and await one exact mission/run binding within the shared bound."""
        async with self._lock:
            try:
                record = self._runs[request.run_id]
            except KeyError as error:
                raise ControlError(ControlRefusal.RUN_NOT_FOUND) from error
            if record.status.mission_id != request.mission_id:
                raise ControlError(ControlRefusal.PATH_BODY_MISMATCH)
            record.cancelled.set()
            task = record.task
        if task is not None:
            try:
                async with asyncio.timeout(self._cancellation_timeout):
                    await asyncio.shield(task)
            except TimeoutError as error:
                raise ControlError(ControlRefusal.CANCELLATION_NOT_ESTABLISHED) from error
        return await self.status(request.run_id)

    async def _execute(self, request: FleetControlStartRequest, record: _RunRecord) -> None:
        """Run one task, containing failures as an observable terminal status."""
        async with self._lock:
            record.status = _status(request, "RUNNING")
        try:
            result = await self._executor.execute(request, record.cancelled)
            identities_match = (
                result.run_id == request.run_id and result.mission_id == request.scenario.mission_id
            )
            record.status = result if identities_match else _status(request, "FAILED")
        except Exception:
            record.status = _status(request, "FAILED")


_CONTROL_VERSION: Final = 1
_TERMINAL_STATES: Final = frozenset({"EXHAUSTED", "CANCELLED", "FAILED"})
_NANOSECONDS_PER_MILLISECOND: Final = 1_000_000
_MILLISECONDS_PER_SECOND: Final = 1_000

type FleetRunState = Literal["ACCEPTED", "RUNNING", "EXHAUSTED", "CANCELLED", "FAILED"]


class FleetControlCode(Enum):
    """Operation refusals that map directly to the closed fleet-control vocabulary."""

    RUN_CONFLICT = "RUN_CONFLICT"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    CANCELLATION_NOT_ESTABLISHED = "CANCELLATION_NOT_ESTABLISHED"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    RUN_FAILED = "RUN_FAILED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class FleetControlError(ValueError):
    """A typed, redacted fleet-control operation refusal."""

    def __init__(self, code: FleetControlCode, value: object) -> None:
        """Retain the closed refusal code and bounded diagnostic value."""
        super().__init__(f"{code.value}: {value!r}")
        self.code = code
        self.value = value


class FleetExecutor(Protocol):
    """Execute one already validated fleet start until exhaustion or cancellation."""

    def __call__(
        self, request: FleetControlStartRequest, cancellation: threading.Event
    ) -> ServeReport:
        """Return the complete deterministic run report."""


def to_fleet_scenario(document: FleetScenarioDocument) -> FleetScenario:
    """Construct the simulator's owning value without dropping or defaulting a field."""
    seen_absences: set[tuple[str, int]] = set()
    absences: dict[str, set[int]] = {}
    for absence in document.absent_heartbeats:
        identity = (absence.drone_id, absence.tick_ordinal)
        if identity in seen_absences:
            raise FleetControlError(FleetControlCode.RUN_FAILED, "duplicate heartbeat absence")
        seen_absences.add(identity)
        absences.setdefault(absence.drone_id, set()).add(absence.tick_ordinal)
    thresholds = document.connectivity_thresholds
    return FleetScenario(
        mission_id=document.mission_id,
        drones=tuple(
            DroneStart(
                drone_id=drone.drone_id,
                sector_id=drone.sector_id,
                latitude_microdegrees=drone.latitude_microdegrees,
                longitude_microdegrees=drone.longitude_microdegrees,
                altitude_metres=drone.altitude_metres,
                heading_degrees=drone.heading_degrees,
                ground_speed_centimetres_per_second=drone.ground_speed_centimetres_per_second,
                battery_permille=drone.battery_permille,
                north_microdegrees_per_tick=drone.north_microdegrees_per_tick,
                east_microdegrees_per_tick=drone.east_microdegrees_per_tick,
                battery_drain_permille_per_tick=drone.battery_drain_permille_per_tick,
            )
            for drone in document.drones
        ),
        tick_interval_milliseconds=document.tick_interval_milliseconds,
        thresholds=ConnectivityThresholds(
            misses_to_degraded=thresholds.misses_to_degraded,
            misses_to_offline=thresholds.misses_to_offline,
            heartbeats_to_recover=thresholds.heartbeats_to_recover,
        ),
        ticks_to_sweep=document.ticks_to_sweep,
        absent_heartbeats={drone_id: frozenset(ticks) for drone_id, ticks in absences.items()},
    )


@dataclass
class _Run:
    """One bounded process-local run and its stable idempotency identity."""

    request: FleetControlStartRequest
    canonical_request: bytes
    state: FleetRunState = "ACCEPTED"
    completed_tick_count: int = 0
    telemetry_publication_count: int = 0
    cancellation: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)


@dataclass
class FleetControl:
    """Own a bounded stable-run registry and its interruptible worker threads."""

    executor: FleetExecutor
    maximum_runs: int
    cancellation_wait_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    _runs: dict[str, _Run] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        """Refuse an unbounded registry or cancellation wait."""
        if self.maximum_runs < 1:
            message = "maximum_runs must be positive"
            raise ValueError(message)
        if self.cancellation_wait_seconds <= 0:
            message = "cancellation_wait_seconds must be positive"
            raise ValueError(message)

    def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Register once by stable run and launch exactly one simulator worker."""
        canonical_request = canonical_bytes(request.model_dump(by_alias=True))
        with self._lock:
            known = self._runs.get(request.run_id)
            if known is not None:
                if known.canonical_request != canonical_request:
                    raise FleetControlError(FleetControlCode.RUN_CONFLICT, request.run_id)
                return self._status(known)
            if len(self._runs) >= self.maximum_runs:
                raise FleetControlError(FleetControlCode.CAPACITY_EXCEEDED, self.maximum_runs)
            try:
                to_fleet_scenario(request.scenario)
            except FleetControlError:
                raise
            except ValueError as error:
                raise FleetControlError(FleetControlCode.RUN_FAILED, request.run_id) from error
            run = _Run(request, canonical_request)
            self._runs[request.run_id] = run
            accepted = self._status(run)
        worker = threading.Thread(
            target=self._execute,
            args=(run,),
            name=f"fleet-run-{request.run_id}",
            daemon=True,
        )
        worker.start()
        return accepted

    def status(self, run_id: str) -> FleetControlRunStatus:
        """Return the current status of one known run."""
        with self._lock:
            return self._status(self._known(run_id))

    def cancel(self, run_id: str, mission_id: str) -> FleetControlRunStatus:
        """Interrupt one run and report success only after it has stopped."""
        with self._lock:
            run = self._known(run_id)
            if run.request.scenario.mission_id != mission_id:
                raise FleetControlError(FleetControlCode.RUN_CONFLICT, run_id)
            if run.state in _TERMINAL_STATES:
                return self._status(run)
            run.cancellation.set()
            done = run.done
        if not done.wait(self.cancellation_wait_seconds):
            raise FleetControlError(FleetControlCode.CANCELLATION_NOT_ESTABLISHED, run_id)
        with self._lock:
            return self._status(self._known(run_id))

    def wait(self, run_id: str, timeout_seconds: float) -> FleetControlRunStatus:
        """Wait within a caller-owned bound, then return the current status."""
        with self._lock:
            run = self._known(run_id)
            done = run.done
        done.wait(timeout_seconds)
        return self.status(run_id)

    def close(self) -> None:
        """Cancel every active worker and wait within one shared shutdown bound."""
        deadline = self.monotonic() + self.cancellation_wait_seconds
        with self._lock:
            active = tuple(run for run in self._runs.values() if run.state not in _TERMINAL_STATES)
            for run in active:
                run.cancellation.set()
        for run in active:
            remaining = deadline - self.monotonic()
            if remaining <= 0 or not run.done.wait(remaining):
                raise FleetControlError(
                    FleetControlCode.CANCELLATION_NOT_ESTABLISHED, run.request.run_id
                )

    def _known(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise FleetControlError(FleetControlCode.RUN_NOT_FOUND, run_id)
        return run

    def _status(self, run: _Run) -> FleetControlRunStatus:
        return FleetControlRunStatus(
            controlVersion=_CONTROL_VERSION,
            missionId=run.request.scenario.mission_id,
            runId=run.request.run_id,
            state=run.state,
            completedTickCount=run.completed_tick_count,
            telemetryPublicationCount=run.telemetry_publication_count,
        )

    def _execute(self, run: _Run) -> None:
        with self._lock:
            run.state = "RUNNING"
        try:
            report = self.executor(run.request, run.cancellation)
        except Exception:
            with self._lock:
                run.state = "FAILED"
                run.done.set()
            return
        with self._lock:
            run.completed_tick_count = report.state.tick
            run.telemetry_publication_count = report.outcomes.get(PublishOutcome.PUBLISHED, 0)
            if report.state.mission is MissionState.EXHAUSTED:
                run.state = "EXHAUSTED"
            elif run.cancellation.is_set():
                run.state = "CANCELLED"
            else:
                run.state = "FAILED"
            run.done.set()


@dataclass(frozen=True)
class InterruptiblePacer:
    """Keep the one-second interval interruptible by the run's cancellation event."""

    cancellation: threading.Event
    monotonic_nanoseconds: Callable[[], int] = time.monotonic_ns

    def now_milliseconds(self) -> int:
        """Return an injected monotonic reading in integer milliseconds."""
        return self.monotonic_nanoseconds() // _NANOSECONDS_PER_MILLISECOND

    def wait(self, milliseconds: int) -> None:
        """Wait for the interval or return immediately when cancellation is requested."""
        self.cancellation.wait(milliseconds / _MILLISECONDS_PER_SECOND)


StampFactory = Callable[[str], StampSource]
PacerFactory = Callable[[threading.Event], Pacer]


@dataclass(frozen=True, repr=False)
class FleetWorker:
    """Adapt one fleet-control request to the existing broker-backed simulation loop."""

    endpoint: BrokerEndpoint
    broker_credential: str
    open_broker: SessionOpener
    stamp_factory: StampFactory
    send_budget: SendBudget
    intake: IntakeBounds
    pacer_factory: PacerFactory
    command_intake_enabled: bool = True

    @override
    def __repr__(self) -> str:
        return "FleetWorker(broker_credential=<redacted>)"

    def __call__(
        self, request: FleetControlStartRequest, cancellation: threading.Event
    ) -> ServeReport:
        """Open one fleet session, publish the run, and close every broker port."""
        scenario = to_fleet_scenario(request.scenario)
        queues: Mapping[str, str] = (
            {drone.drone_id: drone_queue_name(drone.drone_id) for drone in scenario.drones}
            if self.command_intake_enabled
            else {}
        )
        session: FleetSessionPort = self.open_broker(
            self.endpoint,
            Principal.FLEET_SIMULATOR,
            self.broker_credential,
            queues,
        )
        stamps = self.stamp_factory(request.run_id)
        runtime = Runtime(
            endpoint=self.endpoint,
            credential=self.broker_credential,
            open_broker=self.open_broker,
            scenario=scenario,
            stamps=stamps,
            running=lambda: not cancellation.is_set(),
            send_budget=self.send_budget,
            intake=self.intake,
            pacer=self.pacer_factory(cancellation),
            lifecycle=BrokerFleetLifecycle(session.results, request.run_id, stamps),
            command_intake_enabled=self.command_intake_enabled,
        )
        try:
            return serve(session, runtime)
        finally:
            session.close()
