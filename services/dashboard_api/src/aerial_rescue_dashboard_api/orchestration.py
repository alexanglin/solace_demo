"""Durable start/reset orchestration over injected store, scenario, and replay ports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, TypeGuard

from aerial_rescue_contracts import canonical
from aerial_rescue_domain.mission import MissionState, is_terminal

from aerial_rescue_dashboard_api.boundary.documents import (
    CATALOG_SCHEMA,
    REPLAY_SCHEMA,
    find_scenario,
    prepare_live_state,
    replay_initial_state,
    validated_document,
)
from aerial_rescue_dashboard_api.boundary.errors import (
    MESSAGE_BY_CODE,
    STATUS_BY_CODE,
    ApiError,
    ErrorCode,
)
from aerial_rescue_dashboard_api.ports import (
    Activation,
    ClaimedOperation,
    CurrentRun,
    IdentifierSource,
    MutationKind,
    MutationProposal,
    OperationState,
    ReplayPort,
    RunMode,
    ScenarioCancellationNotEstablishedError,
    ScenarioPort,
    ScenarioRunNotFoundError,
    ScenarioRunStatus,
    StoredResponse,
    StorePort,
)

CATALOG_MAX_BYTES: Final = 512 * 1024
REPLAY_MAX_BYTES: Final = 1024 * 1024
CANCELLATION_BUDGET_SECONDS: Final = 15.0


@dataclass(frozen=True)
class MutationAnswer:
    """One exact HTTP answer returned from a new operation or durable replay."""

    status: int
    body: bytes


class OperationCoordinator:
    """Keep accepted mutation state separate from recorder-owned mission lifecycle."""

    def __init__(
        self,
        store: StorePort,
        scenario: ScenarioPort,
        replay: ReplayPort,
        identifiers: IdentifierSource,
    ) -> None:
        """Retain only injected typed ports and a stable-identity source."""
        self._store = store
        self._scenario = scenario
        self._replay = replay
        self._identifiers = identifiers

    async def start(
        self,
        scenario_id: str,
        mode: RunMode,
        scenario_revision: int,
        idempotency_key: str,
        request_digest: str,
    ) -> MutationAnswer:
        """Claim, invoke once, and complete one scenario start."""
        selected = await self._store.current_run() if mode is RunMode.DEGRADED_LIVE else None
        proposal = self._start_proposal(
            scenario_id,
            mode,
            scenario_revision,
            idempotency_key,
            request_digest,
        )
        if _reusable_selection(selected, scenario_id, mode, scenario_revision):
            proposal = replace(
                proposal,
                mission_id=selected.mission_id,
                run_id=selected.run_id,
            )
        operation = await self._claim(proposal)
        if operation.state is OperationState.COMPLETED:
            return _stored_answer(operation)
        if mode is RunMode.REPLAY:
            return await self._complete_replay(operation)
        current = await self._store.current_run()
        if (
            operation.newly_claimed
            and current is not None
            and current.mode is not RunMode.REPLAY
            and not _reusable_live_run(current, operation)
        ):
            return await self._complete_refusal(operation, ErrorCode.OPERATION_CONFLICT)
        scenario = await self._scenario_or_refusal(operation)
        if isinstance(scenario, MutationAnswer):
            return scenario
        return await self._complete_live_start(
            operation, scenario, invoke_start=operation.newly_claimed
        )

    async def reset(self, idempotency_key: str, request_digest: str) -> MutationAnswer:
        """Cancel a live predecessor or create a fresh replay session without deleting history."""
        predecessor = await self._store.current_run()
        if predecessor is None:
            raise ApiError(ErrorCode.OPERATION_CONFLICT)
        proposal = self._reset_proposal(predecessor, idempotency_key, request_digest)
        operation = await self._claim(proposal)
        if operation.state is OperationState.COMPLETED:
            return _stored_answer(operation)
        if operation.mode is RunMode.REPLAY:
            return await self._complete_replay(operation)
        return await self._complete_live_reset(operation)

    async def reconcile_pending(self) -> None:
        """Reconcile the sole pending operation without repeating an uncertain private start."""
        operation = await self._store.pending_operation()
        if operation is None:
            return
        if operation.mode is RunMode.REPLAY:
            await self._complete_replay(operation)
            return
        if operation.kind is MutationKind.RESET:
            await self._complete_live_reset(operation)
            return
        scenario = await self._scenario_or_refusal(operation)
        if isinstance(scenario, MutationAnswer):
            return
        await self._complete_live_start(operation, scenario, invoke_start=False)

    async def _catalog(self) -> dict[str, object]:
        """Fetch and validate the private catalog before projecting it publicly."""
        raw = await self._scenario.catalog()
        document = validated_document(CATALOG_SCHEMA, raw, maximum_bytes=CATALOG_MAX_BYTES)
        return dict(document)

    async def _scenario_definition(self, operation: ClaimedOperation) -> Mapping[str, object]:
        """Resolve the live-only prepared-state source after durable replay checks."""
        catalog = await self._catalog()
        return find_scenario(catalog, operation.scenario_id, operation.scenario_revision)

    async def _scenario_or_refusal(
        self, operation: ClaimedOperation
    ) -> Mapping[str, object] | MutationAnswer:
        """Complete stable catalog-identity refusals so they cannot strand a pending claim."""
        try:
            return await self._scenario_definition(operation)
        except ApiError as refusal:
            if refusal.code not in {
                ErrorCode.SCENARIO_NOT_FOUND,
                ErrorCode.SCENARIO_REVISION_MISMATCH,
            }:
                raise
            return await self._complete_refusal(operation, refusal.code)

    def _start_proposal(
        self,
        scenario_id: str,
        mode: RunMode,
        revision: int,
        key: str,
        request_digest: str,
    ) -> MutationProposal:
        """Allocate mutually exclusive stable identities for one first claim."""
        live = mode is RunMode.DEGRADED_LIVE
        return MutationProposal(
            idempotency_key=key,
            kind=MutationKind.START,
            mode=mode,
            request_digest=request_digest,
            scenario_id=scenario_id,
            scenario_revision=revision,
            mission_id=self._identifiers.new("mission") if live else None,
            run_id=self._identifiers.new("run") if live else None,
            session_id=self._identifiers.new("session") if not live else None,
            predecessor_mission_id=None,
        )

    def _reset_proposal(
        self, predecessor: CurrentRun, key: str, request_digest: str
    ) -> MutationProposal:
        """Allocate a successor identity while retaining the predecessor link."""
        live = predecessor.mode is RunMode.DEGRADED_LIVE
        return MutationProposal(
            idempotency_key=key,
            kind=MutationKind.RESET,
            mode=predecessor.mode,
            request_digest=request_digest,
            scenario_id=predecessor.scenario_id,
            scenario_revision=predecessor.scenario_revision,
            mission_id=self._identifiers.new("mission") if live else None,
            run_id=self._identifiers.new("run") if live else None,
            session_id=self._identifiers.new("session") if not live else None,
            predecessor_mission_id=predecessor.mission_id,
        )

    async def _claim(self, proposal: MutationProposal) -> ClaimedOperation:
        """Compare a durable retry with the complete canonical operation identity."""
        operation = await self._store.claim_operation(proposal)
        if (
            operation.kind is not proposal.kind
            or operation.mode is not proposal.mode
            or operation.request_digest != proposal.request_digest
            or operation.scenario_id != proposal.scenario_id
            or operation.scenario_revision != proposal.scenario_revision
        ):
            raise ApiError(ErrorCode.IDEMPOTENCY_CONFLICT)
        return operation

    async def _cancel_predecessor(self, predecessor: CurrentRun) -> bool:
        """Require authoritative cancellation within the one shared fifteen-second budget."""
        if predecessor.mission_id is None or predecessor.run_id is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        lifecycle = await self._store.mission_lifecycle(predecessor.mission_id)
        try:
            durable_state = MissionState(lifecycle.lower())
        except ValueError as unreadable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unreadable
        if is_terminal(durable_state):
            return True
        status = await self._scenario.cancel(
            predecessor.mission_id,
            predecessor.run_id,
            CANCELLATION_BUDGET_SECONDS,
        )
        _require_private_status(
            status,
            scenario_id=predecessor.scenario_id,
            scenario_revision=predecessor.scenario_revision,
            mission_id=predecessor.mission_id,
            run_id=predecessor.run_id,
        )
        return status.state in {"ABORTED", "EXHAUSTED"}

    async def _complete_refusal(
        self, operation: ClaimedOperation, code: ErrorCode
    ) -> MutationAnswer:
        """Persist one operation-level refusal for byte-identical safe retries."""
        response = _refusal_response(code)
        await self._store.complete_operation(operation.idempotency_key, response)
        return MutationAnswer(response.status, response.body)

    async def _complete_live_start(
        self,
        operation: ClaimedOperation,
        scenario: Mapping[str, object],
        *,
        invoke_start: bool,
    ) -> MutationAnswer:
        """Prepare before one start, or reconcile the same already-prepared run."""
        if operation.mission_id is None or operation.run_id is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        run = CurrentRun(
            mode=RunMode.DEGRADED_LIVE,
            scenario_id=operation.scenario_id,
            scenario_revision=operation.scenario_revision,
            mission_id=operation.mission_id,
            run_id=operation.run_id,
            session_id=None,
        )
        current = await self._store.current_run()
        if current is None or current.mode is RunMode.REPLAY:
            prepared = prepare_live_state(scenario, operation.mission_id, None)
            await self._store.prepare_run(
                Activation(current_run=run, prepared_initial_state=prepared)
            )
        elif not _reusable_live_run(current, operation):
            raise ApiError(ErrorCode.RUN_CONFLICT)
        if invoke_start:
            status = await self._scenario.start(
                operation.scenario_id,
                operation.scenario_revision,
                operation.mission_id,
                operation.run_id,
            )
        else:
            try:
                status = await self._scenario.status(operation.run_id)
            except ScenarioRunNotFoundError:
                status = await self._scenario.recover(
                    operation.scenario_id,
                    operation.scenario_revision,
                    operation.mission_id,
                    operation.run_id,
                )
        _require_private_status(
            status,
            scenario_id=operation.scenario_id,
            scenario_revision=operation.scenario_revision,
            mission_id=operation.mission_id,
            run_id=operation.run_id,
        )
        response = _operation_response(operation)
        await self._store.complete_operation(operation.idempotency_key, response)
        return MutationAnswer(response.status, response.body)

    async def _complete_live_reset(self, operation: ClaimedOperation) -> MutationAnswer:
        """Re-establish predecessor cancellation, then select an unstarted successor."""
        if operation.mission_id is None or operation.run_id is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        predecessor = await self._reset_predecessor(operation)
        scenario = await self._scenario_or_refusal(operation)
        if isinstance(scenario, MutationAnswer):
            return scenario
        try:
            cancelled = await self._cancel_predecessor(predecessor)
        except ScenarioCancellationNotEstablishedError, ScenarioRunNotFoundError:
            cancelled = False
        if not cancelled:
            return await self._complete_refusal(operation, ErrorCode.CANCELLATION_NOT_ESTABLISHED)
        prepared = prepare_live_state(
            scenario,
            operation.mission_id,
            operation.predecessor_mission_id,
        )
        await self._store.prepare_run(
            Activation(
                current_run=CurrentRun(
                    mode=RunMode.DEGRADED_LIVE,
                    scenario_id=operation.scenario_id,
                    scenario_revision=operation.scenario_revision,
                    mission_id=operation.mission_id,
                    run_id=operation.run_id,
                    session_id=None,
                ),
                prepared_initial_state=prepared,
                predecessor_mission_id=operation.predecessor_mission_id,
            )
        )
        response = _operation_response(operation)
        await self._store.complete_operation(operation.idempotency_key, response)
        return MutationAnswer(response.status, response.body)

    async def _reset_predecessor(self, operation: ClaimedOperation) -> CurrentRun:
        """Resolve a predecessor even after a crash moved the pointer to its successor."""
        predecessor_id = operation.predecessor_mission_id
        if predecessor_id is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        current = await self._store.current_run()
        if current is not None and current.mission_id == predecessor_id:
            return current
        retained = await self._store.run_for_mission(predecessor_id)
        if retained is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        return retained

    async def _complete_replay(self, operation: ClaimedOperation) -> MutationAnswer:
        """Validate exact bundle bytes and persist session identity outside their content."""
        if operation.session_id is None:
            raise ApiError(ErrorCode.RUN_CONFLICT)
        prepared = await self._replay.prepare(
            operation.scenario_id,
            operation.scenario_revision,
        )
        bundle = validated_document(
            REPLAY_SCHEMA,
            prepared.bundle_bytes,
            maximum_bytes=REPLAY_MAX_BYTES,
        )
        if bundle.get("scenarioId") != operation.scenario_id:
            raise ApiError(ErrorCode.SCENARIO_NOT_FOUND)
        if bundle.get("scenarioRevision") != operation.scenario_revision:
            raise ApiError(ErrorCode.SCENARIO_REVISION_MISMATCH)
        response = _operation_response(operation)
        activation = Activation(
            current_run=CurrentRun(
                mode=RunMode.REPLAY,
                scenario_id=operation.scenario_id,
                scenario_revision=operation.scenario_revision,
                mission_id=None,
                run_id=None,
                session_id=operation.session_id,
            ),
            prepared_initial_state=replay_initial_state(bundle),
        )
        await self._store.prepare_run(activation)
        await self._store.complete_operation(operation.idempotency_key, response)
        return MutationAnswer(response.status, response.body)


def _require_private_status(
    status: ScenarioRunStatus,
    *,
    scenario_id: str,
    scenario_revision: int,
    mission_id: str,
    run_id: str,
) -> None:
    """Reject any private response that does not name the complete requested run identity."""
    if (
        status.scenario_id != scenario_id
        or status.scenario_revision != scenario_revision
        or status.mission_id != mission_id
        or status.run_id != run_id
    ):
        raise ApiError(ErrorCode.RUN_CONFLICT)


def _reusable_live_run(current: CurrentRun, operation: ClaimedOperation) -> bool:
    """Identify the exact selected PLANNED run that a start may activate."""
    return (
        current.mode is RunMode.DEGRADED_LIVE
        and not current.started
        and current.scenario_id == operation.scenario_id
        and current.scenario_revision == operation.scenario_revision
        and current.mission_id == operation.mission_id
        and current.run_id == operation.run_id
    )


def _reusable_selection(
    selected: CurrentRun | None,
    scenario_id: str,
    mode: RunMode,
    scenario_revision: int,
) -> TypeGuard[CurrentRun]:
    """Return whether a request may reuse the selected unstarted live identity."""
    return (
        selected is not None
        and selected.mode is mode
        and not selected.started
        and selected.scenario_id == scenario_id
        and selected.scenario_revision == scenario_revision
    )


def _operation_response(operation: ClaimedOperation) -> StoredResponse:
    """Build the one canonical accepted response shape from persisted stable identities."""
    body: dict[str, object] = {
        "declaredCount": 23,
        "declaredOnlyCount": 3,
        "mode": operation.mode.value,
        "operationVersion": (
            "dashboard-start-response/v1"
            if operation.kind is MutationKind.START
            else "dashboard-reset-response/v1"
        ),
        "simulatedCount": 20,
    }
    if operation.mode is RunMode.REPLAY:
        body["sessionId"] = operation.session_id
    else:
        body["missionId"] = operation.mission_id
        body["runId"] = operation.run_id
        if operation.kind is MutationKind.RESET:
            body["predecessorMissionId"] = operation.predecessor_mission_id
    return StoredResponse(202, canonical.canonical_bytes(body))


def _stored_answer(operation: ClaimedOperation) -> MutationAnswer:
    """Return the exact completed result or fail closed on malformed persistence."""
    if operation.response is None:
        raise ApiError(ErrorCode.INTERNAL_FAILURE)
    return MutationAnswer(operation.response.status, operation.response.body)


def _refusal_response(code: ErrorCode) -> StoredResponse:
    """Build exact durable bytes for an operation-level typed refusal."""
    return StoredResponse(
        STATUS_BY_CODE[code],
        canonical.canonical_bytes(
            {
                "errorCode": code.value,
                "errorVersion": "dashboard-error/v1",
                "message": MESSAGE_BY_CODE[code],
            }
        ),
    )
