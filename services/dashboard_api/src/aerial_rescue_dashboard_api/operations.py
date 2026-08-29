"""Concrete live and structurally isolated replay dashboard route operations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus
from typing import Protocol, cast, override

from aerial_rescue_contracts import canonical, digest
from aerial_rescue_contracts.view import (
    CheckpointRefused,
    Connectivity,
    DashboardReducedState,
    DeclaredOnlyFleetMember,
    Mission,
    MissionLifecycle,
    Participation,
    PreparedMission,
    ReducerCheckpoint,
    Sector,
    SectorState,
    SimulatedFleetMember,
    Telemetry,
    checkpoint_from_replay,
    prepare_checkpoint,
)

from aerial_rescue_dashboard_api.boundary.application import (
    AssetOutcome,
    EventStream,
    JsonOutcome,
)
from aerial_rescue_dashboard_api.boundary.errors import ApiError
from aerial_rescue_dashboard_api.boundary.mutation_boundary import AuthorizedMutation
from aerial_rescue_dashboard_api.boundary.wire import parse_wire_document
from aerial_rescue_dashboard_api.lifecycle import Dependency, RuntimeReadiness
from aerial_rescue_dashboard_api.messaging.mutations import DashboardMutationError
from aerial_rescue_dashboard_api.orchestration import MutationAnswer
from aerial_rescue_dashboard_api.ports import RunMode as DurableRunMode

_SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/dashboard/"
_CANONICALIZATION_VERSION = 1


class OperationsRefusal(Enum):
    """Why an admitted route operation cannot produce its selected outcome."""

    NO_CURRENT_RUN = "there is no current run to reset"
    IDEMPOTENCY = "the idempotency identity does not bind to this operation"
    SCENARIO = "the selected scenario cannot initialize a closed mission state"
    CONTROL = "scenario control did not confirm the required lifecycle state"
    REPLAY = "the selected replay cannot initialize an isolated session"


class DashboardOperationsError(ValueError):
    """A redacted concrete-operation refusal."""

    def __init__(self, refusal: OperationsRefusal) -> None:
        """Retain only one closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


class DashboardFiles(Protocol):
    """Validated local data available to either composition graph."""

    @property
    def catalog_bytes(self) -> bytes:
        """Return the canonical expanded scenario catalog."""
        ...

    @property
    def ready(self) -> bool:
        """Return whether the local file epoch is validated and open."""
        ...

    async def startup(self) -> None:
        """Validate local roots and committed material."""
        ...

    async def shutdown(self) -> None:
        """Close the local read epoch."""
        ...

    def scenario(self, scenario_id: str, revision: int) -> Mapping[str, object]:
        """Return one validated public scenario descriptor."""
        ...

    async def asset(self, name: str) -> AssetOutcome | None:
        """Return one bounded immutable asset when present."""
        ...

    async def replay(self, session_id: str) -> bytes:
        """Return one validated static replay by session identity."""
        ...

    def replay_for_scenario(self, scenario_id: str, revision: int) -> bytes:
        """Return the validated recording selected for a scenario revision."""
        ...


class ScenarioControlPort(Protocol):
    """The authenticated private run-control capability of the live graph."""

    @property
    def ready(self) -> bool:
        """Return whether the authenticated private client is open."""
        ...

    async def startup(self) -> None:
        """Open the bounded private caller."""
        ...

    async def shutdown(self) -> None:
        """Close the bounded private caller."""
        ...

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> Mapping[str, object]:
        """Start or reconcile one stable private run."""
        ...

    async def status(self, run_id: str) -> Mapping[str, object]:
        """Return one exact stable run status."""
        ...

    async def cancel(
        self,
        mission_id: str,
        run_id: str,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        """Confirm cancellation inside the supplied positive bound."""
        ...


class ProjectionHubPort(Protocol):
    """The snapshot, reducer checkpoint, and finite SSE client owner."""

    async def replace_run(
        self,
        checkpoint: ReducerCheckpoint,
        current_run: Mapping[str, object] | None,
    ) -> None:
        """Replace one checkpoint and close streams of its predecessor."""
        ...

    async def open_events(self) -> EventStream:
        """Allocate one bounded snapshot-first event stream."""
        ...

    async def close(self) -> None:
        """Close all finite client streams."""
        ...


class LiveBrokerPort(Protocol):
    """One owned mixed Solace session plus its store-backed processing loop."""

    @property
    def ready(self) -> bool:
        """Return whether store, session, bindings, recovery, and outbox are ready."""
        ...

    async def startup(self) -> None:
        """Open one owned mixed session and finish initial recovery."""
        ...

    async def shutdown(self) -> None:
        """Cancel processing and close the session in reverse order."""
        ...

    async def activate_mission(self, mission_id: str) -> None:
        """Recover and select one recorder-authoritative mission."""
        ...


class MutationPort(Protocol):
    """Durable command and exact proposal-decision operations."""

    async def command(self, mutation: AuthorizedMutation) -> bytes:
        """Commit and return one command submission response."""
        ...

    async def decide(self, mutation: AuthorizedMutation) -> bytes:
        """Commit and return one exact proposal-decision response."""
        ...


class ScenarioOperationPort(Protocol):
    """Durable dashboard-operation authority for scenario start and reset."""

    async def reconcile_pending(self) -> None:
        """Recover the one pending operation before broker readiness."""
        ...

    async def start(
        self,
        scenario_id: str,
        mode: DurableRunMode,
        scenario_revision: int,
        idempotency_key: str,
        request_digest: str,
    ) -> MutationAnswer:
        """Claim and complete one durable start operation."""
        ...

    async def reset(self, idempotency_key: str, request_digest: str) -> MutationAnswer:
        """Claim and complete one durable reset operation."""
        ...


@dataclass(frozen=True, slots=True)
class _LiveRun:
    scenario_id: str
    scenario_revision: int
    mission_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class _ReplayRun:
    scenario_id: str
    scenario_revision: int
    session_id: str
    bundle: bytes


@dataclass(frozen=True, slots=True)
class LiveOperationPorts:
    """Concrete capabilities owned by one writable application graph."""

    files: DashboardFiles
    scenario: ScenarioControlPort
    broker: LiveBrokerPort
    hub: ProjectionHubPort
    mutations: MutationPort
    scenario_operations: ScenarioOperationPort


class _CommonOperations:
    """Read-only route behavior shared by capabilities, not by graph construction."""

    def __init__(self, files: DashboardFiles, hub: ProjectionHubPort) -> None:
        self._files = files
        self._hub = hub

    async def scenarios(self) -> JsonOutcome:
        """Return the startup-validated canonical public catalog."""
        return JsonOutcome(200, _schema("scenario-catalog"), self._files.catalog_bytes)

    async def open_events(self) -> EventStream:
        """Allocate one finite snapshot-first projection stream."""
        return await self._hub.open_events()

    async def replay_bundle(self, session_id: str) -> JsonOutcome:
        """Return one validated immutable local replay document."""
        return JsonOutcome(200, _schema("replay-bundle"), await self._files.replay(session_id))

    async def asset(self, asset: str) -> AssetOutcome | None:
        """Return one exact content-hashed local asset."""
        return await self._files.asset(asset)


class LiveDashboardOperations(_CommonOperations):
    """Compose writable routes over store, private control, and one mixed Solace session."""

    def __init__(
        self,
        *,
        ports: LiveOperationPorts,
        readiness: RuntimeReadiness,
    ) -> None:
        """Bind the exact live capabilities without acquiring them at construction."""
        super().__init__(ports.files, ports.hub)
        self._scenario = ports.scenario
        self._broker = ports.broker
        self._mutations = ports.mutations
        self._scenario_operations = ports.scenario_operations
        self._readiness = readiness
        self._active: _LiveRun | None = None
        self._lock = asyncio.Lock()
        self._opened = False

    async def open(self) -> None:
        """Acquire local data, private control, store, and broker in fail-closed order."""
        await self._files.startup()
        try:
            await self._scenario.startup()
            self._readiness.set_dependency(Dependency.SCENARIO_CONTROL, ready=self._scenario.ready)
            await self._scenario_operations.reconcile_pending()
            await self._broker.startup()
        except BaseException:
            await self._close_started_resources()
            raise
        self._readiness.set_dependency(Dependency.STORE, ready=self._broker.ready)
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=self._broker.ready)
        self._opened = True

    async def close(self) -> None:
        """Stop admissions externally, then close broker, streams, control, and files."""
        self._opened = False
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=False)
        self._readiness.set_dependency(Dependency.STORE, ready=False)
        self._readiness.set_dependency(Dependency.SCENARIO_CONTROL, ready=False)
        first: BaseException | None = None
        for operation in (
            self._broker.shutdown,
            self._hub.close,
            self._scenario.shutdown,
            self._files.shutdown,
        ):
            try:
                await operation()
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first

    async def start_scenario(
        self,
        scenario_id: str,
        mutation: AuthorizedMutation,
    ) -> JsonOutcome:
        """Delegate the durable start, then activate its selected broker mission."""
        async with self._lock:
            revision = _scenario_revision(mutation)
            try:
                answer = await self._scenario_operations.start(
                    scenario_id,
                    DurableRunMode.DEGRADED_LIVE,
                    revision,
                    mutation.ingress.idempotency_key,
                    _scenario_operation_digest(mutation, "start", scenario_id),
                )
            except ApiError as refusal:
                return _error(refusal.code.value, refusal.public_message, refusal.status)
            outcome = _operation_answer(answer, "start-response")
            if answer.status != HTTPStatus.ACCEPTED:
                return outcome
            document = _mapping(canonical.decode(answer.body))
            selected = _LiveRun(
                scenario_id,
                revision,
                _text(document, "missionId"),
                _text(document, "runId"),
            )
            if self._active != selected:
                scenario = self._files.scenario(scenario_id, revision)
                await self._activate(selected, scenario, None)
            return outcome

    async def reset(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Delegate durable cancellation/reset, then activate its fresh successor."""
        async with self._lock:
            if self._active is None:
                return _error("NO_CURRENT_RUN", OperationsRefusal.NO_CURRENT_RUN.value, 409)
            try:
                answer = await self._scenario_operations.reset(
                    mutation.ingress.idempotency_key,
                    _scenario_operation_digest(mutation, "reset", None),
                )
            except ApiError as refusal:
                return _error(refusal.code.value, refusal.public_message, refusal.status)
            outcome = _operation_answer(answer, "reset-response")
            if answer.status != HTTPStatus.ACCEPTED:
                return outcome
            document = _mapping(canonical.decode(answer.body))
            successor = _LiveRun(
                self._active.scenario_id,
                self._active.scenario_revision,
                _text(document, "missionId"),
                _text(document, "runId"),
            )
            scenario = self._files.scenario(successor.scenario_id, successor.scenario_revision)
            predecessor = _nullable_text(document.get("predecessorMissionId"))
            if self._active != successor:
                await self._activate(successor, scenario, predecessor)
            return outcome

    async def command(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Return durable staging, never publication or command completion."""
        try:
            body = await self._mutations.command(mutation)
        except DashboardMutationError:
            return _error("MUTATION_REFUSED", "operator command was refused", 409)
        return JsonOutcome(202, _schema("command-response"), body)

    async def decide_proposal(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Return durable decision staging after independent store re-binding."""
        try:
            body = await self._mutations.decide(mutation)
        except DashboardMutationError:
            return _error("MUTATION_REFUSED", "proposal decision was refused", 409)
        return JsonOutcome(202, _schema("proposal-decision-response"), body)

    async def _activate(
        self,
        selected: _LiveRun,
        scenario: Mapping[str, object],
        predecessor: str | None,
    ) -> None:
        checkpoint = _initial_checkpoint(scenario, selected.mission_id, predecessor)
        current_run = {
            "mode": "degradedLive",
            "missionId": selected.mission_id,
            "runId": selected.run_id,
        }
        await self._hub.replace_run(checkpoint, current_run)
        await self._broker.activate_mission(selected.mission_id)
        self._active = selected

    async def _close_started_resources(self) -> None:
        """Best-effort unwind for a startup that never became an application epoch."""
        if self._broker.ready:
            await self._broker.shutdown()
        if self._scenario.ready:
            await self._scenario.shutdown()
        if self._files.ready:
            await self._files.shutdown()


class ReplayDashboardOperations(_CommonOperations):
    """Construct replay only from local validated bytes, a reducer, and finite streams."""

    def __init__(
        self,
        *,
        files: DashboardFiles,
        hub: ProjectionHubPort,
        readiness: RuntimeReadiness,
    ) -> None:
        """Bind only immutable files, projection memory, and mode readiness."""
        super().__init__(files, hub)
        self._readiness = readiness
        self._active: _ReplayRun | None = None
        self._aliases: dict[str, bytes] = {}
        self._claims: dict[str, tuple[str, str, bytes]] = {}
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Validate local replay inputs without constructing a store or broker capability."""
        await self._files.startup()
        self._readiness.set_dependency(Dependency.REPLAY_INPUT, ready=self._files.ready)

    async def close(self) -> None:
        """Close streams before the immutable local file epoch."""
        self._readiness.set_dependency(Dependency.REPLAY_INPUT, ready=False)
        try:
            await self._hub.close()
        finally:
            await self._files.shutdown()

    async def start_replay(
        self,
        scenario_id: str,
        mutation: AuthorizedMutation,
    ) -> JsonOutcome:
        """Create one process-local session over a validated immutable recording."""
        async with self._lock:
            scenario_revision = _scenario_revision(mutation)
            scenario = self._files.scenario(scenario_id, scenario_revision)
            session_id = _stable_identifier("replay-session", "start", mutation, scenario_id)
            response = _replay_response("start", session_id, scenario)
            prior = self._replay_claim(mutation, "start", scenario_id, response)
            if prior is not None:
                return JsonOutcome(202, _schema("start-response"), prior)
            source = self._files.replay_for_scenario(scenario_id, scenario_revision)
            run = _replay_run(source, scenario_id, scenario_revision, session_id)
            await self._activate(run)
            self._claims[mutation.ingress.idempotency_key] = (
                "start",
                _replay_request_digest(mutation, "start", scenario_id),
                response,
            )
            return JsonOutcome(202, _schema("start-response"), response)

    async def reset_replay(self, mutation: AuthorizedMutation) -> JsonOutcome:
        """Create a fresh cursor-zero replay identity over the same immutable recording."""
        async with self._lock:
            predecessor = self._active
            if predecessor is None:
                return _error("NO_CURRENT_RUN", OperationsRefusal.NO_CURRENT_RUN.value, 409)
            scope = f"{predecessor.scenario_id}:{predecessor.session_id}"
            session_id = _stable_identifier("replay-session", "reset", mutation, scope)
            scenario = self._files.scenario(
                predecessor.scenario_id,
                predecessor.scenario_revision,
            )
            response = _replay_response("reset", session_id, scenario)
            prior = self._replay_claim(mutation, "reset", scope, response)
            if prior is not None:
                return JsonOutcome(202, _schema("reset-response"), prior)
            source = self._files.replay_for_scenario(
                predecessor.scenario_id,
                predecessor.scenario_revision,
            )
            run = _replay_run(
                source,
                predecessor.scenario_id,
                predecessor.scenario_revision,
                session_id,
            )
            await self._activate(run)
            self._claims[mutation.ingress.idempotency_key] = (
                "reset",
                _replay_request_digest(mutation, "reset", scope),
                response,
            )
            return JsonOutcome(202, _schema("reset-response"), response)

    @override
    async def replay_bundle(self, session_id: str) -> JsonOutcome:
        """Serve an activated rebound bundle, or a validated immutable static bundle."""
        body = self._aliases.get(session_id)
        if body is None:
            body = await self._files.replay(session_id)
        return JsonOutcome(200, _schema("replay-bundle"), body)

    def _replay_claim(
        self,
        mutation: AuthorizedMutation,
        kind: str,
        scope: str,
        response: bytes,
    ) -> bytes | None:
        key = mutation.ingress.idempotency_key
        candidate = _replay_request_digest(mutation, kind, scope)
        prior = self._claims.get(key)
        if prior is None:
            return None
        stored_kind, stored_digest, stored_response = prior
        if not hmac.compare_digest(stored_kind, kind) or not hmac.compare_digest(
            stored_digest, candidate
        ):
            raise DashboardOperationsError(OperationsRefusal.IDEMPOTENCY)
        if not hmac.compare_digest(stored_response, response):
            raise DashboardOperationsError(OperationsRefusal.IDEMPOTENCY)
        return stored_response

    async def _activate(self, run: _ReplayRun) -> None:
        try:
            checkpoint = _replay_checkpoint(run.bundle)
        except (DashboardOperationsError, KeyError, TypeError, ValueError) as error:
            raise DashboardOperationsError(OperationsRefusal.REPLAY) from error
        self._aliases[run.session_id] = run.bundle
        await self._hub.replace_run(
            checkpoint,
            {"mode": "replay", "sessionId": run.session_id},
        )
        self._active = run


def _initial_checkpoint(
    scenario: Mapping[str, object],
    mission_id: str,
    predecessor: str | None,
) -> ReducerCheckpoint:
    try:
        members = tuple(_initial_member(_mapping(item)) for item in _sequence(scenario, "members"))
        checkpoint = prepare_checkpoint(
            PreparedMission(
                mission_id,
                predecessor,
                tuple(
                    identifier
                    for identifier, participation in members
                    if participation is Participation.SIMULATED
                ),
                tuple(
                    identifier
                    for identifier, participation in members
                    if participation is Participation.DECLARED_ONLY
                ),
                tuple(
                    _text(_mapping(item), "identifier") for item in _sequence(scenario, "sectors")
                ),
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DashboardOperationsError(OperationsRefusal.SCENARIO) from error
    return checkpoint


def _initial_member(member: Mapping[str, object]) -> tuple[str, Participation]:
    identifier = _text(member, "identifier")
    participation = _text(member, "participation")
    if participation == "SIMULATED":
        return identifier, Participation.SIMULATED
    if participation == "DECLARED_ONLY":
        return identifier, Participation.DECLARED_ONLY
    raise DashboardOperationsError(OperationsRefusal.SCENARIO)


def _replay_checkpoint(bundle: bytes) -> ReducerCheckpoint:
    document = _mapping(canonical.decode(bundle))
    state = _reduced_state(_mapping(document.get("initialState")))
    witness = _nullable_text(document.get("latestEventDigest"))
    outcome = checkpoint_from_replay(state, witness)
    if isinstance(outcome, CheckpointRefused):
        raise DashboardOperationsError(OperationsRefusal.REPLAY)
    return outcome.checkpoint


def _reduced_state(document: Mapping[str, object]) -> DashboardReducedState:
    mission_value = document.get("currentMission")
    mission = None if mission_value is None else _reduced_mission(_mapping(mission_value))
    return DashboardReducedState(
        mission,
        tuple(_reduced_member(_mapping(item)) for item in _sequence(document, "fleet")),
        _integer(document, "latestAuditOrdinal"),
        tuple(_reduced_sector(_mapping(item)) for item in _sequence(document, "sectors")),
    )


def _reduced_mission(document: Mapping[str, object]) -> Mission:
    return Mission(
        _text(document, "identifier"),
        MissionLifecycle(_text(document, "lifecycle")),
        _nullable_text(document.get("predecessorIdentifier")),
    )


def _reduced_member(
    document: Mapping[str, object],
) -> SimulatedFleetMember | DeclaredOnlyFleetMember:
    identifier = _text(document, "identifier")
    participation = Participation(_text(document, "participation"))
    if participation is Participation.DECLARED_ONLY:
        return DeclaredOnlyFleetMember(identifier)
    telemetry_value = document.get("telemetry")
    telemetry = None if telemetry_value is None else _telemetry(_mapping(telemetry_value))
    return SimulatedFleetMember(
        identifier,
        connectivity=Connectivity(_text(document, "connectivity")),
        telemetry=telemetry,
    )


def _telemetry(document: Mapping[str, object]) -> Telemetry:
    return Telemetry(
        _integer(document, "latitudeMicrodegrees"),
        _integer(document, "longitudeMicrodegrees"),
        _integer(document, "batteryPercent"),
        _integer(document, "altitudeMetres"),
        _integer(document, "headingDegrees"),
        _integer(document, "groundSpeedCentimetresPerSecond"),
    )


def _reduced_sector(document: Mapping[str, object]) -> Sector:
    return Sector(
        _text(document, "identifier"),
        SectorState(_text(document, "state")),
        _nullable_text(document.get("assignedMemberId")),
    )


def _live_response(
    operation: str,
    run: _LiveRun,
    scenario: Mapping[str, object],
    predecessor: str | None,
) -> bytes:
    document: dict[str, object] = {
        "operationVersion": f"dashboard-{operation}-response/v1",
        "mode": "degradedLive",
        "missionId": run.mission_id,
        "runId": run.run_id,
        **_counts(scenario),
    }
    if predecessor is not None:
        document["predecessorMissionId"] = predecessor
    body = canonical.canonical_bytes(document)
    _validate_response(f"{operation}-response", body)
    return body


def _replay_response(operation: str, session_id: str, scenario: Mapping[str, object]) -> bytes:
    body = canonical.canonical_bytes(
        {
            "operationVersion": f"dashboard-{operation}-response/v1",
            "mode": "replay",
            "sessionId": session_id,
            **_counts(scenario),
        }
    )
    _validate_response(f"{operation}-response", body)
    return body


def _counts(scenario: Mapping[str, object]) -> dict[str, object]:
    return {
        "declaredCount": _integer(scenario, "declaredCount"),
        "simulatedCount": _integer(scenario, "simulatedCount"),
        "declaredOnlyCount": _integer(scenario, "declaredOnlyCount"),
    }


def _stable_identifier(
    prefix: str,
    operation: str,
    mutation: AuthorizedMutation,
    scope: str,
) -> str:
    material = canonical.canonical_bytes(
        {
            "body": canonical.decode(mutation.ingress.canonical_body),
            "idempotencyKey": mutation.ingress.idempotency_key,
            "operation": operation,
            "scope": scope,
        }
    )
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:24]}"


def _replay_request_digest(mutation: AuthorizedMutation, operation: str, scope: str) -> str:
    return digest.digest(
        digest.Context.IDEMPOTENCY_BODY,
        {
            "canonicalizationVersion": _CANONICALIZATION_VERSION,
            "body": canonical.decode(mutation.ingress.canonical_body),
            "operation": operation,
            "scope": scope,
        },
    )


def _replay_run(
    source: bytes,
    scenario_id: str,
    scenario_revision: int,
    session_id: str,
) -> _ReplayRun:
    document = dict(_mapping(canonical.decode(source)))
    body = canonical.canonical_bytes(document)
    _validate_response("replay-bundle", body)
    return _ReplayRun(scenario_id, scenario_revision, session_id, body)


def _scenario_revision(mutation: AuthorizedMutation) -> int:
    document = _mapping(mutation.ingress.document.model_dump(mode="python", by_alias=True))
    return _integer(document, "scenarioRevision")


def _scenario_operation_digest(
    mutation: AuthorizedMutation,
    operation: str,
    scenario_id: str | None,
) -> str:
    """Use the dashboard-operation digest profile selected by the durable coordinator."""
    covered: dict[str, object] = {
        "canonicalizationVersion": _CANONICALIZATION_VERSION,
        "operation": operation,
        "request": canonical.decode(mutation.ingress.canonical_body),
    }
    if scenario_id is not None:
        covered["scenarioId"] = scenario_id
    return digest.digest(digest.Context.IDEMPOTENCY_BODY, covered)


def _operation_answer(answer: MutationAnswer, success_schema: str) -> JsonOutcome:
    """Validate and classify a durable dashboard-operation answer."""
    selected = success_schema if answer.status == HTTPStatus.ACCEPTED else "error"
    _validate_response(selected, answer.body)
    return JsonOutcome(answer.status, _schema(selected), answer.body)


def _validate_response(name: str, body: bytes) -> None:
    parse_wire_document(_schema(name), body)


def _error(code: str, message: str, status: int) -> JsonOutcome:
    return JsonOutcome(
        status,
        _schema("error"),
        canonical.canonical_bytes(
            {"errorVersion": "dashboard-error/v1", "errorCode": code, "message": message}
        ),
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DashboardOperationsError(OperationsRefusal.SCENARIO)
    return cast("Mapping[str, object]", value)


def _sequence(document: Mapping[str, object], member: str) -> Sequence[object]:
    value = document.get(member)
    if not isinstance(value, list):
        raise DashboardOperationsError(OperationsRefusal.SCENARIO)
    return cast("list[object]", value)


def _text(document: Mapping[str, object], member: str) -> str:
    value = document.get(member)
    if not isinstance(value, str):
        raise DashboardOperationsError(OperationsRefusal.SCENARIO)
    return value


def _nullable_text(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise DashboardOperationsError(OperationsRefusal.SCENARIO)
    return value


def _integer(document: Mapping[str, object], member: str) -> int:
    value = document.get(member)
    if type(value) is not int:
        raise DashboardOperationsError(OperationsRefusal.SCENARIO)
    return value


def _schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}{name}.schema.json"
