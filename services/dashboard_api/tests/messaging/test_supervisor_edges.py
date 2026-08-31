"""Broker startup, activation, exhaustion, and shutdown failure coverage."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

import pytest
from aerial_rescue_broker.messaging import BrokerLifecycle
from aerial_rescue_dashboard_api.lifecycle import RunMode, RuntimeReadiness
from aerial_rescue_dashboard_api.messaging import broker_runtime as broker_module
from aerial_rescue_dashboard_api.messaging import supervisor as supervisor_module


@dataclass
class _Session:
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)
    receiver_names: tuple[str, ...] = ()
    close_failure: BaseException | None = None
    closed: int = 0

    def __post_init__(self) -> None:
        self.readiness.connected()

    def rebind_complete(self) -> None:
        self.readiness.mark_ready()

    def receive_direct(self, _timeout_milliseconds: int, /) -> None:
        return None

    def receive_guaranteed(self, _name: str, _timeout_milliseconds: int, /) -> None:
        return None

    def close(self) -> None:
        self.closed += 1
        self.readiness.closed()
        if self.close_failure is not None:
            raise self.close_failure


@dataclass
class _Plane:
    session: _Session
    recoveries: list[bool]
    missions: list[str] = field(default_factory=list)

    async def recover(self) -> bool:
        recovered = self.recoveries.pop(0)
        if recovered:
            self.session.rebind_complete()
        return recovered

    def activate_mission(self, mission_id: str) -> None:
        self.missions.append(mission_id)
        self.session.readiness.recovery_required()


def _supervisor(
    session: _Session,
    plane: _Plane,
    close_store: Callable[[], Awaitable[None]],
) -> supervisor_module.DashboardBrokerSupervisor:
    return supervisor_module.DashboardBrokerSupervisor(
        ports=supervisor_module.SupervisorPorts(
            open_session=lambda: cast("supervisor_module.OwnedDashboardSession", session),
            plane=lambda _session: cast("supervisor_module.ManagedDashboardPlane", plane),
            readiness=RuntimeReadiness(RunMode.DEGRADED_LIVE),
            close_store=close_store,
            pause=lambda: asyncio.sleep(0),
        ),
        settings=supervisor_module.SupervisorSettings(0),
    )


@dataclass
class _RaisingPlane:
    """A plane whose startup recovery succeeds and whose serving cycle then fails."""

    session: _Session

    async def recover(self) -> bool:
        self.session.rebind_complete()
        return True

    def activate_mission(self, mission_id: str) -> None:
        del mission_id
        self.session.readiness.recovery_required()

    async def publish_staged(self) -> None:
        message = "outbox drain refused"
        raise RuntimeError(message)


@pytest.mark.asyncio
async def test_a_serving_task_that_raises_is_reported_rather_than_silently_dropped() -> None:
    """A data plane that dies must name what ended it; nobody awaits this task."""
    # Arrange
    session = _Session()
    plane = _RaisingPlane(session)
    reported: list[BaseException] = []
    supervisor = supervisor_module.DashboardBrokerSupervisor(
        ports=supervisor_module.SupervisorPorts(
            open_session=lambda: cast("supervisor_module.OwnedDashboardSession", session),
            plane=lambda _session: cast("supervisor_module.ManagedDashboardPlane", plane),
            readiness=RuntimeReadiness(RunMode.DEGRADED_LIVE),
            close_store=_no_store,
            pause=lambda: asyncio.sleep(0),
            report=reported.append,
        ),
        settings=supervisor_module.SupervisorSettings(0),
    )

    # Act
    await supervisor.startup()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Assert
    assert [type(failure).__name__ for failure in reported] == ["RuntimeError"]


async def _no_store() -> None:
    return None


@pytest.mark.parametrize("timeout", [-1, True])
def test_supervisor_settings_refuse_negative_or_boolean_receive_windows(timeout: int) -> None:
    # Arrange
    candidate = timeout

    # Act
    with pytest.raises(supervisor_module.DashboardSupervisorError) as captured:
        supervisor_module.SupervisorSettings(candidate)

    # Assert
    assert captured.value.refusal is supervisor_module.SupervisorRefusal.CONFIGURATION


@pytest.mark.asyncio
async def test_startup_recovery_failure_closes_session_and_store_without_readiness() -> None:
    # Arrange
    session = _Session()
    plane = _Plane(session, [False])
    store_closes = 0

    async def close_store() -> None:
        nonlocal store_closes
        store_closes += 1

    broker = _supervisor(session, plane, close_store)

    # Act
    with pytest.raises(supervisor_module.DashboardSupervisorError) as captured:
        await broker.startup()

    # Assert
    assert captured.value.refusal is supervisor_module.SupervisorRefusal.RECOVERY
    assert broker.ready is False
    assert broker.exit_status == 0
    assert session.closed == 1
    assert store_closes == 1


@pytest.mark.asyncio
async def test_activate_and_exhaustion_wait_refuse_before_startup() -> None:
    # Arrange
    session = _Session()
    plane = _Plane(session, [True])

    async def close_store() -> None:
        return None

    broker = _supervisor(session, plane, close_store)
    broker._started.set()

    # Act
    with pytest.raises(supervisor_module.DashboardSupervisorError) as activation:
        await broker.activate_mission("mission-synthetic-0001")
    with pytest.raises(supervisor_module.DashboardSupervisorError) as exhaustion:
        await broker.wait_for_exhaustion()

    # Assert
    assert activation.value.refusal is supervisor_module.SupervisorRefusal.NOT_STARTED
    assert exhaustion.value.refusal is supervisor_module.SupervisorRefusal.NOT_STARTED


@pytest.mark.asyncio
async def test_mission_activation_refuses_when_checkpoint_recovery_does_not_complete() -> None:
    # Arrange
    session = _Session()
    plane = _Plane(session, [True, False])

    async def close_store() -> None:
        return None

    broker = _supervisor(session, plane, close_store)
    await broker.startup()

    # Act
    with pytest.raises(supervisor_module.DashboardSupervisorError) as captured:
        await broker.activate_mission("mission-synthetic-0001")
    await broker.shutdown()

    # Assert
    assert captured.value.refusal is supervisor_module.SupervisorRefusal.RECOVERY
    assert plane.missions == ["mission-synthetic-0001"]
    assert broker.ready is False


@pytest.mark.asyncio
async def test_shutdown_attempts_every_resource_and_reraises_the_task_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    task_failure = RuntimeError("task failed")
    session_failure = RuntimeError("session failed")
    store_failure = RuntimeError("store failed")
    session = _Session(close_failure=session_failure)
    plane = _Plane(session, [True])
    store_closes = 0

    async def close_store() -> None:
        nonlocal store_closes
        store_closes += 1
        raise store_failure

    async def fail_serve(
        _session: object,
        _plane: object,
        _ports: object,
    ) -> broker_module.ServeReport:
        raise task_failure

    monkeypatch.setattr(supervisor_module, "serve", fail_serve)
    broker = _supervisor(session, plane, close_store)
    await broker.startup()
    await asyncio.sleep(0)

    # Act
    with pytest.raises(RuntimeError) as captured:
        await broker.shutdown()

    # Assert
    assert captured.value is task_failure
    assert session.closed == 1
    assert store_closes == 1
    assert broker.ready is False


@pytest.mark.asyncio
async def test_terminal_receive_report_sets_nonzero_status_and_clears_broker_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    session = _Session()
    plane = _Plane(session, [True])

    async def close_store() -> None:
        return None

    async def exhaust(
        _session: object,
        _plane: object,
        _ports: object,
    ) -> broker_module.ServeReport:
        return broker_module.ServeReport(1)

    monkeypatch.setattr(supervisor_module, "serve", exhaust)
    broker = _supervisor(session, plane, close_store)

    # Act
    await broker.startup()
    await broker.wait_for_exhaustion()
    status = broker.exit_status
    await broker.shutdown()

    # Assert
    assert status == 1
    assert broker.ready is False


@pytest.mark.asyncio
async def test_shutdown_without_a_started_epoch_is_idempotent_except_for_store_disposal() -> None:
    # Arrange
    session = _Session()
    plane = _Plane(session, [True])
    store_closes = 0

    async def close_store() -> None:
        nonlocal store_closes
        store_closes += 1

    broker = _supervisor(session, plane, close_store)

    # Act
    await broker.shutdown()

    # Assert
    assert store_closes == 1
    assert session.closed == 0
    assert broker.ready is False
