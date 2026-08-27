"""Stable private fleet-run coordination, cancellation, and lifecycle."""

from __future__ import annotations

import asyncio
import unittest
from typing import override

import pytest
from aerial_rescue_fleet_simulator.control import FleetCoordinator
from aerial_rescue_fleet_simulator.control_wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from aerial_rescue_fleet_simulator.http_runtime import ControlError, ControlRefusal

pytestmark = [pytest.mark.unit]

MISSION = "m-2026-0001"
RUN = "run-2026-0001"


def _start(run_id: str = RUN, *, ticks: int = 10) -> FleetControlStartRequest:
    """Return one accepted twenty-drone run request."""
    return FleetControlStartRequest.model_validate(
        {
            "controlVersion": 1,
            "runId": run_id,
            "scenario": {
                "missionId": MISSION,
                "drones": [
                    {
                        "droneId": f"drone-{index:02d}",
                        "sectorId": f"sector-{index:02d}",
                        "latitudeMicrodegrees": 47_000_000 + index,
                        "longitudeMicrodegrees": -122_000_000,
                        "altitudeMetres": 400,
                        "headingDegrees": 0,
                        "groundSpeedCentimetresPerSecond": 850,
                        "batteryPermille": 1_000,
                        "northMicrodegreesPerTick": 10,
                        "eastMicrodegreesPerTick": 0,
                        "batteryDrainPermillePerTick": 5,
                    }
                    for index in range(20)
                ],
                "tickIntervalMilliseconds": 1_000,
                "connectivityThresholds": {
                    "missesToDegraded": 3,
                    "missesToOffline": 6,
                    "heartbeatsToRecover": 2,
                },
                "ticksToSweep": ticks,
                "absentHeartbeats": [],
            },
        }
    )


def _cancel(run_id: str = RUN, mission_id: str = MISSION) -> FleetControlCancelRequest:
    """Return one exact cancellation request."""
    return FleetControlCancelRequest.model_validate(
        {"controlVersion": 1, "missionId": mission_id, "runId": run_id}
    )


class FakeExecutor:
    def __init__(self) -> None:
        """Begin ready with controllable completion and no lifecycle effects."""
        self.ready = True
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[FleetControlStartRequest] = []
        self.lifecycle: list[str] = []
        self.failure: Exception | None = None

    async def startup(self) -> None:
        """Record dependency startup and queue validation."""
        self.lifecycle.append("startup")

    async def shutdown(self) -> None:
        """Record dependency shutdown."""
        self.lifecycle.append("shutdown")

    async def execute(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
    ) -> FleetControlRunStatus:
        """Wait for cancellation or an explicit test release, then return a terminal status."""
        self.calls.append(request)
        self.started.set()
        cancel_wait = asyncio.create_task(cancelled.wait())
        release_wait = asyncio.create_task(self.release.wait())
        done, pending = await asyncio.wait(
            {cancel_wait, release_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.failure is not None:
            self.finished.set()
            raise self.failure
        state = "CANCELLED" if cancel_wait in done else "EXHAUSTED"
        status = FleetControlRunStatus.model_validate(
            {
                "controlVersion": 1,
                "missionId": request.scenario.mission_id,
                "runId": request.run_id,
                "state": state,
                "completedTickCount": 4,
                "telemetryPublicationCount": 80,
            }
        )
        self.finished.set()
        return status


class StubbornExecutor(FakeExecutor):
    """Ignore the cooperative signal so timeout behavior remains observable."""

    @override
    async def execute(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
    ) -> FleetControlRunStatus:
        """Wait for explicit release or task cancellation, ignoring the signal."""
        del cancelled
        self.calls.append(request)
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.finished.set()
        return FleetControlRunStatus.model_validate(
            {
                "controlVersion": 1,
                "missionId": request.scenario.mission_id,
                "runId": request.run_id,
                "state": "EXHAUSTED",
                "completedTickCount": 4,
                "telemetryPublicationCount": 80,
            }
        )


class FleetCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonpositive_lifecycle_bounds_are_refused_before_startup(self) -> None:
        # Arrange
        executor = StubbornExecutor()

        # Act
        with pytest.raises(ValueError, match="bounds must be positive") as timeout_error:
            FleetCoordinator(executor, cancellation_timeout_seconds=0, capacity=1)
        with pytest.raises(ValueError, match="bounds must be positive") as capacity_error:
            FleetCoordinator(executor, cancellation_timeout_seconds=1, capacity=0)

        # Assert
        self.assertEqual(str(timeout_error.value), str(capacity_error.value))
        self.assertEqual(executor.lifecycle, [])

    async def test_start_is_stable_and_changed_body_conflicts_without_a_second_run(self) -> None:
        # Arrange
        executor = FakeExecutor()
        coordinator = FleetCoordinator(executor, cancellation_timeout_seconds=1, capacity=1)
        await coordinator.startup()

        # Act
        first = await coordinator.start(_start())
        await executor.started.wait()
        repeated = await coordinator.start(_start())
        with pytest.raises(ControlError) as raised:
            await coordinator.start(_start(ticks=11))
        executor.release.set()
        await executor.finished.wait()
        final = await coordinator.status(RUN)
        await coordinator.shutdown()

        # Assert
        self.assertEqual((first.state, repeated.state), ("ACCEPTED", "RUNNING"))
        self.assertEqual(final.state, "EXHAUSTED")
        self.assertEqual(raised.value.refusal, ControlRefusal.RUN_CONFLICT)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.lifecycle, ["startup", "shutdown"])

    async def test_cancel_waits_for_the_exact_run_to_stop(self) -> None:
        # Arrange
        executor = FakeExecutor()
        coordinator = FleetCoordinator(executor, cancellation_timeout_seconds=1, capacity=1)
        await coordinator.startup()
        await coordinator.start(_start())
        await executor.started.wait()

        # Act
        cancelled = await coordinator.cancel(_cancel())
        await coordinator.shutdown()

        # Assert
        self.assertEqual(cancelled.state, "CANCELLED")
        self.assertEqual((cancelled.mission_id, cancelled.run_id), (MISSION, RUN))

    async def test_unknown_mismatched_and_over_capacity_runs_fail_closed(self) -> None:
        # Arrange
        executor = FakeExecutor()
        coordinator = FleetCoordinator(executor, cancellation_timeout_seconds=1, capacity=1)
        await coordinator.startup()
        await coordinator.start(_start())
        await executor.started.wait()
        refusals: list[ControlRefusal] = []

        # Act
        for operation in (
            coordinator.status("run-unknown"),
            coordinator.cancel(_cancel(mission_id="m-other")),
            coordinator.start(_start("run-second")),
        ):
            with pytest.raises(ControlError) as raised:
                await operation
            refusals.append(raised.value.refusal)
        executor.release.set()
        await executor.finished.wait()
        await coordinator.shutdown()

        # Assert
        self.assertEqual(
            refusals,
            [
                ControlRefusal.RUN_NOT_FOUND,
                ControlRefusal.PATH_BODY_MISMATCH,
                ControlRefusal.CAPACITY_EXCEEDED,
            ],
        )

    async def test_unknown_cancellation_is_refused_without_signalling_an_existing_run(self) -> None:
        # Arrange
        executor = FakeExecutor()
        coordinator = FleetCoordinator(executor, cancellation_timeout_seconds=1, capacity=1)
        await coordinator.startup()

        # Act
        with pytest.raises(ControlError) as raised:
            await coordinator.cancel(_cancel(run_id="run-unknown"))
        await coordinator.shutdown()

        # Assert
        self.assertEqual(raised.value.refusal, ControlRefusal.RUN_NOT_FOUND)
        self.assertEqual(executor.calls, [])

    async def test_cancel_timeout_refuses_until_the_run_reaches_a_terminal_state(self) -> None:
        # Arrange
        executor = StubbornExecutor()
        coordinator = FleetCoordinator(
            executor,
            cancellation_timeout_seconds=0.001,
            capacity=1,
        )
        await coordinator.startup()
        await coordinator.start(_start())
        await executor.started.wait()

        # Act
        with pytest.raises(ControlError) as raised:
            await coordinator.cancel(_cancel())
        executor.release.set()
        await executor.finished.wait()
        terminal = await coordinator.status(RUN)
        await coordinator.shutdown()

        # Assert
        self.assertEqual(raised.value.refusal, ControlRefusal.CANCELLATION_NOT_ESTABLISHED)
        self.assertEqual(terminal.state, "EXHAUSTED")

    async def test_shutdown_cancels_a_run_that_exceeds_the_shared_completion_bound(self) -> None:
        # Arrange
        executor = StubbornExecutor()
        coordinator = FleetCoordinator(
            executor,
            cancellation_timeout_seconds=0.001,
            capacity=1,
        )
        await coordinator.startup()
        await coordinator.start(_start())
        await executor.started.wait()
        running_task = next(iter(coordinator._runs.values())).task

        # Act
        await coordinator.shutdown()

        # Assert
        self.assertIsNotNone(running_task)
        self.assertTrue(running_task.cancelled() if running_task is not None else False)
        self.assertEqual(executor.lifecycle, ["startup", "shutdown"])

    async def test_executor_failure_is_a_durable_failed_status_not_a_lost_task(self) -> None:
        # Arrange
        executor = FakeExecutor()
        executor.failure = RuntimeError("redacted failure")
        coordinator = FleetCoordinator(executor, cancellation_timeout_seconds=1, capacity=1)
        await coordinator.startup()
        await coordinator.start(_start())
        await executor.started.wait()

        # Act
        executor.release.set()
        await executor.finished.wait()
        status = await coordinator.status(RUN)
        executor.ready = False
        readiness = coordinator.ready
        await coordinator.shutdown()

        # Assert
        self.assertEqual(status.state, "FAILED")
        self.assertFalse(readiness)


if __name__ == "__main__":
    unittest.main()
