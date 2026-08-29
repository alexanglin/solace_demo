"""Thread-safe, mode-specific dashboard liveness and readiness state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock


class RunMode(Enum):
    """The two composition graphs exposed by the dashboard API."""

    DEGRADED_LIVE = "degradedLive"
    REPLAY = "replay"


class Dependency(Enum):
    """A prerequisite that can change while one composition is running."""

    STORE = "store"
    SCENARIO_CONTROL = "scenario-control"
    BROKER_DELIVERY = "broker-delivery"
    REPLAY_INPUT = "replay-input"


class RuntimePhase(Enum):
    """The process-owned admission phase, independent from mission state."""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    SHUTTING_DOWN = "shutting-down"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ReadinessAssessment:
    """One immutable mode-specific readiness answer."""

    ready: bool
    reasons: tuple[str, ...]


_LIVE_REQUIREMENTS = (
    Dependency.STORE,
    Dependency.SCENARIO_CONTROL,
    Dependency.BROKER_DELIVERY,
)
_REPLAY_REQUIREMENTS = (Dependency.REPLAY_INPUT,)


class RuntimeReadiness:
    """Publish dependency and lifecycle changes without partial snapshots."""

    def __init__(self, mode: RunMode) -> None:
        """Create a process that has not started or admitted mutations."""
        self._mode = mode
        self._phase = RuntimePhase.CREATED
        self._dependencies = {dependency: False for dependency in Dependency}
        self._lock = Lock()

    @property
    def mode(self) -> RunMode:
        """Return the composition mode fixed at construction."""
        return self._mode

    @property
    def phase(self) -> RuntimePhase:
        """Return one coherent lifecycle reading."""
        with self._lock:
            return self._phase

    @property
    def accepting_mutations(self) -> bool:
        """Whether route operations may begin new mutation work."""
        with self._lock:
            return self._phase is RuntimePhase.RUNNING

    def set_dependency(self, dependency: Dependency, *, ready: bool) -> None:
        """Publish one dependency edge for callback and event-loop callers."""
        with self._lock:
            self._dependencies[dependency] = ready

    def begin_startup(self) -> None:
        """Move a newly constructed process into startup."""
        self._transition(RuntimePhase.CREATED, RuntimePhase.STARTING)

    def activate(self) -> None:
        """Admit mutations after owned resources have opened."""
        self._transition(RuntimePhase.STARTING, RuntimePhase.RUNNING)

    def abort_startup(self) -> None:
        """Make a failed startup terminal without claiming recovery."""
        self._transition(RuntimePhase.STARTING, RuntimePhase.STOPPED)

    def begin_shutdown(self) -> None:
        """Stop mutation admission before owned resources are closed."""
        self._transition(RuntimePhase.RUNNING, RuntimePhase.SHUTTING_DOWN)

    def finish_shutdown(self) -> None:
        """Record that resource cleanup has completed or failed outwardly."""
        self._transition(RuntimePhase.SHUTTING_DOWN, RuntimePhase.STOPPED)

    def assess(self, requested_mode: RunMode) -> ReadinessAssessment:
        """Assess only dependencies needed to start the requested mode."""
        with self._lock:
            phase = self._phase
            dependencies = dict(self._dependencies)
        if requested_mode is not self._mode:
            return ReadinessAssessment(False, ("mode-unavailable",))
        if phase in {RuntimePhase.CREATED, RuntimePhase.STARTING}:
            return ReadinessAssessment(False, ("starting",))
        if phase is RuntimePhase.SHUTTING_DOWN:
            return ReadinessAssessment(False, ("shutting-down",))
        if phase is RuntimePhase.STOPPED:
            return ReadinessAssessment(False, ("stopped",))
        requirements = (
            _LIVE_REQUIREMENTS if requested_mode is RunMode.DEGRADED_LIVE else _REPLAY_REQUIREMENTS
        )
        reasons = tuple(
            f"{dependency.value}-unavailable"
            for dependency in requirements
            if not dependencies[dependency]
        )
        return ReadinessAssessment(not reasons, reasons)

    def _transition(self, expected: RuntimePhase, successor: RuntimePhase) -> None:
        """Apply one closed lifecycle edge."""
        with self._lock:
            if self._phase is not expected:
                message = "dashboard runtime lifecycle transition refused"
                raise RuntimeError(message)
            self._phase = successor
