"""Fleet private-listener console and explicit dependency composition seam."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from ipaddress import IPv4Address
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aerial_rescue_fleet_simulator.console as console_module
import pytest
from aerial_rescue_fleet_simulator.console import (
    FleetStoreResources,
    ListenerOptions,
    SettingsError,
    SettingsRefusal,
    broker_endpoint,
    default_process_runtime,
    production_bounds,
    run_console,
    serve_control,
    settings_from_environment,
)
from aerial_rescue_fleet_simulator.control_plane.runtime import ServerSettings
from aerial_rescue_fleet_simulator.control_plane.wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from fastapi import FastAPI
from starlette.routing import Route

pytestmark = [pytest.mark.unit]

HOST = "fleet-simulator:8082"
BEARER = "A" * 43


class InertControl:
    """A structurally complete control dependency the console never invokes itself."""

    ready = True

    async def startup(self) -> None:
        """Perform no work."""

    async def shutdown(self) -> None:
        """Perform no work."""

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Remain unavailable outside a request test."""
        del request
        raise AssertionError

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Remain unavailable outside a request test."""
        del run_id
        raise AssertionError

    async def cancel(self, request: FleetControlCancelRequest) -> FleetControlRunStatus:
        """Remain unavailable outside a request test."""
        del request
        raise AssertionError


class RecordingRunner:
    def __init__(self) -> None:
        """Begin without a listener invocation."""
        self.calls: list[tuple[FastAPI, ListenerOptions]] = []

    def __call__(self, application: FastAPI, options: ListenerOptions) -> None:
        """Record one composed listener."""
        self.calls.append((application, options))


class CompletingServer:
    """An injected ASGI server that completes without broker exhaustion."""

    def __init__(self, application: FastAPI, options: ListenerOptions) -> None:
        """Retain the exact listener inputs and begin unrequested."""
        self.application = application
        self.options = options
        self.should_exit = False
        self.served = False

    async def serve(self) -> None:
        """Complete one ordinary listener epoch."""
        self.served = True


class HealthyExhaustion:
    """Remain healthy while an ordinary ASGI server stops."""

    exit_status = 0

    async def wait_for_exhaustion(self) -> None:
        """Wait forever unless supervision cancels this observation."""
        await asyncio.Event().wait()


class ConsoleTests(unittest.TestCase):
    def test_console_builds_only_the_private_unpublished_listener(self) -> None:
        # Arrange
        runner = RecordingRunner()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        secret = Path(temporary.name) / "fleet-bearer"
        secret.write_text(BEARER, encoding="ascii")
        environment = {
            "FLEET_CONTROL_HOST": HOST,
            "FLEET_CONTROL_BEARER_FILE": str(secret),
        }

        # Act
        run_console(InertControl(), environment, runner=runner)

        # Assert
        application, options = runner.calls[0]
        self.assertEqual(
            options,
            ListenerOptions(str(IPv4Address(0)), 8082, False, False, False),
        )
        self.assertEqual(
            {route.path for route in application.routes if isinstance(route, Route)},
            {
                "/healthz",
                "/readyz",
                "/internal/v1/runs",
                "/internal/v1/runs/{run_id}",
                "/internal/v1/runs/{run_id}/cancel",
            },
        )

    def test_settings_refuse_missing_symlinked_weak_and_oversized_bearers(self) -> None:
        # Arrange
        refusals: list[SettingsRefusal] = []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        weak = root / "weak"
        weak.write_text("short", encoding="ascii")
        large = root / "large"
        large.write_text("A" * 130, encoding="ascii")
        link = root / "link"
        link.symlink_to(weak)
        cases: tuple[tuple[dict[str, str], SettingsRefusal], ...] = (
            ({}, SettingsRefusal.MISSING),
            (
                {"FLEET_CONTROL_HOST": HOST, "FLEET_CONTROL_BEARER_FILE": str(link)},
                SettingsRefusal.MATERIAL_UNAVAILABLE,
            ),
            (
                {"FLEET_CONTROL_HOST": HOST, "FLEET_CONTROL_BEARER_FILE": str(weak)},
                SettingsRefusal.INVALID,
            ),
            (
                {"FLEET_CONTROL_HOST": HOST, "FLEET_CONTROL_BEARER_FILE": str(large)},
                SettingsRefusal.MATERIAL_UNAVAILABLE,
            ),
        )

        # Act
        for environment, _expected in cases:
            with pytest.raises(SettingsError) as raised:
                settings_from_environment(environment)
            refusals.append(raised.value.refusal)

        # Assert
        self.assertEqual(refusals, [expected for _environment, expected in cases])

    def test_settings_strip_one_newline_and_refuse_nonascii_or_unreadable_material(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        accepted = root / "accepted"
        accepted.write_bytes(f"{BEARER}\n".encode("ascii"))
        nonascii = root / "nonascii"
        nonascii.write_bytes(b"A" * 42 + b"\xff")
        base = {"FLEET_CONTROL_HOST": HOST}

        # Act
        settings = settings_from_environment({**base, "FLEET_CONTROL_BEARER_FILE": str(accepted)})
        with pytest.raises(SettingsError) as nonascii_error:
            settings_from_environment({**base, "FLEET_CONTROL_BEARER_FILE": str(nonascii)})
        with (
            patch.object(Path, "open", side_effect=OSError("unreadable")),
            pytest.raises(SettingsError) as unreadable_error,
        ):
            settings_from_environment({**base, "FLEET_CONTROL_BEARER_FILE": str(accepted)})

        # Assert
        self.assertEqual(settings.server.bearer, BEARER)
        self.assertEqual(nonascii_error.value.refusal, SettingsRefusal.INVALID)
        self.assertEqual(unreadable_error.value.refusal, SettingsRefusal.MATERIAL_UNAVAILABLE)

    def test_invalid_listener_identity_is_mapped_to_a_closed_settings_refusal(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        secret = Path(temporary.name) / "fleet-bearer"
        secret.write_text(BEARER, encoding="ascii")

        # Act
        with pytest.raises(SettingsError) as raised:
            settings_from_environment(
                {
                    "FLEET_CONTROL_HOST": "INVALID HOST",
                    "FLEET_CONTROL_BEARER_FILE": str(secret),
                }
            )

        # Assert
        self.assertEqual(raised.value.refusal, SettingsRefusal.INVALID)

    def test_broker_and_directory_settings_are_trimmed_defaulted_and_fail_closed(self) -> None:
        # Arrange
        broker_environment = {
            "SOLACE_BROKER_URL": " tcps://broker:55443 ",
            "SOLACE_BROKER_VPN": " default ",
            "TRUST_STORE": " /run/ca.pem ",
        }

        # Act
        endpoint = broker_endpoint(broker_environment)
        defaults = default_process_runtime({})
        overridden = default_process_runtime(
            {
                "AERIAL_RESCUE_DEPLOY_DIR": " /deploy/runtime ",
                "AERIAL_RESCUE_SCHEMA_DIR": " /schemas/runtime ",
            }
        )
        with pytest.raises(SettingsError) as blank_broker:
            broker_endpoint({**broker_environment, "SOLACE_BROKER_VPN": "   "})
        with pytest.raises(SettingsError) as blank_directory:
            default_process_runtime({"AERIAL_RESCUE_SCHEMA_DIR": "   "})

        # Assert
        self.assertEqual(
            (endpoint.url, endpoint.vpn, endpoint.trust_store),
            ("tcps://broker:55443", "default", "/run/ca.pem"),
        )
        self.assertEqual(
            (defaults.deploy, defaults.schema_directory),
            (Path("deploy"), Path("schemas")),
        )
        self.assertEqual(
            (overridden.deploy, overridden.schema_directory),
            (Path("/deploy/runtime"), Path("/schemas/runtime")),
        )
        self.assertEqual(blank_broker.value.refusal, SettingsRefusal.MISSING)
        self.assertEqual(blank_directory.value.refusal, SettingsRefusal.MISSING)

    def test_production_store_bounds_are_complete_and_bounded(self) -> None:
        # Arrange
        expected_members = (
            "pool_size",
            "pool_overflow",
            "checkout_timeout_seconds",
            "connect_timeout_seconds",
            "connect_retries",
            "statement_timeout_milliseconds",
            "lock_timeout_milliseconds",
            "idle_in_transaction_timeout_milliseconds",
            "shutdown_grace_seconds",
        )

        # Act
        bounds = production_bounds()

        # Assert
        self.assertTrue(all(hasattr(bounds, member) for member in expected_members))
        self.assertGreater(bounds.pool_size, 0)
        self.assertGreater(bounds.shutdown_grace_seconds, 0)


class AsyncConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_resources_dispose_the_engine_exactly_once(self) -> None:
        # Arrange
        resources = FleetStoreResources(
            MagicMock(),
            production_bounds(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
        dispose = AsyncMock()

        # Act
        with patch.object(console_module, "close", dispose):
            await resources.close()
            await resources.close()

        # Assert
        self.assertTrue(resources.closed)
        dispose.assert_awaited_once_with(
            resources.engine,
            resources.bounds.shutdown_grace_seconds,
        )

    async def test_ordinary_http_completion_cancels_terminal_supervision(self) -> None:
        # Arrange
        server: CompletingServer | None = None

        def factory(application: FastAPI, options: ListenerOptions) -> CompletingServer:
            nonlocal server
            server = CompletingServer(application, options)
            return server

        private_settings = ServerSettings(HOST, BEARER, 5, 15)

        # Act
        status = await serve_control(
            InertControl(),
            private_settings,
            HealthyExhaustion(),
            server_factory=factory,
        )

        # Assert
        self.assertEqual(status, 0)
        self.assertIsNotNone(server)
        self.assertTrue(server.served if server is not None else False)
        self.assertFalse(server.should_exit if server is not None else True)


if __name__ == "__main__":
    unittest.main()
