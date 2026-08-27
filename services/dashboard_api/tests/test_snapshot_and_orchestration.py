"""Atomic snapshots, history-preserving reset, and pending-run recovery."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from typing import Final, cast
from unittest.mock import patch

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.orchestration import (
    CANCELLATION_BUDGET_SECONDS,
    OperationCoordinator,
)
from aerial_rescue_dashboard_api.ports import (
    ClaimedOperation,
    CurrentRun,
    MutationKind,
    MutationProposal,
    OperationState,
    RunMode,
    ScenarioCancellationNotEstablishedError,
    ScenarioRunNotFoundError,
    ScenarioRunStatus,
    SnapshotBasis,
    StoredEvent,
)
from aerial_rescue_dashboard_api.snapshot import (
    SnapshotService,
    _checkpoint_from_bytes,
)
from aerial_rescue_dashboard_api.snapshot import (
    _integer as snapshot_integer,
)
from aerial_rescue_dashboard_api.snapshot import (
    _mapping as snapshot_mapping,
)
from aerial_rescue_dashboard_api.snapshot import (
    _sequence as snapshot_sequence,
)
from aerial_rescue_dashboard_api.snapshot import (
    _string as snapshot_string,
)

from tests.dashboard_api_support import (
    FakeIdentifiers,
    FakeReplay,
    FakeScenario,
    FakeStore,
    dashboard_fixture,
    live_prepared_state,
)

pytestmark = [pytest.mark.integration]

KEY_ONE: Final = "31f72c3e-2357-4d8d-8ec8-5ca709032590"
KEY_TWO: Final = "4984a66b-ff04-4128-94ea-24578dc54851"


def _event(ordinal: int, lifecycle: str) -> StoredEvent:
    """Return one canonical normalized mission-lifecycle audit payload."""
    return StoredEvent(
        audit_ordinal=ordinal,
        kind="missionLifecycle",
        payload=canonical.canonical_bytes(
            {
                "data": {"lifecycle": lifecycle},
                "eventClass": "MISSION",
                "kind": "missionLifecycle",
                "mission": "mission-test-0001",
                "time": f"2026-08-25T12:00:0{ordinal}.000Z",
            }
        ),
    )


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow one canonical-decoded object for assertions."""
    if not isinstance(value, Mapping):
        message = "expected an object"
        raise TypeError(message)
    return cast(Mapping[str, object], value)


def _live_current(
    mission_id: str = "mission-test-0001",
    run_id: str = "run-test-0001",
    *,
    started: bool = False,
) -> CurrentRun:
    """Build the canonical live run identity shared by orchestration arrangements."""
    return CurrentRun(
        RunMode.DEGRADED_LIVE,
        "wilderness-missing-person",
        1,
        mission_id,
        run_id,
        None,
        started=started,
    )


def _proposal() -> MutationProposal:
    """Build one explicit mutation proposal from the shared accepted identities."""
    return MutationProposal(
        KEY_ONE,
        MutationKind.START,
        RunMode.DEGRADED_LIVE,
        "aa" * 32,
        "wilderness-missing-person",
        1,
        "mission-test-0001",
        "run-test-0001",
        None,
        None,
    )


def _snapshot_service(store: FakeStore) -> SnapshotService:
    """Bind one fake store to the stable snapshot cursor and runtime."""
    return SnapshotService(
        store,
        CursorCodec("runtime-test-0001", b"c" * 32),
        "runtime-test-0001",
    )


def _coordinator(
    store: FakeStore,
    scenario: FakeScenario,
    replay: FakeReplay | None = None,
) -> OperationCoordinator:
    """Compose the operation coordinator from the fakes each test owns."""
    return OperationCoordinator(
        store,
        scenario,
        replay or FakeReplay(dashboard_fixture("replay-bundle")),
        FakeIdentifiers(),
    )


async def _establish_live_run(
    coordinator: OperationCoordinator,
    *,
    key: str = KEY_ONE,
    digest: str = "aa" * 32,
) -> None:
    """Establish the live predecessor used as arrangement by reset tests."""
    await coordinator.start(
        "wilderness-missing-person",
        RunMode.DEGRADED_LIVE,
        1,
        key,
        digest,
    )


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_captures_basis_then_folds_only_through_its_atomic_watermark(
        self,
    ) -> None:
        # Arrange
        current = _live_current()
        store = FakeStore(
            current=current,
            events=(_event(1, "PLANNED"), _event(2, "SEARCHING"), _event(3, "EXHAUSTED")),
        )
        store.basis = SnapshotBasis(current, live_prepared_state(), 2)
        service = _snapshot_service(store)

        # Act
        capture = await service.capture()
        document = _mapping(canonical.decode(capture.body))
        state = _mapping(document["state"])
        mission = _mapping(state["currentMission"])
        timeline = document["timeline"]

        # Assert
        self.assertEqual(2, capture.audit_ordinal)
        self.assertEqual("SEARCHING", mission["lifecycle"])
        self.assertIsInstance(timeline, list)
        self.assertEqual(2, len(cast(list[object], timeline)))
        self.assertEqual(
            ["basis", "events:run-test-0001:0:2:2"],
            [call for call in store.calls if call == "basis" or call.startswith("events:")],
        )
        self.assertNotIn("EXHAUSTED", capture.body.decode())

    async def test_suffix_frame_uses_identical_opaque_id_and_post_fold_digest(self) -> None:
        # Arrange
        current = _live_current()
        store = FakeStore(
            current=current,
            events=(_event(1, "PLANNED"),),
        )
        store.basis = SnapshotBasis(current, live_prepared_state(), 1)
        service = _snapshot_service(store)
        capture = await service.capture()

        # Act
        frame = service.fold_frame(current, capture.checkpoint, _event(2, "SEARCHING"))
        document = _mapping(canonical.decode(frame.body))
        ordered = _mapping(document["event"])

        # Assert
        self.assertEqual(frame.cursor, document["cursor"])
        self.assertEqual(2, ordered["auditOrdinal"])
        self.assertRegex(cast(str, document["digest"]), r"^[0-9a-f]{64}$")

    async def test_stored_kind_mismatch_refuses_before_folding_and_retains_checkpoint(
        self,
    ) -> None:
        # Arrange
        current = _live_current()
        store = FakeStore(
            current=current,
            events=(_event(1, "PLANNED"),),
        )
        store.basis = SnapshotBasis(current, live_prepared_state(), 1)
        service = _snapshot_service(store)
        capture = await service.capture()
        prior = capture.checkpoint
        mismatched = replace(_event(2, "SEARCHING"), kind="sectorLifecycle")

        # Act
        with (
            patch("aerial_rescue_dashboard_api.snapshot._fold", return_value=prior) as fold,
            pytest.raises(ApiError) as refusal,
        ):
            service.fold_frame(current, prior, mismatched)

        # Assert
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, refusal.value.code)
        self.assertIs(prior, capture.checkpoint)
        fold.assert_not_called()

    async def test_invalid_bounds_missing_audit_page_and_gap_suffix_fail_closed(self) -> None:
        # Arrange
        current = _live_current()
        basis = SnapshotBasis(current, live_prepared_state(), 1)
        store = FakeStore(current=current, basis=basis)
        service = _snapshot_service(store)
        zero_basis = SnapshotBasis(current, live_prepared_state(), 0)
        zero_store = FakeStore(current=current, basis=zero_basis)
        zero_service = _snapshot_service(zero_store)
        capture = await zero_service.capture()

        # Act
        with pytest.raises(ApiError) as bounds:
            await service.fold_basis_through(basis, -1)
        with pytest.raises(ApiError) as missing:
            await service.capture()
        with pytest.raises(ApiError) as gap:
            zero_service.fold_frame(current, capture.checkpoint, _event(2, "SEARCHING"))

        # Assert
        self.assertEqual(
            [ErrorCode.DEPENDENCY_UNAVAILABLE] * 3,
            [bounds.value.code, missing.value.code, gap.value.code],
        )

    async def test_replay_snapshot_uses_session_identity_and_timeline_bound_fails_closed(
        self,
    ) -> None:
        # Arrange
        replay_run = CurrentRun(
            RunMode.REPLAY,
            "wilderness-missing-person",
            1,
            None,
            None,
            "session-test-0001",
        )
        replay_store = FakeStore(
            current=replay_run,
            basis=SnapshotBasis(replay_run, live_prepared_state(), 0),
        )
        replay_service = _snapshot_service(replay_store)
        live = _live_current()
        pressured = FakeStore(
            current=live,
            basis=SnapshotBasis(live, live_prepared_state(), 257),
            events=tuple(_event(ordinal, "PLANNED") for ordinal in range(1, 258)),
        )
        pressured_service = _snapshot_service(pressured)

        # Act
        replay_capture = await replay_service.capture()
        replay_document = _mapping(canonical.decode(replay_capture.body))
        with pytest.raises(ApiError) as timeline:
            await pressured_service.capture()

        # Assert
        current_document = _mapping(replay_document["currentRun"])
        self.assertEqual("replay", current_document["mode"])
        self.assertEqual("session-test-0001", current_document["sessionId"])
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, timeline.value.code)

    async def test_anchor_and_defensive_adapters_reject_unprovable_or_wrong_python_values(
        self,
    ) -> None:
        # Arrange
        operations = (
            lambda: _checkpoint_from_bytes(dashboard_fixture("dashboard-reduced-state")),
            lambda: snapshot_mapping(None),
            lambda: snapshot_sequence("not-an-array"),
            lambda: snapshot_string(1),
            lambda: snapshot_integer(True),
        )

        # Act
        errors = []
        for operation in operations:
            with pytest.raises(ApiError) as captured:
                operation()
            errors.append(captured.value.code)

        # Assert
        self.assertEqual([ErrorCode.DEPENDENCY_UNAVAILABLE] * 5, errors)


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_identity_refusals_complete_once_for_exact_retry_after_claim(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        cases = (
            ("scenario-unknown", 1, KEY_ONE, ErrorCode.SCENARIO_NOT_FOUND),
            (
                "wilderness-missing-person",
                99,
                KEY_TWO,
                ErrorCode.SCENARIO_REVISION_MISMATCH,
            ),
        )
        answers = []

        # Act
        for scenario_id, revision, key, _expected in cases:
            first = await coordinator.start(
                scenario_id,
                RunMode.DEGRADED_LIVE,
                revision,
                key,
                "aa" * 32,
            )
            repeated = await coordinator.start(
                scenario_id,
                RunMode.DEGRADED_LIVE,
                revision,
                key,
                "aa" * 32,
            )
            answers.append((first, repeated))

        # Assert
        for (_scenario_id, _revision, key, expected), (first, repeated) in zip(
            cases, answers, strict=True
        ):
            self.assertEqual((first.status, first.body), (repeated.status, repeated.body))
            self.assertEqual(expected.value, _mapping(canonical.decode(first.body))["errorCode"])
            self.assertEqual("completed", store.operations[key].state.value)
        self.assertEqual(2, sum(call.startswith("complete:") for call in store.calls))
        self.assertEqual([], scenario.starts)

    async def test_new_start_refuses_without_replacing_an_existing_current_run(self) -> None:
        # Arrange
        current = _live_current("mission-old", "run-old", started=True)
        store = FakeStore(current=current)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)

        # Act
        first = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            KEY_ONE,
            "aa" * 32,
        )
        repeated = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            KEY_ONE,
            "aa" * 32,
        )

        # Assert
        self.assertEqual((409, first.body), (repeated.status, repeated.body))
        self.assertIs(current, store.current)
        self.assertEqual([], scenario.starts)

    async def test_private_cancellation_refusal_is_completed_for_exact_safe_retry(self) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        await _establish_live_run(coordinator)

        # Act
        with patch.object(
            scenario,
            "cancel",
            side_effect=ScenarioCancellationNotEstablishedError("run-test-0001"),
        ) as cancel:
            first = await coordinator.reset(KEY_TWO, "bb" * 32)
            repeated = await coordinator.reset(KEY_TWO, "bb" * 32)

        # Assert
        self.assertEqual((409, first.body), (repeated.status, repeated.body))
        cancel.assert_awaited_once()

    async def test_completed_start_replays_without_refetching_the_live_catalog(self) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        first = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            KEY_ONE,
            "aa" * 32,
        )

        # Act
        with patch.object(scenario, "catalog", side_effect=RuntimeError("unavailable")) as catalog:
            repeated = await coordinator.start(
                "wilderness-missing-person",
                RunMode.DEGRADED_LIVE,
                1,
                KEY_ONE,
                "aa" * 32,
            )

        # Assert
        self.assertEqual((first.status, first.body), (repeated.status, repeated.body))
        self.assertEqual(1, len(scenario.starts))
        catalog.assert_not_awaited()

    async def test_replay_start_has_no_live_scenario_dependency(self) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        replay = FakeReplay(dashboard_fixture("replay-bundle"))
        coordinator = _coordinator(store, scenario, replay)

        # Act
        with patch.object(scenario, "catalog", side_effect=RuntimeError("unavailable")) as catalog:
            answer = await coordinator.start(
                "wilderness-missing-person",
                RunMode.REPLAY,
                1,
                KEY_ONE,
                "aa" * 32,
            )

        # Assert
        self.assertEqual(202, answer.status)
        self.assertEqual([("wilderness-missing-person", 1)], replay.preparations)
        catalog.assert_not_awaited()

    async def test_replay_start_replaces_the_pointer_without_mutating_an_existing_live_mission(
        self,
    ) -> None:
        # Arrange
        live = _live_current("mission-terminal", "run-terminal")
        store = FakeStore(current=live)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        replay = FakeReplay(dashboard_fixture("replay-bundle"))
        coordinator = _coordinator(store, scenario, replay)

        # Act
        answer = await coordinator.start(
            "wilderness-missing-person",
            RunMode.REPLAY,
            1,
            KEY_ONE,
            "aa" * 32,
        )
        document = _mapping(canonical.decode(answer.body))

        # Assert
        self.assertEqual(202, answer.status)
        self.assertEqual("session-test-0001", document["sessionId"])
        self.assertIs(RunMode.REPLAY, store.current.mode if store.current else None)
        self.assertEqual([], scenario.starts)
        self.assertEqual([], scenario.cancels)
        self.assertEqual("mission-terminal", live.mission_id)

    async def test_live_start_replaces_the_replay_pointer_without_mutating_the_session(
        self,
    ) -> None:
        # Arrange
        replay_run = CurrentRun(
            RunMode.REPLAY,
            "wilderness-missing-person",
            1,
            None,
            None,
            "session-retained",
        )
        store = FakeStore(current=replay_run)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)

        # Act
        answer = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            KEY_ONE,
            "aa" * 32,
        )

        # Assert
        self.assertEqual(202, answer.status)
        self.assertIs(RunMode.DEGRADED_LIVE, store.current.mode if store.current else None)
        self.assertEqual("session-retained", replay_run.session_id)
        self.assertEqual(1, len(scenario.starts))
        self.assertEqual([], scenario.cancels)

    async def test_live_reset_cancels_with_one_shared_budget_and_links_the_successor(self) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        await _establish_live_run(coordinator)
        predecessor = store.current
        if predecessor is None:
            self.fail("start did not establish a predecessor")

        # Act
        answer = await coordinator.reset(KEY_TWO, "bb" * 32)
        document = _mapping(canonical.decode(answer.body))
        successor = store.current
        if successor is None:
            self.fail("reset did not establish a successor")

        # Assert
        self.assertEqual(202, answer.status)
        self.assertEqual(predecessor.mission_id, document["predecessorMissionId"])
        self.assertEqual(CANCELLATION_BUDGET_SECONDS, scenario.cancels[0][2])
        self.assertEqual(1, len(scenario.starts))
        self.assertFalse(successor.started)
        self.assertNotEqual(predecessor.mission_id, successor.mission_id)

    async def test_start_prepares_stable_live_identity_before_private_handoff_and_reconciles_it(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        observed_current: list[CurrentRun | None] = []

        async def uncertain_start(
            _scenario_id: str,
            _scenario_revision: int,
            _mission_id: str,
            _run_id: str,
        ) -> object:
            observed_current.append(store.current)
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)

        # Act
        with (
            patch.object(scenario, "start", side_effect=uncertain_start) as start,
            pytest.raises(ApiError) as uncertain,
        ):
            await coordinator.start(
                "wilderness-missing-person",
                RunMode.DEGRADED_LIVE,
                1,
                KEY_ONE,
                "aa" * 32,
            )
        recovered = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            KEY_ONE,
            "aa" * 32,
        )

        # Assert
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, uncertain.value.code)
        self.assertEqual(202, recovered.status)
        start.assert_awaited_once()
        self.assertEqual(1, len(observed_current))
        self.assertIsNotNone(observed_current[0])
        self.assertEqual(
            "run-test-0001", observed_current[0].run_id if observed_current[0] else None
        )
        self.assertTrue(store.current.started if store.current else False)

    async def test_private_start_refuses_a_mismatched_scenario_identity(self) -> None:
        # Arrange
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(FakeStore(), scenario)
        mismatched = ScenarioRunStatus(
            scenario_id="different-wilderness-scenario",
            scenario_revision=1,
            mission_id="mission-test-0001",
            run_id="run-test-0001",
            state="PLANNED",
        )

        # Act
        with (
            patch.object(scenario, "start", return_value=mismatched),
            pytest.raises(ApiError) as captured,
        ):
            await coordinator.start(
                "wilderness-missing-person",
                RunMode.DEGRADED_LIVE,
                1,
                KEY_ONE,
                "aa" * 32,
            )

        # Assert
        self.assertIs(ErrorCode.RUN_CONFLICT, captured.value.code)

    async def test_private_status_refuses_a_mismatched_scenario_revision(self) -> None:
        # Arrange
        proposal = _proposal()
        store = FakeStore()
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        mismatched = ScenarioRunStatus(
            scenario_id="wilderness-missing-person",
            scenario_revision=2,
            mission_id="mission-test-0001",
            run_id="run-test-0001",
            state="PLANNED",
        )

        # Act
        with (
            patch.object(scenario, "status", return_value=mismatched),
            pytest.raises(ApiError) as captured,
        ):
            await coordinator.reconcile_pending()

        # Assert
        self.assertIs(ErrorCode.RUN_CONFLICT, captured.value.code)

    async def test_private_recovery_refuses_a_mismatched_run_identity(self) -> None:
        # Arrange
        proposal = _proposal()
        store = FakeStore()
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        scenario.missing_runs.add("run-test-0001")
        coordinator = _coordinator(store, scenario)
        mismatched = ScenarioRunStatus(
            scenario_id="wilderness-missing-person",
            scenario_revision=1,
            mission_id="mission-test-0001",
            run_id="run-different-0002",
            state="ABORTED",
        )

        # Act
        with (
            patch.object(scenario, "recover", return_value=mismatched),
            pytest.raises(ApiError) as captured,
        ):
            await coordinator.reconcile_pending()

        # Assert
        self.assertIs(ErrorCode.RUN_CONFLICT, captured.value.code)

    async def test_private_cancel_refuses_a_mismatched_predecessor_identity(self) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        await _establish_live_run(coordinator)
        predecessor = store.current
        mismatched = ScenarioRunStatus(
            scenario_id="wilderness-missing-person",
            scenario_revision=1,
            mission_id="mission-different-0002",
            run_id="run-test-0001",
            state="ABORTED",
        )

        # Act
        with (
            patch.object(scenario, "cancel", return_value=mismatched),
            pytest.raises(ApiError) as captured,
        ):
            await coordinator.reset(KEY_TWO, "bb" * 32)

        # Assert
        self.assertIs(ErrorCode.RUN_CONFLICT, captured.value.code)
        self.assertIs(predecessor, store.current)

    async def test_start_after_reset_activates_the_selected_successor_without_replacing_it(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        await _establish_live_run(coordinator)
        await coordinator.reset(KEY_TWO, "bb" * 32)
        successor = store.current
        key = "ada6dd4f-b742-447c-8479-9778919d993b"

        # Act
        answer = await coordinator.start(
            "wilderness-missing-person",
            RunMode.DEGRADED_LIVE,
            1,
            key,
            "cc" * 32,
        )
        document = _mapping(canonical.decode(answer.body))

        # Assert
        self.assertEqual(202, answer.status)
        self.assertIsNotNone(successor)
        self.assertEqual(successor.mission_id if successor else None, document["missionId"])
        self.assertEqual(successor.run_id if successor else None, document["runId"])
        self.assertEqual(2, len(scenario.starts))
        self.assertTrue(store.current.started if store.current else False)

    async def test_unknown_pending_run_delegates_exactly_one_aborted_recovery_to_scenario(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        proposal = _proposal()
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        scenario.missing_runs.add("run-test-0001")
        coordinator = _coordinator(store, scenario)

        # Act
        await coordinator.reconcile_pending()
        await coordinator.reconcile_pending()

        # Assert
        self.assertEqual([("mission-test-0001", "run-test-0001")], scenario.recoveries)
        self.assertEqual([], scenario.starts)
        self.assertEqual("completed", store.operations[KEY_ONE].state.value)

    async def test_unestablished_cancellation_completes_exact_refusal_without_moving_pointer(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        await _establish_live_run(coordinator)
        predecessor = store.current
        scenario.cancel_state = "SEARCHING"

        # Act
        first = await coordinator.reset(KEY_TWO, "bb" * 32)
        second = await coordinator.reset(KEY_TWO, "bb" * 32)

        # Assert
        self.assertEqual((409, 409), (first.status, second.status))
        self.assertEqual(first.body, second.body)
        refusal = _mapping(canonical.decode(first.body))
        self.assertEqual("CANCELLATION_NOT_ESTABLISHED", refusal["errorCode"])
        self.assertIs(predecessor, store.current)
        self.assertEqual(1, len(scenario.cancels))
        self.assertEqual("completed", store.operations[KEY_TWO].state.value)

    async def test_same_key_with_different_canonical_content_refuses_without_an_effect(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)
        await _establish_live_run(coordinator)

        # Act
        with pytest.raises(ApiError, match="idempotency") as captured:
            await coordinator.start(
                "wilderness-missing-person",
                RunMode.REPLAY,
                1,
                KEY_ONE,
                "bb" * 32,
            )

        # Assert
        self.assertIs(ErrorCode.IDEMPOTENCY_CONFLICT, captured.value.code)
        self.assertEqual(1, len(scenario.starts))

    async def test_replay_start_reset_and_pending_recovery_never_call_live_control(self) -> None:
        # Arrange
        store = FakeStore()
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        replay = FakeReplay(dashboard_fixture("replay-bundle"))
        coordinator = _coordinator(store, scenario, replay)

        # Act
        started = await coordinator.start(
            "wilderness-missing-person",
            RunMode.REPLAY,
            1,
            KEY_ONE,
            "aa" * 32,
        )
        reset = await coordinator.reset(KEY_TWO, "bb" * 32)
        pending_key = "ada6dd4f-b742-447c-8479-9778919d993b"
        pending = replace(
            _proposal(),
            idempotency_key=pending_key,
            mode=RunMode.REPLAY,
            request_digest="cc" * 32,
            mission_id=None,
            run_id=None,
            session_id="session-test-pending",
        )
        await store.claim_operation(pending)
        await coordinator.reconcile_pending()

        # Assert
        self.assertEqual((202, 202), (started.status, reset.status))
        self.assertEqual([], scenario.starts)
        self.assertEqual([], scenario.cancels)
        self.assertEqual(3, len(replay.preparations))
        self.assertEqual("completed", store.operations[pending_key].state.value)

    async def test_pending_live_reset_status_reconciles_without_repeating_cancel_or_start(
        self,
    ) -> None:
        # Arrange
        current = _live_current("mission-old", "run-old")
        proposal = replace(
            _proposal(),
            kind=MutationKind.RESET,
            predecessor_mission_id="mission-old",
        )
        store = FakeStore(current=current)
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)

        # Act
        answer = await coordinator.reset(KEY_ONE, "aa" * 32)

        # Assert
        self.assertEqual(202, answer.status)
        self.assertEqual(
            [("mission-old", "run-old", CANCELLATION_BUDGET_SECONDS)], scenario.cancels
        )
        self.assertEqual([], scenario.starts)
        self.assertEqual("mission-test-0001", store.current.mission_id if store.current else None)

    async def test_pending_reset_after_pointer_move_recancels_retained_predecessor_idempotently(
        self,
    ) -> None:
        # Arrange
        predecessor = _live_current("mission-old", "run-old", started=True)
        successor = _live_current()
        proposal = replace(
            _proposal(),
            kind=MutationKind.RESET,
            predecessor_mission_id=predecessor.mission_id,
        )
        store = FakeStore(
            current=successor,
            runs_by_mission={"mission-old": predecessor},
        )
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)

        # Act
        await coordinator.reconcile_pending()

        # Assert
        self.assertEqual(
            [("mission-old", "run-old", CANCELLATION_BUDGET_SECONDS)], scenario.cancels
        )
        self.assertEqual([], scenario.starts)
        self.assertIs(successor, store.current)
        self.assertEqual("completed", store.operations[KEY_ONE].state.value)

    async def test_terminal_predecessor_completes_pending_reset_without_private_control(
        self,
    ) -> None:
        # Arrange
        predecessor = _live_current("mission-old", "run-old", started=True)
        proposal = replace(
            _proposal(),
            kind=MutationKind.RESET,
            predecessor_mission_id=predecessor.mission_id,
        )
        store = FakeStore(
            current=predecessor,
            mission_lifecycles={"mission-old": "EXHAUSTED"},
        )
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)

        # Act
        with (
            patch.object(scenario, "cancel", side_effect=AssertionError("cancel called")) as cancel,
            patch.object(
                scenario, "recover", side_effect=AssertionError("recover called")
            ) as recover,
            patch.object(scenario, "start", side_effect=AssertionError("start called")) as start,
        ):
            await coordinator.reconcile_pending()
            await coordinator.reconcile_pending()

        # Assert
        completed = store.operations[KEY_ONE]
        self.assertEqual("completed", completed.state.value)
        self.assertEqual(202, completed.response.status if completed.response else None)
        self.assertEqual("mission-test-0001", store.current.mission_id if store.current else None)
        self.assertIs(predecessor, store.runs_by_mission["mission-old"])
        self.assertEqual(1, sum(call.startswith("prepare:") for call in store.calls))
        cancel.assert_not_awaited()
        recover.assert_not_awaited()
        start.assert_not_awaited()

    async def test_missing_nonterminal_predecessor_completes_cancellation_refusal(self) -> None:
        # Arrange
        predecessor = _live_current("mission-old", "run-old", started=True)
        proposal = replace(
            _proposal(),
            kind=MutationKind.RESET,
            predecessor_mission_id=predecessor.mission_id,
        )
        store = FakeStore(
            current=predecessor,
            mission_lifecycles={"mission-old": "SEARCHING"},
        )
        await store.claim_operation(proposal)
        scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
        coordinator = _coordinator(store, scenario)

        # Act
        with patch.object(
            scenario,
            "cancel",
            side_effect=ScenarioRunNotFoundError("run-old"),
        ) as cancel:
            await coordinator.reconcile_pending()
            await coordinator.reconcile_pending()

        # Assert
        completed = store.operations[KEY_ONE]
        refusal = _mapping(
            canonical.decode(completed.response.body if completed.response else b"{}")
        )
        self.assertEqual("completed", completed.state.value)
        self.assertEqual(409, completed.response.status if completed.response else None)
        self.assertEqual("CANCELLATION_NOT_ESTABLISHED", refusal["errorCode"])
        self.assertIs(predecessor, store.current)
        self.assertEqual(1, cancel.await_count)
        self.assertEqual(0, sum(call.startswith("prepare:") for call in store.calls))

    async def test_corrupt_or_conflicting_pending_representations_fail_closed(self) -> None:
        # Arrange
        catalog = dashboard_fixture("scenario-catalog")
        cases: list[tuple[FakeStore, FakeScenario, RunMode]] = []
        live_proposal = _proposal()
        missing_live = FakeStore()
        missing_live.operations[KEY_ONE] = replace(
            ClaimedOperation.from_proposal(live_proposal),
            mission_id=None,
            newly_claimed=False,
        )
        cases.append((missing_live, FakeScenario(catalog), RunMode.DEGRADED_LIVE))
        mismatch_store = FakeStore()
        mismatch_store.operations[KEY_ONE] = replace(
            ClaimedOperation.from_proposal(live_proposal),
            newly_claimed=False,
        )
        cases.append(
            (
                mismatch_store,
                FakeScenario(catalog, status_mission_id="mission-other"),
                RunMode.DEGRADED_LIVE,
            )
        )
        replay_proposal = replace(
            live_proposal,
            mode=RunMode.REPLAY,
            mission_id=None,
            run_id=None,
            session_id="session-test-0001",
        )
        missing_replay = FakeStore()
        missing_replay.operations[KEY_ONE] = replace(
            ClaimedOperation.from_proposal(replay_proposal),
            session_id=None,
            newly_claimed=False,
        )
        cases.append((missing_replay, FakeScenario(catalog), RunMode.REPLAY))
        malformed_completed = FakeStore()
        malformed_completed.operations[KEY_ONE] = replace(
            ClaimedOperation.from_proposal(live_proposal),
            state=OperationState.COMPLETED,
            response=None,
            newly_claimed=False,
        )
        cases.append((malformed_completed, FakeScenario(catalog), RunMode.DEGRADED_LIVE))

        # Act
        errors = []
        for store, scenario, mode in cases:
            coordinator = _coordinator(store, scenario)
            with pytest.raises(ApiError) as captured:
                await coordinator.start(
                    "wilderness-missing-person",
                    mode,
                    1,
                    KEY_ONE,
                    "aa" * 32,
                )
            errors.append(captured.value.code)

        # Assert
        self.assertEqual(
            [
                ErrorCode.RUN_CONFLICT,
                ErrorCode.RUN_CONFLICT,
                ErrorCode.RUN_CONFLICT,
                ErrorCode.INTERNAL_FAILURE,
            ],
            errors,
        )

    async def test_reset_without_current_or_with_invalid_live_identity_refuses(self) -> None:
        # Arrange
        scenarios = FakeScenario(dashboard_fixture("scenario-catalog"))
        replay = FakeReplay(dashboard_fixture("replay-bundle"))
        empty = _coordinator(FakeStore(), scenarios, replay)
        invalid_store = FakeStore(
            current=CurrentRun(
                RunMode.DEGRADED_LIVE,
                "wilderness-missing-person",
                1,
                None,
                None,
                None,
            )
        )
        invalid = _coordinator(invalid_store, scenarios, replay)

        # Act
        with pytest.raises(ApiError) as absent:
            await empty.reset(KEY_ONE, "aa" * 32)
        with pytest.raises(ApiError) as corrupt:
            await invalid.reset(KEY_TWO, "bb" * 32)

        # Assert
        self.assertIs(ErrorCode.OPERATION_CONFLICT, absent.value.code)
        self.assertIs(ErrorCode.RUN_CONFLICT, corrupt.value.code)
