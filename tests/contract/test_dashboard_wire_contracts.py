"""The complete browser-facing dashboard contract inventory.

The Playwright fixture source is deliberately not a production type authority.  These tests pin the
closed schemas and shared golden fixtures that must exist before generated TypeScript or a runtime
validator may consume dashboard input.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.view import (
    DashboardEvent,
    EventClass,
    OrderedDashboardEvent,
    ordered_event_digest,
)
from jsonschema import validators
from jsonschema.protocols import Validator
from referencing import Registry, Resource

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


def _validator_for(name: str) -> Validator:
    """Build one offline validator with every committed schema preloaded."""
    schemas = tuple(_load(path) for path in sorted(REPO_ROOT.glob("schemas/**/*.schema.json")))
    resources = ((cast("str", schema["$id"]), Resource.from_contents(schema)) for schema in schemas)
    registry = Registry().with_resources(resources)
    schema = _load(_schema_path(name))
    validator_type = validators.validator_for(schema)
    return validator_type(schema, registry=registry)


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
        expected: dict[str, tuple[list[str], list[str]]] = {}
        for name in PUBLIC_SCHEMA_NAMES:
            valid = [_fixture_path(name, "baseline").relative_to(REPO_ROOT).as_posix()]
            if name == "ordered-dashboard-event":
                valid.append(
                    _fixture_path(name, "snapshot-anchor-telemetry")
                    .relative_to(REPO_ROOT)
                    .as_posix()
                )
            if name == "replay-bundle":
                valid.append(
                    _fixture_path(name, "reducer-parity").relative_to(REPO_ROOT).as_posix()
                )
            expected[_schema_path(name).relative_to(REPO_ROOT).as_posix()] = (
                valid,
                [_fixture_path(name, "unknown-member").relative_to(REPO_ROOT).as_posix()],
            )

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

    def test_ordered_event_witness_lives_only_on_snapshot_and_replay_anchors(self) -> None:
        # Arrange
        snapshot = _load(_schema_path("dashboard-snapshot"))
        replay = _load(_schema_path("replay-bundle"))
        reduced_state = _load(_schema_path("dashboard-reduced-state"))
        event_frame = _load(_schema_path("dashboard-event-frame"))
        replay_integrity = _load(_schema_path("replay-integrity"))
        source_signal = _load(_schema_path("source-signal"))

        # Act
        anchor_members = (
            frozenset(_properties(snapshot)),
            frozenset(_properties(replay)),
        )
        forbidden_members = tuple(
            frozenset(_all_property_names(schema))
            for schema in (reduced_state, event_frame, replay_integrity, source_signal)
        )

        # Assert
        self.assertTrue(all("latestEventDigest" in members for members in anchor_members))
        self.assertTrue(all("latestEventDigest" not in members for members in forbidden_members))

    def test_source_signals_contain_only_transport_observations(self) -> None:
        # Arrange
        source_signal = _load(_schema_path("source-signal"))
        signal = cast("dict[str, object]", _properties(source_signal)["signal"])

        # Act
        values = signal["enum"]

        # Assert
        self.assertEqual(["connecting", "disconnected", "offline", "recovered"], values)


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

    def test_snapshot_validator_refuses_an_otherwise_valid_telemetry_timeline_entry(self) -> None:
        # Arrange
        snapshot = _load(_fixture_path("dashboard-snapshot", "baseline"))
        snapshot["timeline"] = [
            {
                "auditOrdinal": 4,
                "event": {
                    "kind": "droneTelemetry",
                    "eventClass": "TELEMETRY",
                    "mission": "mission-synthetic-0001",
                    "time": "2026-08-24T12:00:04.000Z",
                    "data": {
                        "droneId": "drone-sim-01",
                        "latitudeMicrodegrees": 44475000,
                        "longitudeMicrodegrees": -79245000,
                        "batteryPercent": 96,
                        "altitudeMetres": 83,
                        "headingDegrees": 45,
                        "groundSpeedCentimetresPerSecond": 950,
                    },
                },
            }
        ]
        validator = _validator_for("dashboard-snapshot")

        # Act
        errors = tuple(validator.iter_errors(cast("contract_gate.JsonObject", snapshot)))

        # Assert
        self.assertTrue(errors)

    def test_snapshot_and_replay_require_a_nullable_lowercase_digest_witness(self) -> None:
        # Arrange
        schemas = tuple(
            _load(_schema_path(name)) for name in ("dashboard-snapshot", "replay-bundle")
        )
        expected_reference = (
            "https://aerial-rescue.invalid/schemas/v1/canonical.schema.json#/$defs/lowercaseSha256"
        )

        # Act
        witness_contracts = tuple(
            (
                "latestEventDigest" in cast("list[str]", schema["required"]),
                cast("dict[str, object]", _properties(schema)["latestEventDigest"]),
            )
            for schema in schemas
        )

        # Assert
        self.assertEqual(
            (
                (
                    True,
                    {
                        "anyOf": [
                            {"type": "null"},
                            {"$ref": expected_reference},
                        ]
                    },
                ),
            )
            * 2,
            witness_contracts,
        )

    def test_snapshot_and_replay_schema_refuse_a_missing_witness(self) -> None:
        # Arrange
        cases = tuple(
            (
                _validator_for(name),
                _load(_fixture_path(name, "baseline")),
            )
            for name in ("dashboard-snapshot", "replay-bundle")
        )
        for _, document in cases:
            document.pop("latestEventDigest", None)

        # Act
        error_counts = tuple(
            len(tuple(validator.iter_errors(cast("contract_gate.JsonObject", document))))
            for validator, document in cases
        )

        # Assert
        self.assertEqual((1, 1), error_counts)

    def test_snapshot_anchor_witness_is_manifest_backed_by_ordinal_four_telemetry(self) -> None:
        # Arrange
        fixture = _fixture_path("ordered-dashboard-event", "snapshot-anchor-telemetry")
        manifest = tomllib.loads(
            (REPO_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        ordered_entry = next(
            entry
            for entry in entries
            if entry["schema"] == "schemas/v1/dashboard/ordered-dashboard-event.schema.json"
        )

        # Act
        registered = fixture.relative_to(REPO_ROOT).as_posix() in cast(
            "list[str]", ordered_entry["valid"]
        )
        event = _load(fixture)

        # Assert
        self.assertTrue(registered)
        self.assertEqual(4, event["auditOrdinal"])
        self.assertEqual("droneTelemetry", cast("dict[str, object]", event["event"])["kind"])

    def test_snapshot_and_replay_witnesses_equal_the_production_ordered_event_digest(self) -> None:
        # Arrange
        document = _load(_fixture_path("ordered-dashboard-event", "snapshot-anchor-telemetry"))
        event_document = cast("dict[str, object]", document["event"])
        ordered_event = OrderedDashboardEvent(
            cast("int", document["auditOrdinal"]),
            DashboardEvent(
                cast("str", event_document["kind"]),
                EventClass[cast("str", event_document["eventClass"])],
                cast("str", event_document["mission"]),
                cast("str", event_document["time"]),
                cast("dict[str, object]", event_document["data"]),
            ),
        )
        snapshot = _load(_fixture_path("dashboard-snapshot", "baseline"))
        replay = _load(_fixture_path("replay-bundle", "baseline"))

        # Act
        computed = ordered_event_digest(ordered_event)

        # Assert
        self.assertEqual(
            (computed, computed),
            (snapshot["latestEventDigest"], replay["latestEventDigest"]),
        )


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

    def test_in_memory_mutation_outcomes_have_no_wire_contract_artifacts(self) -> None:
        # Arrange
        schema_path = _schema_path("mutation-outcome")
        fixture_directory = FIXTURE_ROOT / "mutation-outcome"
        manifest = tomllib.loads(
            (REPO_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )

        # Act
        registered = tuple(
            entry
            for entry in cast("list[dict[str, object]]", manifest["contracts"])
            if entry["schema"] == "schemas/v1/dashboard/mutation-outcome.schema.json"
        )

        # Assert
        self.assertFalse(schema_path.exists())
        self.assertEqual((), tuple(fixture_directory.glob("*.json")))
        self.assertEqual((), registered)


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

    def test_health_carries_only_real_process_liveness(self) -> None:
        # Arrange
        health = _load(_schema_path("health"))

        # Act
        members = frozenset(_properties(health))
        required = frozenset(cast("list[str]", health["required"]))

        # Assert
        self.assertEqual(frozenset({"healthVersion", "status"}), members)
        self.assertEqual(members, required)

    def test_reset_request_is_exactly_an_empty_object(self) -> None:
        # Arrange
        reset = _load(_schema_path("reset-request"))

        # Act
        shape = (reset.get("type"), reset.get("properties"), reset.get("additionalProperties"))

        # Assert
        self.assertEqual(("object", {}, False), shape)


class DashboardReplayContractTests(unittest.TestCase):
    def test_baseline_replay_checksum_covers_canonical_material_without_self_reference(
        self,
    ) -> None:
        # Arrange
        bundle = _load(_fixture_path("replay-bundle", "baseline"))
        original = deepcopy(bundle)
        material = deepcopy(bundle)
        integrity = cast("dict[str, object]", material["integrity"])
        committed = cast(
            "str",
            cast("dict[str, object]", bundle["integrity"])["checksum"],
        )

        # Act
        removed = integrity.pop("checksum")
        computed = hashlib.sha256(canonical_bytes(material)).hexdigest()

        # Assert
        self.assertEqual(committed, removed)
        self.assertEqual(committed, computed)
        self.assertEqual(original, bundle)

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
