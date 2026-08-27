"""Authenticated private HTTP runtime for fleet start, status, and cancellation."""

from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import aerial_rescue_fleet_simulator.control_plane.runtime as http_runtime_module
import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_fleet_simulator.control_plane.runtime import (
    ControlError,
    ControlRefusal,
    ServerSettings,
    create_application,
)
from aerial_rescue_fleet_simulator.control_plane.wire import (
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from fastapi import FastAPI

pytestmark = [pytest.mark.unit]

HOST = "fleet-simulator:8082"
BEARER = "A" * 43
RUN = "run-2026-0001"
MISSION = "m-2026-0001"


def _start_document() -> dict[str, object]:
    """Return one closed twenty-drone start document."""
    drones = [
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
    ]
    return {
        "controlVersion": 1,
        "runId": RUN,
        "scenario": {
            "missionId": MISSION,
            "drones": drones,
            "tickIntervalMilliseconds": 1_000,
            "connectivityThresholds": {
                "missesToDegraded": 3,
                "missesToOffline": 6,
                "heartbeatsToRecover": 2,
            },
            "ticksToSweep": 10,
            "absentHeartbeats": [],
        },
    }


def _status(state: str = "RUNNING") -> FleetControlRunStatus:
    """Return one valid private run status."""
    return FleetControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "missionId": MISSION,
            "runId": RUN,
            "state": state,
            "completedTickCount": 3,
            "telemetryPublicationCount": 60,
        }
    )


def _headers(**changes: str) -> dict[str, str]:
    """Return exact private request headers with optional substitutions."""
    headers = {
        "Host": HOST,
        "Authorization": f"Bearer {BEARER}",
        "Content-Type": "application/json",
    }
    headers.update(changes)
    return headers


class FakeControl:
    def __init__(self) -> None:
        """Begin ready with no lifecycle or operation effects."""
        self.ready = True
        self.lifecycle: list[str] = []
        self.calls: list[tuple[str, object]] = []
        self.refusal: ControlRefusal | None = None
        self.unexpected: Exception | None = None

    async def startup(self) -> None:
        """Record bounded application startup."""
        self.lifecycle.append("startup")

    async def shutdown(self) -> None:
        """Record bounded application shutdown."""
        self.lifecycle.append("shutdown")

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Record one validated start."""
        self.calls.append(("start", request))
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise ControlError(self.refusal)
        return _status()

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Record one authenticated status read."""
        self.calls.append(("status", run_id))
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise ControlError(self.refusal)
        return _status()

    async def cancel(self, request: FleetControlCancelRequest) -> FleetControlRunStatus:
        """Record one validated cancellation."""
        self.calls.append(("cancel", request))
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise ControlError(self.refusal)
        return _status("CANCELLED")


@asynccontextmanager
async def _client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Run application lifespan around an in-process HTTP client."""
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://fleet-simulator:8082",
        ) as client:
            yield client


class HttpRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_parser_result_fails_closed_for_mutation_routes(self) -> None:
        # Arrange
        control = FakeControl()
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)

        # Act
        with patch.object(
            http_runtime_module,
            "_parse",
            AsyncMock(return_value=object()),
        ):
            async with _client(application) as client:
                start = await client.post(
                    "/internal/v1/runs",
                    content=b"{}",
                    headers=_headers(),
                )
                cancel = await client.post(
                    f"/internal/v1/runs/{RUN}/cancel",
                    content=b"{}",
                    headers=_headers(),
                )

        # Assert
        self.assertEqual(
            [(500, "INTERNAL_FAILURE"), (500, "INTERNAL_FAILURE")],
            [(response.status_code, response.json()["errorCode"]) for response in (start, cancel)],
        )
        self.assertEqual([], control.calls)

    async def test_authorized_start_is_canonical_and_lifecycle_is_bounded(self) -> None:
        # Arrange
        control = FakeControl()
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)

        # Act
        async with _client(application) as client:
            response = await client.post(
                "/internal/v1/runs",
                content=canonical.canonical_bytes(_start_document()),
                headers=_headers(),
            )

        # Assert
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.content,
            canonical.canonical_bytes(_status().model_dump(mode="json", by_alias=True)),
        )
        self.assertIsInstance(control.calls[0][1], FleetControlStartRequest)
        self.assertEqual(control.lifecycle, ["startup", "shutdown"])

    async def test_host_authentication_and_media_are_refused_before_body_schema(self) -> None:
        # Arrange
        cases = (
            ({"Host": "attacker.invalid", "Authorization": "Bearer wrong"}, "HOST_INVALID"),
            ({"Host": HOST, "Authorization": "Bearer wrong"}, "AUTHENTICATION_FAILED"),
            (_headers(**{"Content-Type": "text/plain"}), "UNSUPPORTED_MEDIA_TYPE"),
        )
        observed: list[tuple[str, str, list[tuple[str, object]]]] = []

        # Act
        for headers, expected in cases:
            control = FakeControl()
            application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)
            async with _client(application) as client:
                response = await client.post(
                    "/internal/v1/runs",
                    content=b'{"not":"a start"}',
                    headers=headers,
                )
            observed.append((response.json()["errorCode"], expected, control.calls))

        # Assert
        self.assertEqual(
            [(actual, calls) for actual, _expected, calls in observed],
            [
                ("HOST_INVALID", []),
                ("AUTHENTICATION_FAILED", []),
                ("UNSUPPORTED_MEDIA_TYPE", []),
            ],
        )
        self.assertTrue(all(actual == expected for actual, expected, _calls in observed))

    async def test_cancel_binds_path_to_body_and_unknown_run_refusal_is_redacted(self) -> None:
        # Arrange
        control = FakeControl()
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)
        mismatch = canonical.canonical_bytes(
            {"controlVersion": 1, "missionId": MISSION, "runId": "another-run"}
        )

        # Act
        async with _client(application) as client:
            conflict = await client.post(
                f"/internal/v1/runs/{RUN}/cancel",
                content=mismatch,
                headers=_headers(),
            )
            control.refusal = ControlRefusal.RUN_NOT_FOUND
            missing = await client.get(
                f"/internal/v1/runs/{RUN}",
                headers={"Host": HOST, "Authorization": f"Bearer {BEARER}"},
            )

        # Assert
        self.assertEqual(
            (conflict.status_code, conflict.json()["errorCode"]),
            (409, "PATH_BODY_MISMATCH"),
        )
        self.assertEqual((missing.status_code, missing.json()["errorCode"]), (404, "RUN_NOT_FOUND"))
        self.assertNotIn("sensitive", missing.text)

    async def test_liveness_stays_up_while_dependency_readiness_degrades_and_recovers(self) -> None:
        # Arrange
        control = FakeControl()
        control.ready = False
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)

        # Act
        async with _client(application) as client:
            live = await client.get("/healthz", headers={"Host": HOST})
            degraded = await client.get("/readyz", headers={"Host": HOST})
            control.ready = True
            recovered = await client.get("/readyz", headers={"Host": HOST})

        # Assert
        self.assertEqual((live.status_code, live.json()), (200, {"status": "live"}))
        self.assertEqual((degraded.status_code, degraded.json()), (503, {"ready": False}))
        self.assertEqual((recovered.status_code, recovered.json()), (200, {"ready": True}))

    async def test_body_canonical_schema_and_status_path_fail_closed(self) -> None:
        # Arrange
        cases = (
            (b"x" * (256 * 1024 + 1), _headers(), "BODY_TOO_LARGE"),
            (b'{"runId":"one","runId":"two"}', _headers(), "CANONICAL_JSON_INVALID"),
            (canonical.canonical_bytes({"unexpected": True}), _headers(), "SCHEMA_INVALID"),
        )
        observed: list[tuple[str, list[tuple[str, object]]]] = []

        # Act
        for body, headers, _expected in cases:
            control = FakeControl()
            application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)
            async with _client(application) as client:
                response = await client.post(
                    "/internal/v1/runs",
                    content=body,
                    headers=headers,
                )
            observed.append((response.json()["errorCode"], control.calls))
        control = FakeControl()
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)
        async with _client(application) as client:
            invalid_path = await client.get(
                "/internal/v1/runs/UPPERCASE",
                headers={"Host": HOST, "Authorization": f"Bearer {BEARER}"},
            )

        # Assert
        self.assertEqual(
            observed,
            [(expected, []) for _body, _headers, expected in cases],
        )
        self.assertEqual(invalid_path.json()["errorCode"], "SCHEMA_INVALID")
        self.assertEqual(control.calls, [])

    async def test_read_and_probe_admission_rejects_bad_or_duplicate_host(self) -> None:
        # Arrange
        control = FakeControl()
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)
        duplicate_host = [("Host", HOST), ("Host", HOST), ("Authorization", f"Bearer {BEARER}")]

        # Act
        async with _client(application) as client:
            unauthorized = await client.get(
                f"/internal/v1/runs/{RUN}",
                headers={"Host": HOST, "Authorization": "Bearer wrong"},
            )
            duplicate = await client.get(
                f"/internal/v1/runs/{RUN}",
                headers=duplicate_host,
            )
            health = await client.get("/healthz", headers={"Host": "attacker.invalid"})
            readiness = await client.get("/readyz", headers={"Host": "attacker.invalid"})

        # Assert
        self.assertEqual(
            [
                response.json()["errorCode"]
                for response in (unauthorized, duplicate, health, readiness)
            ],
            ["AUTHENTICATION_FAILED", "HOST_INVALID", "HOST_INVALID", "HOST_INVALID"],
        )
        self.assertEqual(control.calls, [])

    async def test_valid_cancel_and_operation_failures_use_the_closed_wire_shape(self) -> None:
        # Arrange
        cancel = canonical.canonical_bytes(
            {"controlVersion": 1, "missionId": MISSION, "runId": RUN}
        )
        control = FakeControl()
        application = create_application(ServerSettings(HOST, BEARER, 1, 1), control)

        # Act
        async with _client(application) as client:
            cancelled = await client.post(
                f"/internal/v1/runs/{RUN}/cancel",
                content=cancel,
                headers=_headers(),
            )
            control.refusal = ControlRefusal.RUN_FAILED
            refused = await client.get(
                f"/internal/v1/runs/{RUN}",
                headers={"Host": HOST, "Authorization": f"Bearer {BEARER}"},
            )
            control.refusal = None
            control.unexpected = RuntimeError("sensitive transport failure")
            failed = await client.get(
                f"/internal/v1/runs/{RUN}",
                headers={"Host": HOST, "Authorization": f"Bearer {BEARER}"},
            )

        # Assert
        self.assertEqual((cancelled.status_code, cancelled.json()["state"]), (200, "CANCELLED"))
        self.assertEqual((refused.status_code, refused.json()["errorCode"]), (500, "RUN_FAILED"))
        self.assertEqual(
            (failed.status_code, failed.json()["errorCode"]), (500, "INTERNAL_FAILURE")
        )
        self.assertNotIn("sensitive", failed.text)

    async def test_settings_reject_ambiguous_admission_and_lifecycle_values(self) -> None:
        # Arrange
        values = (
            ("", BEARER, 1, 1),
            (HOST, "short", 1, 1),
            (HOST, BEARER, 0, 1),
            (HOST, BEARER, 1, 0),
        )
        accepted_refusals: list[bool] = []

        # Act
        for host, bearer, startup, shutdown in values:
            with pytest.raises(ValueError, match="invalid private"):
                ServerSettings(host, bearer, startup, shutdown)
            accepted_refusals.append(True)

        # Assert
        self.assertEqual(accepted_refusals, [True] * len(values))
        self.assertNotIn(BEARER, repr(ServerSettings(HOST, BEARER, 1, 1)))


if __name__ == "__main__":
    unittest.main()
