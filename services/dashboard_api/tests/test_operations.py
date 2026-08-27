"""Concrete live and structurally isolated replay route-operation tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Final, cast, override

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api import operations as operations_module
from aerial_rescue_dashboard_api.application import AuthorizedMutation, EventStream
from aerial_rescue_dashboard_api.files import DashboardFileSettings, FilesystemDashboardData
from aerial_rescue_dashboard_api.ingress import parse_mutation
from aerial_rescue_dashboard_api.lifecycle import RunMode, RuntimeReadiness
from aerial_rescue_dashboard_api.mutations import DashboardMutationError, MutationRefusal
from aerial_rescue_dashboard_api.operations import (
    LiveDashboardOperations,
    LiveOperationPorts,
    ReplayDashboardOperations,
)
from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_store.idempotency import (
    ClaimOutcome,
    StoredClaim,
    StoredClaimError,
    StoredClaimRefusal,
)

_ROOT = Path(__file__).parents[3]
_KEY: Final = "123e4567-e89b-42d3-a456-426614174000"
_RESET_KEY: Final = "123e4567-e89b-42d3-a456-426614174001"
_NOW: Final = "2026-08-26T12:00:00.000Z"


def _schema(name: str) -> str:
    return f"https://aerial-rescue.invalid/schemas/v1/dashboard/{name}.schema.json"


def _mutation(name: str, key: str) -> AuthorizedMutation:
    body = (_ROOT / f"fixtures/golden/v1/dashboard/{name}/baseline.json").read_bytes()
    canonical_body = canonical.canonical_bytes(canonical.decode(body))
    return AuthorizedMutation(
        parse_mutation(
            schema_id=_schema(name),
            body=canonical_body,
            content_type="application/json",
            idempotency_key=key,
            path_bindings={},
        ),
        "local-operator",
    )


def _files(tmp_path: Path) -> FilesystemDashboardData:
    assets = tmp_path / "assets"
    replays = tmp_path / "replays"
    assets.mkdir()
    replays.mkdir()
    (assets / "index-12345678.js").write_bytes(b"export {};")
    raw = (_ROOT / "fixtures/golden/v1/dashboard/replay-bundle/baseline.json").read_bytes()
    replay = canonical.canonical_bytes(canonical.decode(raw))
    (replays / "replay-session-synthetic-0001.json").write_bytes(replay)
    return FilesystemDashboardData(
        DashboardFileSettings(_ROOT / "scenarios", assets, replays, 262_144)
    )


@dataclass
class _Scenario:
    ready: bool = False
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def startup(self) -> None:
        self.ready = True
        self.calls.append(("startup", None))

    async def shutdown(self) -> None:
        self.ready = False
        self.calls.append(("shutdown", None))

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> Mapping[str, object]:
        self.calls.append(("start", (scenario_id, scenario_revision, mission_id, run_id)))
        return {
            "controlVersion": 1,
            "scenarioId": scenario_id,
            "scenarioRevision": scenario_revision,
            "missionId": mission_id,
            "runId": run_id,
            "state": "PLANNED",
            "declaredCount": 23,
            "simulatedCount": 20,
            "declaredOnlyCount": 3,
            "completedTickCount": 0,
            "telemetryPublicationCount": 0,
        }

    async def status(self, run_id: str) -> Mapping[str, object]:
        self.calls.append(("status", run_id))
        return {"runId": run_id, "state": "PLANNED"}

    async def cancel(
        self,
        mission_id: str,
        run_id: str,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append(("cancel", (mission_id, run_id, timeout_seconds)))
        return {"missionId": mission_id, "runId": run_id, "state": "ABORTED"}


@dataclass
class _Hub:
    replacements: list[tuple[object, Mapping[str, object] | None]] = field(default_factory=list)
    closed: int = 0

    async def replace_run(
        self, checkpoint: object, current_run: Mapping[str, object] | None
    ) -> None:
        self.replacements.append((checkpoint, current_run))

    async def open_events(self) -> EventStream:
        raise AssertionError

    async def close(self) -> None:
        self.closed += 1


@dataclass
class _Broker:
    ready: bool = False
    activated: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    async def startup(self) -> None:
        self.ready = True
        self.calls.append("startup")

    async def shutdown(self) -> None:
        self.ready = False
        self.calls.append("shutdown")

    async def activate_mission(self, mission_id: str) -> None:
        self.activated.append(mission_id)


@dataclass
class _MutationService:
    async def command(self, _mutation: AuthorizedMutation) -> bytes:
        return canonical.canonical_bytes(
            {
                "operationVersion": "dashboard-command-response/v1",
                "missionId": "mission-synthetic-0001",
                "commandId": "command-synthetic-0001",
                "eventId": "event-synthetic-0001",
            }
        )

    async def decide(self, _mutation: AuthorizedMutation) -> bytes:
        raise AssertionError


@dataclass
class _ClaimState:
    claims: dict[str, tuple[StoredClaim, bytes | None]] = field(default_factory=dict)


@dataclass
class _Transaction:
    state: _ClaimState

    async def claim(self, request: StoredClaim) -> ClaimOutcome:
        prior = self.state.claims.get(request.idempotency_key)
        if prior is None:
            self.state.claims[request.idempotency_key] = (request, None)
            return ClaimOutcome(IdempotencyDecision.EXECUTE, None)
        stored, result = prior
        assert stored.kind is request.kind
        assert stored.body_digest == request.body_digest
        assert result is not None
        return ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, result)

    async def record_result(self, idempotency_key: str, result: bytes) -> None:
        claim, prior = self.state.claims[idempotency_key]
        assert prior is None
        self.state.claims[idempotency_key] = (claim, result)


@dataclass
class _Transactions:
    state: _ClaimState = field(default_factory=_ClaimState)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_Transaction]:
        yield _Transaction(self.state)


@pytest.mark.asyncio
async def test_live_start_is_durable_repeatable_and_activates_one_broker_mission(
    tmp_path: Path,
) -> None:
    # Arrange
    scenario = _Scenario()
    broker = _Broker()
    hub = _Hub()
    operations = LiveDashboardOperations(
        ports=LiveOperationPorts(
            _files(tmp_path),
            scenario,
            broker,
            hub,
            _MutationService(),
            _Transactions(),
        ),
        readiness=RuntimeReadiness(RunMode.DEGRADED_LIVE),
        claimed_at=lambda: _NOW,
    )
    mutation = _mutation("start-request", _KEY)

    # Act
    await operations.open()
    first = await operations.start_scenario("wilderness-missing-person", mutation)
    repeated = await operations.start_scenario("wilderness-missing-person", mutation)
    await operations.close()

    # Assert
    assert first == repeated
    assert first.status_code == HTTPStatus.ACCEPTED
    assert [name for name, _value in scenario.calls].count("start") == 1
    assert len(broker.activated) == 1
    assert hub.replacements[-1][1] == {
        "mode": "degradedLive",
        "missionId": broker.activated[0],
        "runId": cast("dict[str, object]", canonical.decode(first.body))["runId"],
    }
    assert broker.calls == ["startup", "shutdown"]


@pytest.mark.asyncio
async def test_live_reset_confirms_predecessor_cancellation_before_starting_successor(
    tmp_path: Path,
) -> None:
    # Arrange
    scenario = _Scenario()
    broker = _Broker()
    operations = LiveDashboardOperations(
        ports=LiveOperationPorts(
            _files(tmp_path),
            scenario,
            broker,
            _Hub(),
            _MutationService(),
            _Transactions(),
        ),
        readiness=RuntimeReadiness(RunMode.DEGRADED_LIVE),
        claimed_at=lambda: _NOW,
    )
    await operations.open()
    started = await operations.start_scenario(
        "wilderness-missing-person", _mutation("start-request", _KEY)
    )

    # Act
    reset = await operations.reset(_mutation("reset-request", _RESET_KEY))
    await operations.close()

    # Assert
    start_document = cast("dict[str, object]", canonical.decode(started.body))
    reset_document = cast("dict[str, object]", canonical.decode(reset.body))
    effects = [name for name, _value in scenario.calls if name in {"start", "cancel"}]
    assert effects == ["start", "cancel", "start"]
    assert reset_document["predecessorMissionId"] == start_document["missionId"]
    assert reset_document["missionId"] != start_document["missionId"]
    assert broker.activated == [start_document["missionId"], reset_document["missionId"]]


@pytest.mark.asyncio
async def test_replay_graph_rebinds_a_fresh_session_without_any_writer_capability(
    tmp_path: Path,
) -> None:
    # Arrange
    files = _files(tmp_path)
    hub = _Hub()
    operations = ReplayDashboardOperations(
        files=files,
        hub=hub,
        readiness=RuntimeReadiness(RunMode.REPLAY),
    )

    # Act
    await operations.open()
    started = await operations.start_replay(
        "wilderness-missing-person", _mutation("start-request", _KEY)
    )
    reset = await operations.reset_replay(_mutation("reset-request", _RESET_KEY))
    reset_document = cast("dict[str, object]", canonical.decode(reset.body))
    reset_session_id = cast("str", reset_document["sessionId"])
    bundle = await operations.replay_bundle(reset_session_id)
    await operations.close()

    # Assert
    start_document = cast("dict[str, object]", canonical.decode(started.body))
    rebound = cast("dict[str, object]", canonical.decode(bundle.body))
    assert start_document["sessionId"] != reset_document["sessionId"]
    assert rebound["sessionId"] == reset_document["sessionId"]
    assert hub.replacements[-1][1] == {
        "mode": "replay",
        "sessionId": reset_document["sessionId"],
    }
    assert not hasattr(operations, "publisher")
    assert not hasattr(operations, "transactions")


@dataclass
class _RefusingMutations:
    async def command(self, _mutation: AuthorizedMutation) -> bytes:
        raise DashboardMutationError(MutationRefusal.REQUEST)

    async def decide(self, _mutation: AuthorizedMutation) -> bytes:
        raise DashboardMutationError(MutationRefusal.REQUEST)


@dataclass
class _CancelRefusedScenario(_Scenario):
    @override
    async def cancel(
        self,
        mission_id: str,
        run_id: str,
        *,
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append(("cancel", (mission_id, run_id, timeout_seconds)))
        return {"missionId": mission_id, "runId": run_id, "state": "SEARCHING"}


@dataclass
class _FailingBroker(_Broker):
    ready_before_failure: bool = False

    @override
    async def startup(self) -> None:
        self.ready = self.ready_before_failure
        message = "broker startup failed"
        raise RuntimeError(message)


@dataclass
class _FailingCloseBroker(_Broker):
    @override
    async def shutdown(self) -> None:
        self.ready = False
        self.calls.append("shutdown")
        message = "broker shutdown failed"
        raise RuntimeError(message)


@dataclass
class _FailingCloseHub(_Hub):
    @override
    async def close(self) -> None:
        self.closed += 1
        message = "hub close failed"
        raise RuntimeError(message)


@dataclass
class _OutcomeTransaction:
    outcome: ClaimOutcome | None = None
    refusal: StoredClaimRefusal | None = None

    async def claim(self, _request: StoredClaim) -> ClaimOutcome:
        if self.refusal is not None:
            raise StoredClaimError(self.refusal, "redacted")
        if self.outcome is None:
            return ClaimOutcome(IdempotencyDecision.EXECUTE, None)
        return self.outcome

    async def record_result(self, _idempotency_key: str, _result: bytes) -> None:
        return None


@dataclass
class _OutcomeTransactions:
    transaction: _OutcomeTransaction

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_OutcomeTransaction]:
        yield self.transaction


def _live(
    tmp_path: Path,
    *,
    scenario: _Scenario | None = None,
    broker: _Broker | None = None,
    mutations: object | None = None,
    transactions: object | None = None,
) -> LiveDashboardOperations:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return LiveDashboardOperations(
        ports=LiveOperationPorts(
            _files(tmp_path),
            scenario or _Scenario(),
            broker or _Broker(),
            _Hub(),
            cast("operations_module.MutationPort", mutations or _MutationService()),
            cast("operations_module.ScenarioMutationTransactions", transactions or _Transactions()),
        ),
        readiness=RuntimeReadiness(RunMode.DEGRADED_LIVE),
        claimed_at=lambda: _NOW,
    )


@pytest.mark.asyncio
async def test_live_routes_return_no_current_run_and_closed_mutation_refusals(
    tmp_path: Path,
) -> None:
    # Arrange
    operations = _live(tmp_path, mutations=_RefusingMutations())
    reset_mutation = _mutation("reset-request", _RESET_KEY)
    command_mutation = _mutation("operator-command-request", _KEY)
    decision_mutation = _mutation("proposal-decision-request", _RESET_KEY)

    # Act
    reset = await operations.reset(reset_mutation)
    command = await operations.command(command_mutation)
    decision = await operations.decide_proposal(decision_mutation)

    # Assert
    assert reset.status_code == HTTPStatus.CONFLICT
    assert command.status_code == HTTPStatus.CONFLICT
    assert decision.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_prior_start_restores_a_fresh_process_epoch_before_returning_result(
    tmp_path: Path,
) -> None:
    # Arrange
    transactions = _Transactions()
    first = _live(tmp_path / "first", transactions=transactions)
    second_scenario = _Scenario()
    second_broker = _Broker()
    second = _live(
        tmp_path / "second",
        scenario=second_scenario,
        broker=second_broker,
        transactions=transactions,
    )
    mutation = _mutation("start-request", _KEY)
    await first.open()
    expected = await first.start_scenario("wilderness-missing-person", mutation)
    await first.close()
    await second.open()

    # Act
    repeated = await second.start_scenario("wilderness-missing-person", mutation)
    await second.close()

    # Assert
    assert repeated == expected
    assert [name for name, _value in second_scenario.calls].count("status") == 1
    assert len(second_broker.activated) == 1


@pytest.mark.asyncio
async def test_repeated_reset_returns_prior_result_without_repeating_control_effects(
    tmp_path: Path,
) -> None:
    # Arrange
    scenario = _Scenario()
    operations = _live(tmp_path, scenario=scenario)
    await operations.open()
    await operations.start_scenario(
        "wilderness-missing-person",
        _mutation("start-request", _KEY),
    )
    predecessor = operations._active
    mutation = _mutation("reset-request", _RESET_KEY)

    # Act
    first = await operations.reset(mutation)
    operations._active = predecessor
    repeated = await operations.reset(mutation)
    await operations.close()

    # Assert
    assert repeated == first
    assert [name for name, _value in scenario.calls].count("cancel") == 1


@pytest.mark.asyncio
async def test_reset_refuses_when_private_control_does_not_confirm_cancellation(
    tmp_path: Path,
) -> None:
    # Arrange
    scenario = _CancelRefusedScenario()
    operations = _live(tmp_path, scenario=scenario)
    await operations.open()
    await operations.start_scenario(
        "wilderness-missing-person",
        _mutation("start-request", _KEY),
    )

    # Act
    with pytest.raises(operations_module.DashboardOperationsError) as captured:
        await operations.reset(_mutation("reset-request", _RESET_KEY))
    await operations.close()

    # Assert
    assert captured.value.refusal is operations_module.OperationsRefusal.CONTROL
    assert [name for name, _value in scenario.calls].count("start") == 1


@pytest.mark.parametrize("ready_before_failure", [False, True])
@pytest.mark.asyncio
async def test_open_unwinds_only_resources_that_reached_ready_state(
    tmp_path: Path,
    ready_before_failure: bool,
) -> None:
    # Arrange
    scenario = _Scenario()
    broker = _FailingBroker(ready_before_failure=ready_before_failure)
    operations = _live(tmp_path, scenario=scenario, broker=broker)

    # Act
    with pytest.raises(RuntimeError) as captured:
        await operations.open()

    # Assert
    assert str(captured.value) == "broker startup failed"
    assert scenario.ready is False
    assert broker.calls == (["shutdown"] if ready_before_failure else [])


@pytest.mark.asyncio
async def test_startup_unwind_is_a_noop_before_any_resource_reaches_ready(tmp_path: Path) -> None:
    # Arrange
    operations = _live(tmp_path)

    # Act
    await operations._close_started_resources()

    # Assert
    assert operations._broker.ready is False
    assert operations._scenario.ready is False
    assert operations._files.ready is False


@pytest.mark.asyncio
async def test_close_attempts_every_resource_and_reraises_the_first_failure(tmp_path: Path) -> None:
    # Arrange
    broker = _FailingCloseBroker()
    hub = _FailingCloseHub()
    scenario = _Scenario()
    operations = LiveDashboardOperations(
        ports=LiveOperationPorts(
            _files(tmp_path),
            scenario,
            broker,
            hub,
            _MutationService(),
            _Transactions(),
        ),
        readiness=RuntimeReadiness(RunMode.DEGRADED_LIVE),
        claimed_at=lambda: _NOW,
    )
    await operations.open()

    # Act
    with pytest.raises(RuntimeError) as captured:
        await operations.close()

    # Assert
    assert str(captured.value) == "broker shutdown failed"
    assert hub.closed == 1
    assert scenario.ready is False


@pytest.mark.asyncio
async def test_claim_maps_store_refusals_and_incomplete_prior_outcomes_fail_closed(
    tmp_path: Path,
) -> None:
    # Arrange
    mutation = _mutation("start-request", _KEY)
    not_recorded = _live(
        tmp_path / "not-recorded",
        transactions=_OutcomeTransactions(
            _OutcomeTransaction(refusal=StoredClaimRefusal.RESULT_NOT_RECORDED)
        ),
    )
    mismatch = _live(
        tmp_path / "mismatch",
        transactions=_OutcomeTransactions(
            _OutcomeTransaction(refusal=StoredClaimRefusal.BODY_MISMATCH)
        ),
    )
    incomplete = _live(
        tmp_path / "incomplete",
        transactions=_OutcomeTransactions(
            _OutcomeTransaction(outcome=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, None))
        ),
    )

    # Act
    retriable = await not_recorded._claim(
        mutation,
        IdempotencyKind.DASHBOARD_START,
        "mission-synthetic-0001",
        "wilderness-missing-person",
    )
    with pytest.raises(operations_module.DashboardOperationsError) as refused:
        await mismatch._claim(
            mutation,
            IdempotencyKind.DASHBOARD_START,
            "mission-synthetic-0001",
            "wilderness-missing-person",
        )
    with pytest.raises(operations_module.DashboardOperationsError) as missing_result:
        await incomplete._claim(
            mutation,
            IdempotencyKind.DASHBOARD_START,
            "mission-synthetic-0001",
            "wilderness-missing-person",
        )

    # Assert
    assert retriable is None
    assert refused.value.refusal is operations_module.OperationsRefusal.IDEMPOTENCY
    assert missing_result.value.refusal is operations_module.OperationsRefusal.IDEMPOTENCY


@pytest.mark.asyncio
async def test_replay_no_current_repeat_claims_and_static_bundle_are_deterministic(
    tmp_path: Path,
) -> None:
    # Arrange
    files = _files(tmp_path)
    operations = ReplayDashboardOperations(
        files=files,
        hub=_Hub(),
        readiness=RuntimeReadiness(RunMode.REPLAY),
    )
    start_mutation = _mutation("start-request", _KEY)
    reset_mutation = _mutation("reset-request", _RESET_KEY)
    await operations.open()

    # Act
    missing = await operations.reset_replay(reset_mutation)
    started = await operations.start_replay("wilderness-missing-person", start_mutation)
    repeated_start = await operations.start_replay(
        "wilderness-missing-person",
        start_mutation,
    )
    predecessor = operations._active
    reset = await operations.reset_replay(reset_mutation)
    operations._active = predecessor
    repeated_reset = await operations.reset_replay(reset_mutation)
    static = await operations.replay_bundle("replay-session-synthetic-0001")
    expected_static = files.replay_for_scenario("wilderness-missing-person", 1)
    await operations.close()

    # Assert
    assert missing.status_code == HTTPStatus.CONFLICT
    assert repeated_start == started
    assert repeated_reset == reset
    assert static.body == expected_static


def test_replay_claim_refuses_kind_digest_or_result_mismatches(tmp_path: Path) -> None:
    # Arrange
    operations = ReplayDashboardOperations(
        files=_files(tmp_path),
        hub=_Hub(),
        readiness=RuntimeReadiness(RunMode.REPLAY),
    )
    mutation = _mutation("start-request", _KEY)
    response = b"expected"
    digest_value = operations_module._replay_request_digest(
        mutation,
        "start",
        "wilderness-missing-person",
    )

    # Act
    operations._claims[_KEY] = ("reset", digest_value, response)
    with pytest.raises(operations_module.DashboardOperationsError) as kind:
        operations._replay_claim(mutation, "start", "wilderness-missing-person", response)
    operations._claims[_KEY] = ("start", digest_value, b"different")
    with pytest.raises(operations_module.DashboardOperationsError) as result:
        operations._replay_claim(mutation, "start", "wilderness-missing-person", response)

    # Assert
    assert kind.value.refusal is operations_module.OperationsRefusal.IDEMPOTENCY
    assert result.value.refusal is operations_module.OperationsRefusal.IDEMPOTENCY


def test_replay_checkpoint_and_closed_helpers_refuse_noncanonical_shapes(tmp_path: Path) -> None:
    # Arrange
    source = _files(tmp_path)
    raw = source._settings.replay_root / "replay-session-synthetic-0001.json"
    document = dict(cast("dict[str, object]", canonical.decode(raw.read_bytes())))
    state = dict(cast("dict[str, object]", document["initialState"]))
    state["currentMission"] = None
    document["initialState"] = state
    invalid_anchor = canonical.canonical_bytes(document)

    # Act
    with pytest.raises(operations_module.DashboardOperationsError) as member:
        operations_module._initial_member(
            {"identifier": "drone-synthetic-0001", "participation": "UNKNOWN"}
        )
    with pytest.raises(operations_module.DashboardOperationsError) as checkpoint:
        operations_module._replay_checkpoint(invalid_anchor)
    with pytest.raises(operations_module.DashboardOperationsError) as mapping:
        operations_module._mapping([])
    with pytest.raises(operations_module.DashboardOperationsError) as sequence:
        operations_module._sequence({}, "member")
    with pytest.raises(operations_module.DashboardOperationsError) as text:
        operations_module._text({"member": 1}, "member")
    with pytest.raises(operations_module.DashboardOperationsError) as nullable:
        operations_module._nullable_text(1)
    with pytest.raises(operations_module.DashboardOperationsError) as integer:
        operations_module._integer({"member": True}, "member")

    # Assert
    refusals = (member, checkpoint, mapping, sequence, text, nullable, integer)
    assert all(
        captured.value.refusal
        in {
            operations_module.OperationsRefusal.SCENARIO,
            operations_module.OperationsRefusal.REPLAY,
        }
        for captured in refusals
    )
