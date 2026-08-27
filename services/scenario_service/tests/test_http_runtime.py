from __future__ import annotations

import json
import logging
import unittest
from collections.abc import Sequence
from typing import Final, cast, override

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_scenario_service.http_runtime import (
    ControlError,
    ControlRefusal,
    ServerSettings,
    create_application,
)
from aerial_rescue_scenario_service.wire import (
    ScenarioControlCancelRequest,
    ScenarioControlRunStatus,
    ScenarioControlStartRequest,
)
from fastapi import FastAPI

from tests.http_support import PrivateRequestHeaders, asgi_client_for

pytestmark = [pytest.mark.unit]

HOST: Final = "scenario-service:8081"
BEARER: Final = "a" * 64
AUTHORIZATION: Final = f"Bearer {BEARER}"
RUN_ID: Final = "run-2026-0001"
MISSION_ID: Final = "mission-2026-0001"
_client: Final = asgi_client_for("http://scenario-service:8081")
_headers: Final = PrivateRequestHeaders(HOST, BEARER, title_case=True)


def _status(*, state: str = "SEARCHING") -> ScenarioControlRunStatus:
    return ScenarioControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "scenarioId": "wilderness-missing-person",
            "scenarioRevision": 1,
            "missionId": MISSION_ID,
            "runId": RUN_ID,
            "state": state,
        }
    )


def _start_bytes(**changes: object) -> bytes:
    document: dict[str, object] = {
        "controlVersion": 1,
        "scenarioId": "wilderness-missing-person",
        "scenarioRevision": 1,
        "missionId": MISSION_ID,
        "runId": RUN_ID,
    }
    document.update(changes)
    return canonical.canonical_bytes(document)


def _cancel_bytes(**changes: object) -> bytes:
    document: dict[str, object] = {
        "controlVersion": 1,
        "missionId": MISSION_ID,
        "runId": RUN_ID,
    }
    document.update(changes)
    return canonical.canonical_bytes(document)


class FakeControl:
    def __init__(self, *, ready: bool = True) -> None:
        """Begin with scripted readiness and no operations or lifecycle effects."""
        self.ready = ready
        self.calls: list[tuple[str, object]] = []
        self.lifecycle: list[str] = []
        self.refusal: ControlRefusal | None = None
        self.unexpected: Exception | None = None
        self.invalid_result = False

    async def startup(self) -> None:
        self.lifecycle.append("startup")

    async def shutdown(self) -> None:
        self.lifecycle.append("shutdown")

    async def start(self, request: ScenarioControlStartRequest) -> ScenarioControlRunStatus:
        self.calls.append(("start", request))
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise ControlError(self.refusal, "sensitive upstream detail")
        if self.invalid_result:
            return cast("ScenarioControlRunStatus", object())
        return _status()

    async def status(self, run_id: str) -> ScenarioControlRunStatus:
        self.calls.append(("status", run_id))
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise ControlError(self.refusal, "sensitive upstream detail")
        if self.invalid_result:
            return cast("ScenarioControlRunStatus", object())
        return _status()

    async def cancel(
        self, request: ScenarioControlCancelRequest, remaining_seconds: float
    ) -> ScenarioControlRunStatus:
        self.calls.append(("cancel", (request, remaining_seconds)))
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise ControlError(self.refusal, "sensitive upstream detail")
        if self.invalid_result:
            return cast("ScenarioControlRunStatus", object())
        return _status(state="ABORTED")


def _application(control: FakeControl) -> FastAPI:
    """Build the private runtime with deterministic lifecycle bounds."""
    settings = ServerSettings(HOST, BEARER, startup_timeout_seconds=1, shutdown_timeout_seconds=1)
    return create_application(settings, control)


class RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        """Record error-level events without relying on an assertion context manager."""
        super().__init__(logging.ERROR)
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Append one emitted record for assertions after the exercised operation."""
        self.records.append(record)


class HttpRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_start_is_validated_and_returned_as_canonical_json(self) -> None:
        # Arrange
        control = FakeControl()
        application = _application(control)

        # Act
        async with _client(application) as client:
            response = await client.post(
                "/internal/v1/runs", content=_start_bytes(), headers=_headers()
            )

        # Assert
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(
            response.content,
            canonical.canonical_bytes(_status().model_dump(mode="json", by_alias=True)),
        )
        self.assertEqual(len(control.calls), 1)
        self.assertEqual(control.calls[0][0], "start")
        self.assertIsInstance(control.calls[0][1], ScenarioControlStartRequest)
        self.assertEqual(control.lifecycle, ["startup", "shutdown"])

    async def test_request_admission_refuses_in_the_governed_order_without_an_operation(
        self,
    ) -> None:
        # Arrange
        cases: Sequence[tuple[bytes, dict[str, str], str, int]] = (
            (_start_bytes(), _headers(Host="attacker.invalid"), "HOST_INVALID", 400),
            (
                _start_bytes(),
                _headers(Host="attacker.invalid", Authorization="Bearer wrong"),
                "HOST_INVALID",
                400,
            ),
            (
                _start_bytes(),
                _headers(Authorization="Bearer wrong", **{"Content-Type": "text/plain"}),
                "AUTHENTICATION_FAILED",
                401,
            ),
            (
                _start_bytes(),
                _headers(**{"Content-Type": "text/plain"}),
                "UNSUPPORTED_MEDIA_TYPE",
                415,
            ),
            (b"x" * (256 * 1024 + 1), _headers(), "BODY_TOO_LARGE", 413),
            (b'{"runId":"one","runId":"two"}', _headers(), "CANONICAL_JSON_INVALID", 400),
            (
                _start_bytes(unexpected="not-accepted"),
                _headers(),
                "SCHEMA_INVALID",
                422,
            ),
        )

        # Act
        observed: list[tuple[int, str]] = []
        operation_calls: list[list[tuple[str, object]]] = []
        for body, headers, _expected_code, _expected_status in cases:
            control = FakeControl()
            application = _application(control)
            async with _client(application) as client:
                response = await client.post("/internal/v1/runs", content=body, headers=headers)
            observed.append((response.status_code, response.json()["errorCode"]))
            operation_calls.append(control.calls)

        # Assert
        self.assertEqual(
            observed,
            [(expected_status, expected_code) for _, _, expected_code, expected_status in cases],
        )
        self.assertEqual(operation_calls, [[] for _case in cases])

    async def test_reads_require_host_and_bearer_before_lookup(self) -> None:
        # Arrange
        control = FakeControl()
        application = _application(control)

        # Act
        async with _client(application) as client:
            refused = await client.get(
                f"/internal/v1/runs/{RUN_ID}",
                headers={"Host": "attacker.invalid", "Authorization": "Bearer wrong"},
            )
            accepted = await client.get(
                f"/internal/v1/runs/{RUN_ID}",
                headers={"Host": HOST, "Authorization": AUTHORIZATION},
            )

        # Assert
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["errorCode"], "HOST_INVALID")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(control.calls, [("status", RUN_ID)])

    async def test_cancel_binds_the_path_before_calling_the_control_port(self) -> None:
        # Arrange
        control = FakeControl()
        application = _application(control)

        # Act
        async with _client(application) as client:
            refused = await client.post(
                f"/internal/v1/runs/{RUN_ID}/cancel",
                content=_cancel_bytes(runId="another-run"),
                headers=_headers(),
            )
            accepted = await client.post(
                f"/internal/v1/runs/{RUN_ID}/cancel",
                content=_cancel_bytes(),
                headers=_headers(),
            )

        # Assert
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["errorCode"], "PATH_BODY_MISMATCH")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(len(control.calls), 1)
        operation, arguments = control.calls[0]
        request, remaining_seconds = cast("tuple[ScenarioControlCancelRequest, float]", arguments)
        self.assertEqual(operation, "cancel")
        self.assertEqual(request.run_id, RUN_ID)
        self.assertGreater(remaining_seconds, 0)
        self.assertLessEqual(remaining_seconds, 15)

    async def test_operation_refusals_use_only_the_closed_redacted_body(self) -> None:
        # Arrange
        control = FakeControl()
        control.refusal = ControlRefusal.RUN_NOT_FOUND
        application = _application(control)

        # Act
        async with _client(application) as client:
            response = await client.get(
                f"/internal/v1/runs/{RUN_ID}",
                headers={"Host": HOST, "Authorization": AUTHORIZATION},
            )

        # Assert
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {
                "controlVersion": 1,
                "errorCode": "RUN_NOT_FOUND",
                "message": "The requested run was not found.",
            },
        )
        self.assertNotIn("sensitive", response.text)

    async def test_liveness_is_distinct_from_dependency_readiness(self) -> None:
        # Arrange
        control = FakeControl(ready=False)
        application = _application(control)

        # Act
        async with _client(application) as client:
            live = await client.get("/healthz", headers={"Host": HOST})
            not_ready = await client.get("/readyz", headers={"Host": HOST})
            control.ready = True
            ready = await client.get("/readyz", headers={"Host": HOST})

        # Assert
        self.assertEqual((live.status_code, live.json()), (200, {"status": "live"}))
        self.assertEqual(
            (not_ready.status_code, not_ready.json()),
            (503, {"ready": False}),
        )
        self.assertEqual((ready.status_code, ready.json()), (200, {"ready": True}))

    async def test_settings_refuse_ambiguous_host_secret_and_lifecycle_bounds(self) -> None:
        # Arrange
        values: Sequence[tuple[str, str, float, float]] = (
            ("", BEARER, 1, 1),
            ("https://scenario-service:8081", BEARER, 1, 1),
            (HOST, "short", 1, 1),
            (HOST, BEARER, 0, 1),
            (HOST, BEARER, 1, 0),
        )

        # Act
        outcomes: list[bool] = []
        for host, bearer, startup, shutdown in values:
            with pytest.raises(ValueError, match=r"(?:host|bearer|timeout)"):
                ServerSettings(host, bearer, startup, shutdown)
            outcomes.append(True)

        # Assert
        self.assertEqual(outcomes, [True] * len(values))
        self.assertNotIn(BEARER, repr(ServerSettings(HOST, BEARER, 1, 1)))

    async def test_framework_error_bodies_do_not_replace_the_closed_refusal_shape(self) -> None:
        # Arrange
        control = FakeControl()
        application = _application(control)

        # Act
        async with _client(application) as client:
            response = await client.get(
                "/internal/v1/runs/UPPERCASE",
                headers={"Host": HOST, "Authorization": AUTHORIZATION},
            )

        # Assert
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errorCode"], "SCHEMA_INVALID")
        self.assertEqual(set(response.json()), {"controlVersion", "errorCode", "message"})
        self.assertEqual(control.calls, [])
        self.assertNotIn("input", json.dumps(response.json()))

    async def test_probe_host_duplicate_headers_and_cancel_authentication_fail_closed(self) -> None:
        # Arrange
        control = FakeControl()
        application = _application(control)
        duplicate_headers = [
            ("Host", HOST),
            ("Host", HOST),
            ("Authorization", AUTHORIZATION),
        ]

        # Act
        async with _client(application) as client:
            health = await client.get("/healthz", headers={"Host": "attacker.invalid"})
            readiness = await client.get("/readyz", headers={"Host": "attacker.invalid"})
            duplicate = await client.get(f"/internal/v1/runs/{RUN_ID}", headers=duplicate_headers)
            cancel = await client.post(
                f"/internal/v1/runs/{RUN_ID}/cancel",
                content=_cancel_bytes(),
                headers=_headers(Authorization="Bearer wrong"),
            )

        # Assert
        self.assertEqual(
            [response.json()["errorCode"] for response in (health, readiness, duplicate, cancel)],
            ["HOST_INVALID", "HOST_INVALID", "HOST_INVALID", "AUTHENTICATION_FAILED"],
        )
        self.assertEqual(control.calls, [])

    async def test_unexpected_and_invalid_operation_results_return_internal_failure(self) -> None:
        # Arrange
        unexpected_control = FakeControl()
        unexpected_control.unexpected = RuntimeError("sensitive operation detail")
        invalid_control = FakeControl()
        invalid_control.invalid_result = True
        applications = (
            _application(unexpected_control),
            _application(invalid_control),
        )
        logger = logging.getLogger("aerial_rescue_scenario_service.http_runtime")
        recording_handler = RecordingHandler()
        logger.addHandler(recording_handler)

        # Act
        responses: list[httpx.Response] = []
        try:
            for application in applications:
                async with _client(application) as client:
                    responses.append(
                        await client.get(
                            f"/internal/v1/runs/{RUN_ID}",
                            headers={"Host": HOST, "Authorization": AUTHORIZATION},
                        )
                    )
        finally:
            logger.removeHandler(recording_handler)

        # Assert
        self.assertEqual(
            [response.json()["errorCode"] for response in responses],
            ["INTERNAL_FAILURE", "INTERNAL_FAILURE"],
        )
        self.assertEqual(len(recording_handler.records), 2)
        self.assertEqual(
            [record.getMessage() for record in recording_handler.records],
            [
                "scenario control operation failed",
                "scenario control operation returned an invalid result type",
            ],
        )
        self.assertFalse(any("sensitive" in response.text for response in responses))
        self.assertFalse(
            any(
                "sensitive" in recording_handler.format(record)
                for record in recording_handler.records
            )
        )

    async def test_cancel_refuses_when_body_admission_spends_the_entire_budget(self) -> None:
        # Arrange
        control = FakeControl()
        readings = iter((10.0, 26.0))
        application = create_application(
            ServerSettings(HOST, BEARER, startup_timeout_seconds=1, shutdown_timeout_seconds=1),
            control,
            monotonic=lambda: next(readings),
        )

        # Act
        async with _client(application) as client:
            response = await client.post(
                f"/internal/v1/runs/{RUN_ID}/cancel",
                content=_cancel_bytes(),
                headers=_headers(),
            )

        # Assert
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["errorCode"], "CANCELLATION_NOT_ESTABLISHED")
        self.assertEqual(control.calls, [])
