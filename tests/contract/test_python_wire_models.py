"""Service-local strict Python twins for the dashboard and private-control schemas."""

from __future__ import annotations

import importlib
import tomllib
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import pytest
from aerial_rescue_contracts import canonical

pytestmark = [pytest.mark.contract]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/"

DASHBOARD_SERVER_SCHEMA_IDS = frozenset(
    {
        f"{SCHEMA_PREFIX}dashboard/{name}.schema.json"
        for name in (
            "bootstrap",
            "dashboard-event-frame",
            "dashboard-event",
            "dashboard-reduced-state",
            "dashboard-snapshot",
            "error",
            "health",
            "ordered-dashboard-event",
            "readiness",
            "replay-bundle",
            "replay-integrity",
            "reset-request",
            "reset-response",
            "scenario-catalog",
            "start-request",
            "start-response",
            "stream-overloaded",
        )
    }
)
DASHBOARD_BROWSER_ONLY_SCHEMA_IDS = frozenset(
    {
        f"{SCHEMA_PREFIX}dashboard/mutation-outcome.schema.json",
        f"{SCHEMA_PREFIX}dashboard/source-signal.schema.json",
    }
)
SCENARIO_FILE_SCHEMA_IDS = frozenset(
    {
        f"{SCHEMA_PREFIX}scenario/catalog.schema.json",
        f"{SCHEMA_PREFIX}scenario/definition.schema.json",
    }
)
SCENARIO_CONTROL_SCHEMA_IDS = frozenset(
    {
        f"{SCHEMA_PREFIX}rpc/scenario-control-{suffix}.schema.json"
        for suffix in ("cancel-request", "refusal", "run-status", "start-request")
    }
)
FLEET_CONTROL_SCHEMA_IDS = frozenset(
    {
        f"{SCHEMA_PREFIX}rpc/fleet-control-{suffix}.schema.json"
        for suffix in ("cancel-request", "refusal", "run-status", "start-request")
    }
)


class DumpableModel(Protocol):
    """The Pydantic behavior the cross-service contract oracle consumes."""

    def model_dump(self, *, mode: str, by_alias: bool) -> object:
        """Return the accepted wire value."""


class WireModule(Protocol):
    """The uniform service-local registry surface fixed by ADR-0106."""

    SERVER_MODEL_BY_SCHEMA_ID: Mapping[str, object]
    CLIENT_MODEL_BY_SCHEMA_ID: Mapping[str, object]
    FILE_MODEL_BY_SCHEMA_ID: Mapping[str, object]
    BROWSER_ONLY_SCHEMA_IDS: frozenset[str]

    def parse_wire_document(self, schema_id: str, raw: str | bytes) -> DumpableModel:
        """Canonical-decode and strictly validate one owned document."""


def _wire_module(module_name: str) -> WireModule:
    """Import one service registry without making its implementation a test authority."""
    return cast("WireModule", importlib.import_module(module_name))


def _owned_model_ids(module: WireModule) -> frozenset[str]:
    """Return every server, client, and file model identity owned by one service."""
    return frozenset(
        (
            *module.SERVER_MODEL_BY_SCHEMA_ID,
            *module.CLIENT_MODEL_BY_SCHEMA_ID,
            *module.FILE_MODEL_BY_SCHEMA_ID,
        )
    )


def _fixture_path(schema_id: str, fixture_name: str) -> Path:
    """Resolve the manifest fixture convention for one reserved-host schema identity."""
    relative = schema_id.removeprefix(SCHEMA_PREFIX)
    family, filename = relative.split("/", maxsplit=1)
    stem = filename.removesuffix(".schema.json")
    return REPOSITORY_ROOT / "fixtures/golden/v1" / family / stem / f"{fixture_name}.json"


class PythonWireModelInventoryTests(unittest.TestCase):
    def test_each_service_owns_exactly_its_server_client_file_and_browser_shapes(self) -> None:
        # Arrange
        dashboard = _wire_module("aerial_rescue_dashboard_api.wire")
        scenario = _wire_module("aerial_rescue_scenario_service.wire")
        fleet = _wire_module("aerial_rescue_fleet_simulator.control_wire")
        expected = (
            (
                DASHBOARD_SERVER_SCHEMA_IDS,
                SCENARIO_CONTROL_SCHEMA_IDS,
                frozenset(),
                DASHBOARD_BROWSER_ONLY_SCHEMA_IDS,
            ),
            (
                SCENARIO_CONTROL_SCHEMA_IDS,
                FLEET_CONTROL_SCHEMA_IDS,
                SCENARIO_FILE_SCHEMA_IDS,
                frozenset(),
            ),
            (FLEET_CONTROL_SCHEMA_IDS, frozenset(), frozenset(), frozenset()),
        )

        # Act
        actual = tuple(
            (
                frozenset(module.SERVER_MODEL_BY_SCHEMA_ID),
                frozenset(module.CLIENT_MODEL_BY_SCHEMA_ID),
                frozenset(module.FILE_MODEL_BY_SCHEMA_ID),
                module.BROWSER_ONLY_SCHEMA_IDS,
            )
            for module in (dashboard, scenario, fleet)
        )

        # Assert
        self.assertEqual(expected, actual)
        self.assertEqual(
            19,
            len(DASHBOARD_SERVER_SCHEMA_IDS | DASHBOARD_BROWSER_ONLY_SCHEMA_IDS),
        )

    def test_every_owned_baseline_round_trips_through_its_strict_model(self) -> None:
        # Arrange
        modules = (
            _wire_module("aerial_rescue_dashboard_api.wire"),
            _wire_module("aerial_rescue_scenario_service.wire"),
            _wire_module("aerial_rescue_fleet_simulator.control_wire"),
        )
        cases = tuple(
            (module, schema_id, _fixture_path(schema_id, "baseline").read_bytes())
            for module in modules
            for schema_id in sorted(_owned_model_ids(module))
        )

        # Act
        round_trips = tuple(
            (
                schema_id,
                model.model_dump(mode="python", by_alias=True),
                canonical.decode(raw),
            )
            for module, schema_id, raw in cases
            for model in (module.parse_wire_document(schema_id, raw),)
        )

        # Assert
        self.assertEqual(
            tuple((schema_id, expected, expected) for schema_id, _, expected in round_trips),
            round_trips,
        )

    def test_every_owned_unknown_member_fixture_is_refused(self) -> None:
        # Arrange
        modules = (
            _wire_module("aerial_rescue_dashboard_api.wire"),
            _wire_module("aerial_rescue_scenario_service.wire"),
            _wire_module("aerial_rescue_fleet_simulator.control_wire"),
        )
        cases = tuple(
            (module, schema_id, _fixture_path(schema_id, "unknown-member").read_bytes())
            for module in modules
            for schema_id in sorted(_owned_model_ids(module))
        )

        # Act
        refusals: list[ValueError] = []
        for module, schema_id, raw in cases:
            with pytest.raises(ValueError, match="Extra inputs are not permitted") as captured:
                module.parse_wire_document(schema_id, raw)
            refusals.append(captured.value)

        # Assert
        self.assertEqual(len(cases), len(refusals))
        self.assertTrue(
            all(not isinstance(error, canonical.CanonicalizationError) for error in refusals)
        )

    def test_canonical_duplicate_keys_and_floats_are_refused_before_model_validation(self) -> None:
        # Arrange
        dashboard = _wire_module("aerial_rescue_dashboard_api.wire")
        schema_id = f"{SCHEMA_PREFIX}dashboard/health.schema.json"
        duplicate = (
            b'{"healthVersion":"dashboard-health/v1","status":"ok",'
            b'"runtimeId":"runtime-01","status":"ok"}'
        )
        floating = b'{"healthVersion":"dashboard-health/v1","status":"ok","runtimeId":1.5}'

        # Act
        with pytest.raises(canonical.CanonicalizationError) as duplicate_error:
            dashboard.parse_wire_document(schema_id, duplicate)
        with pytest.raises(canonical.CanonicalizationError) as floating_error:
            dashboard.parse_wire_document(schema_id, floating)

        # Assert
        self.assertEqual(canonical.Refusal.DUPLICATE_KEY, duplicate_error.value.refusal)
        self.assertEqual(canonical.Refusal.UNSUPPORTED_TYPE, floating_error.value.refusal)

    def test_strict_models_refuse_boolean_integer_coercion(self) -> None:
        # Arrange
        dashboard = _wire_module("aerial_rescue_dashboard_api.wire")
        schema_id = f"{SCHEMA_PREFIX}dashboard/readiness.schema.json"
        value = cast(
            "dict[str, object]",
            canonical.decode(_fixture_path(schema_id, "baseline").read_bytes()),
        )
        value["readinessVersion"] = True
        raw = canonical.canonical_bytes(value)

        # Act
        with pytest.raises(ValueError, match="Input should be") as captured:
            dashboard.parse_wire_document(schema_id, raw)

        # Assert
        self.assertNotIsInstance(captured.value, canonical.CanonicalizationError)

    def test_calendar_invalid_dashboard_instants_are_refused_semantically(self) -> None:
        # Arrange
        dashboard = _wire_module("aerial_rescue_dashboard_api.wire")
        schema_id = f"{SCHEMA_PREFIX}dashboard/dashboard-event.schema.json"
        value = cast(
            "dict[str, object]",
            canonical.decode(_fixture_path(schema_id, "baseline").read_bytes()),
        )
        value["time"] = "2026-02-31T12:00:00.000Z"
        raw = canonical.canonical_bytes(value)

        # Act
        with pytest.raises(
            ValueError,
            match="instant names a date that does not exist",
        ) as captured:
            dashboard.parse_wire_document(schema_id, raw)

        # Assert
        self.assertNotIsInstance(captured.value, canonical.CanonicalizationError)

    def test_client_and_server_twins_are_distinct_classes_with_the_same_fixture_oracle(
        self,
    ) -> None:
        # Arrange
        dashboard = _wire_module("aerial_rescue_dashboard_api.wire")
        scenario = _wire_module("aerial_rescue_scenario_service.wire")
        fleet = _wire_module("aerial_rescue_fleet_simulator.control_wire")
        pairs = tuple(
            (
                dashboard.CLIENT_MODEL_BY_SCHEMA_ID[schema_id],
                scenario.SERVER_MODEL_BY_SCHEMA_ID[schema_id],
            )
            for schema_id in sorted(SCENARIO_CONTROL_SCHEMA_IDS)
        ) + tuple(
            (
                scenario.CLIENT_MODEL_BY_SCHEMA_ID[schema_id],
                fleet.SERVER_MODEL_BY_SCHEMA_ID[schema_id],
            )
            for schema_id in sorted(FLEET_CONTROL_SCHEMA_IDS)
        )

        # Act
        distinct = tuple(client_model is not server_model for client_model, server_model in pairs)

        # Assert
        self.assertEqual((True,) * len(pairs), distinct)

    def test_every_python_model_schema_is_manifest_owned(self) -> None:
        # Arrange
        manifest = tomllib.loads(
            (REPOSITORY_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        manifest_ids = {
            f"https://aerial-rescue.invalid/{cast('str', entry['schema'])}" for entry in entries
        }
        modules = (
            _wire_module("aerial_rescue_dashboard_api.wire"),
            _wire_module("aerial_rescue_scenario_service.wire"),
            _wire_module("aerial_rescue_fleet_simulator.control_wire"),
        )

        # Act
        owned_ids = frozenset(
            schema_id for module in modules for schema_id in _owned_model_ids(module)
        )

        # Assert
        self.assertLessEqual(owned_ids, manifest_ids)
