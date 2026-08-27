from __future__ import annotations

import unittest
from typing import Final, cast

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_scenario_service.control import FleetControlError, FleetControlRefusal
from aerial_rescue_scenario_service.fleet_http import FleetHttpClient, FleetHttpSettings
from aerial_rescue_scenario_service.wire import (
    FleetControlCancelRequest,
    FleetControlStartRequest,
)

pytestmark = [pytest.mark.unit]

HOST: Final = "fleet-simulator:8082"
BEARER: Final = "b" * 64
RUN_ID: Final = "run-2026-0001"
MISSION_ID: Final = "mission-2026-0001"


def _start_request() -> FleetControlStartRequest:
    drones = [
        {
            "droneId": f"drone-sim-{ordinal:02d}",
            "sectorId": f"sector-{ordinal:02d}",
            "latitudeMicrodegrees": 44_472_000 + ordinal,
            "longitudeMicrodegrees": -79_248_000 + ordinal,
            "altitudeMetres": 120,
            "headingDegrees": 90,
            "groundSpeedCentimetresPerSecond": 850,
            "batteryPermille": 970,
            "northMicrodegreesPerTick": 0,
            "eastMicrodegreesPerTick": 76,
            "batteryDrainPermillePerTick": 2,
        }
        for ordinal in range(1, 21)
    ]
    return FleetControlStartRequest.model_validate(
        {
            "controlVersion": 1,
            "runId": RUN_ID,
            "scenario": {
                "missionId": MISSION_ID,
                "drones": drones,
                "tickIntervalMilliseconds": 1000,
                "connectivityThresholds": {
                    "missesToDegraded": 3,
                    "missesToOffline": 6,
                    "heartbeatsToRecover": 2,
                },
                "ticksToSweep": 12,
                "absentHeartbeats": [{"droneId": "drone-sim-07", "tickOrdinal": 2}],
            },
        }
    )


def _status_bytes(state: str = "RUNNING") -> bytes:
    return canonical.canonical_bytes(
        {
            "controlVersion": 1,
            "missionId": MISSION_ID,
            "runId": RUN_ID,
            "state": state,
            "completedTickCount": 3,
            "telemetryPublicationCount": 60,
        }
    )


def _cancel_request() -> FleetControlCancelRequest:
    return FleetControlCancelRequest.model_validate(
        {"controlVersion": 1, "missionId": MISSION_ID, "runId": RUN_ID}
    )


async def _started_client(transport: httpx.AsyncBaseTransport) -> FleetHttpClient:
    """Build and start the canonical in-memory fleet-control client."""
    client = FleetHttpClient(
        FleetHttpSettings("http://fleet-simulator:8082", HOST, BEARER),
        transport=transport,
    )
    await client.startup()
    return client


class FleetHttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_sends_canonical_authenticated_request_with_exact_timeouts(self) -> None:
        # Arrange
        observed: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(
                202, content=_status_bytes(), headers={"content-type": "application/json"}
            )

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        status = await client.start(_start_request())

        # Assert
        self.assertEqual(status.state, "RUNNING")
        self.assertEqual(len(observed), 1)
        request = observed[0]
        self.assertEqual((request.method, request.url.path), ("POST", "/internal/v1/runs"))
        self.assertEqual(request.headers["host"], HOST)
        self.assertEqual(request.headers["authorization"], f"Bearer {BEARER}")
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(
            request.content,
            canonical.canonical_bytes(_start_request().model_dump(mode="json", by_alias=True)),
        )
        self.assertEqual(request.extensions["timeout"]["connect"], 1)
        self.assertEqual(request.extensions["timeout"]["read"], 5)

    async def test_uncertain_start_reconciles_by_status_without_repeating_start(self) -> None:
        # Arrange
        paths: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append((request.method, request.url.path))
            if request.method == "POST":
                message = "uncertain"
                raise httpx.ReadTimeout(message, request=request)
            return httpx.Response(
                200, content=_status_bytes(), headers={"content-type": "application/json"}
            )

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        status = await client.start(_start_request())

        # Assert
        self.assertEqual(status.state, "RUNNING")
        self.assertEqual(
            paths,
            [("POST", "/internal/v1/runs"), ("GET", f"/internal/v1/runs/{RUN_ID}")],
        )

    async def test_malformed_start_success_reconciles_by_status_without_repeating_start(
        self,
    ) -> None:
        # Arrange
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.method == "POST":
                return httpx.Response(
                    202,
                    content=b"not-canonical-json",
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(
                200, content=_status_bytes(), headers={"content-type": "application/json"}
            )

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        status = await client.start(_start_request())

        # Assert
        self.assertEqual(status.state, "RUNNING")
        self.assertEqual(
            requests,
            [("POST", "/internal/v1/runs"), ("GET", f"/internal/v1/runs/{RUN_ID}")],
        )

    async def test_closed_fleet_refusal_is_preserved_and_malformed_replies_fail_closed(
        self,
    ) -> None:
        # Arrange
        replies = iter(
            (
                httpx.Response(
                    409,
                    content=canonical.canonical_bytes(
                        {
                            "controlVersion": 1,
                            "errorCode": "RUN_CONFLICT",
                            "message": "The run conflicts.",
                        }
                    ),
                    headers={"content-type": "application/json"},
                ),
                httpx.Response(
                    200, content=b"not-json", headers={"content-type": "application/json"}
                ),
            )
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return next(replies)

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        with pytest.raises(FleetControlError) as conflict:
            await client.status(RUN_ID)
        with pytest.raises(FleetControlError) as malformed:
            await client.status(RUN_ID)

        # Assert
        self.assertEqual(conflict.value.refusal, FleetControlRefusal.RUN_CONFLICT)
        self.assertEqual(malformed.value.refusal, FleetControlRefusal.INTERNAL_FAILURE)
        self.assertNotIn("not-json", str(malformed.value))

    async def test_transport_failure_is_a_redacted_unavailable_refusal(self) -> None:
        # Arrange
        def handler(request: httpx.Request) -> httpx.Response:
            message = "sensitive endpoint detail"
            raise httpx.ConnectError(message, request=request)

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        with pytest.raises(FleetControlError) as captured:
            await client.status(RUN_ID)

        # Assert
        self.assertEqual(captured.value.refusal, FleetControlRefusal.INTERNAL_FAILURE)
        self.assertNotIn("sensitive", str(captured.value))

    async def test_cancel_uses_only_the_positive_remaining_shared_budget(self) -> None:
        # Arrange
        observed: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(
                200,
                content=_status_bytes("CANCELLED"),
                headers={"content-type": "application/json"},
            )

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        status = await client.cancel(_cancel_request(), 0.25)
        with pytest.raises(FleetControlError) as expired:
            await client.cancel(_cancel_request(), 0)

        # Assert
        self.assertEqual(status.state, "CANCELLED")
        self.assertEqual(len(observed), 1)
        request = observed[0]
        self.assertEqual(
            (request.method, request.url.path),
            ("POST", f"/internal/v1/runs/{RUN_ID}/cancel"),
        )
        self.assertEqual(request.extensions["timeout"]["connect"], 0.25)
        self.assertEqual(request.extensions["timeout"]["read"], 0.25)
        self.assertEqual(expired.value.refusal, FleetControlRefusal.CANCELLATION_NOT_ESTABLISHED)

    async def test_lifecycle_readiness_closes_the_owned_client_once(self) -> None:
        # Arrange
        client = FleetHttpClient(
            FleetHttpSettings("http://fleet-simulator:8082", HOST, BEARER),
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, content=_status_bytes(), headers={"content-type": "application/json"}
                )
            ),
        )

        # Act
        before = client.ready
        await client.startup()
        during = client.ready
        await client.shutdown()
        after = client.ready
        await client.shutdown()

        # Assert
        self.assertEqual((before, during, after), (False, True, False))
        with pytest.raises(FleetControlError) as closed:
            await client.status(RUN_ID)
        with pytest.raises(FleetControlError) as restart:
            await client.startup()
        self.assertEqual(closed.value.refusal, FleetControlRefusal.INTERNAL_FAILURE)
        self.assertEqual(restart.value.refusal, FleetControlRefusal.INTERNAL_FAILURE)

    async def test_settings_refuse_non_http_urls_ambiguous_hosts_and_weak_secrets(self) -> None:
        # Arrange
        values = (
            ("https://fleet-simulator:8082", HOST, BEARER),
            ("http://fleet-simulator:8082/path", HOST, BEARER),
            ("http://fleet-simulator:8082", "http://fleet-simulator:8082", BEARER),
            ("http://fleet-simulator:8082", HOST, "short"),
        )

        # Act
        outcomes: list[bool] = []
        for base_url, host, bearer in values:
            with pytest.raises(ValueError, match=r"(?:URL|Host|host|bearer)"):
                FleetHttpSettings(base_url, host, bearer)
            outcomes.append(True)

        # Assert
        self.assertEqual(outcomes, [True] * len(values))
        self.assertNotIn(
            BEARER, repr(FleetHttpSettings("http://fleet-simulator:8082", HOST, BEARER))
        )

    async def test_settings_refuse_unparsable_values_and_mismatched_authorities(self) -> None:
        # Arrange
        values = (
            (cast("str", object()), HOST),
            ("http://another-host:8082", HOST),
        )

        # Act
        outcomes: list[bool] = []
        for base_url, host in values:
            with pytest.raises(ValueError, match=r"(?:invalid|same authority)"):
                FleetHttpSettings(base_url, host, BEARER)
            outcomes.append(True)

        # Assert
        self.assertEqual(outcomes, [True, True])

    async def test_response_media_size_status_and_refusal_shapes_fail_closed(self) -> None:
        # Arrange
        responses = (
            httpx.Response(200, content=_status_bytes(), headers={"content-type": "text/plain"}),
            httpx.Response(
                200,
                content=b"x" * (256 * 1024 + 1),
                headers={"content-type": "application/json"},
            ),
            httpx.Response(
                200,
                content=canonical.canonical_bytes({}),
                headers={"content-type": "application/json"},
            ),
            httpx.Response(
                500,
                content=canonical.canonical_bytes({}),
                headers={"content-type": "application/json"},
            ),
        )

        # Act
        refusals: list[FleetControlRefusal] = []
        for response in responses:
            client = await _started_client(
                httpx.MockTransport(lambda _request, item=response: item)
            )
            with pytest.raises(FleetControlError) as captured:
                await client.status(RUN_ID)
            refusals.append(captured.value.refusal)
            await client.shutdown()

        # Assert
        self.assertEqual(refusals, [FleetControlRefusal.INTERNAL_FAILURE] * len(responses))

    async def test_uncertain_cancel_never_claims_the_run_stopped(self) -> None:
        # Arrange
        def handler(request: httpx.Request) -> httpx.Response:
            message = "uncertain cancellation"
            raise httpx.ReadTimeout(message, request=request)

        client = await _started_client(httpx.MockTransport(handler))

        # Act
        with pytest.raises(FleetControlError) as captured:
            await client.cancel(_cancel_request(), 1)

        # Assert
        self.assertEqual(
            captured.value.refusal,
            FleetControlRefusal.CANCELLATION_NOT_ESTABLISHED,
        )
