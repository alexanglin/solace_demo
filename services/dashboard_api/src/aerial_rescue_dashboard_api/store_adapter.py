"""Transaction-owning adapter from the dashboard ports to the R4 store repositories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, cast

from aerial_rescue_store import StoreError
from aerial_rescue_store.dashboard_events import (
    EventSession,
    capture_snapshot_basis,
    read_event_page,
    read_suffix_page,
)
from aerial_rescue_store.dashboard_operations import (
    DashboardOperation,
    DashboardOperationError,
    DashboardOperationRefusal,
    OperationClaim,
    OperationMode,
    OperationResult,
    accepted_start,
    claim,
    complete,
    pending,
)
from aerial_rescue_store.dashboard_operations import (
    OperationKind as StoreOperationKind,
)
from aerial_rescue_store.dashboard_runs import (
    DashboardMission,
    DashboardRun,
    create_mission,
    create_run,
    current_run,
    mission_lifecycle_for_update,
    move_current_run,
    run_by_identity,
    run_by_mission,
)
from aerial_rescue_store.dashboard_runs import (
    RunMode as StoreRunMode,
)
from aerial_rescue_store.session import close, transaction
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import (
    Activation,
    ClaimedOperation,
    CurrentRun,
    MutationKind,
    MutationProposal,
    OperationState,
    RunMode,
    SnapshotBasis,
    StoredEvent,
    StoredResponse,
)

_STORE_FAILURES: Final = (StoreError, SQLAlchemyError)


@dataclass
class SqlStore:
    """Own bounded transactions while leaving SQL representation inside packages/store."""

    session_factory: Callable[[], AsyncSession]
    pool: AsyncEngine
    shutdown_grace_seconds: int

    async def close(self) -> None:
        """Dispose the database pool inside its store-owned shutdown bound."""
        await close(self.pool, self.shutdown_grace_seconds)

    async def readiness(self) -> tuple[str, ...]:
        """Prove the dashboard run read path reaches the migrated schema."""
        try:
            async with transaction(self.session_factory) as session:
                await current_run(session, shared=True)
        except _STORE_FAILURES:
            return ("dashboard-store-unavailable",)
        return ()

    async def claim_operation(self, proposal: MutationProposal) -> ClaimedOperation:
        """Claim one durable operation and preserve the repository's exact stable identity."""
        request = OperationClaim(
            idempotency_key=proposal.idempotency_key,
            operation_kind=StoreOperationKind(proposal.kind.value),
            mode=OperationMode(proposal.mode.value),
            request_digest=proposal.request_digest,
            scenario_id=proposal.scenario_id,
            scenario_revision=proposal.scenario_revision,
            mission_id=proposal.mission_id,
            run_id=proposal.run_id,
            session_id=proposal.session_id,
            predecessor_mission_id=proposal.predecessor_mission_id,
        )
        try:
            async with transaction(self.session_factory) as session:
                stored = await claim(session, request)
        except DashboardOperationError as refusal:
            raise _operation_error(refusal) from refusal
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return _claimed_operation(stored)

    async def prepare_run(self, activation: Activation) -> CurrentRun:
        """Persist stable history and select it before any private control request."""
        try:
            async with transaction(self.session_factory) as session:
                prepared = await self._prepare(session, activation)
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return prepared

    async def _prepare(
        self,
        session: AsyncSession,
        activation: Activation,
    ) -> CurrentRun:
        """Append history once, or verify the pointer already names the same prepared run."""
        selected = activation.current_run
        predecessor = await current_run(session, shared=False)
        if predecessor is not None and predecessor.run_identity == selected.identity:
            if not _matches_activation(predecessor, activation):
                raise ApiError(ErrorCode.RUN_CONFLICT)
            return _current_run(predecessor, started=await _started(session, predecessor))
        if selected.mode is RunMode.DEGRADED_LIVE:
            if selected.mission_id is None:
                raise ApiError(ErrorCode.RUN_CONFLICT)
            if not _expected_predecessor(predecessor, activation.predecessor_mission_id):
                raise ApiError(ErrorCode.RUN_CONFLICT)
            await create_mission(
                session,
                DashboardMission(
                    mission_id=selected.mission_id,
                    scenario_id=selected.scenario_id,
                    scenario_revision=selected.scenario_revision,
                    lifecycle="PLANNED",
                    predecessor_mission_id=activation.predecessor_mission_id,
                ),
            )
        await create_run(session, _dashboard_run(selected, activation))
        await move_current_run(
            session,
            predecessor.run_identity if predecessor is not None else None,
            selected.identity,
        )
        return selected

    async def complete_operation(self, idempotency_key: str, response: StoredResponse) -> None:
        """Complete exact response bytes without creating or selecting another run."""
        try:
            async with transaction(self.session_factory) as session:
                await complete(
                    session,
                    idempotency_key,
                    OperationResult(response.status, response.body),
                )
        except DashboardOperationError as refusal:
            raise _operation_error(refusal) from refusal
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable

    async def pending_operation(self) -> ClaimedOperation | None:
        """Read the single pending operation with all restart-recovery context."""
        try:
            async with transaction(self.session_factory) as session:
                stored = await pending(session)
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return None if stored is None else _claimed_operation(stored)

    async def current_run(self) -> CurrentRun | None:
        """Return the current pointer's immutable run representation."""
        try:
            async with transaction(self.session_factory) as session:
                stored = await current_run(session, shared=True)
                started = False if stored is None else await _started(session, stored)
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return None if stored is None else _current_run(stored, started=started)

    async def run_for_mission(self, mission_id: str) -> CurrentRun | None:
        """Return a retained predecessor run and its derived accepted-start state."""
        try:
            async with transaction(self.session_factory) as session:
                stored = await run_by_mission(session, mission_id)
                started = False if stored is None else await _started(session, stored)
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return None if stored is None else _current_run(stored, started=started)

    async def mission_lifecycle(self, mission_id: str) -> str:
        """Read recorder-owned lifecycle and release its row lock before private control."""
        try:
            async with transaction(self.session_factory) as session:
                lifecycle = await mission_lifecycle_for_update(session, mission_id)
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return lifecycle

    async def replay_session_known(self, session_id: str) -> bool:
        """Require a retained replay run before serving the shared immutable bundle."""
        try:
            async with transaction(self.session_factory) as session:
                stored = await run_by_identity(session, session_id)
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return (
            stored is not None
            and stored.mode is StoreRunMode.REPLAY
            and stored.session_id == session_id
        )

    async def capture_snapshot_basis(self) -> SnapshotBasis | None:
        """Capture pointer, prepared bytes, and audit watermark in one committed transaction."""
        try:
            async with transaction(self.session_factory) as session:
                basis = await capture_snapshot_basis(cast("EventSession", session))
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        if basis is None:
            return None
        return SnapshotBasis(
            current_run=_current_run(basis.run),
            prepared_initial_state=basis.run.prepared_initial_state,
            audit_watermark=basis.audit_watermark,
        )

    async def read_events(
        self,
        run: CurrentRun,
        after_ordinal: int,
        through_ordinal: int | None,
        limit: int,
    ) -> tuple[StoredEvent, ...]:
        """Read one bounded ordered live page, while replay remains bundle-backed."""
        if run.mode is RunMode.REPLAY:
            return ()
        if run.mission_id is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        try:
            async with transaction(self.session_factory) as session:
                if through_ordinal is None:
                    page = await read_suffix_page(
                        cast("EventSession", session), run.mission_id, after_ordinal, limit
                    )
                else:
                    page = await read_event_page(
                        cast("EventSession", session),
                        run.mission_id,
                        after_ordinal,
                        through_ordinal,
                        limit,
                    )
        except _STORE_FAILURES as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable
        return tuple(StoredEvent(item.audit_ordinal, item.kind, item.payload) for item in page)


def _claimed_operation(stored: DashboardOperation) -> ClaimedOperation:
    """Adapt the strict repository representation without changing exact result bytes."""
    response = (
        None if stored.result is None else StoredResponse(stored.result.status, stored.result.body)
    )
    return ClaimedOperation(
        idempotency_key=stored.idempotency_key,
        kind=MutationKind(stored.operation_kind.value),
        mode=RunMode(stored.mode.value),
        request_digest=stored.request_digest,
        scenario_id=stored.scenario_id,
        scenario_revision=stored.scenario_revision,
        mission_id=stored.mission_id,
        run_id=stored.run_id,
        session_id=stored.session_id,
        predecessor_mission_id=stored.predecessor_mission_id,
        state=OperationState(stored.state.value),
        response=response,
        newly_claimed=stored.newly_claimed,
    )


def _current_run(stored: DashboardRun, *, started: bool = False) -> CurrentRun:
    """Adapt one immutable persisted run into the dashboard port representation."""
    return CurrentRun(
        mode=RunMode(stored.mode.value),
        scenario_id=stored.scenario_id,
        scenario_revision=stored.scenario_revision,
        mission_id=stored.mission_id,
        run_id=stored.run_id,
        session_id=stored.session_id,
        started=started,
    )


def _dashboard_run(selected: CurrentRun, activation: Activation) -> DashboardRun:
    """Build the mutually exclusive persisted run selected by activation mode."""
    return DashboardRun(
        run_identity=selected.identity,
        mode=StoreRunMode(selected.mode.value),
        scenario_id=selected.scenario_id,
        scenario_revision=selected.scenario_revision,
        mission_id=selected.mission_id,
        run_id=selected.run_id,
        session_id=selected.session_id,
        prepared_initial_state=activation.prepared_initial_state,
    )


async def _started(session: AsyncSession, stored: DashboardRun) -> bool:
    """Derive live start state from its completed 202 start operation."""
    if stored.mode is not StoreRunMode.DEGRADED_LIVE or stored.run_id is None:
        return False
    return await accepted_start(session, stored.run_id)


def _matches_activation(stored: DashboardRun, activation: Activation) -> bool:
    """Require an idempotent preparation to name the exact immutable run representation."""
    proposed = _dashboard_run(activation.current_run, activation)
    return (
        stored.mode is proposed.mode
        and stored.scenario_id == proposed.scenario_id
        and stored.scenario_revision == proposed.scenario_revision
        and stored.mission_id == proposed.mission_id
        and stored.run_id == proposed.run_id
        and stored.session_id == proposed.session_id
        and stored.prepared_initial_state == proposed.prepared_initial_state
    )


def _expected_predecessor(
    predecessor: DashboardRun | None, predecessor_mission_id: str | None
) -> bool:
    """Bind live starts to an empty/replay pointer and resets to their exact mission."""
    if predecessor_mission_id is None:
        return predecessor is None or predecessor.mode is StoreRunMode.REPLAY
    return predecessor is not None and predecessor.mission_id == predecessor_mission_id


def _operation_error(refusal: DashboardOperationError) -> ApiError:
    """Map redacted repository conflict categories into the closed public vocabulary."""
    if refusal.refusal in {
        DashboardOperationRefusal.OPERATION_MISMATCH,
        DashboardOperationRefusal.MODE_MISMATCH,
        DashboardOperationRefusal.REQUEST_MISMATCH,
        DashboardOperationRefusal.SCENARIO_MISMATCH,
    }:
        return ApiError(ErrorCode.IDEMPOTENCY_CONFLICT)
    if refusal.refusal is DashboardOperationRefusal.ANOTHER_OPERATION_PENDING:
        return ApiError(ErrorCode.OPERATION_CONFLICT)
    return ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
