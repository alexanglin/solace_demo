"""Owned dashboard mixed-session and broker-supervisor tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from aerial_rescue_broker.messaging import BrokerLifecycle
from aerial_rescue_dashboard_api.lifecycle import Dependency, RunMode, RuntimeReadiness
from aerial_rescue_dashboard_api.messaging.supervisor import (
    DashboardBrokerSupervisor,
    SupervisorPorts,
    SupervisorSettings,
)

_EXPECTED_RECOVERIES = 2


@dataclass
class _Session:
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)
    receiver_names: tuple[str, ...] = ()
    closed: int = 0
    receive_calls: int = 0

    def __post_init__(self) -> None:
        self.readiness.connected()

    def rebind_complete(self) -> None:
        self.readiness.mark_ready()

    def receive_direct(self, _timeout_milliseconds: int, /) -> None:
        self.receive_calls += 1

    def receive_guaranteed(self, _name: str, _timeout_milliseconds: int, /) -> None:
        return None

    def close(self) -> None:
        self.closed += 1
        self.readiness.closed()


@dataclass
class _Plane:
    session: _Session
    recoveries: int = 0
    publications: int = 0
    missions: list[str] = field(default_factory=list)

    async def recover(self) -> bool:
        self.recoveries += 1
        self.session.rebind_complete()
        return True

    async def publish_staged(self) -> None:
        self.publications += 1

    def activate_mission(self, mission_id: str) -> None:
        self.missions.append(mission_id)
        self.session.readiness.recovery_required()


@pytest.mark.asyncio
async def test_supervisor_recovers_before_readiness_and_closes_session_before_store() -> None:
    # Arrange
    session = _Session()
    plane = _Plane(session)
    readiness = RuntimeReadiness(RunMode.DEGRADED_LIVE)
    readiness.set_dependency(Dependency.SCENARIO_CONTROL, ready=True)
    readiness.begin_startup()
    shutdown_order: list[str] = []
    supervisor = DashboardBrokerSupervisor(
        ports=SupervisorPorts(
            open_session=lambda: session,
            plane=lambda _session: plane,
            readiness=readiness,
            close_store=lambda: _record_store_close(shutdown_order),
            pause=lambda: asyncio.sleep(0),
        ),
        settings=SupervisorSettings(receive_timeout_milliseconds=0),
    )

    # Act
    exhaustion = asyncio.create_task(supervisor.wait_for_exhaustion())
    await asyncio.sleep(0)
    await supervisor.startup()
    readiness.activate()
    await supervisor.activate_mission("mission-synthetic-0001")
    await asyncio.sleep(0)
    await supervisor.shutdown()
    await exhaustion

    # Assert
    assert plane.recoveries >= _EXPECTED_RECOVERIES
    assert plane.missions == ["mission-synthetic-0001"]
    assert session.receive_calls > 0
    assert session.closed == 1
    assert shutdown_order == ["store"]
    assert readiness.assess(RunMode.DEGRADED_LIVE).reasons == (
        "store-unavailable",
        "broker-delivery-unavailable",
    )


async def _record_store_close(order: list[str]) -> None:
    order.append("store")
