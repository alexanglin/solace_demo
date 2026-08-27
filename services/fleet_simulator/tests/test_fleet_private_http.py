"""Fleet private HTTP admission order and operation mapping."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast
from unittest.mock import patch

import httpx
import pytest
from aerial_rescue_fleet_simulator.control import FleetControl
from aerial_rescue_fleet_simulator.control_wire import (
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from aerial_rescue_fleet_simulator.http import (
    CONTROL_PORT,
    FleetHttpConfig,
    create_app,
    serve,
)
from aerial_rescue_fleet_simulator.service import ServeReport
from fastapi import FastAPI

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
START_BYTES: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/fleet-control-start-request/baseline.json"
).read_bytes()
AUTH_VALUE: Final = "fleet-secret-000000000000000000000000000000000000"
HOST: Final = "fleet-simulator:8082"


class _NeverExecutor:
    """A worker that remains cancellable without completing on its own."""

    def __call__(
        self, request: FleetControlStartRequest, cancellation: threading.Event
    ) -> ServeReport:
        """Block until the control test cancels the run."""
        cancellation.wait(1)
        raise RuntimeError(request.run_id)


def _app() -> FastAPI:
    """Return the fleet app over a bounded in-memory controller."""
    control = FleetControl(_NeverExecutor(), maximum_runs=2, cancellation_wait_seconds=1)
    config = FleetHttpConfig(expected_host=HOST, bearer_secret=AUTH_VALUE)
    return create_app(control, config)


async def _exchange(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: httpx.Headers | dict[str, str],
    content: bytes | str | None = None,
) -> httpx.Response:
    """Send one request directly through the ASGI boundary."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://fleet-simulator:8082"
    ) as client:
        return await client.request(method, path, headers=headers, content=content)


def _headers(
    *,
    host: str = HOST,
    authorization_value: str = AUTH_VALUE,
    media_type: str = "application/json",
) -> dict[str, str]:
    """Return one private request's admission headers."""
    return {
        "host": host,
        "authorization": f"Bearer {authorization_value}",
        "content-type": media_type,
    }


def _error_code(response_body: bytes) -> str:
    """Return the strict refusal code from one response."""
    return cast("str", json.loads(response_body)["errorCode"])


class FleetHttpAdmissionTests(unittest.TestCase):
    def test_body_requests_refuse_in_host_auth_media_bound_canonical_schema_order(self) -> None:
        # Arrange
        oversized = b"{" + b" " * (256 * 1024)
        cases = (
            (
                _headers(
                    host="not-fleet:8082",
                    authorization_value="wrong",
                    media_type="text/plain",
                ),
                b"{}",
            ),
            (_headers(authorization_value="wrong", media_type="text/plain"), b"{}"),
            (_headers(media_type="text/plain"), b"{}"),
            (_headers(media_type="application/json; charset=latin-1"), b"{}"),
            (_headers(), oversized),
            (_headers(), b'{"controlVersion":1,"controlVersion":1}'),
            (_headers(), b"{}"),
        )
        expected = (
            "HOST_INVALID",
            "AUTHENTICATION_FAILED",
            "UNSUPPORTED_MEDIA_TYPE",
            "UNSUPPORTED_MEDIA_TYPE",
            "BODY_TOO_LARGE",
            "CANONICAL_JSON_INVALID",
            "SCHEMA_INVALID",
        )
        app = _app()

        # Act
        actual = tuple(
            _error_code(
                asyncio.run(
                    _exchange(
                        app,
                        "POST",
                        "/internal/v1/runs",
                        headers=headers,
                        content=content,
                    )
                ).content
            )
            for headers, content in cases
        )

        # Assert
        self.assertEqual(expected, actual)

    def test_path_binding_precedes_operation_and_reads_authenticate_before_lookup(self) -> None:
        # Arrange
        cancel = {
            "controlVersion": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
        }
        app = _app()

        # Act
        mismatch = asyncio.run(
            _exchange(
                app,
                "POST",
                "/internal/v1/runs/run-other/cancel",
                headers=_headers(),
                content=json.dumps(cancel),
            )
        )
        unauthenticated = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/runs/run-missing",
                headers={"host": HOST, "authorization": "Bearer wrong"},
            )
        )
        missing = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/runs/run-missing",
                headers={"host": HOST, "authorization": f"Bearer {AUTH_VALUE}"},
            )
        )

        # Assert
        self.assertEqual("PATH_BODY_MISMATCH", _error_code(mismatch.content))
        self.assertEqual("AUTHENTICATION_FAILED", _error_code(unauthenticated.content))
        self.assertEqual("RUN_NOT_FOUND", _error_code(missing.content))

    def test_start_returns_canonical_status_and_the_private_port_is_8082(self) -> None:
        # Arrange
        expected_run = cast("str", json.loads(START_BYTES)["runId"])
        app = _app()

        # Act
        response = asyncio.run(
            _exchange(
                app,
                "POST",
                "/internal/v1/runs",
                headers=_headers(),
                content=START_BYTES,
            )
        )
        document = json.loads(response.content)

        # Assert
        self.assertEqual(202, response.status_code)
        self.assertEqual(expected_run, document["runId"])
        self.assertEqual("application/json", response.headers["content-type"])
        self.assertEqual(8082, CONTROL_PORT)

    def test_status_cancel_duplicate_bearer_and_internal_failure_are_closed(self) -> None:
        # Arrange
        app = _app()
        cancel = {
            "controlVersion": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
        }
        duplicate = httpx.Headers(
            [
                ("host", HOST),
                ("authorization", f"Bearer {AUTH_VALUE}"),
                ("authorization", f"Bearer {AUTH_VALUE}"),
            ]
        )

        class FailingOperations:
            def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
                raise AssertionError(request.run_id)

            def status(self, run_id: str) -> FleetControlRunStatus:
                message = f"private detail {run_id}"
                raise RuntimeError(message)

            def cancel(self, run_id: str, mission_id: str) -> FleetControlRunStatus:
                raise AssertionError((run_id, mission_id))

        failing = create_app(
            FailingOperations(),
            FleetHttpConfig(expected_host=HOST, bearer_secret=AUTH_VALUE),
        )

        # Act
        asyncio.run(
            _exchange(app, "POST", "/internal/v1/runs", headers=_headers(), content=START_BYTES)
        )
        status = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/runs/run-synthetic-0001",
                headers={"host": HOST, "authorization": f"Bearer {AUTH_VALUE}"},
            )
        )
        refused = asyncio.run(
            _exchange(app, "GET", "/internal/v1/runs/run-synthetic-0001", headers=duplicate)
        )
        cancelled = asyncio.run(
            _exchange(
                app,
                "POST",
                "/internal/v1/runs/run-synthetic-0001/cancel",
                headers=_headers(),
                content=json.dumps(cancel),
            )
        )
        internal = asyncio.run(
            _exchange(
                failing,
                "GET",
                "/internal/v1/runs/run-synthetic-0001",
                headers={"host": HOST, "authorization": f"Bearer {AUTH_VALUE}"},
            )
        )

        # Assert
        self.assertEqual(200, status.status_code)
        self.assertEqual("AUTHENTICATION_FAILED", _error_code(refused.content))
        self.assertEqual("FAILED", json.loads(cancelled.content)["state"])
        self.assertEqual("INTERNAL_FAILURE", _error_code(internal.content))
        self.assertNotIn(b"private detail", internal.content)

    def test_configuration_and_listener_are_strict_and_internal_only(self) -> None:
        # Arrange
        factories: tuple[Callable[[], FleetHttpConfig], ...] = (
            lambda: FleetHttpConfig("", AUTH_VALUE),
            lambda: FleetHttpConfig(HOST, "short"),
            lambda: FleetHttpConfig(HOST, AUTH_VALUE, maximum_body_bytes=0),
        )
        app = _app()

        # Act
        failures: list[ValueError] = []
        for factory in factories:
            with pytest.raises(ValueError, match=r"(ASCII|bits|positive)") as raised:
                factory()
            failures.append(raised.value)
        with patch("aerial_rescue_fleet_simulator.http.uvicorn.run") as run:
            serve(app)

        # Assert
        self.assertEqual(3, len(failures))
        run.assert_called_once_with(
            app, host=str(ipaddress.ip_address(0)), port=8082, access_log=False
        )
