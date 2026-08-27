"""Production configuration, built assets, private control, replay, and cleanup seams."""

from __future__ import annotations

import asyncio
import runpy
import unittest
from dataclasses import dataclass, fields
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, cast
from unittest.mock import Mock, patch

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api import scenario_client as scenario_module
from aerial_rescue_dashboard_api.delivery import assets as dashboard_assets
from aerial_rescue_dashboard_api.delivery import production
from aerial_rescue_dashboard_api.delivery.assets import load_built_dashboard
from aerial_rescue_dashboard_api.delivery.production import (
    DASHBOARD_ASSET_ROOT,
    DASHBOARD_SOCKET_SETTING,
    RECORDER_READINESS_PATH_SETTING,
    SCENARIO_CONTROL_URL,
    SCENARIO_CREDENTIAL_PATH_SETTING,
    DashboardConfiguration,
    ProductionResources,
    ProductionRuntime,
    RecorderLeaseReadiness,
    configuration,
)
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import (
    ScenarioCancellationNotEstablishedError,
    ScenarioRunNotFoundError,
    StorePort,
)
from aerial_rescue_dashboard_api.replay import ReplayFilePort
from aerial_rescue_dashboard_api.scenario_client import ScenarioHttpClient
from aerial_rescue_dashboard_api.store_adapter import SqlStore
from aerial_rescue_observability.freshness import FreshnessLease
from fastapi import FastAPI

from tests.dashboard_api_support import dashboard_fixture

pytestmark = [pytest.mark.integration]

CONTROL_VALUE: Final = "c" * 32
DATABASE_VALUE: Final = "p" * 32


def _environment(test: unittest.TestCase) -> tuple[dict[str, str], Path]:
    """Create one complete temporary production filesystem and environment."""
    root = Path(test.enterContext(TemporaryDirectory()))
    asset_root = root / "dashboard"
    asset_directory = asset_root / "assets"
    asset_directory.mkdir(parents=True)
    (asset_root / "index.html").write_text(
        '<!doctype html><html><head></head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (asset_directory / "index-BV67a0d0.js").write_bytes(b"export const ready=true;\n")
    scenario_secret = root / "scenario-control-secret"
    scenario_secret.write_text(CONTROL_VALUE + "\n", encoding="ascii")
    postgres_secret = root / "postgres-password"
    postgres_secret.write_text(DATABASE_VALUE + "\n", encoding="ascii")
    recorder_readiness = root / "recorder-readiness" / "ready.json"
    recorder_readiness.parent.mkdir()
    return (
        {
            "DASHBOARD_ASSET_ROOT": str(asset_root),
            "DASHBOARD_SOCKET": "/run/aerial-rescue/dashboard-api.sock",
            "POSTGRES_DB": "aerial_rescue",
            "POSTGRES_PASSWORD_FILE": str(postgres_secret),
            "POSTGRES_USER": "aerial_rescue",
            "SCENARIO_CONTROL_SECRET_FILE": str(scenario_secret),
            "SCENARIO_CONTROL_URL": "http://scenario-service:8081",
            "RECORDER_READINESS_PATH": str(recorder_readiness),
        },
        asset_root,
    )


class ProductionConfigurationTests(unittest.TestCase):
    def test_configuration_and_built_asset_manifest_keep_both_credentials_redacted(self) -> None:
        # Arrange
        environment, asset_root = _environment(self)

        # Act
        configured = configuration(environment)
        built = load_built_dashboard(asset_root)
        rendered = repr(configured)
        script = built.assets.get("index-BV67a0d0.js")

        # Assert
        self.assertNotIn(CONTROL_VALUE, rendered)
        self.assertNotIn(DATABASE_VALUE, rendered)
        self.assertNotIn(environment[SCENARIO_CREDENTIAL_PATH_SETTING], rendered)
        self.assertIn("<!--DASHBOARD_BOOTSTRAP-->", built.index_template)
        self.assertIsNotNone(script)
        assert script is not None
        self.assertEqual(b"export const ready=true;\n", script.body)

    def test_missing_or_topology_changing_dashboard_settings_fail_before_resource_creation(
        self,
    ) -> None:
        # Arrange
        environment, _asset_root = _environment(self)
        candidates = [
            {**environment, name: " "}
            for name in (
                SCENARIO_CREDENTIAL_PATH_SETTING,
                SCENARIO_CONTROL_URL,
                DASHBOARD_ASSET_ROOT,
                DASHBOARD_SOCKET_SETTING,
                RECORDER_READINESS_PATH_SETTING,
            )
        ]
        candidates.extend(
            (
                {**environment, SCENARIO_CONTROL_URL: "http://attacker:8081"},
                {
                    **environment,
                    DASHBOARD_SOCKET_SETTING: str(Path("/") / "tmp" / "dashboard.sock"),
                },
                {**environment, DASHBOARD_ASSET_ROOT: "relative"},
            )
        )

        # Act
        captured = []
        for candidate in candidates:
            with pytest.raises(ValueError, match=r".+") as error:
                configuration(candidate)
            captured.append(error.value)

        # Assert
        self.assertEqual(8, len(captured))
        self.assertTrue(all(CONTROL_VALUE not in str(error) for error in captured))


class RecorderLeaseReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_freshness_loss_and_recovery_are_observed_without_process_restart(self) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        path = root / "ready.json"
        now = 5_000
        lease = FreshnessLease(path, lambda: now, lambda: 1)
        port = RecorderLeaseReadiness(path, lambda: now)

        # Act
        missing = await port.readiness()
        lease.activate()
        fresh = await port.readiness()
        path.write_bytes(b"{")
        malformed = await port.readiness()
        path.unlink()
        lease.activate()
        recovered = await port.readiness()

        # Assert
        self.assertEqual(("recorder-capture-unavailable",), missing)
        self.assertEqual((), fresh)
        self.assertEqual(("recorder-capture-unavailable",), malformed)
        self.assertEqual((), recovered)

    def test_secret_material_refuses_missing_nonregular_nonascii_short_and_nul_inputs(
        self,
    ) -> None:
        # Arrange
        environment, _asset_root = _environment(self)
        root = Path(self.enterContext(TemporaryDirectory()))
        missing = root / "missing"
        directory = root / "directory"
        directory.mkdir()
        nonascii = root / "nonascii"
        nonascii.write_bytes(b"\xff" * 32)
        short = root / "short"
        short.write_text("short\n", encoding="ascii")
        nul = root / "nul"
        nul.write_bytes(b"x" * 32 + b"\x00")
        candidates = (missing, directory, nonascii, short, nul)
        refusals = []

        # Act
        for candidate in candidates:
            with pytest.raises(ValueError, match="secret material") as error:
                configuration({**environment, SCENARIO_CREDENTIAL_PATH_SETTING: str(candidate)})
            refusals.append(error.value)

        # Assert
        self.assertEqual(5, len(refusals))
        self.assertTrue(
            all(
                str(candidate) not in str(error)
                for candidate, error in zip(candidates, refusals, strict=True)
            )
        )


class BuiltAssetTests(unittest.TestCase):
    def test_invalid_asset_names_media_and_bootstrap_shapes_are_refused(self) -> None:
        # Arrange
        roots: list[Path] = []
        for index, (html, asset_name) in enumerate(
            (
                ("<html><head></head></html>", "plain.js"),
                ("<html><head></head></html>", "index-12345678.exe"),
                ("<html><body></body></html>", "index-12345678.js"),
                (
                    "<html><head><!--DASHBOARD_BOOTSTRAP--><!--DASHBOARD_BOOTSTRAP--></head></html>",
                    "index-12345678.js",
                ),
            )
        ):
            root = Path(self.enterContext(TemporaryDirectory())) / str(index)
            (root / "assets").mkdir(parents=True)
            (root / "index.html").write_text(html, encoding="utf-8")
            (root / "assets" / asset_name).write_bytes(b"asset")
            roots.append(root)
        failures = []

        # Act
        for root in roots:
            with pytest.raises(ValueError, match=r"asset|bootstrap|insertion") as error:
                load_built_dashboard(root)
            failures.append(error.value)

        # Assert
        self.assertEqual(4, len(failures))

    def test_asset_inventory_and_regular_file_checks_reject_every_filesystem_escape(
        self,
    ) -> None:
        # Arrange
        missing = Path(self.enterContext(TemporaryDirectory())) / "missing"
        missing.mkdir()
        (missing / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
        empty = Path(self.enterContext(TemporaryDirectory()))
        (empty / "assets").mkdir()
        (empty / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
        symlink_root = Path(self.enterContext(TemporaryDirectory()))
        (symlink_root / "assets").mkdir()
        (symlink_root / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
        target = symlink_root / "target"
        target.write_bytes(b"asset")
        (symlink_root / "assets" / "index-12345678.js").symlink_to(target)
        small = symlink_root / "small"
        small.write_bytes(b"x")
        failures = []

        # Act
        for root in (missing, empty, symlink_root):
            with pytest.raises(ValueError, match=r"unavailable|count|outside") as error:
                load_built_dashboard(root)
            failures.append(error.value)
        with pytest.raises(ValueError, match="unavailable") as absent_error:
            dashboard_assets._read_regular(missing / "absent", 1)
        with (
            patch.object(Path, "read_bytes", side_effect=OSError),
            pytest.raises(ValueError, match="unavailable") as read_error,
        ):
            dashboard_assets._read_regular(small, 1)
        with (
            patch.object(Path, "read_bytes", return_value=b"xx"),
            pytest.raises(ValueError, match="changed") as race_error,
        ):
            dashboard_assets._read_regular(small, 1)

        # Assert
        self.assertEqual(3, len(failures))
        self.assertIsInstance(absent_error.value.__cause__, OSError)
        self.assertIsInstance(read_error.value.__cause__, OSError)
        self.assertIsNone(race_error.value.__cause__)


def _status(run_id: str = "run-test-0001") -> bytes:
    """Return one strict private scenario run status."""
    return canonical.canonical_bytes(
        {
            "controlVersion": 1,
            "missionId": "mission-test-0001",
            "runId": run_id,
            "scenarioId": "wilderness-missing-person",
            "scenarioRevision": 1,
            "state": "PLANNED",
        }
    )


def _refusal(code: str) -> bytes:
    """Return one strict private control refusal."""
    return canonical.canonical_bytes(
        {"controlVersion": 1, "errorCode": code, "message": "redacted refusal"}
    )


class ScenarioClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_private_calls_use_exact_host_bearer_canonical_bodies_and_validated_results(
        self,
    ) -> None:
        # Arrange
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            body = (
                dashboard_fixture("scenario-catalog")
                if request.url.path.endswith("scenarios")
                else _status()
            )
            return httpx.Response(
                202 if request.method == "POST" else 200,
                content=body,
                headers={"Content-Type": "application/json"},
            )

        client = ScenarioHttpClient(
            "http://scenario-service:8081",
            CONTROL_VALUE,
            transport=httpx.MockTransport(handler),
        )

        # Act
        catalog = await client.catalog()
        started = await client.start(
            "wilderness-missing-person", 1, "mission-test-0001", "run-test-0001"
        )
        found = await client.status("run-test-0001")
        cancelled = await client.cancel("mission-test-0001", "run-test-0001", 15.0)
        recovered = await client.recover(
            "wilderness-missing-person", 1, "mission-test-0001", "run-test-0001"
        )
        await client.close()

        # Assert
        self.assertIn(b'"catalogVersion": "scenario-catalog/v1"', catalog)
        self.assertEqual(
            ["run-test-0001"] * 4,
            [started.run_id, found.run_id, cancelled.run_id, recovered.run_id],
        )
        self.assertEqual({"scenario-service:8081"}, {item.headers["Host"] for item in requests})
        self.assertEqual(
            {f"Bearer {CONTROL_VALUE}"},
            {item.headers["Authorization"] for item in requests},
        )
        self.assertEqual(
            [b"", b""],
            [item.content for item in requests if item.method == "GET"],
        )
        recovery = next(item for item in requests if item.url.path.endswith("/recover"))
        self.assertEqual(
            {
                "controlVersion": 1,
                "missionId": "mission-test-0001",
                "runId": "run-test-0001",
                "scenarioId": "wilderness-missing-person",
                "scenarioRevision": 1,
            },
            canonical.decode(recovery.content),
        )

    async def test_private_cancellation_refusal_maps_to_the_durable_operation_outcome(self) -> None:
        # Arrange
        calls: list[str] = []
        refusal = canonical.canonical_bytes(
            {
                "controlVersion": 1,
                "errorCode": "CANCELLATION_NOT_ESTABLISHED",
                "message": "run did not stop inside the cancellation bound",
            }
        )

        def refuse(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return httpx.Response(
                409, content=refusal, headers={"Content-Type": "application/json"}
            )

        transport = httpx.MockTransport(refuse)
        client = ScenarioHttpClient(
            "http://scenario-service:8081", CONTROL_VALUE, transport=transport
        )

        # Act
        with pytest.raises(ScenarioCancellationNotEstablishedError):
            await client.cancel("mission-test-0001", "run-test-0001", 15.0)
        await client.close()

        # Assert
        self.assertEqual(["/internal/v1/runs/run-test-0001/cancel"], calls)

    async def test_invalid_origins_credentials_readiness_and_catalog_refusals_fail_closed(
        self,
    ) -> None:
        # Arrange
        def catalog(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=dashboard_fixture("scenario-catalog"),
                headers={"Content-Type": "application/json"},
            )

        def refuse(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                content=_refusal("SCENARIO_NOT_FOUND"),
                headers={"Content-Type": "application/json"},
            )

        good = ScenarioHttpClient(
            "http://scenario-service:8081/",
            CONTROL_VALUE,
            transport=httpx.MockTransport(catalog),
        )
        bad = ScenarioHttpClient(
            "http://scenario-service:8081",
            CONTROL_VALUE,
            transport=httpx.MockTransport(refuse),
        )

        # Act
        ready = await good.readiness()
        refused_ready = await bad.readiness()
        with pytest.raises(ApiError) as catalog_error:
            await bad.catalog()
        with pytest.raises(ValueError, match="private HTTP port 8081") as url_error:
            ScenarioHttpClient("https://scenario-service:8081", CONTROL_VALUE)
        with pytest.raises(ValueError, match="256 ASCII bits") as credential_error:
            ScenarioHttpClient("http://scenario-service:8081", "short")
        await good.close()
        await bad.close()

        # Assert
        self.assertEqual((), ready)
        self.assertEqual(("scenario-control-unavailable",), refused_ready)
        self.assertIs(ErrorCode.SCENARIO_NOT_FOUND, catalog_error.value.code)
        self.assertNotIn(CONTROL_VALUE, str(url_error.value))
        self.assertNotIn(CONTROL_VALUE, str(credential_error.value))

    async def test_status_cancel_and_private_refusal_mapping_remain_closed_and_typed(
        self,
    ) -> None:
        # Arrange
        def missing(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                content=_refusal("RUN_NOT_FOUND"),
                headers={"Content-Type": "application/json"},
            )

        client = ScenarioHttpClient(
            "http://scenario-service:8081",
            CONTROL_VALUE,
            transport=httpx.MockTransport(missing),
        )
        mappings = (
            (404, "RUN_NOT_FOUND", ScenarioRunNotFoundError, None),
            (
                409,
                "CANCELLATION_NOT_ESTABLISHED",
                ScenarioCancellationNotEstablishedError,
                None,
            ),
            (404, "SCENARIO_NOT_FOUND", ApiError, ErrorCode.SCENARIO_NOT_FOUND),
            (
                409,
                "SCENARIO_REVISION_MISMATCH",
                ApiError,
                ErrorCode.SCENARIO_REVISION_MISMATCH,
            ),
            (409, "RUN_CONFLICT", ApiError, ErrorCode.RUN_CONFLICT),
            (500, "INTERNAL_FAILURE", ApiError, ErrorCode.DEPENDENCY_UNAVAILABLE),
        )
        mapped_codes: list[ErrorCode | None] = []

        # Act
        with pytest.raises(ScenarioRunNotFoundError):
            await client.status("run-unknown")
        with pytest.raises(ScenarioRunNotFoundError):
            await client.start("wilderness-missing-person", 1, "mission-test-0001", "run-test-0001")
        with pytest.raises(ScenarioCancellationNotEstablishedError):
            await client.cancel("mission-test-0001", "run-test-0001", 0)
        for status, code, exception, _expected_code in mappings:
            with pytest.raises(exception) as error:
                client._raise_refusal(status, _refusal(code))
            mapped_codes.append(error.value.code if isinstance(error.value, ApiError) else None)
        await client.close()

        # Assert
        self.assertEqual(
            [
                None,
                None,
                ErrorCode.SCENARIO_NOT_FOUND,
                ErrorCode.SCENARIO_REVISION_MISMATCH,
                ErrorCode.RUN_CONFLICT,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
            ],
            mapped_codes,
        )

    async def test_transport_media_size_network_and_defensive_type_failures_are_bounded(
        self,
    ) -> None:
        # Arrange
        def wrong_media(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{}", headers={"Content-Type": "text/plain"})

        def oversized(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"x" * 8,
                headers={"Content-Type": "application/json"},
            )

        def unavailable(request: httpx.Request) -> httpx.Response:
            message = "unavailable"
            raise httpx.ConnectError(message, request=request)

        clients = [
            ScenarioHttpClient(
                "http://scenario-service:8081",
                CONTROL_VALUE,
                transport=httpx.MockTransport(handler),
            )
            for handler in (wrong_media, oversized, unavailable)
        ]
        errors: list[ErrorCode] = []

        # Act
        for client, maximum in zip(clients, (8, 1, 8), strict=True):
            with pytest.raises(ApiError) as error:
                await client._request(
                    "GET", "/internal/v1/scenarios", None, timeout=1, maximum_bytes=maximum
                )
            errors.append(error.value.code)
            await client.close()
        with pytest.raises(ApiError) as string_error:
            scenario_module._string(1)
        with pytest.raises(ApiError) as integer_error:
            scenario_module._integer(True)

        # Assert
        self.assertEqual([ErrorCode.DEPENDENCY_UNAVAILABLE] * 3, errors)
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, string_error.value.code)
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, integer_error.value.code)


@dataclass
class _KnownSessions:
    known: set[str]

    async def replay_session_known(self, session_id: str) -> bool:
        return session_id in self.known


@dataclass
class _Closable:
    fail: bool = False
    calls: int = 0

    async def close(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError


class ReplayAndCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_port_serves_exact_validator_bytes_only_for_a_durable_session(
        self,
    ) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        path = root / "wilderness-missing-person.r1.replay.json"
        exact = dashboard_fixture("replay-bundle")
        path.write_bytes(exact)
        known = _KnownSessions({"session-test-0001"})
        replay = ReplayFilePort(path, cast("StorePort", known))

        # Act
        prepared = await replay.prepare("wilderness-missing-person", 1)
        accepted = await replay.bundle("session-test-0001")
        unknown = await replay.bundle("session-unknown")

        # Assert
        self.assertEqual(exact, prepared.bundle_bytes)
        self.assertEqual({"bundle_bytes"}, {field.name for field in fields(prepared)})
        self.assertEqual(exact, accepted)
        self.assertIsNone(unknown)

    async def test_replay_readiness_identity_and_filesystem_refusals_are_exact_and_bounded(
        self,
    ) -> None:
        # Arrange
        root = Path(self.enterContext(TemporaryDirectory()))
        valid = root / "valid.replay.json"
        valid.write_bytes(dashboard_fixture("replay-bundle"))
        missing = root / "missing.replay.json"
        directory = root / "directory"
        directory.mkdir()
        store = cast("StorePort", _KnownSessions({"session-test-0001"}))
        replay = ReplayFilePort(valid, store)
        unavailable = [ReplayFilePort(path, store) for path in (missing, directory)]

        # Act
        ready = await replay.readiness()
        refused_ready = [await item.readiness() for item in unavailable]
        with pytest.raises(ApiError) as scenario_error:
            await replay.prepare("another-scenario", 1)
        with pytest.raises(ApiError) as revision_error:
            await replay.prepare("wilderness-missing-person", 99)
        with (
            patch.object(Path, "read_bytes", side_effect=OSError),
            pytest.raises(ApiError) as read_error,
        ):
            await replay.prepare("wilderness-missing-person", 1)

        # Assert
        self.assertEqual((), ready)
        self.assertEqual(
            [("validated-replay-unavailable",)] * 2,
            refused_ready,
        )
        self.assertIs(ErrorCode.SCENARIO_NOT_FOUND, scenario_error.value.code)
        self.assertIs(ErrorCode.SCENARIO_REVISION_MISMATCH, revision_error.value.code)
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, read_error.value.code)

    async def test_resource_owner_compose_and_module_entrypoint_have_bounded_cleanup(self) -> None:
        # Arrange
        environment, _asset_root = _environment(self)
        configured = configuration(environment)
        first_scenario = _Closable()
        first_store = _Closable()
        first = ProductionResources(
            cast("ScenarioHttpClient", first_scenario), cast("SqlStore", first_store)
        )
        failed_scenario = _Closable(fail=True)
        fallback_store = _Closable()
        fallback = ProductionResources(
            cast("ScenarioHttpClient", failed_scenario), cast("SqlStore", fallback_store)
        )
        composed_store = cast("SqlStore", _Closable())
        composed_scenario = cast("ScenarioHttpClient", _Closable())
        composed_replay = cast("ReplayFilePort", object())
        application = FastAPI()
        entrypoint = Mock()

        # Act
        await first.close()
        await first.close()
        with pytest.raises(RuntimeError):
            await fallback.close()
        with (
            patch.object(production, "create_engine", return_value=object()),
            patch.object(production, "create_session_factory", return_value=object()),
            patch.object(production, "SqlStore", return_value=composed_store),
            patch.object(production, "ScenarioHttpClient", return_value=composed_scenario),
            patch.object(production, "ReplayFilePort", return_value=composed_replay),
            patch.object(production, "create_app", return_value=application),
        ):
            runtime = production.compose(configured)
        with patch.object(production, "main", entrypoint):
            runpy.run_module("aerial_rescue_dashboard_api.__main__", run_name="__main__")
        closed_runtime = ProductionRuntime(
            FastAPI(), first, Path("/run/aerial-rescue/dashboard-api.sock")
        )
        with (
            patch.object(production, "configuration", return_value=configured),
            patch.object(production, "compose", return_value=closed_runtime),
            patch.object(production, "run_unix_socket"),
        ):
            await asyncio.to_thread(production.main)

        # Assert
        self.assertEqual((1, 1), (first_scenario.calls, first_store.calls))
        self.assertEqual((1, 1), (failed_scenario.calls, fallback_store.calls))
        self.assertIs(application, runtime.application)
        self.assertIs(composed_store, runtime.resources.store)
        self.assertEqual(Path(environment["DASHBOARD_SOCKET"]), runtime.socket_path)
        entrypoint.assert_called_once_with()

    async def test_production_main_closes_resources_when_the_server_refuses_before_lifespan(
        self,
    ) -> None:
        # Arrange
        @dataclass
        class Resources:
            closed: bool = False
            calls: int = 0

            async def close(self) -> None:
                self.closed = True
                self.calls += 1

        resources = Resources()
        runtime = ProductionRuntime(
            FastAPI(), cast("ProductionResources", resources), Path("/run/dashboard.sock")
        )
        configured = cast("DashboardConfiguration", object())

        # Act
        with (
            patch.object(production, "configuration", return_value=configured),
            patch.object(production, "compose", return_value=runtime),
            patch.object(production, "run_unix_socket", side_effect=RuntimeError),
            pytest.raises(RuntimeError),
        ):
            await asyncio.to_thread(production.main)

        # Assert
        self.assertEqual((True, 1), (resources.closed, resources.calls))
