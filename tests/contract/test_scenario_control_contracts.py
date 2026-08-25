"""The scenario-file and private run-control contract inventory fixed by ADR-0100/0107."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from typing import cast

import pytest

pytestmark = [pytest.mark.contract]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_SCHEMA_ROOT = REPO_ROOT / "schemas/v1/scenario"
RPC_SCHEMA_ROOT = REPO_ROOT / "schemas/v1/rpc"
FIXTURE_ROOT = REPO_ROOT / "fixtures/golden/v1"

SCENARIO_SCHEMA_NAMES = ("catalog", "definition")
CONTROL_SCHEMA_NAMES = (
    "fleet-control-cancel-request",
    "fleet-control-refusal",
    "fleet-control-run-status",
    "fleet-control-start-request",
    "scenario-control-cancel-request",
    "scenario-control-refusal",
    "scenario-control-run-status",
    "scenario-control-start-request",
)


def _schema_path(name: str) -> Path:
    """Return the path of one required scenario or private-control schema."""
    root = SCENARIO_SCHEMA_ROOT if name in SCENARIO_SCHEMA_NAMES else RPC_SCHEMA_ROOT
    return root / f"{name}.schema.json"


def _fixture_path(name: str, fixture: str) -> Path:
    """Return one shared golden-fixture path."""
    family = "scenario" if name in SCENARIO_SCHEMA_NAMES else "rpc"
    return FIXTURE_ROOT / family / name / f"{fixture}.json"


def _load(path: Path) -> dict[str, object]:
    """Load one JSON object after inventory establishes that the file exists."""
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _properties(schema: dict[str, object]) -> dict[str, object]:
    """Return the properties of one object schema."""
    return cast("dict[str, object]", schema["properties"])


class ScenarioControlInventoryTests(unittest.TestCase):
    def test_every_scenario_and_control_shape_has_a_schema_and_polarity_pair(self) -> None:
        # Arrange
        names = SCENARIO_SCHEMA_NAMES + CONTROL_SCHEMA_NAMES
        expected = {_schema_path(name).relative_to(REPO_ROOT).as_posix() for name in names} | {
            _fixture_path(name, fixture).relative_to(REPO_ROOT).as_posix()
            for name in names
            for fixture in ("baseline", "unknown-member")
        }

        # Act
        missing = sorted(path for path in expected if not (REPO_ROOT / path).is_file())

        # Assert
        self.assertEqual([], missing)

    def test_every_scenario_and_control_schema_has_its_reserved_host_identity(self) -> None:
        # Arrange
        paths = tuple(_schema_path(name) for name in SCENARIO_SCHEMA_NAMES + CONTROL_SCHEMA_NAMES)
        expected = tuple(
            "https://aerial-rescue.invalid/" + path.relative_to(REPO_ROOT).as_posix()
            for path in paths
        )

        # Act
        actual = tuple(_load(path)["$id"] for path in paths)

        # Assert
        self.assertEqual(expected, actual)

    def test_every_scenario_and_control_schema_is_manifest_owned(self) -> None:
        # Arrange
        names = SCENARIO_SCHEMA_NAMES + CONTROL_SCHEMA_NAMES
        manifest = tomllib.loads(
            (REPO_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        expected = {
            _schema_path(name).relative_to(REPO_ROOT).as_posix(): (
                [_fixture_path(name, "baseline").relative_to(REPO_ROOT).as_posix()],
                [_fixture_path(name, "unknown-member").relative_to(REPO_ROOT).as_posix()],
            )
            for name in names
        }

        # Act
        actual = {
            cast("str", entry["schema"]): (entry["valid"], entry["invalid"])
            for entry in entries
            if cast("str", entry["schema"]) in expected
        }

        # Assert
        self.assertEqual(expected, actual)


class ScenarioFileShapeTests(unittest.TestCase):
    def test_catalog_and_definition_keep_identity_metadata_and_simulation_inputs_distinct(
        self,
    ) -> None:
        # Arrange
        catalog = _load(_schema_path("catalog"))
        definition = _load(_schema_path("definition"))
        expected_catalog = frozenset({"catalogVersion", "scenarios"})
        expected_definition = frozenset(
            {
                "absentHeartbeats",
                "connectivityThresholds",
                "definitionVersion",
                "identifier",
                "lastKnownLocation",
                "members",
                "revision",
                "searchAreaSquareMetres",
                "searchPolygon",
                "sectors",
                "summary",
                "tickIntervalMilliseconds",
                "ticksToSweep",
                "title",
            }
        )

        # Act
        actual = (frozenset(_properties(catalog)), frozenset(_properties(definition)))

        # Assert
        self.assertEqual((expected_catalog, expected_definition), actual)

    def test_fleet_start_carries_only_run_identity_and_the_lossless_fleet_scenario(self) -> None:
        # Arrange
        start = _load(_schema_path("fleet-control-start-request"))
        scenario = cast("dict[str, object]", _properties(start)["scenario"])
        expected_start = frozenset({"controlVersion", "runId", "scenario"})
        expected_scenario = frozenset(
            {
                "absentHeartbeats",
                "connectivityThresholds",
                "drones",
                "missionId",
                "tickIntervalMilliseconds",
                "ticksToSweep",
            }
        )

        # Act
        actual = (frozenset(_properties(start)), frozenset(_properties(scenario)))

        # Assert
        self.assertEqual((expected_start, expected_scenario), actual)

    def test_control_messages_use_one_status_shape_for_every_success(self) -> None:
        # Arrange
        expected = {
            "scenario-control-start-request": frozenset(
                {"controlVersion", "missionId", "runId", "scenarioId", "scenarioRevision"}
            ),
            "scenario-control-cancel-request": frozenset({"controlVersion", "missionId", "runId"}),
            "scenario-control-refusal": frozenset({"controlVersion", "errorCode", "message"}),
            "fleet-control-cancel-request": frozenset({"controlVersion", "missionId", "runId"}),
            "fleet-control-refusal": frozenset({"controlVersion", "errorCode", "message"}),
        }
        schemas = {name: _load(_schema_path(name)) for name in expected}

        # Act
        actual = {name: frozenset(_properties(schema)) for name, schema in schemas.items()}

        # Assert
        self.assertEqual(expected, actual)

    def test_statuses_carry_stable_identity_and_separate_publication_instruments(self) -> None:
        # Arrange
        expected_common = {
            "completedTickCount",
            "controlVersion",
            "missionId",
            "runId",
            "state",
            "telemetryPublicationCount",
        }
        expected = {
            "fleet-control-run-status": frozenset(expected_common),
            "scenario-control-run-status": frozenset(
                expected_common
                | {
                    "declaredCount",
                    "declaredOnlyCount",
                    "scenarioId",
                    "scenarioRevision",
                    "simulatedCount",
                }
            ),
        }
        schemas = {name: _load(_schema_path(name)) for name in expected}

        # Act
        actual = {name: frozenset(_properties(schema)) for name, schema in schemas.items()}

        # Assert
        self.assertEqual(expected, actual)
