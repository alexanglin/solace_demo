"""Scenario private HTTP admission, catalog, and route-operation mapping."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast, override
from unittest.mock import patch

import httpx
import pytest
from aerial_rescue_scenario_service.control import ScenarioControlCode, ScenarioControlError
from aerial_rescue_scenario_service.http import (
    CONTROL_PORT,
    ScenarioHttpConfig,
    create_app,
    serve,
)
from aerial_rescue_scenario_service.wire import (
    ScenarioCatalogResponse,
    ScenarioControlRecoveryRequest,
    ScenarioControlRunStatus,
    ScenarioControlStartRequest,
    parse_wire_document,
)
from fastapi import FastAPI

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
START_BYTES: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/scenario-control-start-request/baseline.json"
).read_bytes()
CATALOG_BYTES: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/dashboard/scenario-catalog/baseline.json"
).read_bytes()
CATALOG_ID: Final = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json"
)
AUTH_VALUE: Final = "scenario-secret-000000000000000000000000000000000"
OTHER_HOP_AUTH_VALUE: Final = "fleet-secret-000000000000000000000000000000000000"
HOST: Final = "scenario-service:8081"


def _status(state: str = "SEARCHING") -> ScenarioControlRunStatus:
    """Return one strict scenario status."""
    return ScenarioControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "scenarioId": "wilderness-missing-person",
            "scenarioRevision": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
            "state": state,
        }
    )


class _Operations:
    """Deterministic scenario operations behind the HTTP trust boundary."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.catalog = cast(
            "ScenarioCatalogResponse", parse_wire_document(CATALOG_ID, CATALOG_BYTES)
        )

    def catalog_response(self) -> ScenarioCatalogResponse:
        """Return the validated browser-facing catalog."""
        self.calls.append("catalog")
        return self.catalog

    def start(self, request: ScenarioControlStartRequest) -> ScenarioControlRunStatus:
        """Return the current run after a valid start."""
        self.calls.append(f"start:{request.run_id}")
        return _status()

    def status(self, run_id: str) -> ScenarioControlRunStatus:
        """Refuse an unknown run at the operation layer."""
        self.calls.append(f"status:{run_id}")
        raise ScenarioControlError(ScenarioControlCode.RUN_NOT_FOUND, run_id)

    def cancel(self, run_id: str, mission_id: str) -> ScenarioControlRunStatus:
        """Return an established cancellation."""
        self.calls.append(f"cancel:{run_id}:{mission_id}")
        return _status("ABORTED")

    def recover(self, request: ScenarioControlRecoveryRequest) -> ScenarioControlRunStatus:
        """Return a completed lost-run recovery."""
        self.calls.append(f"recover:{request.run_id}")
        return _status("ABORTED")


def _app(operations: _Operations | None = None) -> tuple[FastAPI, _Operations]:
    """Return the scenario app and its injected operation spy."""
    selected = _Operations() if operations is None else operations
    config = ScenarioHttpConfig(expected_host=HOST, bearer_secret=AUTH_VALUE)
    return (create_app(selected, config), selected)


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
        transport=transport, base_url="http://scenario-service:8081"
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


def _error_code(content: bytes) -> str:
    """Return the refusal code from canonical response bytes."""
    return cast("str", json.loads(content)["errorCode"])


class ScenarioHttpAdmissionTests(unittest.TestCase):
    def test_body_requests_refuse_in_host_auth_media_bound_canonical_schema_order(self) -> None:
        # Arrange
        oversized = b"{" + b" " * (256 * 1024)
        cases = (
            (
                _headers(
                    host="not-scenario:8081",
                    authorization_value="wrong",
                    media_type="text/plain",
                ),
                b"{}",
            ),
            (
                _headers(
                    authorization_value=OTHER_HOP_AUTH_VALUE,
                    media_type="text/plain",
                ),
                b"{}",
            ),
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
        app, operations = _app()

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
        self.assertEqual([], operations.calls)

    def test_catalog_read_authenticates_before_operation_and_uses_the_existing_projection(
        self,
    ) -> None:
        # Arrange
        app, operations = _app()

        # Act
        refused = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/scenarios",
                headers={
                    "host": HOST,
                    "authorization": f"Bearer {OTHER_HOP_AUTH_VALUE}",
                },
            )
        )
        accepted = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/scenarios",
                headers={"host": HOST, "authorization": f"Bearer {AUTH_VALUE}"},
            )
        )
        duplicate = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/scenarios",
                headers=httpx.Headers(
                    [
                        ("host", HOST),
                        ("authorization", f"Bearer {AUTH_VALUE}"),
                        ("authorization", f"Bearer {AUTH_VALUE}"),
                    ]
                ),
            )
        )

        # Assert
        self.assertEqual("AUTHENTICATION_FAILED", _error_code(refused.content))
        self.assertEqual("AUTHENTICATION_FAILED", _error_code(duplicate.content))
        self.assertEqual(200, accepted.status_code)
        self.assertEqual("scenario-catalog/v1", json.loads(accepted.content)["catalogVersion"])
        self.assertEqual(["catalog"], operations.calls)

    def test_path_body_binding_precedes_cancel_and_recovery_operations(self) -> None:
        # Arrange
        cancel = {
            "controlVersion": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
        }
        recovery = {
            "controlVersion": 1,
            "scenarioId": "wilderness-missing-person",
            "scenarioRevision": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
        }
        app, operations = _app()

        # Act
        cancel_response = asyncio.run(
            _exchange(
                app,
                "POST",
                "/internal/v1/runs/run-other/cancel",
                headers=_headers(),
                content=json.dumps(cancel),
            )
        )
        recover_response = asyncio.run(
            _exchange(
                app,
                "POST",
                "/internal/v1/runs/run-other/recover",
                headers=_headers(),
                content=json.dumps(recovery),
            )
        )

        # Assert
        self.assertEqual("PATH_BODY_MISMATCH", _error_code(cancel_response.content))
        self.assertEqual("PATH_BODY_MISMATCH", _error_code(recover_response.content))
        self.assertEqual([], operations.calls)

    def test_valid_start_is_accepted_on_private_port_8081(self) -> None:
        # Arrange
        app, operations = _app()

        # Act
        response = asyncio.run(
            _exchange(app, "POST", "/internal/v1/runs", headers=_headers(), content=START_BYTES)
        )

        # Assert
        self.assertEqual(202, response.status_code)
        self.assertEqual("run-synthetic-0001", json.loads(response.content)["runId"])
        self.assertEqual(["start:run-synthetic-0001"], operations.calls)
        self.assertEqual(8081, CONTROL_PORT)

    def test_status_cancel_and_recovery_map_only_after_valid_admission(self) -> None:
        # Arrange
        cancel = {
            "controlVersion": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
        }
        recovery = {
            "controlVersion": 1,
            "scenarioId": "wilderness-missing-person",
            "scenarioRevision": 1,
            "missionId": "mission-synthetic-0001",
            "runId": "run-synthetic-0001",
        }
        app, operations = _app()

        # Act
        status = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/runs/run-synthetic-0001",
                headers={"host": HOST, "authorization": f"Bearer {AUTH_VALUE}"},
            )
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
        recovered = asyncio.run(
            _exchange(
                app,
                "POST",
                "/internal/v1/runs/run-synthetic-0001/recover",
                headers=_headers(),
                content=json.dumps(recovery),
            )
        )

        # Assert
        self.assertEqual("RUN_NOT_FOUND", _error_code(status.content))
        self.assertEqual("ABORTED", json.loads(cancelled.content)["state"])
        self.assertEqual("ABORTED", json.loads(recovered.content)["state"])
        self.assertEqual(
            [
                "status:run-synthetic-0001",
                "cancel:run-synthetic-0001:mission-synthetic-0001",
                "recover:run-synthetic-0001",
            ],
            operations.calls,
        )

    def test_invalid_configuration_and_internal_failure_are_redacted(self) -> None:
        # Arrange
        factories: tuple[Callable[[], ScenarioHttpConfig], ...] = (
            lambda: ScenarioHttpConfig("", AUTH_VALUE),
            lambda: ScenarioHttpConfig(HOST, "short"),
            lambda: ScenarioHttpConfig(HOST, AUTH_VALUE, maximum_body_bytes=0),
        )

        class FailingOperations(_Operations):
            @override
            def catalog_response(self) -> ScenarioCatalogResponse:
                message = "internal detail"
                raise RuntimeError(message)

        app, _operations = _app(FailingOperations())

        # Act
        failures: list[ValueError] = []
        for factory in factories:
            with pytest.raises(ValueError, match=r"(ASCII|bits|positive)") as raised:
                factory()
            failures.append(raised.value)
        response = asyncio.run(
            _exchange(
                app,
                "GET",
                "/internal/v1/scenarios",
                headers={"host": HOST, "authorization": f"Bearer {AUTH_VALUE}"},
            )
        )

        # Assert
        self.assertEqual(3, len(failures))
        self.assertEqual("INTERNAL_FAILURE", _error_code(response.content))
        self.assertNotIn(b"internal detail", response.content)

    def test_serve_uses_only_the_private_listener_and_disables_access_logs(self) -> None:
        # Arrange
        app, _operations = _app()

        # Act
        with patch("aerial_rescue_scenario_service.http.uvicorn.run") as run:
            serve(app)

        # Assert
        run.assert_called_once_with(
            app, host=str(ipaddress.ip_address(0)), port=8081, access_log=False
        )
