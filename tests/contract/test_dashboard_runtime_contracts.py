"""Pre-runtime contracts selected by ADR-0114 through ADR-0116."""

from __future__ import annotations

import hashlib
import importlib
import json
import tomllib
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts.canonical import canonical_bytes

pytestmark = [pytest.mark.contract]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/"
PUBLIC_ERROR_CODES = frozenset(
    {
        "ASSET_NOT_FOUND",
        "AUTHENTICATION_FAILED",
        "BODY_TOO_LARGE",
        "CANCELLATION_NOT_ESTABLISHED",
        "CANONICAL_JSON_INVALID",
        "DEPENDENCY_UNAVAILABLE",
        "HOST_INVALID",
        "IDEMPOTENCY_CONFLICT",
        "IDEMPOTENCY_KEY_INVALID",
        "INTERNAL_FAILURE",
        "METHOD_NOT_ALLOWED",
        "MODE_INVALID",
        "MODE_UNAVAILABLE",
        "MUTATION_REFUSED",
        "NOT_READY",
        "NO_CURRENT_RUN",
        "OPERATION_CONFLICT",
        "ORIGIN_INVALID",
        "PATH_BODY_MISMATCH",
        "PATH_INVALID",
        "REPLAY_READ_ONLY",
        "REPLAY_SESSION_NOT_FOUND",
        "REQUEST_INVALID",
        "ROUTE_NOT_FOUND",
        "RUN_CONFLICT",
        "SCENARIO_NOT_FOUND",
        "SCENARIO_REVISION_MISMATCH",
        "SCHEMA_INVALID",
        "SSE_CAPACITY_EXCEEDED",
        "UNSUPPORTED_MEDIA_TYPE",
    }
)


def _load(path: Path) -> dict[str, object]:
    """Load one committed JSON object."""
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _schema(relative_path: str) -> dict[str, object]:
    """Load one schema below the version-one schema root."""
    return _load(REPOSITORY_ROOT / "schemas/v1" / relative_path)


def _fixture(relative_path: str) -> dict[str, object]:
    """Load one version-one golden fixture."""
    return _load(REPOSITORY_ROOT / "fixtures/golden/v1" / relative_path)


def _properties(schema: dict[str, object]) -> dict[str, object]:
    """Return one object schema's properties."""
    return cast("dict[str, object]", schema["properties"])


class SessionNeutralReplayContractTests(unittest.TestCase):
    def test_replay_bundle_and_checksum_are_session_neutral(self) -> None:
        # Arrange
        schema = _schema("dashboard/replay-bundle.schema.json")
        bundle = _fixture("dashboard/replay-bundle/baseline.json")
        material = deepcopy(bundle)
        integrity = cast("dict[str, object]", material["integrity"])
        committed = cast("str", integrity.pop("checksum"))

        # Act
        computed = hashlib.sha256(canonical_bytes(material)).hexdigest()
        schema_members = frozenset(_properties(schema))
        fixture_members = frozenset(bundle)

        # Assert
        self.assertNotIn("sessionId", schema_members)
        self.assertNotIn("sessionId", fixture_members)
        self.assertEqual(committed, computed)


class ScenarioRecoveryContractTests(unittest.TestCase):
    def test_scenario_recovery_is_a_closed_manifest_owned_rpc(self) -> None:
        # Arrange
        relative_schema = "schemas/v1/rpc/scenario-control-recovery-request.schema.json"
        schema_path = REPOSITORY_ROOT / relative_schema
        baseline_path = (
            REPOSITORY_ROOT
            / "fixtures/golden/v1/rpc/scenario-control-recovery-request/baseline.json"
        )
        unknown_path = (
            REPOSITORY_ROOT
            / "fixtures/golden/v1/rpc/scenario-control-recovery-request/unknown-member.json"
        )
        manifest = tomllib.loads(
            (REPOSITORY_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )

        # Act
        schema = _load(schema_path)
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        owned = tuple(entry for entry in entries if entry["schema"] == relative_schema)

        # Assert
        self.assertTrue(baseline_path.is_file())
        self.assertTrue(unknown_path.is_file())
        self.assertEqual(
            frozenset(
                {
                    "controlVersion",
                    "missionId",
                    "runId",
                    "scenarioId",
                    "scenarioRevision",
                }
            ),
            frozenset(_properties(schema)),
        )
        self.assertEqual(False, schema["additionalProperties"])
        self.assertEqual(1, len(owned))

    def test_scenario_private_routes_add_catalog_and_recovery_only(self) -> None:
        # Arrange
        module = importlib.import_module("aerial_rescue_scenario_service.http_contract")
        expected_paths = (
            "/internal/v1/scenarios",
            "/internal/v1/runs",
            "/internal/v1/runs/{runId}",
            "/internal/v1/runs/{runId}/cancel",
            "/internal/v1/runs/{runId}/recover",
        )

        # Act
        routes = cast("tuple[tuple[object, ...], ...]", module.ROUTE_EXPECTATIONS)
        actual_paths = tuple(cast("str", route[1]) for route in routes)

        # Assert
        self.assertEqual(expected_paths, actual_paths)


class RecordingContractTests(unittest.TestCase):
    def test_recording_header_and_record_have_manifest_owned_polarity_pairs(self) -> None:
        # Arrange
        names = ("header", "record")
        manifest = tomllib.loads(
            (REPOSITORY_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        expected = {
            f"schemas/v1/recording/{name}.schema.json": (
                [f"fixtures/golden/v1/recording/{name}/baseline.json"],
                [f"fixtures/golden/v1/recording/{name}/unknown-member.json"],
            )
            for name in names
        }

        # Act
        actual = {
            cast("str", entry["schema"]): (entry["valid"], entry["invalid"])
            for entry in entries
            if cast("str", entry["schema"]).startswith("schemas/v1/recording/")
        }

        # Assert
        self.assertEqual(expected, actual)
        self.assertTrue(
            all(
                (REPOSITORY_ROOT / path).is_file()
                for schema_path, pairs in expected.items()
                for path in (schema_path, *pairs[0], *pairs[1])
            )
        )

    def test_recording_documents_expose_only_normalized_replay_material(self) -> None:
        # Arrange
        header = _schema("recording/header.schema.json")
        record = _schema("recording/record.schema.json")
        expected_header = frozenset(
            {
                "checksum",
                "checksumAlgorithm",
                "eventCount",
                "expectedFinalDigest",
                "initialState",
                "latestEventDigest",
                "recordingVersion",
                "scenarioId",
                "scenarioRevision",
            }
        )

        # Act
        header_members = frozenset(_properties(header))
        record_members = frozenset(_properties(record))

        # Assert
        self.assertEqual(expected_header, header_members)
        self.assertEqual(frozenset({"orderedEvent", "recordVersion"}), record_members)
        self.assertNotIn("sessionId", header_members)


class PublicErrorContractTests(unittest.TestCase):
    def test_public_error_codes_are_closed_before_the_http_runtime(self) -> None:
        # Arrange
        schema = _schema("dashboard/error.schema.json")
        error_code = cast("dict[str, object]", _properties(schema)["errorCode"])

        # Act
        values = frozenset(cast("list[str]", error_code.get("enum", [])))

        # Assert
        self.assertEqual(PUBLIC_ERROR_CODES, values)


if __name__ == "__main__":
    unittest.main()
