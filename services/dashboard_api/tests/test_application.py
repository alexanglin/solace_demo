"""FastAPI public surface, ordered ingress security, caching, and exact responses."""

from __future__ import annotations

import hashlib
import unittest
from typing import Final
from unittest.mock import patch

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.application import ApplicationPorts, RuntimeSettings, create_app
from aerial_rescue_dashboard_api.delivery.assets import Asset, AssetCatalog
from aerial_rescue_dashboard_api.http_contract import ROUTE_EXPECTATIONS
from fastapi import FastAPI

from tests.dashboard_api_support import (
    BEARER,
    HOST,
    ORIGIN,
    FakeIdentifiers,
    FakeRecorderReadiness,
    FakeReplay,
    FakeScenario,
    FakeStore,
    dashboard_fixture,
)

pytestmark = [pytest.mark.integration]

KEY: Final = "31f72c3e-2357-4d8d-8ec8-5ca709032590"
KEY_TWO: Final = "4984a66b-ff04-4128-94ea-24578dc54851"
START_BODY: Final = b'{"mode":"degradedLive","scenarioRevision":1}'


def _application(
    *, recorder_reasons: tuple[str, ...] = ()
) -> tuple[FastAPI, FakeStore, FakeScenario, FakeReplay]:
    """Create one fully injected ASGI application."""
    store = FakeStore()
    scenario = FakeScenario(dashboard_fixture("scenario-catalog"))
    replay = FakeReplay(dashboard_fixture("replay-bundle"))
    recorder = FakeRecorderReadiness(recorder_reasons)
    assets = AssetCatalog(
        {
            "app.0123456789abcdef.js": Asset(
                body=b"export const ready=true;\n",
                media_type="text/javascript; charset=utf-8",
            )
        }
    )
    ports = ApplicationPorts(
        store=store,
        scenario=scenario,
        replay=replay,
        recorder=recorder,
        identifiers=FakeIdentifiers(),
    )
    settings = RuntimeSettings(
        runtime_id="runtime-test-0001",
        bearer=BEARER,
        allowed_hosts=frozenset({HOST}),
        dashboard_origin=ORIGIN,
        cursor_key=b"c" * 32,
        index_template=(
            "<!doctype html><html><body><!--DASHBOARD_BOOTSTRAP-->"
            '<script src="/assets/app.0123456789abcdef.js"></script></body></html>'
        ),
        assets=assets,
    )
    return create_app(settings, ports), store, scenario, replay


async def _request(
    application: FastAPI,
    method: str,
    path: str,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Exercise the ASGI app without Starlette's deprecated synchronous test client."""
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return await client.request(method, path, content=content, headers=headers)


def _mutation_headers(**overrides: str) -> dict[str, str]:
    """Return the complete accepted mutation header set."""
    headers = {
        "Authorization": f"Bearer {BEARER}",
        "Content-Type": "application/json",
        "Idempotency-Key": KEY,
        "Origin": ORIGIN,
    }
    headers.update(overrides)
    return headers


class PublicReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_is_minimal_liveness_and_never_discloses_runtime_or_bearer(
        self,
    ) -> None:
        # Arrange
        client, _, _, _ = _application()

        # Act
        response = await _request(client, "GET", "/api/v1/health")

        # Assert
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "healthVersion": "dashboard-health/v1",
                "status": "alive",
            },
            response.json(),
        )
        self.assertNotIn(BEARER, response.text)
        self.assertNotIn("runtime-test-0001", response.text)

    async def test_readiness_uses_only_dependencies_required_by_the_selected_mode(self) -> None:
        # Arrange
        client, store, scenario, replay = _application(
            recorder_reasons=("recorder-capture-unavailable",)
        )
        scenario.ready_reasons = ("scenario-unavailable",)

        # Act
        live = await _request(client, "GET", "/api/v1/readiness?mode=degradedLive")
        replay_response = await _request(client, "GET", "/api/v1/readiness?mode=replay")

        # Assert
        self.assertEqual((503, 200), (live.status_code, replay_response.status_code))
        self.assertEqual(
            ["scenario-unavailable", "recorder-capture-unavailable"],
            live.json()["reasons"],
        )
        self.assertEqual([], replay_response.json()["reasons"])
        self.assertEqual(
            ["readiness", "readiness"],
            [call for call in store.calls if call == "readiness"],
        )
        self.assertEqual((), replay.ready_reasons)

    async def test_dynamic_shell_is_no_store_and_hashed_assets_are_exact_and_immutable(
        self,
    ) -> None:
        # Arrange
        client, _, _, _ = _application()

        # Act
        index = await _request(client, "GET", "/")
        asset = await _request(client, "GET", "/assets/app.0123456789abcdef.js")
        missing = await _request(client, "GET", "/assets/app.ffffffffffffffff.js")

        # Assert
        self.assertEqual(
            (200, 200, 404),
            (index.status_code, asset.status_code, missing.status_code),
        )
        self.assertEqual("no-store", index.headers["cache-control"])
        self.assertIn(BEARER, index.text)
        self.assertIn('"runtimeId":"runtime-test-0001"', index.text)
        self.assertEqual("public, max-age=31536000, immutable", asset.headers["cache-control"])
        self.assertEqual(
            '"' + hashlib.sha256(b"export const ready=true;\n").hexdigest() + '"',
            asset.headers["etag"],
        )
        self.assertEqual(b"export const ready=true;\n", asset.content)
        self.assertEqual("ASSET_NOT_FOUND", missing.json()["errorCode"])

    async def test_replay_route_serves_the_validators_exact_output_bytes(self) -> None:
        # Arrange
        client, _, _, replay = _application()
        replay.known_sessions.add("session-test-0001")

        # Act
        response = await _request(client, "GET", "/api/v1/replays/session-test-0001")

        # Assert
        self.assertEqual(200, response.status_code)
        self.assertEqual(replay.bundle_bytes, response.content)
        self.assertEqual("no-store", response.headers["cache-control"])

    async def test_catalog_missing_replay_unknown_route_and_dependency_failure_are_typed(
        self,
    ) -> None:
        # Arrange
        client, _, scenario, _ = _application()

        # Act
        catalog = await _request(client, "GET", "/api/v1/scenarios")
        replay = await _request(client, "GET", "/api/v1/replays/session-missing")
        invalid_replay = await _request(client, "GET", "/api/v1/replays/INVALID")
        missing_route = await _request(client, "GET", "/not-a-route")
        with patch.object(scenario, "catalog", side_effect=RuntimeError):
            failed = await _request(client, "GET", "/api/v1/scenarios")

        # Assert
        self.assertEqual(
            (200, 404, 404, 404, 500),
            tuple(
                response.status_code
                for response in (catalog, replay, invalid_replay, missing_route, failed)
            ),
        )
        self.assertEqual("scenario-catalog/v1", catalog.json()["catalogVersion"])
        self.assertEqual("REPLAY_SESSION_NOT_FOUND", replay.json()["errorCode"])
        self.assertEqual("INTERNAL_FAILURE", failed.json()["errorCode"])


class MutationSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_refusal_order_is_host_origin_bearer_before_any_route_effect(self) -> None:
        # Arrange
        client, store, scenario, _ = _application()
        target = "/api/v1/scenarios/wilderness-missing-person/start"
        invalid = _mutation_headers(
            Origin="null",
            Authorization="Bearer stale",
            **{"Content-Type": "text/plain", "Idempotency-Key": "bad"},
        )

        # Act
        host = await _request(
            client,
            "POST",
            target,
            content=b"{bad",
            headers=invalid | {"Host": "attacker.test"},
        )
        origin = await _request(client, "POST", target, content=b"{bad", headers=invalid)
        bearer = await _request(
            client,
            "POST",
            target,
            content=b"{bad",
            headers=invalid | {"Origin": ORIGIN},
        )

        # Assert
        self.assertEqual(
            ["HOST_INVALID", "ORIGIN_INVALID", "AUTHENTICATION_FAILED"],
            [host.json()["errorCode"], origin.json()["errorCode"], bearer.json()["errorCode"]],
        )
        self.assertEqual([], store.calls)
        self.assertEqual([], scenario.starts)

    async def test_body_refusal_order_is_media_size_key_canonical_then_schema(self) -> None:
        # Arrange
        client, store, _, _ = _application()
        target = "/api/v1/scenarios/wilderness-missing-person/start"

        # Act
        media = await _request(
            client,
            "POST",
            target,
            content=b"{}",
            headers=_mutation_headers(**{"Content-Type": "text/plain", "Idempotency-Key": "bad"}),
        )
        size = await _request(
            client,
            "POST",
            target,
            content=b"{" + b"x" * 4096,
            headers=_mutation_headers(),
        )
        key = await _request(
            client,
            "POST",
            target,
            content=b"{bad",
            headers=_mutation_headers(**{"Idempotency-Key": "BAD"}),
        )
        malformed = await _request(
            client,
            "POST",
            target,
            content=b'{"mode":"replay","mode":"degradedLive"}',
            headers=_mutation_headers(),
        )
        schema = await _request(
            client,
            "POST",
            target,
            content=b'{"mode":"degradedLive"}',
            headers=_mutation_headers(),
        )

        # Assert
        self.assertEqual(
            [
                "UNSUPPORTED_MEDIA_TYPE",
                "BODY_TOO_LARGE",
                "IDEMPOTENCY_KEY_INVALID",
                "CANONICAL_JSON_INVALID",
                "SCHEMA_INVALID",
            ],
            [item.json()["errorCode"] for item in (media, size, key, malformed, schema)],
        )
        self.assertEqual([], store.calls)

    async def test_successful_start_replays_exact_stored_response_without_a_second_effect(
        self,
    ) -> None:
        # Arrange
        client, store, scenario, _ = _application()
        target = "/api/v1/scenarios/wilderness-missing-person/start"
        headers = _mutation_headers()

        # Act
        first = await _request(client, "POST", target, content=START_BODY, headers=headers)
        second = await _request(client, "POST", target, content=START_BODY, headers=headers)

        # Assert
        self.assertEqual((202, 202), (first.status_code, second.status_code))
        self.assertEqual(first.content, second.content)
        self.assertEqual(1, len(scenario.starts))
        self.assertEqual(1, sum(call.startswith("complete:") for call in store.calls))
        self.assertEqual("no-store", first.headers["cache-control"])

    async def test_framework_405_and_query_validation_use_closed_error_schema_not_422(self) -> None:
        # Arrange
        client, _, _, _ = _application()

        # Act
        method = await _request(client, "PUT", "/api/v1/health")
        query = await _request(client, "GET", "/api/v1/readiness?mode=live")

        # Assert
        self.assertEqual((405, 400), (method.status_code, query.status_code))
        self.assertEqual(
            ("METHOD_NOT_ALLOWED", "SCHEMA_INVALID"),
            (
                method.json()["errorCode"],
                query.json()["errorCode"],
            ),
        )
        self.assertNotEqual(422, query.status_code)

    async def test_path_key_depth_query_and_reset_edges_keep_the_closed_status_mapping(
        self,
    ) -> None:
        # Arrange
        client, _, _, _ = _application()
        target = "/api/v1/scenarios/wilderness-missing-person/start"
        headers_without_key = _mutation_headers()
        del headers_without_key["Idempotency-Key"]
        nested: object = None
        for _index in range(17):
            nested = {"a": nested}

        # Act
        invalid_path = await _request(
            client,
            "POST",
            "/api/v1/scenarios/INVALID/start",
            content=START_BODY,
            headers=_mutation_headers(),
        )
        missing_key = await _request(
            client,
            "POST",
            target,
            content=START_BODY,
            headers=headers_without_key,
        )
        too_deep = await _request(
            client,
            "POST",
            target,
            content=canonical.canonical_bytes(nested),
            headers=_mutation_headers(),
        )
        missing_mode = await _request(client, "GET", "/api/v1/readiness")
        started = await _request(
            client,
            "POST",
            target,
            content=START_BODY,
            headers=_mutation_headers(),
        )
        reset = await _request(
            client,
            "POST",
            "/api/v1/scenarios/current/reset",
            content=b"{}",
            headers=_mutation_headers(**{"Idempotency-Key": KEY_TWO}),
        )

        # Assert
        self.assertEqual(
            (400, 400, 400, 400, 202, 202),
            tuple(
                response.status_code
                for response in (
                    invalid_path,
                    missing_key,
                    too_deep,
                    missing_mode,
                    started,
                    reset,
                )
            ),
        )
        self.assertEqual("SCHEMA_INVALID", invalid_path.json()["errorCode"])
        self.assertEqual("IDEMPOTENCY_KEY_INVALID", missing_key.json()["errorCode"])
        self.assertEqual("CANONICAL_JSON_INVALID", too_deep.json()["errorCode"])
        self.assertEqual("dashboard-reset-response/v1", reset.json()["operationVersion"])

    async def test_openapi_contains_only_the_accepted_public_route_inventory(self) -> None:
        # Arrange
        client, _, _, _ = _application()
        application = client

        # Act
        document = application.openapi()
        routes = {
            (method.upper(), path) for path, item in document["paths"].items() for method in item
        }

        # Assert
        self.assertEqual(
            {
                ("GET", "/api/v1/health"),
                ("GET", "/api/v1/readiness"),
                ("GET", "/api/v1/scenarios"),
                ("POST", "/api/v1/scenarios/{scenarioId}/start"),
                ("POST", "/api/v1/scenarios/current/reset"),
                ("GET", "/api/v1/events"),
                ("GET", "/api/v1/replays/{sessionId}"),
                ("GET", "/"),
                ("GET", "/assets/{asset}"),
            },
            routes,
        )
        self.assertNotIn("/api/v1/approvals", document["paths"])

    def test_openapi_status_and_body_references_match_the_framework_free_registry(self) -> None:
        # Arrange
        application, _, _, _ = _application()

        # Act
        document = application.openapi()

        # Assert
        for method, path, _queries, request_body, responses in ROUTE_EXPECTATIONS:
            operation = document["paths"][path][method.lower()]
            self.assertEqual(
                {str(status) for status, _body in responses},
                set(operation["responses"]),
            )
            self.assertNotIn("422", operation["responses"])
            if request_body is not None:
                media_type, _kind, schema_ids = request_body
                self.assertEqual(
                    schema_ids[0],
                    operation["requestBody"]["content"][media_type]["schema"]["$ref"],
                )
        for path in (
            "/api/v1/scenarios/{scenarioId}/start",
            "/api/v1/scenarios/current/reset",
        ):
            operation = document["paths"][path]["post"]
            self.assertEqual([{"bearerAuth": []}], operation["security"])
            self.assertIn("Idempotency-Key", [item["name"] for item in operation["parameters"]])
