"""Concrete Fleet process settings and broker-exhaustion supervision."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping

import pytest
from aerial_rescue_fleet_simulator.console import (
    ListenerOptions,
    SettingsError,
    fleet_drone_ids,
    serve_control,
)
from aerial_rescue_fleet_simulator.control_plane.runtime import ServerSettings
from aerial_rescue_fleet_simulator.control_plane.wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from fastapi import FastAPI

pytestmark = [pytest.mark.unit]


class InertControl:
    """A complete control port unused by the fake ASGI server."""

    ready = True

    async def startup(self) -> None:
        """Perform no startup work."""

    async def shutdown(self) -> None:
        """Perform no shutdown work."""

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Remain unavailable outside an HTTP request."""
        del request
        raise AssertionError

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Remain unavailable outside an HTTP request."""
        del run_id
        raise AssertionError

    async def cancel(self, request: FleetControlCancelRequest) -> FleetControlRunStatus:
        """Remain unavailable outside an HTTP request."""
        del request
        raise AssertionError


class FakeExhaustion:
    """A controllable executor terminal signal."""

    def __init__(self) -> None:
        """Begin healthy with a zero exit status."""
        self.exhausted = asyncio.Event()
        self.exit_status = 0

    async def wait_for_exhaustion(self) -> None:
        """Wait until the test declares SDK recovery exhausted."""
        await self.exhausted.wait()

    def fail(self) -> None:
        """Declare terminal broker recovery and a nonzero result."""
        self.exit_status = 1
        self.exhausted.set()


class FakeServer:
    """An ASGI server that exits only when supervision requests it."""

    def __init__(self, application: FastAPI, options: ListenerOptions) -> None:
        """Retain exact listener inputs and begin serving."""
        self.application = application
        self.options = options
        self.started = asyncio.Event()
        self._should_exit = False
        self._exit_requested = asyncio.Event()

    @property
    def should_exit(self) -> bool:
        """Return whether supervision requested graceful exit."""
        return self._should_exit

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        """Wake the fake server on the terminal transition."""
        self._should_exit = value
        if value:
            self._exit_requested.set()

    async def serve(self) -> None:
        """Wait for one graceful-exit request."""
        self.started.set()
        await self._exit_requested.wait()


class FakeServerFactory:
    """Construct and retain one fake ASGI server."""

    def __init__(self) -> None:
        """Begin without construction."""
        self.server: FakeServer | None = None

    def __call__(self, application: FastAPI, options: ListenerOptions) -> FakeServer:
        """Return one observable fake server."""
        self.server = FakeServer(application, options)
        return self.server


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_broker_exhaustion_requests_graceful_http_exit_and_returns_nonzero(self) -> None:
        # Arrange
        exhaustion = FakeExhaustion()
        factory = FakeServerFactory()
        settings = ServerSettings(
            host="fleet-simulator:8082",
            bearer="A" * 43,
            startup_timeout_seconds=5,
            shutdown_timeout_seconds=15,
        )
        serving = asyncio.create_task(
            serve_control(InertControl(), settings, exhaustion, server_factory=factory)
        )
        while factory.server is None:
            await asyncio.sleep(0)
        await factory.server.started.wait()

        # Act
        exhaustion.fail()
        status = await serving

        # Assert
        self.assertEqual(status, 1)
        self.assertTrue(factory.server.should_exit)
        self.assertEqual(factory.server.options.port, 8082)

    async def test_configured_roster_is_exact_unique_and_secret_safe_on_refusal(self) -> None:
        # Arrange
        accepted: Mapping[str, str] = {
            "FLEET_DRONE_IDS": "drone-02,drone-01",
        }
        refused: tuple[dict[str, str], ...] = (
            {},
            {"FLEET_DRONE_IDS": ""},
            {"FLEET_DRONE_IDS": "drone-01,drone-01"},
            {"FLEET_DRONE_IDS": "drone-01,,drone-02"},
        )
        failures: list[str] = []

        # Act
        roster = fleet_drone_ids(accepted)
        for environment in refused:
            with pytest.raises(SettingsError) as raised:
                fleet_drone_ids(environment)
            failures.append(str(raised.value))

        # Assert
        self.assertEqual(roster, ("drone-01", "drone-02"))
        self.assertEqual(len(failures), len(refused))
        self.assertTrue(all("drone-01" not in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
