"""Fail-closed boundary branches for settings, assets, documents, security, and server seams."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_dashboard_api.application import (
    RuntimeSettings,
    SecureIdentifiers,
    fresh_runtime_settings,
)
from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.delivery.assets import Asset, AssetCatalog
from aerial_rescue_dashboard_api.delivery.openapi import _operation
from aerial_rescue_dashboard_api.delivery.server import _run_uvicorn
from aerial_rescue_dashboard_api.documents import (
    CATALOG_SCHEMA,
    _mapping,
    _sequence,
    _string,
    find_scenario,
    validated_document,
)
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import CurrentRun, RunMode
from aerial_rescue_dashboard_api.security import (
    AdmissionMiddleware,
    _origin_tuple,
    _require_bearer,
    _require_host,
    _require_origin,
)
from aerial_rescue_dashboard_api.sse import ClientBuffer
from aerial_rescue_dashboard_api.wire import (
    _calendar_instant,
    _require_ordinal_witness,
    parse_wire_document,
)
from fastapi import FastAPI
from pydantic import RootModel
from starlette.types import Message, Receive, Scope, Send

from tests.dashboard_api_support import BEARER, HOST, ORIGIN, dashboard_fixture

pytestmark = [pytest.mark.unit]


def _settings(**changes: object) -> RuntimeSettings:
    """Return valid settings with one optional field replacement."""
    accepted = RuntimeSettings(
        runtime_id="runtime-test-0001",
        bearer=BEARER,
        allowed_hosts=frozenset({HOST}),
        dashboard_origin=ORIGIN,
        cursor_key=b"c" * 32,
        index_template="<html><!--DASHBOARD_BOOTSTRAP--></html>",
        assets=AssetCatalog({}),
    )
    return RuntimeSettings(
        runtime_id=cast(str, changes.get("runtime_id", accepted.runtime_id)),
        bearer=cast(str, changes.get("bearer", accepted.bearer)),
        allowed_hosts=cast(frozenset[str], changes.get("allowed_hosts", accepted.allowed_hosts)),
        dashboard_origin=cast(str, changes.get("dashboard_origin", accepted.dashboard_origin)),
        cursor_key=cast(bytes, changes.get("cursor_key", accepted.cursor_key)),
        index_template=cast(str, changes.get("index_template", accepted.index_template)),
        assets=cast(AssetCatalog, changes.get("assets", accepted.assets)),
    )


def _scope(headers: list[tuple[bytes, bytes]]) -> Scope:
    """Return a minimal typed HTTP scope preserving repeated raw fields."""
    value = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/scenarios/current/reset",
        "raw_path": b"/api/v1/scenarios/current/reset",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8080),
    }
    return cast(Scope, cast(object, value))


class SettingsAndIdentityTests(unittest.TestCase):
    def test_each_invalid_immutable_runtime_setting_fails_startup(self) -> None:
        # Arrange
        invalid: tuple[dict[str, object], ...] = (
            {"runtime_id": "INVALID"},
            {"bearer": ""},
            {"allowed_hosts": frozenset()},
            {"dashboard_origin": "http://127.0.0.1:8080/"},
            {"index_template": "<html></html>"},
        )

        # Act
        captured = []
        for changes in invalid:
            with pytest.raises(ValueError, match=r".+") as error:
                _settings(**changes)
            captured.append(error.value)

        # Assert
        self.assertEqual(5, len(captured))
        self.assertTrue(all(str(error) for error in captured))

    def test_default_identity_and_runtime_generators_return_independent_schema_safe_values(
        self,
    ) -> None:
        # Arrange
        assets = AssetCatalog({})

        # Act
        first = SecureIdentifiers().new("mission")
        runtime = fresh_runtime_settings(
            allowed_hosts=frozenset({HOST}),
            dashboard_origin=ORIGIN,
            index_template="<!--DASHBOARD_BOOTSTRAP-->",
            assets=assets,
        )

        # Assert
        self.assertRegex(first, r"^mission-[0-9a-f]{32}$")
        self.assertRegex(runtime.runtime_id, r"^runtime-[0-9a-f]{32}$")
        self.assertGreaterEqual(len(runtime.bearer), 32)
        self.assertEqual(32, len(runtime.cursor_key))

    def test_current_run_refuses_identity_missing_for_its_selected_mode(self) -> None:
        # Arrange
        run = CurrentRun(RunMode.REPLAY, "scenario", 1, None, None, None)

        # Act
        with pytest.raises(ValueError, match="missing") as captured:
            _ = run.identity

        # Assert
        self.assertIn("missing", str(captured.value))


class CursorAssetAndBufferEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_constructor_and_cursor_bounds_fail_closed(self) -> None:
        # Arrange
        codec = CursorCodec("runtime-test-0001", b"k" * 32)

        # Act
        with pytest.raises(ValueError, match=r".+") as key_error:
            CursorCodec("runtime-test-0001", b"short")
        with pytest.raises(ValueError, match=r".+") as ordinal_error:
            codec.issue("run-test-0001", -1)
        with pytest.raises(ValueError, match=r".+") as bounds_error:
            codec.resolve("0" * 64, "run-test-0001", oldest_ordinal=2, latest_ordinal=1)
        malformed = codec.resolve("G" * 64, "run-test-0001", oldest_ordinal=0, latest_ordinal=1)
        unmatched = codec.resolve("0" * 64, "run-test-0001", oldest_ordinal=0, latest_ordinal=1)
        with pytest.raises(ValueError, match=r".+") as capacity_error:
            ClientBuffer(capacity=0)
        with pytest.raises(ValueError, match=r".+") as asset_error:
            AssetCatalog({"app.js": Asset(b"x", "text/javascript")})

        # Assert
        self.assertIsNone(malformed)
        self.assertIsNone(unmatched)
        self.assertEqual(
            5, len((key_error, ordinal_error, bounds_error, capacity_error, asset_error))
        )


class DocumentBoundaryEdgeTests(unittest.TestCase):
    def test_dependency_documents_refuse_size_schema_selection_and_narrowing_failures(self) -> None:
        # Arrange
        catalog = validated_document(
            CATALOG_SCHEMA, dashboard_fixture("scenario-catalog"), maximum_bytes=512 * 1024
        )

        # Act
        captured = []
        for operation in (
            lambda: validated_document(CATALOG_SCHEMA, b"{}", maximum_bytes=1),
            lambda: validated_document(CATALOG_SCHEMA, b"{}", maximum_bytes=100),
            lambda: find_scenario(catalog, "unknown-scenario", 1),
            lambda: find_scenario(catalog, "wilderness-missing-person", 2),
            lambda: _mapping(None),
            lambda: _sequence("not-an-array"),
            lambda: _string(1),
        ):
            with pytest.raises(ApiError) as error:
                operation()
            captured.append(error.value.code)

        # Assert
        self.assertEqual(7, len(captured))
        self.assertIn(ErrorCode.SCENARIO_NOT_FOUND, captured)
        self.assertIn(ErrorCode.SCENARIO_REVISION_MISMATCH, captured)

    def test_non_object_pydantic_dump_is_refused_and_openapi_rejects_body_without_media(
        self,
    ) -> None:
        # Arrange
        model = RootModel[list[int]]([1])

        # Act
        with (
            patch("aerial_rescue_dashboard_api.documents.parse_wire_document", return_value=model),
            pytest.raises(ApiError) as document_error,
        ):
            validated_document(CATALOG_SCHEMA, b"[]", maximum_bytes=100)
        with pytest.raises(ValueError, match="media type") as openapi_error:
            _operation("POST", "/test", (), (None, "json", ()), ())

        # Assert
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, document_error.value.code)
        self.assertIn("media type", str(openapi_error.value))

    def test_wire_helpers_refuse_invalid_calendar_witness_and_unowned_schema(self) -> None:
        # Arrange
        operations = (
            lambda: _calendar_instant("2026-02-30T12:00:00.000Z"),
            lambda: _require_ordinal_witness(0, "ab" * 32),
            lambda: parse_wire_document("https://example.invalid/unowned", b"{}"),
        )

        # Act
        captured = []
        for operation in operations:
            with pytest.raises(ValueError, match=r".+") as error:
                operation()
            captured.append(error.value)

        # Assert
        self.assertEqual(3, len(captured))
        self.assertTrue(all(str(error) for error in captured))


class SecurityAndServerEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_headers_and_malformed_origin_are_refused_before_downstream(
        self,
    ) -> None:
        # Arrange
        repeated_host = _scope([(b"host", HOST.encode()), (b"host", HOST.encode())])
        no_origin = _scope([(b"host", HOST.encode())])
        no_bearer = _scope([(b"host", HOST.encode()), (b"origin", ORIGIN.encode())])

        # Act
        with pytest.raises(ApiError) as host_error:
            _require_host(repeated_host, frozenset({HOST}))
        with pytest.raises(ApiError) as origin_error:
            _require_origin(no_origin, ORIGIN)
        with pytest.raises(ApiError) as bearer_error:
            _require_bearer(no_bearer, BEARER)
        malformed_origin = _origin_tuple("http://[invalid:8080")
        origin_with_path = _origin_tuple(f"{ORIGIN}/")

        # Assert
        self.assertIs(ErrorCode.HOST_INVALID, host_error.value.code)
        self.assertIs(ErrorCode.ORIGIN_INVALID, origin_error.value.code)
        self.assertIs(ErrorCode.AUTHENTICATION_FAILED, bearer_error.value.code)
        self.assertIsNone(malformed_origin)
        self.assertIsNone(origin_with_path)

    async def test_middleware_passthrough_does_not_add_public_proxy_headers(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []

        async def downstream(scope: Scope, _receive: Receive, send: Send) -> None:
            calls.append(cast(str, scope["type"]))
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})

        async def receive() -> Message:
            return {"type": "http.disconnect"}

        sent: list[Message] = []

        async def send(message: Message) -> None:
            sent.append(message)

        middleware = AdmissionMiddleware(
            downstream,
            allowed_hosts=frozenset({HOST}),
            dashboard_origin=ORIGIN,
            bearer=BEARER,
        )
        websocket = cast(Scope, cast(object, {"type": "websocket"}))
        http = _scope([(b"host", HOST.encode())])
        http["method"] = "GET"

        # Act
        await middleware(websocket, receive, send)
        await middleware(http, receive, send)

        # Assert
        self.assertEqual(["websocket", "http"], calls)
        headers = dict(cast(list[tuple[bytes, bytes]], sent[0]["headers"]))
        self.assertNotIn(b"content-security-policy", headers)
        self.assertNotIn(b"referrer-policy", headers)
        self.assertNotIn(b"x-content-type-options", headers)

    async def test_uvicorn_adapter_refuses_non_app_and_forwards_explicit_socket_options(
        self,
    ) -> None:
        # Arrange
        options: dict[str, object] = {
            "uds": str(Path("/") / "tmp" / "dashboard-test.sock"),
            "proxy_headers": False,
            "server_header": False,
            "timeout_graceful_shutdown": 5,
        }

        # Act
        with pytest.raises(TypeError) as invalid:
            _run_uvicorn(object(), **options)
        with patch("aerial_rescue_dashboard_api.delivery.server.uvicorn.run") as runner:
            _run_uvicorn(FastAPI(), **options)

        # Assert
        self.assertIn("FastAPI", str(invalid.value))
        runner.assert_called_once()
