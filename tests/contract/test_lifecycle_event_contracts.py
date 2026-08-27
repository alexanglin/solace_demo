"""The three guaranteed lifecycle source events and their composed envelope contracts."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from typing import cast

import pytest

pytestmark = [pytest.mark.contract]

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "schemas" / "contract-manifest.toml"
SCHEMA_ID_BASE = "https://aerial-rescue.invalid/"
COMPOSED_SCHEMA_BRANCHES = 2
RUN_ID_PATTERN = "(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])"

LIFECYCLE_CONTRACTS = {
    "drone-event-connectivity-changed": {
        "eventType": "aerial-rescue.v1.drone.event.connectivity-changed",
        "family": "DRONE_EVENT",
        "required": frozenset({"missionId", "droneId", "connectivity"}),
        "stateMember": "connectivity",
        "states": ("CONNECTED", "DEGRADED", "OFFLINE"),
        "sourceKind": "connectivity-lifecycle",
    },
    "mission-event-lifecycle": {
        "eventType": "aerial-rescue.v1.mission.event.lifecycle",
        "family": "MISSION_EVENT",
        "required": frozenset({"missionId", "lifecycle"}),
        "stateMember": "lifecycle",
        "states": ("PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"),
        "sourceKind": "mission-lifecycle",
    },
    "sector-event-lifecycle": {
        "eventType": "aerial-rescue.v1.sector.event.lifecycle",
        "family": "SECTOR_EVENT",
        "required": frozenset({"missionId", "sectorId", "state", "assignedMemberId"}),
        "stateMember": "state",
        "states": ("UNASSIGNED", "ASSIGNED", "AT_RISK", "SEARCHED"),
        "sourceKind": "sector-lifecycle",
    },
}


def _schema(relative: str) -> dict[str, object]:
    """Load one schema, returning an empty object while its red-phase artifact is absent."""
    path = REPO_ROOT / relative
    if not path.is_file():
        return {}
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _registrations() -> dict[str, dict[str, object]]:
    """Return manifest registrations keyed by their schema path."""
    document = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = cast("list[dict[str, object]]", document["contracts"])
    return {cast("str", entry["schema"]): entry for entry in entries}


def _payload_path(name: str) -> str:
    """Return the repository path of one lifecycle payload schema."""
    return f"schemas/v1/payload/{name}.schema.json"


def _event_path(name: str) -> str:
    """Return the repository path of one lifecycle composed-event schema."""
    return f"schemas/v1/event/{name}.schema.json"


def _fixture_prefix(schema_path: str) -> str:
    """Return the golden-fixture directory owned by one lifecycle schema."""
    kind = "payload" if "/payload/" in schema_path else "event"
    name = Path(schema_path).name.removesuffix(".schema.json")
    return f"fixtures/golden/v1/{kind}/{name}/"


def _composition(schema: dict[str, object]) -> dict[str, object]:
    """Return the composed schema's binding properties, or an empty object while absent."""
    branches = schema.get("allOf")
    if not isinstance(branches, list) or len(branches) != COMPOSED_SCHEMA_BRANCHES:
        return {}
    branch = branches[1]
    if not isinstance(branch, dict):
        return {}
    properties = branch.get("properties")
    return cast("dict[str, object]", properties) if isinstance(properties, dict) else {}


class LifecycleArtifactInventoryTests(unittest.TestCase):
    def test_manifest_owns_each_payload_and_composed_schema_with_polarity_fixtures(self) -> None:
        # Arrange
        paths = tuple(
            path
            for name in LIFECYCLE_CONTRACTS
            for path in (_payload_path(name), _event_path(name))
        )
        registrations = _registrations()

        # Act
        ownership = {}
        for path in paths:
            registration = registrations.get(path, {})
            valid = tuple(cast("list[str]", registration.get("valid", [])))
            invalid = tuple(cast("list[str]", registration.get("invalid", [])))
            fixture_paths = valid + invalid
            ownership[path] = (
                bool(valid),
                bool(invalid),
                bool(fixture_paths),
                all(item.startswith(_fixture_prefix(path)) for item in fixture_paths),
                all((REPO_ROOT / item).is_file() for item in fixture_paths),
            )

        # Assert
        self.assertEqual(
            {path: (True, True, True, True, True) for path in paths},
            ownership,
        )


class LifecyclePayloadSchemaTests(unittest.TestCase):
    def test_payloads_are_closed_and_carry_exactly_the_source_state_members(self) -> None:
        # Arrange
        expected = {
            name: cast("frozenset[str]", contract["required"])
            for name, contract in LIFECYCLE_CONTRACTS.items()
        }

        # Act
        facts = {}
        for name in LIFECYCLE_CONTRACTS:
            schema = _schema(_payload_path(name))
            properties = schema.get("properties", {})
            facts[name] = (
                schema.get("$id"),
                schema.get("additionalProperties"),
                frozenset(cast("list[str]", schema.get("required", []))),
                frozenset(cast("dict[str, object]", properties)),
            )

        # Assert
        self.assertEqual(
            {
                name: (
                    SCHEMA_ID_BASE + _payload_path(name),
                    False,
                    members,
                    members,
                )
                for name, members in expected.items()
            },
            facts,
        )

    def test_payload_state_vocabulary_matches_the_dashboard_lifecycle_contract(self) -> None:
        # Arrange
        expected = {
            name: tuple(cast("tuple[str, ...]", contract["states"]))
            for name, contract in LIFECYCLE_CONTRACTS.items()
        }

        # Act
        actual = {}
        for name, contract in LIFECYCLE_CONTRACTS.items():
            schema = _schema(_payload_path(name))
            properties = cast("dict[str, dict[str, object]]", schema.get("properties", {}))
            state_member = cast("str", contract["stateMember"])
            actual[name] = tuple(
                cast("list[str]", properties.get(state_member, {}).get("enum", []))
            )

        # Assert
        self.assertEqual(expected, actual)

    def test_sector_assignment_is_explicitly_nullable_and_identifier_bound(self) -> None:
        # Arrange
        expected = (
            {"type": "null"},
            {"$ref": SCHEMA_ID_BASE + "schemas/v1/canonical.schema.json#/$defs/identifier"},
        )

        # Act
        schema = _schema(_payload_path("sector-event-lifecycle"))
        properties = cast("dict[str, dict[str, object]]", schema.get("properties", {}))
        alternatives = tuple(
            cast("list[dict[str, object]]", properties.get("assignedMemberId", {}).get("anyOf", []))
        )

        # Assert
        self.assertEqual(expected, alternatives)

    def test_sector_assignment_nullability_is_bound_to_the_sector_state(self) -> None:
        # Arrange
        identifier = {"$ref": SCHEMA_ID_BASE + "schemas/v1/canonical.schema.json#/$defs/identifier"}
        expected = (
            {
                "properties": {
                    "assignedMemberId": {"type": "null"},
                    "state": {"const": "UNASSIGNED"},
                }
            },
            {
                "properties": {
                    "assignedMemberId": identifier,
                    "state": {"enum": ["ASSIGNED", "AT_RISK", "SEARCHED"]},
                }
            },
        )

        # Act
        schema = _schema(_payload_path("sector-event-lifecycle"))
        variants = tuple(cast("list[dict[str, object]]", schema.get("anyOf", [])))

        # Assert
        self.assertEqual(expected, variants)


class LifecycleComposedSchemaTests(unittest.TestCase):
    def test_each_composed_schema_binds_its_type_dataschema_and_payload(self) -> None:
        # Arrange
        expected = {
            name: (
                SCHEMA_ID_BASE + _event_path(name),
                cast("str", contract["eventType"]),
                SCHEMA_ID_BASE + _payload_path(name),
            )
            for name, contract in LIFECYCLE_CONTRACTS.items()
        }

        # Act
        facts = {}
        for name in LIFECYCLE_CONTRACTS:
            schema = _schema(_event_path(name))
            properties = _composition(schema)
            event_type = cast("dict[str, object]", properties.get("type", {}))
            dataschema = cast("dict[str, object]", properties.get("dataschema", {}))
            data = cast("dict[str, object]", properties.get("data", {}))
            facts[name] = (
                schema.get("$id"),
                event_type.get("const"),
                dataschema.get("const"),
                data.get("$ref"),
            )

        # Assert
        self.assertEqual(
            {
                name: (schema_id, event_type, payload_id, payload_id)
                for name, (schema_id, event_type, payload_id) in expected.items()
            },
            facts,
        )

    def test_each_composed_schema_binds_the_event_type_to_its_run_scoped_source_kind(self) -> None:
        # Arrange
        expected = {
            name: f"^urn:aerial-rescue:{contract['sourceKind']}:{RUN_ID_PATTERN}$"
            for name, contract in LIFECYCLE_CONTRACTS.items()
        }

        # Act
        patterns = {
            name: cast(
                "dict[str, object]", _composition(_schema(_event_path(name))).get("source", {})
            ).get("pattern")
            for name in LIFECYCLE_CONTRACTS
        }

        # Assert
        self.assertEqual(expected, patterns)

    def test_each_composed_schema_owns_a_wrong_source_one_reason_negative(self) -> None:
        # Arrange
        registrations = _registrations()
        expected = {
            _event_path(
                name
            ): f"fixtures/golden/v1/event/{name}/source-not-{contract['sourceKind']}.json"
            for name, contract in LIFECYCLE_CONTRACTS.items()
        }

        # Act
        held = {
            path: fixture in cast("list[str]", registrations.get(path, {}).get("invalid", []))
            and (REPO_ROOT / fixture).is_file()
            for path, fixture in expected.items()
        }

        # Assert
        self.assertEqual({path: True for path in expected}, held)

    def test_accepted_events_record_the_run_scoped_source_conventions(self) -> None:
        # Arrange
        expected = {
            "drone-event-connectivity-changed": (
                "urn:aerial-rescue:connectivity-lifecycle:run-synthetic-0001"
            ),
            "mission-event-lifecycle": "urn:aerial-rescue:mission-lifecycle:run-synthetic-0001",
            "sector-event-lifecycle": "urn:aerial-rescue:sector-lifecycle:run-synthetic-0001",
        }

        # Act
        sources = {
            name: cast(
                "dict[str, object]",
                json.loads(
                    (REPO_ROOT / f"fixtures/golden/v1/event/{name}/baseline.json").read_text(
                        encoding="utf-8"
                    )
                ),
            ).get("source")
            if (REPO_ROOT / f"fixtures/golden/v1/event/{name}/baseline.json").is_file()
            else None
            for name in LIFECYCLE_CONTRACTS
        }

        # Assert
        self.assertEqual(expected, sources)


class LifecycleTopicFixtureTests(unittest.TestCase):
    def test_topic_case_schema_and_accepted_fixture_cover_all_three_lifecycle_types(self) -> None:
        # Arrange
        expected_cases = {
            (
                "DRONE_EVENT",
                "aerial-rescue/v1/mission-01/drone/drone-01/event/connectivity-changed",
                "aerial-rescue.v1.drone.event.connectivity-changed",
            ),
            (
                "MISSION_EVENT",
                "aerial-rescue/v1/mission-01/mission/event/lifecycle",
                "aerial-rescue.v1.mission.event.lifecycle",
            ),
            (
                "SECTOR_EVENT",
                "aerial-rescue/v1/mission-01/sector/sector-01/event/lifecycle",
                "aerial-rescue.v1.sector.event.lifecycle",
            ),
        }
        topic_case_schema = _schema("schemas/v1/topic-cases.schema.json")
        accepted = cast(
            "dict[str, object]",
            json.loads(
                (REPO_ROOT / "fixtures/golden/v1/topics/accepted.json").read_text(encoding="utf-8")
            ),
        )

        # Act
        definitions = cast("dict[str, dict[str, object]]", topic_case_schema.get("$defs", {}))
        case_properties = cast(
            "dict[str, dict[str, object]]", definitions.get("case", {}).get("properties", {})
        )
        family_names = frozenset(
            cast("list[str]", case_properties.get("family", {}).get("enum", []))
        )
        recorded = {
            (
                cast("str", case.get("family")),
                cast("str", case.get("topic")),
                cast("str", case.get("type")),
            )
            for case in cast("list[dict[str, object]]", accepted.get("cases", []))
            if case.get("type") in {item[2] for item in expected_cases}
        }

        # Assert
        self.assertEqual(
            (frozenset({"MISSION_EVENT", "SECTOR_EVENT"}), expected_cases),
            (family_names & frozenset({"MISSION_EVENT", "SECTOR_EVENT"}), recorded),
        )


if __name__ == "__main__":
    unittest.main()
