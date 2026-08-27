"""Scenario lifecycle orchestration over catalog, fleet HTTP, and mission publication ports."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Literal, Protocol

from aerial_rescue_contracts.canonical import canonical_bytes

from aerial_rescue_scenario_service.catalog import (
    CatalogRefusal,
    LoadedScenario,
    ScenarioCatalogError,
    ScenarioCatalogLoader,
    project_fleet_scenario,
)
from aerial_rescue_scenario_service.fleet_client import FleetClientCode, FleetClientError
from aerial_rescue_scenario_service.lifecycle import MissionLifecycle, MissionLifecyclePort
from aerial_rescue_scenario_service.wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
    ScenarioCatalogResponse,
    ScenarioControlRecoveryRequest,
    ScenarioControlRunStatus,
    ScenarioControlStartRequest,
)

_CONTROL_VERSION: Final = 1
_DECLARED_COUNT: Final = 23
_SIMULATED_COUNT: Final = 20
_DECLARED_ONLY_COUNT: Final = 3
_TERMINAL_STATES: Final = frozenset({"EXHAUSTED", "ABORTED"})

type ScenarioState = MissionLifecycle


class ScenarioControlCode(Enum):
    """Operation refusals mapped to the closed scenario-control vocabulary."""

    RUN_CONFLICT = "RUN_CONFLICT"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    CANCELLATION_NOT_ESTABLISHED = "CANCELLATION_NOT_ESTABLISHED"
    SCENARIO_NOT_FOUND = "SCENARIO_NOT_FOUND"
    SCENARIO_REVISION_MISMATCH = "SCENARIO_REVISION_MISMATCH"
    FLEET_UNAVAILABLE = "FLEET_UNAVAILABLE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class ScenarioControlError(ValueError):
    """A typed, redacted scenario-control operation refusal."""

    def __init__(self, code: ScenarioControlCode, value: object) -> None:
        """Retain the closed refusal code and bounded diagnostic value."""
        super().__init__(f"{code.value}: {value!r}")
        self.code = code
        self.value = value


class FleetControlPort(Protocol):
    """The separate-process fleet-control calls scenario orchestration may make."""

    def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start once or reconcile an uncertain start by status."""

    def status(self, run_id: str) -> FleetControlRunStatus:
        """Return one stable fleet run's current status."""

    def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        """Stop a fleet run inside the caller's shared remaining budget."""


MonitorWait = Callable[[threading.Event], bool]


@dataclass
class _Run:
    """One bounded scenario run and the lifecycle facts acknowledged for it."""

    scenario_id: str
    scenario_revision: Literal[1]
    mission_id: str
    run_id: str
    canonical_start: bytes | None
    canonical_recovery: bytes | None
    state: ScenarioState | None = None
    published: set[ScenarioState] = field(default_factory=set)
    monitor_stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    event_lock: threading.Lock = field(default_factory=threading.Lock)
    monitor_started: bool = False
    monitor_thread: threading.Thread | None = None
    recovered: bool = False
    handoff_pending: bool = False
    handoff_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def identity(self) -> tuple[str, int, str, str]:
        """Return the stable semantic identity shared by start and recovery."""
        return (self.scenario_id, self.scenario_revision, self.mission_id, self.run_id)


@dataclass
class ScenarioControl:
    """Coordinate scenario-only mission lifecycle around one bounded fleet registry."""

    loader: ScenarioCatalogLoader
    fleet: FleetControlPort
    mission_events: MissionLifecyclePort
    maximum_runs: int
    monitor_wait: MonitorWait
    cancellation_budget_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    _runs: dict[str, _Run] = field(default_factory=dict, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def __post_init__(self) -> None:
        """Refuse an unbounded registry or cancellation budget."""
        if self.maximum_runs < 1:
            message = "maximum_runs must be positive"
            raise ValueError(message)
        if self.cancellation_budget_seconds <= 0:
            message = "cancellation_budget_seconds must be positive"
            raise ValueError(message)

    def catalog_response(self) -> ScenarioCatalogResponse:
        """Return the strict browser-facing projection from the single catalog loader."""
        return self.loader.catalog_response()

    def start(self, request: ScenarioControlStartRequest) -> ScenarioControlRunStatus:
        """Publish PLANNED, start fleet once, then actively monitor mission completion."""
        canonical_start = canonical_bytes(request.model_dump(by_alias=True))
        known = self._existing_start(request.run_id, canonical_start)
        if known is not None:
            return self._reconcile_pending_handoff(known)
        loaded = self._load_prepared(request.scenario_id, request.scenario_revision)
        run = _Run(
            scenario_id=request.scenario_id,
            scenario_revision=request.scenario_revision,
            mission_id=request.mission_id,
            run_id=request.run_id,
            canonical_start=canonical_start,
            canonical_recovery=None,
            handoff_pending=True,
        )
        registered = self._register_start(run, canonical_start)
        if registered is not run:
            return self._reconcile_pending_handoff(registered)
        try:
            return self._begin_handoff(run, loaded)
        finally:
            run.handoff_lock.release()

    def status(self, run_id: str) -> ScenarioControlRunStatus:
        """Return the current scenario-owned status without manufacturing a fleet fact."""
        with self._lock:
            run = self._known(run_id)
        return self._reconcile_pending_handoff(run)

    def cancel(self, run_id: str, mission_id: str) -> ScenarioControlRunStatus:
        """Stop fleet inside one monotonic budget, then publish ABORTED if it stopped."""
        started = self.monotonic()
        with self._lock:
            run = self._known(run_id)
            if run.mission_id != mission_id:
                raise ScenarioControlError(ScenarioControlCode.RUN_CONFLICT, run_id)
            if run.state in _TERMINAL_STATES:
                return self._status(run)
        remaining = self.cancellation_budget_seconds - (self.monotonic() - started)
        if remaining <= 0:
            raise ScenarioControlError(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, run_id)
        request = FleetControlCancelRequest(
            controlVersion=_CONTROL_VERSION,
            missionId=mission_id,
            runId=run_id,
        )
        try:
            status = self.fleet.cancel(request, remaining)
        except FleetClientError as error:
            code = (
                ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED
                if error.code is FleetClientCode.CANCELLATION_NOT_ESTABLISHED
                else ScenarioControlCode.FLEET_UNAVAILABLE
            )
            raise ScenarioControlError(code, run_id) from error
        if status.state in {"ACCEPTED", "RUNNING"}:
            raise ScenarioControlError(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, run_id)
        self._apply_fleet(run, status, publish=True)
        return self._status(run)

    def recover(self, request: ScenarioControlRecoveryRequest) -> ScenarioControlRunStatus:
        """Reconcile fleet first and publish one ABORTED fact only for an unknown run."""
        canonical_recovery = canonical_bytes(request.model_dump(by_alias=True))
        expected = (
            request.scenario_id,
            request.scenario_revision,
            request.mission_id,
            request.run_id,
        )
        with self._lock:
            run = self._runs.get(request.run_id)
            if run is not None:
                if run.identity != expected or (
                    run.canonical_recovery is not None
                    and run.canonical_recovery != canonical_recovery
                ):
                    raise ScenarioControlError(ScenarioControlCode.RUN_CONFLICT, request.run_id)
                if run.recovered:
                    return self._status(run)
            else:
                self._admit_capacity()
        self._load_prepared(request.scenario_id, request.scenario_revision)
        if run is None:
            candidate = _Run(
                scenario_id=request.scenario_id,
                scenario_revision=request.scenario_revision,
                mission_id=request.mission_id,
                run_id=request.run_id,
                canonical_start=None,
                canonical_recovery=canonical_recovery,
            )
            with self._lock:
                run = self._runs.setdefault(request.run_id, candidate)
                if run.identity != expected:
                    raise ScenarioControlError(ScenarioControlCode.RUN_CONFLICT, request.run_id)
        run.canonical_recovery = canonical_recovery
        try:
            status = self.fleet.status(request.run_id)
        except FleetClientError as error:
            if error.code is not FleetClientCode.RUN_NOT_FOUND:
                raise ScenarioControlError(
                    ScenarioControlCode.FLEET_UNAVAILABLE, request.run_id
                ) from error
            self._transition(run, "ABORTED")
            run.recovered = True
            run.done.set()
            return self._status(run)
        self._apply_fleet(run, status, publish=False)
        run.recovered = True
        self._start_monitor(run)
        return self._status(run)

    def wait(self, run_id: str, timeout_seconds: float) -> ScenarioControlRunStatus:
        """Wait within a caller-owned bound for one run to finish."""
        with self._lock:
            run = self._known(run_id)
            done = run.done
        done.wait(timeout_seconds)
        return self.status(run_id)

    def close(self) -> None:
        """Stop every monitor and join them within one shared shutdown bound."""
        deadline = self.monotonic() + self.cancellation_budget_seconds
        with self._lock:
            runs = tuple(self._runs.values())
            for run in runs:
                run.monitor_stop.set()
            threads = tuple(run.monitor_thread for run in runs if run.monitor_thread is not None)
        for thread in threads:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise ScenarioControlError(
                    ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, thread.name
                )
            thread.join(remaining)
            if thread.is_alive():
                raise ScenarioControlError(
                    ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, thread.name
                )

    def _load_prepared(self, scenario_id: str, revision: int) -> LoadedScenario:
        try:
            loaded = self.loader.load(scenario_id, revision)
        except ScenarioCatalogError as error:
            if error.reason is CatalogRefusal.SCENARIO_NOT_FOUND:
                code = ScenarioControlCode.SCENARIO_NOT_FOUND
            elif error.reason is CatalogRefusal.REVISION_NOT_FOUND:
                code = ScenarioControlCode.SCENARIO_REVISION_MISMATCH
            else:
                code = ScenarioControlCode.INTERNAL_FAILURE
            raise ScenarioControlError(code, (scenario_id, revision)) from error
        simulated = sum(
            member.participation == "SIMULATED_DRONE" for member in loaded.definition.members
        )
        declared_only = len(loaded.definition.members) - simulated
        if (len(loaded.definition.members), simulated, declared_only) != (
            _DECLARED_COUNT,
            _SIMULATED_COUNT,
            _DECLARED_ONLY_COUNT,
        ):
            raise ScenarioControlError(
                ScenarioControlCode.SCENARIO_REVISION_MISMATCH, (scenario_id, revision)
            )
        return loaded

    def _admit_capacity(self) -> None:
        if len(self._runs) >= self.maximum_runs:
            raise ScenarioControlError(ScenarioControlCode.INTERNAL_FAILURE, "run capacity")

    def _known(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise ScenarioControlError(ScenarioControlCode.RUN_NOT_FOUND, run_id)
        return run

    def _status(self, run: _Run) -> ScenarioControlRunStatus:
        if run.state is None:
            raise ScenarioControlError(ScenarioControlCode.INTERNAL_FAILURE, run.run_id)
        return ScenarioControlRunStatus(
            controlVersion=_CONTROL_VERSION,
            scenarioId=run.scenario_id,
            scenarioRevision=run.scenario_revision,
            missionId=run.mission_id,
            runId=run.run_id,
            state=run.state,
        )

    def _existing_start(self, run_id: str, canonical_start: bytes) -> _Run | None:
        with self._lock:
            known = self._runs.get(run_id)
            if known is None:
                self._admit_capacity()
            elif known.canonical_start != canonical_start:
                raise ScenarioControlError(ScenarioControlCode.RUN_CONFLICT, run_id)
            return known

    def _register_start(self, run: _Run, canonical_start: bytes) -> _Run:
        run.handoff_lock.acquire()
        try:
            with self._lock:
                known = self._runs.get(run.run_id)
                if known is None:
                    self._admit_capacity()
                    self._runs[run.run_id] = run
                    return run
                if known.canonical_start != canonical_start:
                    raise ScenarioControlError(ScenarioControlCode.RUN_CONFLICT, run.run_id)
                return known
        finally:
            with self._lock:
                registered = self._runs.get(run.run_id)
            if registered is not run:
                run.handoff_lock.release()

    def _begin_handoff(self, run: _Run, loaded: LoadedScenario) -> ScenarioControlRunStatus:
        self._transition(run, "PLANNED")
        fleet_request = FleetControlStartRequest(
            controlVersion=_CONTROL_VERSION,
            runId=run.run_id,
            scenario=project_fleet_scenario(loaded.definition, run.mission_id),
        )
        try:
            status = self.fleet.start(fleet_request)
        except FleetClientError as error:
            if error.code is FleetClientCode.RUN_NOT_FOUND:
                self._abort_lost_handoff(run)
                return self._status(run)
            raise ScenarioControlError(ScenarioControlCode.FLEET_UNAVAILABLE, run.run_id) from error
        self._establish_handoff(run, status)
        return self._status(run)

    def _reconcile_pending_handoff(self, run: _Run) -> ScenarioControlRunStatus:
        with run.handoff_lock:
            with self._lock:
                if not run.handoff_pending:
                    return self._status(run)
            try:
                status = self.fleet.status(run.run_id)
            except FleetClientError as error:
                if error.code is FleetClientCode.RUN_NOT_FOUND:
                    self._abort_lost_handoff(run)
                    return self._status(run)
                raise ScenarioControlError(
                    ScenarioControlCode.FLEET_UNAVAILABLE, run.run_id
                ) from error
            self._establish_handoff(run, status)
            return self._status(run)

    def _abort_lost_handoff(self, run: _Run) -> None:
        self._transition(run, "ABORTED")
        with self._lock:
            run.handoff_pending = False

    def _establish_handoff(self, run: _Run, status: FleetControlRunStatus) -> None:
        self._apply_fleet(run, status, publish=True)
        with self._lock:
            run.handoff_pending = False
        self._start_monitor(run)

    def _transition(self, run: _Run, state: ScenarioState) -> None:
        with run.event_lock:
            with self._lock:
                if run.state in _TERMINAL_STATES or state in run.published:
                    return
            self.mission_events.publish(run.run_id, run.mission_id, state)
            with self._lock:
                run.published.add(state)
                run.state = state
                if state in _TERMINAL_STATES:
                    run.done.set()
                    run.monitor_stop.set()

    def _apply_fleet(self, run: _Run, status: FleetControlRunStatus, *, publish: bool) -> None:
        if status.run_id != run.run_id or status.mission_id != run.mission_id:
            raise ScenarioControlError(ScenarioControlCode.FLEET_UNAVAILABLE, run.run_id)
        target: ScenarioState
        if status.state in {"ACCEPTED", "RUNNING"}:
            target = "SEARCHING"
        elif status.state == "EXHAUSTED":
            target = "EXHAUSTED"
        else:
            target = "ABORTED"
        if publish:
            if target == "EXHAUSTED" and run.state == "PLANNED":
                self._transition(run, "SEARCHING")
            self._transition(run, target)
        else:
            with self._lock:
                run.state = target
                if target in _TERMINAL_STATES:
                    run.done.set()

    def _start_monitor(self, run: _Run) -> None:
        with self._lock:
            if run.state in _TERMINAL_STATES or run.monitor_started:
                return
            run.monitor_started = True
        thread = threading.Thread(
            target=self._monitor,
            args=(run,),
            name=f"scenario-run-{run.run_id}",
            daemon=True,
        )
        with self._lock:
            run.monitor_thread = thread
        thread.start()

    def _monitor(self, run: _Run) -> None:
        while not run.monitor_stop.is_set():
            if self.monitor_wait(run.monitor_stop):
                return
            try:
                status = self.fleet.status(run.run_id)
            except FleetClientError:
                self._transition(run, "ABORTED")
                return
            self._apply_fleet(run, status, publish=True)
            if status.state in {"EXHAUSTED", "CANCELLED", "FAILED"}:
                return
