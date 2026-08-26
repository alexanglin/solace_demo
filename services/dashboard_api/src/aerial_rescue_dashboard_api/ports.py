"""Typed injected seams for dashboard persistence, scenario control, and replay validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol


class RunMode(Enum):
    """The two public dashboard runtime modes."""

    DEGRADED_LIVE = "degradedLive"
    REPLAY = "replay"


class MutationKind(Enum):
    """The only public state-changing operations in this increment."""

    START = "start"
    RESET = "reset"


class OperationState(Enum):
    """Whether an operation still needs reconciliation or owns exact response bytes."""

    PENDING = "pending"
    COMPLETED = "completed"


@dataclass(frozen=True)
class StoredResponse:
    """An exact status and response byte sequence retained by the store."""

    status: int
    body: bytes


@dataclass(frozen=True)
class CurrentRun:
    """The public identity of one live run or replay session."""

    mode: RunMode
    scenario_id: str
    scenario_revision: int
    mission_id: str | None
    run_id: str | None
    session_id: str | None
    started: bool = False

    @property
    def identity(self) -> str:
        """Return the run-bound identity used by opaque cursors."""
        identity = self.run_id if self.mode is RunMode.DEGRADED_LIVE else self.session_id
        if identity is None:
            message = "current run is missing the identity required by its mode"
            raise ValueError(message)
        return identity


@dataclass(frozen=True)
class MutationProposal:
    """A complete stable operation identity proposed for one durable claim."""

    idempotency_key: str
    kind: MutationKind
    mode: RunMode
    request_digest: str
    scenario_id: str
    scenario_revision: int
    mission_id: str | None
    run_id: str | None
    session_id: str | None
    predecessor_mission_id: str | None


@dataclass(frozen=True)
class ClaimedOperation:
    """The durable operation returned for a new claim or a retry."""

    idempotency_key: str
    kind: MutationKind
    mode: RunMode
    request_digest: str
    scenario_id: str
    scenario_revision: int
    mission_id: str | None
    run_id: str | None
    session_id: str | None
    predecessor_mission_id: str | None
    state: OperationState
    response: StoredResponse | None
    newly_claimed: bool

    @classmethod
    def from_proposal(cls, proposal: MutationProposal) -> ClaimedOperation:
        """Create the pending representation of a successful first claim."""
        return cls(
            idempotency_key=proposal.idempotency_key,
            kind=proposal.kind,
            mode=proposal.mode,
            request_digest=proposal.request_digest,
            scenario_id=proposal.scenario_id,
            scenario_revision=proposal.scenario_revision,
            mission_id=proposal.mission_id,
            run_id=proposal.run_id,
            session_id=proposal.session_id,
            predecessor_mission_id=proposal.predecessor_mission_id,
            state=OperationState.PENDING,
            response=None,
            newly_claimed=True,
        )


@dataclass(frozen=True)
class Activation:
    """A prepared run and pointer representation persisted before external effects."""

    current_run: CurrentRun
    prepared_initial_state: bytes
    predecessor_mission_id: str | None = None


@dataclass(frozen=True)
class SnapshotBasis:
    """Run, prepared bytes, and committed audit watermark captured atomically."""

    current_run: CurrentRun
    prepared_initial_state: bytes
    audit_watermark: int


@dataclass(frozen=True)
class StoredEvent:
    """One audit ordinal and its exact canonical normalized-event bytes."""

    audit_ordinal: int
    kind: str
    payload: bytes


@dataclass(frozen=True)
class ScenarioRunStatus:
    """The private scenario service's authoritative run representation."""

    scenario_id: str
    scenario_revision: int
    mission_id: str
    run_id: str
    state: Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]


class ScenarioRunNotFoundError(Exception):
    """The private status endpoint has no representation for a stable pending run."""


class ScenarioCancellationNotEstablishedError(Exception):
    """The private cancel endpoint could not establish a stopped or terminal run."""


@dataclass(frozen=True)
class ReplayPreparation:
    """One validator-approved exact replay bundle."""

    bundle_bytes: bytes


class IdentifierSource(Protocol):
    """Generate non-secret stable identifiers before an operation is claimed."""

    def new(self, kind: str) -> str:
        """Return a fresh identifier for the requested namespace."""


class ResourcePort(Protocol):
    """Release production resources during application lifespan shutdown."""

    async def close(self) -> None:
        """Close every owned network and database resource."""


class StorePort(Protocol):
    """High-level transaction seam implemented with the R4 store repositories."""

    async def readiness(self) -> tuple[str, ...]:
        """Return bounded store/read-path readiness reasons."""

    async def claim_operation(self, proposal: MutationProposal) -> ClaimedOperation:
        """Claim or lock-and-return one durable operation."""

    async def prepare_run(self, activation: Activation) -> CurrentRun:
        """Persist and select one prepared run before any private control effect."""

    async def complete_operation(self, idempotency_key: str, response: StoredResponse) -> None:
        """Persist exact response bytes without creating or selecting a run."""

    async def pending_operation(self) -> ClaimedOperation | None:
        """Lock and return the at-most-one pending operation."""

    async def current_run(self) -> CurrentRun | None:
        """Return the current run pointer."""

    async def run_for_mission(self, mission_id: str) -> CurrentRun | None:
        """Return one retained live run by its operational mission identity."""

    async def mission_lifecycle(self, mission_id: str) -> str:
        """Return the recorder-owned durable lifecycle for one operational mission."""

    async def replay_session_known(self, session_id: str) -> bool:
        """Return whether history contains this exact replay-session identity."""

    async def capture_snapshot_basis(self) -> SnapshotBasis | None:
        """Capture the pointer, exact prepared bytes, and audit watermark atomically."""

    async def read_events(
        self,
        run: CurrentRun,
        after_ordinal: int,
        through_ordinal: int | None,
        limit: int,
    ) -> tuple[StoredEvent, ...]:
        """Read one bounded, audit-ordered event page."""


class ScenarioPort(Protocol):
    """Authenticated private scenario catalog and lifecycle control."""

    async def readiness(self) -> tuple[str, ...]:
        """Return bounded catalog/control readiness reasons."""

    async def catalog(self) -> bytes:
        """Return a validated public catalog representation."""

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Start exactly one stable run without automatic uncertain retry."""

    async def status(self, run_id: str) -> ScenarioRunStatus:
        """Reconcile one uncertain run using its stable identity."""

    async def cancel(self, mission_id: str, run_id: str, timeout: float) -> ScenarioRunStatus:
        """Establish cancellation within the caller's remaining shared budget."""

    async def recover(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Delegate a lost-run ABORTED lifecycle publication to scenario service."""


class RecorderReadinessPort(Protocol):
    """The active recorder-capture prerequisite used only by degraded-live mode."""

    async def readiness(self) -> tuple[str, ...]:
        """Return one bounded public reason when the recorder lease is not fresh."""


class ReplayPort(Protocol):
    """Isolated replay validation and exact-byte artifact access."""

    async def readiness(self) -> tuple[str, ...]:
        """Return bounded store/artifact readiness reasons."""

    async def prepare(self, scenario_id: str, scenario_revision: int) -> ReplayPreparation:
        """Return validator-approved exact bytes for one scenario revision."""

    async def bundle(self, session_id: str) -> bytes | None:
        """Return exact validator output bytes for one known session."""
