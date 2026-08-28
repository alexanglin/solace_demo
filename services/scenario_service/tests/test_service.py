from __future__ import annotations

import ast
import ipaddress
import os
import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, override
from unittest.mock import patch

import aerial_rescue_scenario_service.service as service_module
import pytest
from aerial_rescue_scenario_service.http_contract import ROUTE_EXPECTATIONS
from aerial_rescue_scenario_service.service import (
    ListenerOptions,
    SettingsError,
    build_application,
    main,
    settings_from_environment,
)
from fastapi import FastAPI
from starlette.routing import Route

pytestmark = [pytest.mark.unit]

SCENARIO_BEARER: Final = "a" * 64
FLEET_BEARER: Final = "b" * 64


class RecordingEnvironment(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        """Copy values and begin recording exact key reads."""
        self._values = dict(values)
        self.reads: list[str] = []

    @override
    def __getitem__(self, key: str) -> str:
        """Record and return one requested environment value."""
        self.reads.append(key)
        return self._values[key]

    @override
    def __iter__(self) -> Iterator[str]:
        """Iterate available names without counting that as a value read."""
        return iter(self._values)

    @override
    def __len__(self) -> int:
        """Return the number of available names."""
        return len(self._values)


def _environment(root: Path) -> RecordingEnvironment:
    scenario_secret = root / "scenario-control.secret"
    fleet_secret = root / "fleet-control.secret"
    scenario_secret.write_text(SCENARIO_BEARER + "\n", encoding="ascii")
    fleet_secret.write_text(FLEET_BEARER + "\n", encoding="ascii")
    return RecordingEnvironment(
        {
            "SCENARIO_CONTROL_HOST": "scenario-service:8081",
            "SCENARIO_CONTROL_BEARER_FILE": str(scenario_secret),
            "SCENARIO_CATALOG_ROOT": str(root / "scenarios"),
            "FLEET_CONTROL_URL": "http://fleet-simulator:8082",
            "FLEET_CONTROL_HOST": "fleet-simulator:8082",
            "FLEET_CONTROL_BEARER_FILE": str(fleet_secret),
        }
    )


def _imported_modules(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.ImportFrom):
        return () if node.module is None else (node.module,)
    return tuple(alias.name for alias in node.names)


class ScenarioServiceCompositionTests(unittest.TestCase):
    def test_settings_read_only_the_private_http_and_catalog_inputs(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        environment = _environment(root)

        # Act
        settings = settings_from_environment(environment)
        rendered = repr(settings)
        temporary.cleanup()

        # Assert
        self.assertEqual(settings.server.host, "scenario-service:8081")
        self.assertEqual(settings.fleet.base_url, "http://fleet-simulator:8082")
        self.assertEqual(settings.catalog_root, root / "scenarios")
        self.assertNotIn(SCENARIO_BEARER, rendered)
        self.assertNotIn(FLEET_BEARER, rendered)
        self.assertFalse(any("SOLACE" in name or "BROKER" in name for name in environment.reads))
        self.assertEqual(
            set(environment.reads),
            {
                "SCENARIO_CONTROL_HOST",
                "SCENARIO_CONTROL_BEARER_FILE",
                "SCENARIO_CATALOG_ROOT",
                "FLEET_CONTROL_URL",
                "FLEET_CONTROL_HOST",
                "FLEET_CONTROL_BEARER_FILE",
            },
        )

    def test_missing_symlinked_or_reused_secret_material_fails_closed(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        missing = _environment(root)
        missing._values.pop("SCENARIO_CONTROL_HOST")
        symlinked = _environment(root)
        real_secret = root / "real.secret"
        real_secret.write_text(SCENARIO_BEARER, encoding="ascii")
        linked_secret = root / "linked.secret"
        linked_secret.symlink_to(real_secret)
        symlinked._values["SCENARIO_CONTROL_BEARER_FILE"] = str(linked_secret)
        reused = _environment(root)
        reused._values["FLEET_CONTROL_BEARER_FILE"] = reused._values["SCENARIO_CONTROL_BEARER_FILE"]

        # Act
        outcomes: list[str] = []
        for environment in (missing, symlinked, reused):
            with pytest.raises(SettingsError) as captured:
                settings_from_environment(environment)
            outcomes.append(str(captured.value))
        temporary.cleanup()

        # Assert
        self.assertEqual(
            outcomes,
            [
                "required runtime setting is missing",
                "private bearer material is unavailable",
                "private bearer credentials must be distinct",
            ],
        )
        self.assertFalse(any(SCENARIO_BEARER in outcome for outcome in outcomes))

    def test_composition_builds_the_private_routes_without_a_broker_capability(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        settings = settings_from_environment(_environment(root))

        # Act
        application = build_application(settings)
        source_paths = tuple(
            Path(__file__).parents[1].joinpath("src/aerial_rescue_scenario_service").glob("*.py")
        )
        imported_modules = {
            module
            for path in source_paths
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for module in _imported_modules(node)
        }
        temporary.cleanup()

        # Assert
        self.assertIsInstance(application, FastAPI)
        self.assertEqual(
            {route.path for route in application.routes if isinstance(route, Route)},
            {
                "/healthz",
                "/readyz",
                "/internal/v1/scenarios",
                "/internal/v1/runs",
                "/internal/v1/runs/{run_id}",
                "/internal/v1/runs/{run_id}/cancel",
                "/internal/v1/runs/{run_id}/recover",
            },
        )
        self.assertFalse(any(name.startswith("aerial_rescue_broker") for name in imported_modules))
        self.assertFalse(any("solace" in name for name in imported_modules))

    def test_deployed_application_mounts_every_route_the_contract_registry_declares(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        settings = settings_from_environment(_environment(Path(temporary.name)))
        declared = {
            (method, path.replace("{runId}", "{run_id}"))
            for method, path, _query, _body, _responses in ROUTE_EXPECTATIONS
        }

        # Act
        application = build_application(settings)
        mounted = {
            (method, route.path)
            for route in application.routes
            if isinstance(route, Route) and route.methods is not None
            for method in route.methods
        }
        temporary.cleanup()

        # Assert
        self.assertEqual(declared, {pair for pair in mounted if pair[1].startswith("/internal/")})
        self.assertEqual(
            {("GET", "/healthz"), ("GET", "/readyz")},
            {pair for pair in mounted if not pair[1].startswith("/internal/")},
        )

    def test_console_entrypoint_passes_the_bounded_internal_listener_to_uvicorn(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        environment = _environment(Path(temporary.name))
        calls: list[tuple[FastAPI, ListenerOptions]] = []

        def runner(application: FastAPI, options: ListenerOptions) -> None:
            calls.append((application, options))

        # Act
        main(environment, runner=runner)
        temporary.cleanup()

        # Assert
        self.assertEqual(len(calls), 1)
        application, options = calls[0]
        self.assertIsInstance(application, FastAPI)
        self.assertEqual(
            options,
            ListenerOptions(
                host=str(ipaddress.IPv4Address(0)),
                port=8081,
                access_log=False,
                proxy_headers=False,
                server_header=False,
                timeout_graceful_shutdown_seconds=15,
            ),
        )

    def test_empty_invalid_and_unavailable_runtime_material_is_refused_redacted(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        environments: list[RecordingEnvironment] = []
        for name in ("empty", "missing", "directory", "oversized", "nonascii", "invalid"):
            case_root = root / name
            case_root.mkdir()
            environments.append(_environment(case_root))
        environments[0]._values["SCENARIO_CONTROL_HOST"] = ""
        environments[1]._values["SCENARIO_CONTROL_BEARER_FILE"] = str(root / "absent.secret")
        directory_secret = root / "directory/bearer-directory"
        directory_secret.mkdir()
        environments[2]._values["SCENARIO_CONTROL_BEARER_FILE"] = str(directory_secret)
        oversized_secret = root / "oversized/oversized.secret"
        oversized_secret.write_bytes(b"a" * 130)
        environments[3]._values["SCENARIO_CONTROL_BEARER_FILE"] = str(oversized_secret)
        nonascii_secret = root / "nonascii/nonascii.secret"
        nonascii_secret.write_bytes(b"\xff" * 64)
        environments[4]._values["SCENARIO_CONTROL_BEARER_FILE"] = str(nonascii_secret)
        environments[5]._values["SCENARIO_CONTROL_HOST"] = "not a host"

        # Act
        refusals: list[str] = []
        for environment in environments:
            with pytest.raises(SettingsError) as captured:
                settings_from_environment(environment)
            refusals.append(str(captured.value))
        temporary.cleanup()

        # Assert
        self.assertEqual(
            refusals,
            [
                "required runtime setting is missing",
                "private bearer material is unavailable",
                "private bearer material is unavailable",
                "private bearer material is unavailable",
                "private bearer material is unavailable",
                "private runtime setting is invalid",
            ],
        )

    def test_distinct_files_cannot_reuse_a_bearer_and_newline_is_optional(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        reused = _environment(root)
        fleet_path = Path(reused._values["FLEET_CONTROL_BEARER_FILE"])
        fleet_path.write_text(SCENARIO_BEARER, encoding="ascii")
        valid_root = root / "valid"
        valid_root.mkdir()
        valid = _environment(valid_root)
        Path(valid._values["SCENARIO_CONTROL_BEARER_FILE"]).write_text(
            SCENARIO_BEARER, encoding="ascii"
        )

        # Act
        with pytest.raises(SettingsError) as captured:
            settings_from_environment(reused)
        settings = settings_from_environment(valid)
        temporary.cleanup()

        # Assert
        self.assertEqual(str(captured.value), "private bearer credentials must be distinct")
        self.assertEqual(settings.server.host, "scenario-service:8081")

    def test_base64url_bearer_encodings_preserve_the_generator_owned_entropy_contract(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        environment = _environment(root)
        Path(environment._values["SCENARIO_CONTROL_BEARER_FILE"]).write_text(
            "A" * 43, encoding="ascii"
        )
        Path(environment._values["FLEET_CONTROL_BEARER_FILE"]).write_text(
            "B" * 43, encoding="ascii"
        )

        # Act
        settings = settings_from_environment(environment)
        temporary.cleanup()

        # Assert
        self.assertEqual(settings.server.bearer, "A" * 43)
        self.assertEqual(settings.fleet.bearer, "B" * 43)

    def test_none_environment_and_default_runner_are_selected_only_inside_main(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        environment = _environment(Path(temporary.name))
        calls: list[tuple[FastAPI, ListenerOptions]] = []

        def runner(application: FastAPI, options: ListenerOptions) -> None:
            calls.append((application, options))

        # Act
        with (
            patch.object(os, "environ", environment),
            patch.object(service_module, "_run_uvicorn", runner),
        ):
            main()
        temporary.cleanup()

        # Assert
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0][0], FastAPI)
