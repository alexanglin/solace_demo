"""Deterministic injected ports for dashboard API tests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, Literal

from aerial_rescue_dashboard_api.documents import (
    find_scenario,
    prepare_live_state,
    validated_document,
)
from aerial_rescue_dashboard_api.ports import (
    Activation,
    ClaimedOperation,
    CurrentRun,
    MutationProposal,
    OperationState,
    ReplayPreparation,
    ScenarioRunNotFoundError,
    ScenarioRunStatus,
    SnapshotBasis,
    StoredEvent,
    StoredResponse,
)

ACCEPTED_STATUS: Final = 202
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
HOST: Final = "127.0.0.1:8080"
ORIGIN: Final = "http://127.0.0.1:8080"
BEARER: Final = "test-bearer-not-a-real-secret"


def dashboard_fixture(name: str) -> bytes:
    """Return one current golden dashboard document."""
    return (REPOSITORY_ROOT / f"fixtures/golden/v1/dashboard/{name}/baseline.json").read_bytes()


def live_prepared_state() -> bytes:
    """Build the exact prepared state from the validated wilderness catalog."""
    catalog = validated_document(
        "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json",
        dashboard_fixture("scenario-catalog"),
        maximum_bytes=512 * 1024,
    )
    scenario = find_scenario(catalog, "wilderness-missing-person", 1)
    return prepare_live_state(scenario, "mission-test-0001", None)


@dataclass
class FakeIdentifiers:
    """Return stable, readable identifiers and record each requested kind."""

    counts: dict[str, int] = field(default_factory=dict)

    def new(self, kind: str) -> str:
        """Return the next deterministic identifier of one kind."""
        count = self.counts.get(kind, 0) + 1
        self.counts[kind] = count
        return f"{kind}-test-{count:04d}"


@dataclass
class FakeRecorderReadiness:
    """Expose the active recorder lease outcome without importing a service implementation."""

    reasons: tuple[str, ...] = ()
    checks: int = 0

    async def readiness(self) -> tuple[str, ...]:
        """Return deterministic recorder-capture readiness reasons."""
        self.checks += 1
        return self.reasons


@dataclass
class FakeStore:
    """Model the high-level transaction seam backed by the R4 repositories."""

    ready_reasons: tuple[str, ...] = ()
    operations: dict[str, ClaimedOperation] = field(default_factory=dict)
    current: CurrentRun | None = None
    basis: SnapshotBasis | None = None
    events: tuple[StoredEvent, ...] = ()
    calls: list[str] = field(default_factory=list)
    runs_by_mission: dict[str, CurrentRun] = field(default_factory=dict)
    mission_lifecycles: dict[str, str] = field(default_factory=dict)

    async def readiness(self) -> tuple[str, ...]:
        """Return deterministic store readiness reasons."""
        self.calls.append("readiness")
        return self.ready_reasons

    async def claim_operation(self, proposal: MutationProposal) -> ClaimedOperation:
        """Claim a new operation or return the exact existing representation."""
        self.calls.append(f"claim:{proposal.idempotency_key}")
        existing = self.operations.get(proposal.idempotency_key)
        if existing is not None:
            return replace(existing, newly_claimed=False)
        claimed = ClaimedOperation.from_proposal(proposal)
        self.operations[proposal.idempotency_key] = claimed
        return claimed

    async def prepare_run(self, activation: Activation) -> CurrentRun:
        """Select one stable prepared identity without implying it was started."""
        self.calls.append(f"prepare:{activation.current_run.identity}")
        if self.current is not None and self.current.identity == activation.current_run.identity:
            return self.current
        if self.current is not None and self.current.mission_id is not None:
            self.runs_by_mission[self.current.mission_id] = self.current
        selected = replace(activation.current_run, started=False)
        self.current = selected
        if selected.mission_id is not None:
            self.runs_by_mission[selected.mission_id] = selected
        return selected

    async def complete_operation(self, idempotency_key: str, response: StoredResponse) -> None:
        """Retain exact response bytes without moving the current pointer."""
        self.calls.append(f"complete:{idempotency_key}")
        operation = self.operations[idempotency_key]
        self.operations[idempotency_key] = replace(
            operation,
            state=OperationState.COMPLETED,
            response=response,
            newly_claimed=False,
        )
        if (
            response.status == ACCEPTED_STATUS
            and operation.kind.value == "start"
            and self.current is not None
            and operation.run_id == self.current.run_id
        ):
            self.current = replace(self.current, started=True)
            if self.current.mission_id is not None:
                self.runs_by_mission[self.current.mission_id] = self.current

    async def pending_operation(self) -> ClaimedOperation | None:
        """Return the one pending operation when present."""
        self.calls.append("pending")
        return next(
            (item for item in self.operations.values() if item.state is OperationState.PENDING),
            None,
        )

    async def current_run(self) -> CurrentRun | None:
        """Return the current run pointer."""
        self.calls.append("current")
        return self.current

    async def run_for_mission(self, mission_id: str) -> CurrentRun | None:
        """Return one retained predecessor by operational mission identity."""
        self.calls.append(f"mission-run:{mission_id}")
        if self.current is not None and self.current.mission_id == mission_id:
            return self.current
        return self.runs_by_mission.get(mission_id)

    async def mission_lifecycle(self, mission_id: str) -> str:
        """Return recorder-owned lifecycle for one retained operational mission."""
        self.calls.append(f"mission-lifecycle:{mission_id}")
        return self.mission_lifecycles.get(mission_id, "PLANNED")

    async def replay_session_known(self, session_id: str) -> bool:
        """Return whether a retained replay run owns the requested session."""
        self.calls.append(f"replay-known:{session_id}")
        return any(operation.session_id == session_id for operation in self.operations.values())

    async def capture_snapshot_basis(self) -> SnapshotBasis | None:
        """Return the atomic run/prepared-state/watermark basis."""
        self.calls.append("basis")
        return self.basis

    async def read_events(
        self,
        run: CurrentRun,
        after_ordinal: int,
        through_ordinal: int | None,
        limit: int,
    ) -> tuple[StoredEvent, ...]:
        """Return the bounded ordered slice requested by the service."""
        self.calls.append(f"events:{run.identity}:{after_ordinal}:{through_ordinal}:{limit}")
        selected = tuple(
            item
            for item in self.events
            if item.audit_ordinal > after_ordinal
            and (through_ordinal is None or item.audit_ordinal <= through_ordinal)
        )
        return selected[:limit]


@dataclass
class FakeScenario:
    """Expose validated catalog/control results without a network."""

    catalog_bytes: bytes
    ready_reasons: tuple[str, ...] = ()
    starts: list[tuple[str, str]] = field(default_factory=list)
    cancels: list[tuple[str, str, float]] = field(default_factory=list)
    recoveries: list[tuple[str, str]] = field(default_factory=list)
    missing_runs: set[str] = field(default_factory=set)
    cancel_state: Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"] = "ABORTED"
    status_mission_id: str = "mission-test-0001"
    status_run_id: str | None = None

    async def readiness(self) -> tuple[str, ...]:
        """Return private scenario dependency reasons."""
        return self.ready_reasons

    async def catalog(self) -> bytes:
        """Return exact private catalog bytes."""
        return self.catalog_bytes

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Accept one stable run identity."""
        self.starts.append((mission_id, run_id))
        return ScenarioRunStatus(
            scenario_id=scenario_id,
            scenario_revision=scenario_revision,
            mission_id=mission_id,
            run_id=run_id,
            state="PLANNED",
        )

    async def status(self, run_id: str) -> ScenarioRunStatus:
        """Reconcile an uncertain start from its stable identity."""
        if run_id in self.missing_runs:
            raise ScenarioRunNotFoundError(run_id)
        return ScenarioRunStatus(
            scenario_id="wilderness-missing-person",
            scenario_revision=1,
            mission_id=self.status_mission_id,
            run_id=self.status_run_id or run_id,
            state="PLANNED",
        )

    async def cancel(self, mission_id: str, run_id: str, timeout: float) -> ScenarioRunStatus:
        """Record the shared cancellation budget and establish cancellation."""
        self.cancels.append((mission_id, run_id, timeout))
        return ScenarioRunStatus(
            scenario_id="wilderness-missing-person",
            scenario_revision=1,
            mission_id=mission_id,
            run_id=run_id,
            state=self.cancel_state,
        )

    async def recover(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Delegate lost-run lifecycle recovery to scenario service."""
        self.recoveries.append((mission_id, run_id))
        return ScenarioRunStatus(
            scenario_id=scenario_id,
            scenario_revision=scenario_revision,
            mission_id=mission_id,
            run_id=run_id,
            state="ABORTED",
        )


@dataclass
class FakeReplay:
    """Return validator-approved exact bundle bytes."""

    bundle_bytes: bytes
    ready_reasons: tuple[str, ...] = ()
    known_sessions: set[str] = field(default_factory=set)
    preparations: list[tuple[str, int]] = field(default_factory=list)

    async def readiness(self) -> tuple[str, ...]:
        """Return replay dependency reasons."""
        return self.ready_reasons

    async def prepare(self, scenario_id: str, scenario_revision: int) -> ReplayPreparation:
        """Record one scenario-bound validation and return exact bytes."""
        self.preparations.append((scenario_id, scenario_revision))
        return ReplayPreparation(bundle_bytes=self.bundle_bytes)

    async def bundle(self, session_id: str) -> bytes | None:
        """Return exact validator bytes only for an established session."""
        return self.bundle_bytes if session_id in self.known_sessions else None
