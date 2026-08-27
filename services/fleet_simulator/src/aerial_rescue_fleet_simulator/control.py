"""Stable private fleet-run coordination over an injected executable runtime."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Protocol

from aerial_rescue_contracts import canonical

from aerial_rescue_fleet_simulator.control_wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from aerial_rescue_fleet_simulator.http_runtime import ControlError, ControlRefusal

_ACTIVE_STATES = frozenset({"ACCEPTED", "RUNNING"})


class RunExecutor(Protocol):
    """The broker/store-backed runtime the coordinator owns but does not implement."""

    @property
    def ready(self) -> bool:
        """Return whether queues, store, broker, bindings, and outboxes are ready."""

    async def startup(self) -> None:
        """Validate queues and acquire all bounded dependencies."""

    async def shutdown(self) -> None:
        """Stop intake and release consumers, publishers, and store resources."""

    async def execute(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
    ) -> FleetControlRunStatus:
        """Run one accepted scenario until terminal state or cancellation."""


@dataclass(slots=True)
class _RunRecord:
    """One stable request binding and its current task-owned status."""

    digest: str
    status: FleetControlRunStatus
    cancelled: asyncio.Event
    task: asyncio.Task[None] | None = None


def _status(
    request: FleetControlStartRequest,
    state: str,
    ticks: int = 0,
    telemetry: int = 0,
) -> FleetControlRunStatus:
    """Build one strict status from coordinator-owned counters."""
    return FleetControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "missionId": request.scenario.mission_id,
            "runId": request.run_id,
            "state": state,
            "completedTickCount": ticks,
            "telemetryPublicationCount": telemetry,
        }
    )


def _digest(request: FleetControlStartRequest) -> str:
    """Return the stable digest binding one run identifier to its exact request."""
    document = request.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(canonical.canonical_bytes(document)).hexdigest()


class FleetCoordinator:
    """Coordinate bounded concurrent runs without owning broker or SQL implementations."""

    def __init__(
        self,
        executor: RunExecutor,
        *,
        cancellation_timeout_seconds: float,
        capacity: int,
    ) -> None:
        """Bind explicit cancellation and run-capacity limits."""
        if cancellation_timeout_seconds <= 0 or capacity < 1:
            message = "fleet coordinator bounds must be positive"
            raise ValueError(message)
        self._executor = executor
        self._cancellation_timeout = cancellation_timeout_seconds
        self._capacity = capacity
        self._runs: dict[str, _RunRecord] = {}
        self._lock = asyncio.Lock()
        self._started = False

    @property
    def ready(self) -> bool:
        """Require successful lifecycle startup and executor recovery readiness."""
        return self._started and self._executor.ready

    async def startup(self) -> None:
        """Validate and acquire the executor dependencies before accepting work."""
        await self._executor.startup()
        self._started = True

    async def shutdown(self) -> None:
        """Signal every run, bound their completion, then close the executor."""
        self._started = False
        async with self._lock:
            records = tuple(self._runs.values())
            for record in records:
                record.cancelled.set()
            tasks = tuple(record.task for record in records if record.task is not None)
        try:
            async with asyncio.timeout(self._cancellation_timeout):
                await asyncio.gather(*tasks)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._executor.shutdown()

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start one stable request, reconcile an exact repeat, or fail closed."""
        digest = _digest(request)
        async with self._lock:
            known = self._runs.get(request.run_id)
            if known is not None:
                if known.digest != digest:
                    raise ControlError(ControlRefusal.RUN_CONFLICT)
                return known.status
            active = sum(record.status.state in _ACTIVE_STATES for record in self._runs.values())
            if active >= self._capacity:
                raise ControlError(ControlRefusal.CAPACITY_EXCEEDED)
            record = _RunRecord(digest, _status(request, "ACCEPTED"), asyncio.Event())
            self._runs[request.run_id] = record
            record.task = asyncio.create_task(self._execute(request, record))
            return record.status

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Return one stable run status or a closed not-found refusal."""
        async with self._lock:
            try:
                return self._runs[run_id].status
            except KeyError as error:
                raise ControlError(ControlRefusal.RUN_NOT_FOUND) from error

    async def cancel(self, request: FleetControlCancelRequest) -> FleetControlRunStatus:
        """Signal and await one exact mission/run binding within the shared bound."""
        async with self._lock:
            try:
                record = self._runs[request.run_id]
            except KeyError as error:
                raise ControlError(ControlRefusal.RUN_NOT_FOUND) from error
            if record.status.mission_id != request.mission_id:
                raise ControlError(ControlRefusal.PATH_BODY_MISMATCH)
            record.cancelled.set()
            task = record.task
        if task is not None:
            try:
                async with asyncio.timeout(self._cancellation_timeout):
                    await asyncio.shield(task)
            except TimeoutError as error:
                raise ControlError(ControlRefusal.CANCELLATION_NOT_ESTABLISHED) from error
        return await self.status(request.run_id)

    async def _execute(self, request: FleetControlStartRequest, record: _RunRecord) -> None:
        """Run one task, containing failures as an observable terminal status."""
        async with self._lock:
            record.status = _status(request, "RUNNING")
        try:
            result = await self._executor.execute(request, record.cancelled)
            identities_match = (
                result.run_id == request.run_id and result.mission_id == request.scenario.mission_id
            )
            record.status = result if identities_match else _status(request, "FAILED")
        except Exception:
            record.status = _status(request, "FAILED")
