"""The complete browser-facing dashboard contract inventory.

The Playwright fixture source is deliberately not a production type authority.  These tests pin the
closed schemas and shared golden fixtures that must exist before generated TypeScript or a runtime
validator may consume dashboard input.
"""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from typing import cast

import pytest

from tools import contract_gate

pytestmark = [pytest.mark.contract]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "schemas/v1/dashboard"
FIXTURE_ROOT = REPO_ROOT / "fixtures/golden/v1/dashboard"

PUBLIC_SCHEMA_NAMES = (
    "bootstrap",
    "dashboard-event",
    "dashboard-event-frame",
    "dashboard-reduced-state",
    "dashboard-snapshot",
    "error",
    "health",
    "mutation-outcome",
    "ordered-dashboard-event",
    "readiness",
    "replay-bundle",
    "replay-integrity",
    "reset-request",
    "reset-response",
    "scenario-catalog",
    "source-signal",
    "start-request",
    "start-response",
    "stream-overloaded",
)


def _schema_path(name: str) -> Path:
    """Return the path of one required dashboard schema."""
    return SCHEMA_ROOT / f"{name}.schema.json"


def _fixture_path(name: str, fixture: str) -> Path:
    """Return one shared golden-fixture path."""
    return FIXTURE_ROOT / name / f"{fixture}.json"


def _load(path: Path) -> dict[str, object]:
    """Load one JSON object after its inventory test has established the file exists."""
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _properties(schema: dict[str, object]) -> dict[str, object]:
    """Return the properties of one object schema."""
    return cast("dict[str, object]", schema["properties"])


def _definitions(schema: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the named definitions of one schema."""
    return cast("dict[str, dict[str, object]]", schema["$defs"])


def _all_property_names(value: object) -> frozenset[str]:
    """Return every object member named by a schema document."""
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        own = frozenset(cast("dict[str, object]", mapping.get("properties", {})))
        nested = frozenset(name for item in mapping.values() for name in _all_property_names(item))
        return own | nested
    if isinstance(value, list):
        return frozenset(name for item in value for name in _all_property_names(item))
    return frozenset()


class DashboardWireInventoryTests(unittest.TestCase):
    def test_every_public_dashboard_shape_has_a_schema_and_polarity_pair(self) -> None:
        # Arrange
        expected = {
            _schema_path(name).relative_to(REPO_ROOT).as_posix() for name in PUBLIC_SCHEMA_NAMES
        } | {
            _fixture_path(name, fixture).relative_to(REPO_ROOT).as_posix()
            for name in PUBLIC_SCHEMA_NAMES
            for fixture in ("baseline", "unknown-member")
        }

        # Act
        missing = sorted(path for path in expected if not (REPO_ROOT / path).is_file())

        # Assert
        self.assertEqual([], missing)

    def test_every_public_dashboard_schema_has_its_reserved_host_identity(self) -> None:
        # Arrange
        paths = tuple(_schema_path(name) for name in PUBLIC_SCHEMA_NAMES)
        expected = tuple(
            "https://aerial-rescue.invalid/" + path.relative_to(REPO_ROOT).as_posix()
            for path in paths
        )

        # Act
        actual = tuple(_load(path)["$id"] for path in paths)

        # Assert
        self.assertEqual(expected, actual)

    def test_every_public_dashboard_schema_is_manifest_owned_with_one_reason_pair(self) -> None:
        # Arrange
        manifest = tomllib.loads(
            (REPO_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        expected = {
            _schema_path(name).relative_to(REPO_ROOT).as_posix(): (
                [_fixture_path(name, "baseline").relative_to(REPO_ROOT).as_posix()],
                [_fixture_path(name, "unknown-member").relative_to(REPO_ROOT).as_posix()],
            )
            for name in PUBLIC_SCHEMA_NAMES
        }

        # Act
        actual = {
            cast("str", entry["schema"]): (entry["valid"], entry["invalid"])
            for entry in entries
            if cast("str", entry["schema"]).startswith("schemas/v1/dashboard/")
        }

        # Assert
        self.assertEqual(expected, actual)

    def test_the_contract_gate_accepts_the_dashboard_inventory(self) -> None:
        # Arrange
        root = REPO_ROOT

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([], errors)


class DashboardStateAuthorityTests(unittest.TestCase):
    def test_declared_only_members_have_no_connectivity_telemetry_or_sector_state(self) -> None:
        # Arrange
        reduced_state = _load(_schema_path("dashboard-reduced-state"))
        declared_only = _definitions(reduced_state)["declaredOnlyFleetMember"]

        # Act
        members = frozenset(_properties(declared_only))

        # Assert
        self.assertEqual(frozenset({"identifier", "participation"}), members)

    def test_simulated_members_do_not_duplicate_sector_assignment_or_lifecycle(self) -> None:
        # Arrange
        reduced_state = _load(_schema_path("dashboard-reduced-state"))
        simulated = _definitions(reduced_state)["simulatedFleetMember"]

        # Act
        duplicated = {"assignedMemberId", "sectorId", "sectorState", "state"} & set(
            _properties(simulated)
        )

        # Assert
        self.assertEqual(set(), duplicated)

    def test_sectors_alone_carry_assignment_and_lifecycle(self) -> None:
        # Arrange
        reduced_state = _load(_schema_path("dashboard-reduced-state"))
        sector = _definitions(reduced_state)["sector"]

        # Act
        members = frozenset(_properties(sector))

        # Assert
        self.assertEqual(frozenset({"assignedMemberId", "identifier", "state"}), members)

    def test_reduced_state_excludes_server_operation_and_presentation_state(self) -> None:
        # Arrange
        reduced_state = _load(_schema_path("dashboard-reduced-state"))
        forbidden = {
            "cursor",
            "digest",
            "filter",
            "mode",
            "operation",
            "playback",
            "runtimeId",
            "selectedMemberId",
            "timeline",
        }

        # Act
        present = forbidden & set(_all_property_names(reduced_state))

        # Assert
        self.assertEqual(set(), present)


class DashboardEventContractTests(unittest.TestCase):
    def test_the_normalized_event_retains_exactly_the_five_accepted_members(self) -> None:
        # Arrange
        event_schema = _load(_schema_path("dashboard-event"))
        variants = _definitions(event_schema)

        # Act
        variant_members = {
            name: frozenset(_properties(schema)) for name, schema in variants.items()
        }

        # Assert
        self.assertEqual(
            {
                name: frozenset({"data", "eventClass", "kind", "mission", "time"})
                for name in variants
            },
            variant_members,
        )

    def test_order_is_an_audit_wrapper_not_a_sixth_normalized_event_member(self) -> None:
        # Arrange
        ordered = _load(_schema_path("ordered-dashboard-event"))

        # Act
        members = frozenset(_properties(ordered))

        # Assert
        self.assertEqual(frozenset({"auditOrdinal", "event"}), members)

    def test_snapshot_timeline_contains_ordered_events_not_preformatted_labels(self) -> None:
        # Arrange
        snapshot = _load(_schema_path("dashboard-snapshot"))
        timeline = cast("dict[str, object]", _properties(snapshot)["timeline"])
        items = cast("dict[str, object]", timeline["items"])

        # Act
        reference = items.get("$ref")

        # Assert
        self.assertEqual(
            "https://aerial-rescue.invalid/schemas/v1/dashboard/ordered-dashboard-event.schema.json",
            reference,
        )

    def test_snapshot_timeline_excludes_telemetry_at_the_schema_boundary(self) -> None:
        # Arrange
        snapshot = _load(_schema_path("dashboard-snapshot"))
        timeline = cast("dict[str, object]", _properties(snapshot)["timeline"])
        items = cast("dict[str, object]", timeline["items"])
        event = cast(
            "dict[str, object]",
            cast("dict[str, object]", items.get("properties", {})).get("event", {}),
        )
        variants = cast("list[dict[str, object]]", event.get("anyOf", []))
        expected = {
            "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event.schema.json"
            "#/$defs/connectivityChanged",
            "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event.schema.json"
            "#/$defs/missionLifecycle",
            "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event.schema.json"
            "#/$defs/sectorLifecycle",
        }

        # Act
        references = {cast("str", variant["$ref"]) for variant in variants}

        # Assert
        self.assertEqual(expected, references)


class DashboardCollectionBoundTests(unittest.TestCase):
    def test_every_browser_collection_has_its_owned_upper_bound(self) -> None:
        # Arrange
        readiness = _load(_schema_path("readiness"))
        snapshot = _load(_schema_path("dashboard-snapshot"))
        replay = _load(_schema_path("replay-bundle"))
        catalog = _load(_schema_path("scenario-catalog"))
        catalog_definitions = _definitions(catalog)
        expected = (20, 256, 512, 256, 256)

        # Act
        actual = (
            cast("dict[str, object]", _properties(readiness)["reasons"]).get("maxItems"),
            cast("dict[str, object]", _properties(snapshot)["timeline"]).get("maxItems"),
            cast("dict[str, object]", _properties(replay)["events"]).get("maxItems"),
            cast(
                "dict[str, object]",
                _properties(catalog_definitions["polygon"])["vertices"],
            ).get("maxItems"),
            cast(
                "dict[str, object]",
                _properties(catalog_definitions["sectorPolygon"])["vertices"],
            ).get("maxItems"),
        )

        # Assert
        self.assertEqual(expected, actual)

    def test_shared_state_snapshot_and_replay_fixtures_exercise_populated_branches(self) -> None:
        # Arrange
        reduced_state = _load(_fixture_path("dashboard-reduced-state", "baseline"))
        snapshot = _load(_fixture_path("dashboard-snapshot", "baseline"))
        replay = _load(_fixture_path("replay-bundle", "baseline"))
        snapshot_state = cast("dict[str, object]", snapshot["state"])

        # Act
        populated = (
            bool(cast("list[object]", reduced_state["fleet"])),
            bool(cast("list[object]", reduced_state["sectors"])),
            snapshot["currentRun"] is not None,
            bool(cast("list[object]", snapshot["timeline"])),
            bool(cast("list[object]", snapshot_state["fleet"])),
            bool(cast("list[object]", replay["events"])),
        )

        # Assert
        self.assertEqual((True, True, True, True, True, True), populated)


class DashboardMutationContractTests(unittest.TestCase):
    def test_scenario_revision_is_integer_one_in_catalog_and_start_request(self) -> None:
        # Arrange
        catalog = _load(_schema_path("scenario-catalog"))
        start = _load(_schema_path("start-request"))
        scenario = _definitions(catalog)["scenario"]

        # Act
        revisions = (
            cast("dict[str, object]", _properties(scenario)["revision"])["const"],
            cast("dict[str, object]", _properties(start)["scenarioRevision"])["const"],
        )

        # Assert
        self.assertEqual((1, 1), revisions)

    def test_mutation_outcomes_carry_no_reducer_owned_current_mission(self) -> None:
        # Arrange
        outcome = _load(_schema_path("mutation-outcome"))

        # Act
        members = _all_property_names(outcome)

        # Assert
        self.assertNotIn("currentMission", members)

    def test_reset_request_is_exactly_an_empty_object(self) -> None:
        # Arrange
        reset = _load(_schema_path("reset-request"))

        # Act
        shape = (reset.get("type"), reset.get("properties"), reset.get("additionalProperties"))

        # Assert
        self.assertEqual(("object", {}, False), shape)


class DashboardReplayContractTests(unittest.TestCase):
    def test_replay_integrity_is_a_distinct_versioned_document(self) -> None:
        # Arrange
        bundle = _load(_schema_path("replay-bundle"))
        integrity = cast("dict[str, object]", _properties(bundle)["integrity"])

        # Act
        reference = integrity.get("$ref")

        # Assert
        self.assertEqual(
            "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-integrity.schema.json",
            reference,
        )

    def test_playback_state_is_absent_from_the_replay_bundle(self) -> None:
        # Arrange
        bundle = _load(_schema_path("replay-bundle"))
        forbidden = {"cursor", "isPlaying", "playbackPosition", "speed"}

        # Act
        present = forbidden & set(_all_property_names(bundle))

        # Assert
        self.assertEqual(set(), present)


if __name__ == "__main__":
    unittest.main()
