"""Production SQL store composition over the focused dashboard repositories."""

from __future__ import annotations

import unittest
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Final, cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_dashboard_api import store_adapter as adapter
from aerial_rescue_dashboard_api.boundary.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import (
    Activation,
    CurrentRun,
    MutationKind,
    MutationProposal,
    RunMode,
    StoredResponse,
)
from aerial_rescue_dashboard_api.store_adapter import SqlStore
from aerial_rescue_store.dashboard.events import (
    SnapshotBasis as StoredSnapshotBasis,
)
from aerial_rescue_store.dashboard.events import StoredDashboardEvent
from aerial_rescue_store.dashboard.operations import (
    DashboardOperation,
    DashboardOperationError,
    DashboardOperationRefusal,
    OperationKind,
    OperationMode,
    OperationResult,
    OperationState,
)
from aerial_rescue_store.dashboard.runs import DashboardRun
from aerial_rescue_store.dashboard.runs import RunMode as StoredRunMode
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = [pytest.mark.integration]

KEY: Final = "31f72c3e-2357-4d8d-8ec8-5ca709032590"
RESPONSE: Final = StoredResponse(202, b'{"accepted":true}')

_SESSION = cast("AsyncSession", object())


@asynccontextmanager
async def _transaction(_factory: Callable[[], AsyncSession]) -> AsyncIterator[AsyncSession]:
    yield _SESSION


def _store() -> SqlStore:
    return SqlStore(
        lambda: _SESSION,
        cast("AsyncEngine", object()),
        5,
    )


def _proposal(mode: RunMode = RunMode.DEGRADED_LIVE) -> MutationProposal:
    live = mode is RunMode.DEGRADED_LIVE
    return MutationProposal(
        idempotency_key=KEY,
        kind=MutationKind.START,
        mode=mode,
        request_digest="d" * 64,
        scenario_id="wilderness-missing-person",
        scenario_revision=1,
        mission_id="mission-test-0001" if live else None,
        run_id="run-test-0001" if live else None,
        session_id=None if live else "session-test-0001",
        predecessor_mission_id=None,
    )


def _operation(
    *,
    mode: OperationMode = OperationMode.DEGRADED_LIVE,
    completed: bool = True,
) -> DashboardOperation:
    live = mode is OperationMode.DEGRADED_LIVE
    return DashboardOperation(
        idempotency_key=KEY,
        operation_kind=OperationKind.START,
        mode=mode,
        request_digest="d" * 64,
        scenario_id="wilderness-missing-person",
        scenario_revision=1,
        mission_id="mission-test-0001" if live else None,
        run_id="run-test-0001" if live else None,
        session_id=None if live else "session-test-0001",
        predecessor_mission_id=None,
        state=OperationState.COMPLETED if completed else OperationState.PENDING,
        result=OperationResult(202, RESPONSE.body) if completed else None,
        newly_claimed=False,
    )


def _run(mode: StoredRunMode = StoredRunMode.DEGRADED_LIVE) -> DashboardRun:
    live = mode is StoredRunMode.DEGRADED_LIVE
    return DashboardRun(
        run_identity="run-test-0001" if live else "session-test-0001",
        mode=mode,
        scenario_id="wilderness-missing-person",
        scenario_revision=1,
        mission_id="mission-test-0001" if live else None,
        run_id="run-test-0001" if live else None,
        session_id=None if live else "session-test-0001",
        prepared_initial_state=b'{"state":true}',
    )


class StoreAdapterOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_preparation_precedes_completion_and_is_idempotent_at_the_selected_pointer(
        self,
    ) -> None:
        # Arrange
        store = _store()
        live = _run()
        selected = adapter._current_run(live)
        activation = Activation(selected, b'{"state":true}')
        create_mission = AsyncMock()
        create_run = AsyncMock()
        move = AsyncMock()
        current = AsyncMock(side_effect=[None, live])

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "current_run", current),
            patch.object(adapter, "create_mission", create_mission),
            patch.object(adapter, "create_run", create_run),
            patch.object(adapter, "move_current_run", move),
            patch.object(adapter, "accepted_start", AsyncMock(return_value=False)),
        ):
            first = await store.prepare_run(activation)
            repeated = await store.prepare_run(activation)

        # Assert
        self.assertEqual((selected, selected), (first, repeated))
        create_mission.assert_awaited_once()
        create_run.assert_awaited_once()
        move.assert_awaited_once_with(_SESSION, None, "run-test-0001")

    async def test_live_preparation_moves_an_existing_replay_pointer(self) -> None:
        # Arrange
        store = _store()
        replay = _run(StoredRunMode.REPLAY)
        live = _run()
        selected = adapter._current_run(live)
        activation = Activation(selected, b'{"state":true}')
        create_mission = AsyncMock()
        create_run = AsyncMock()
        move = AsyncMock()

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "current_run", AsyncMock(return_value=replay)),
            patch.object(adapter, "create_mission", create_mission),
            patch.object(adapter, "create_run", create_run),
            patch.object(adapter, "move_current_run", move),
        ):
            prepared = await store.prepare_run(activation)

        # Assert
        self.assertEqual(selected, prepared)
        create_mission.assert_awaited_once()
        create_run.assert_awaited_once()
        move.assert_awaited_once_with(_SESSION, "session-test-0001", "run-test-0001")

    async def test_close_readiness_and_claim_preserve_exact_repository_values(self) -> None:
        # Arrange
        store = _store()
        closer = AsyncMock()
        repository_claim = AsyncMock(return_value=_operation())
        readable = AsyncMock(return_value=None)
        unavailable = AsyncMock(side_effect=SQLAlchemyError("unavailable"))

        # Act
        with patch.object(adapter, "close", closer):
            await store.close()
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "current_run", readable),
        ):
            ready = await store.readiness()
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "current_run", unavailable),
        ):
            refused = await store.readiness()
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "claim", repository_claim),
        ):
            claimed = await store.claim_operation(_proposal())

        # Assert
        closer.assert_awaited_once()
        self.assertEqual((), ready)
        self.assertEqual(("dashboard-store-unavailable",), refused)
        self.assertEqual(RESPONSE.body, claimed.response.body if claimed.response else None)
        self.assertFalse(claimed.newly_claimed)

    async def test_claim_maps_every_closed_repository_conflict_and_store_failure(self) -> None:
        # Arrange
        store = _store()
        categories = (
            DashboardOperationRefusal.OPERATION_MISMATCH,
            DashboardOperationRefusal.MODE_MISMATCH,
            DashboardOperationRefusal.REQUEST_MISMATCH,
            DashboardOperationRefusal.SCENARIO_MISMATCH,
            DashboardOperationRefusal.ANOTHER_OPERATION_PENDING,
            DashboardOperationRefusal.CLAIM_VANISHED,
        )
        codes: list[ErrorCode] = []

        # Act
        with patch.object(adapter, "transaction", _transaction):
            for category in categories:
                refusal = DashboardOperationError(category, "redacted")
                with (
                    patch.object(adapter, "claim", AsyncMock(side_effect=refusal)),
                    pytest.raises(ApiError) as error,
                ):
                    await store.claim_operation(_proposal())
                codes.append(error.value.code)
            with (
                patch.object(
                    adapter,
                    "claim",
                    AsyncMock(side_effect=SQLAlchemyError("unavailable")),
                ),
                pytest.raises(ApiError) as store_error,
            ):
                await store.claim_operation(_proposal())

        # Assert
        self.assertEqual(
            [ErrorCode.IDEMPOTENCY_CONFLICT] * 4
            + [ErrorCode.OPERATION_CONFLICT, ErrorCode.DEPENDENCY_UNAVAILABLE],
            codes,
        )
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, store_error.value.code)

    async def test_completion_persists_exact_response_without_creating_or_moving_history(
        self,
    ) -> None:
        # Arrange
        store = _store()
        complete = AsyncMock()
        create_mission = AsyncMock()
        create_run = AsyncMock()
        move = AsyncMock()

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "complete", complete),
            patch.object(adapter, "create_mission", create_mission),
            patch.object(adapter, "create_run", create_run),
            patch.object(adapter, "move_current_run", move),
        ):
            await store.complete_operation(KEY, RESPONSE)

        # Assert
        complete.assert_awaited_once()
        create_mission.assert_not_awaited()
        create_run.assert_not_awaited()
        move.assert_not_awaited()

    async def test_preparation_refuses_missing_live_identity_and_completion_maps_failures(
        self,
    ) -> None:
        # Arrange
        store = _store()
        malformed = CurrentRun(
            RunMode.DEGRADED_LIVE,
            "wilderness-missing-person",
            1,
            None,
            "run-test-0001",
            None,
        )
        repository_refusal = DashboardOperationError(
            DashboardOperationRefusal.COMPLETION_REJECTED, KEY
        )

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "current_run", AsyncMock(return_value=None)),
            pytest.raises(ApiError) as identity_error,
        ):
            await store.prepare_run(Activation(malformed, b"{}"))
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "complete", AsyncMock(side_effect=repository_refusal)),
            pytest.raises(ApiError) as completion_error,
        ):
            await store.complete_operation(KEY, RESPONSE)
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(
                adapter,
                "complete",
                AsyncMock(side_effect=SQLAlchemyError("unavailable")),
            ),
            pytest.raises(ApiError) as store_error,
        ):
            await store.complete_operation(KEY, RESPONSE)

        # Assert
        self.assertEqual(
            [ErrorCode.RUN_CONFLICT, ErrorCode.DEPENDENCY_UNAVAILABLE] * 1
            + [ErrorCode.DEPENDENCY_UNAVAILABLE],
            [identity_error.value.code, completion_error.value.code, store_error.value.code],
        )


class StoreAdapterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_mission_lifecycle_read_is_strict_and_closes_its_transaction(self) -> None:
        # Arrange
        store = _store()

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(
                adapter,
                "mission_lifecycle_for_update",
                AsyncMock(return_value="EXHAUSTED"),
            ) as lifecycle,
        ):
            terminal = await store.mission_lifecycle("mission-test-0001")
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(
                adapter,
                "mission_lifecycle_for_update",
                AsyncMock(side_effect=SQLAlchemyError("unavailable")),
            ),
            pytest.raises(ApiError) as unavailable,
        ):
            await store.mission_lifecycle("mission-test-0001")

        # Assert
        self.assertEqual("EXHAUSTED", terminal)
        lifecycle.assert_awaited_once_with(_SESSION, "mission-test-0001")
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, unavailable.value.code)

    async def test_pending_current_history_and_snapshot_reads_map_both_empty_and_present(
        self,
    ) -> None:
        # Arrange
        store = _store()
        operation = _operation(completed=False)
        live = _run()
        replay = _run(StoredRunMode.REPLAY)
        basis = StoredSnapshotBasis(live, 7)

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "accepted_start", AsyncMock(return_value=True)),
        ):
            with patch.object(adapter, "pending", AsyncMock(side_effect=[None, operation])):
                no_pending = await store.pending_operation()
                pending = await store.pending_operation()
            with patch.object(adapter, "current_run", AsyncMock(side_effect=[None, live])):
                no_current = await store.current_run()
                current = await store.current_run()
            with patch.object(
                adapter,
                "run_by_identity",
                AsyncMock(side_effect=[None, live, replay]),
            ):
                unknown = await store.replay_session_known("session-test-0001")
                wrong_mode = await store.replay_session_known("session-test-0001")
                known = await store.replay_session_known("session-test-0001")
            with patch.object(
                adapter,
                "capture_snapshot_basis",
                AsyncMock(side_effect=[None, basis]),
            ):
                no_basis = await store.capture_snapshot_basis()
                captured = await store.capture_snapshot_basis()

        # Assert
        self.assertIsNone(no_pending)
        self.assertEqual(KEY, pending.idempotency_key if pending else None)
        self.assertIsNone(no_current)
        self.assertEqual("run-test-0001", current.run_id if current else None)
        self.assertTrue(current.started if current else False)
        self.assertEqual((False, False, True), (unknown, wrong_mode, known))
        self.assertIsNone(no_basis)
        self.assertEqual(7, captured.audit_watermark if captured else None)
        self.assertEqual(b'{"state":true}', captured.prepared_initial_state if captured else None)

    async def test_read_failures_are_closed_and_event_pages_preserve_exact_payloads(self) -> None:
        # Arrange
        store = _store()
        live = adapter._current_run(_run())
        replay = adapter._current_run(_run(StoredRunMode.REPLAY))
        malformed = CurrentRun(
            RunMode.DEGRADED_LIVE,
            "wilderness-missing-person",
            1,
            None,
            "run-test-0001",
            None,
        )
        event = StoredDashboardEvent(3, "missionLifecycle", b'{"kind":"missionLifecycle"}')
        failures: list[ErrorCode] = []

        # Act
        replay_page = await store.read_events(replay, 0, None, 10)
        with pytest.raises(ApiError) as conflict:
            await store.read_events(malformed, 0, None, 10)
        with patch.object(adapter, "transaction", _transaction):
            with patch.object(adapter, "read_suffix_page", AsyncMock(return_value=(event,))):
                suffix = await store.read_events(live, 2, None, 10)
            with patch.object(adapter, "read_event_page", AsyncMock(return_value=(event,))):
                bounded = await store.read_events(live, 2, 5, 10)
            readers = (
                ("pending", store.pending_operation),
                ("current_run", store.current_run),
                ("run_by_identity", lambda: store.replay_session_known("session-test-0001")),
                ("capture_snapshot_basis", store.capture_snapshot_basis),
                ("read_suffix_page", lambda: store.read_events(live, 0, None, 10)),
            )
            for name, read in readers:
                with (
                    patch.object(
                        adapter,
                        name,
                        AsyncMock(side_effect=SQLAlchemyError("unavailable")),
                    ),
                    pytest.raises(ApiError) as error,
                ):
                    await read()
                failures.append(error.value.code)

        # Assert
        self.assertEqual((), replay_page)
        self.assertIs(ErrorCode.RUN_CONFLICT, conflict.value.code)
        self.assertEqual(event.payload, suffix[0].payload)
        self.assertEqual(event.payload, bounded[0].payload)
        self.assertEqual([ErrorCode.DEPENDENCY_UNAVAILABLE] * 5, failures)

    async def test_predecessor_read_derives_started_state_and_fails_closed(self) -> None:
        # Arrange
        store = _store()
        live = _run()

        # Act
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(adapter, "run_by_mission", AsyncMock(return_value=live)),
            patch.object(adapter, "accepted_start", AsyncMock(return_value=True)),
        ):
            predecessor = await store.run_for_mission("mission-test-0001")
        with (
            patch.object(adapter, "transaction", _transaction),
            patch.object(
                adapter,
                "run_by_mission",
                AsyncMock(side_effect=SQLAlchemyError("unavailable")),
            ),
            pytest.raises(ApiError) as unavailable,
        ):
            await store.run_for_mission("mission-test-0001")

        # Assert
        self.assertEqual("run-test-0001", predecessor.run_id if predecessor else None)
        self.assertTrue(predecessor.started if predecessor else False)
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, unavailable.value.code)


if __name__ == "__main__":
    unittest.main()
