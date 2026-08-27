"""Owned mixed-session, recovery, and reverse-shutdown broker supervisor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

from aerial_rescue_dashboard_api.lifecycle import Dependency, RuntimeReadiness
from aerial_rescue_dashboard_api.messaging.broker_runtime import (
    DashboardDataPlane,
    DashboardServingSession,
    ServePorts,
    serve,
)


class SupervisorRefusal(Enum):
    """Why the live broker/store epoch cannot become usable."""

    CONFIGURATION = "dashboard broker supervisor settings are invalid"
    RECOVERY = "dashboard durable recovery did not complete"
    NOT_STARTED = "dashboard broker supervisor is not started"


class DashboardSupervisorError(RuntimeError):
    """A redacted supervisor refusal."""

    def __init__(self, refusal: SupervisorRefusal) -> None:
        """Retain only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


class OwnedDashboardSession(DashboardServingSession, Protocol):
    """The mixed receive session plus its one reverse-order close capability."""

    def close(self) -> None:
        """Close receivers, publisher, and the owned connection."""
        ...


class ManagedDashboardPlane(Protocol):
    """The data-plane operations the process supervisor sequences."""

    async def recover(self) -> bool:
        """Recover audit, inbox, and outbox before broker readiness."""
        ...

    def activate_mission(self, mission_id: str) -> None:
        """Select one recorder-authoritative mission and remove readiness."""
        ...


@dataclass(frozen=True, slots=True)
class SupervisorSettings:
    """The console-owned finite receive window."""

    receive_timeout_milliseconds: int

    def __post_init__(self) -> None:
        """Refuse a negative or boolean wait before a session is opened."""
        if (
            type(self.receive_timeout_milliseconds) is not int
            or self.receive_timeout_milliseconds < 0
        ):
            raise DashboardSupervisorError(SupervisorRefusal.CONFIGURATION)


@dataclass(frozen=True, slots=True)
class SupervisorPorts:
    """Lazy external capabilities owned for exactly one supervisor epoch."""

    open_session: Callable[[], OwnedDashboardSession]
    plane: Callable[[OwnedDashboardSession], ManagedDashboardPlane]
    readiness: RuntimeReadiness
    close_store: Callable[[], Awaitable[None]]
    pause: Callable[[], Awaitable[None] | None]


class DashboardBrokerSupervisor:
    """Own exactly one mixed Solace session and its SQLAlchemy resource epoch."""

    def __init__(
        self,
        *,
        ports: SupervisorPorts,
        settings: SupervisorSettings,
    ) -> None:
        """Retain lazy constructors without opening a socket, task, or database session."""
        self._open_session = ports.open_session
        self._plane_factory = ports.plane
        self._readiness = ports.readiness
        self._close_store = ports.close_store
        self._pause = ports.pause
        self._settings = settings
        self._session: OwnedDashboardSession | None = None
        self._plane: ManagedDashboardPlane | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._exit_status = 0
        self._started = asyncio.Event()

    @property
    def ready(self) -> bool:
        """Return true only while session, application recovery, and loop are healthy."""
        session = self._session
        task = self._task
        return (
            session is not None
            and task is not None
            and not task.done()
            and session.readiness.is_ready()
        )

    @property
    def exit_status(self) -> int:
        """Return nonzero only after active-session recovery exhausted."""
        return self._exit_status

    async def startup(self) -> None:
        """Open once, complete durable recovery, then start bounded mixed receive."""
        session = self._open_session()
        self._session = session
        plane = self._plane_factory(session)
        self._plane = plane
        try:
            recovered = await plane.recover()
            _require_recovered(recovered)
            self._running = True
            self._task = asyncio.create_task(self._run(session, plane))
            self._started.set()
        except BaseException:
            session.close()
            self._session = None
            self._plane = None
            await self._close_store()
            raise
        self._readiness.set_dependency(Dependency.STORE, ready=True)
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=True)

    async def activate_mission(self, mission_id: str) -> None:
        """Recover a new mission checkpoint before restoring broker readiness."""
        plane = self._plane
        if plane is None:
            raise DashboardSupervisorError(SupervisorRefusal.NOT_STARTED)
        plane.activate_mission(mission_id)
        recovered = await plane.recover()
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=recovered)
        if not recovered:
            raise DashboardSupervisorError(SupervisorRefusal.RECOVERY)

    async def wait_for_exhaustion(self) -> None:
        """Wait until the receive loop stops because recovery is terminal."""
        await self._started.wait()
        task = self._task
        if task is None:
            raise DashboardSupervisorError(SupervisorRefusal.NOT_STARTED)
        await task

    async def shutdown(self) -> None:
        """Stop the loop, then close session before disposing the SQLAlchemy pool."""
        self._running = False
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=False)
        self._readiness.set_dependency(Dependency.STORE, ready=False)
        failures = (
            await self._stop_task(),
            self._close_session(),
            await self._dispose_store(),
        )
        self._task = None
        self._session = None
        self._plane = None
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=False)
        self._readiness.set_dependency(Dependency.STORE, ready=False)
        first = next((failure for failure in failures if failure is not None), None)
        if first is not None:
            raise first

    async def _run(
        self,
        session: OwnedDashboardSession,
        plane: ManagedDashboardPlane,
    ) -> None:
        report = await serve(
            session,
            cast("DashboardDataPlane", plane),
            ServePorts(
                running=lambda: self._running,
                readiness=self._broker_readiness,
                pause=self._pause,
                receive_timeout_milliseconds=self._settings.receive_timeout_milliseconds,
            ),
        )
        self._exit_status = report.exit_status
        if report.exit_status:
            self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=False)

    def _broker_readiness(self, ready: bool) -> None:
        self._readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=ready)

    async def _stop_task(self) -> BaseException | None:
        task = self._task
        if task is None:
            return None
        try:
            await task
        except BaseException as error:
            return error
        return None

    def _close_session(self) -> BaseException | None:
        session = self._session
        if session is None:
            return None
        try:
            session.close()
        except BaseException as error:
            return error
        return None

    async def _dispose_store(self) -> BaseException | None:
        try:
            await self._close_store()
        except BaseException as error:
            return error
        return None


def _require_recovered(recovered: bool) -> None:
    if not recovered:
        raise DashboardSupervisorError(SupervisorRefusal.RECOVERY)
