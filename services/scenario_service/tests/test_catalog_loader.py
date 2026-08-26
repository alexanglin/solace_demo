"""Strict, confined loading of the current scenario catalog contract."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

import pytest
from aerial_rescue_scenario_service.catalog import (
    CATALOG_FILENAME,
    CatalogRefusal,
    RootedScenarioSource,
    ScenarioCatalogError,
    ScenarioCatalogLoader,
    project_fleet_scenario,
)
from aerial_rescue_scenario_service.wire import DeclaredOnlyMember, SimulatedMember

pytestmark = [pytest.mark.unit]

SCENARIO_ID: Final = "wilderness-missing-person"
REVISION: Final = 1
DEFINITION_PATH: Final = "v1/wilderness-missing-person.r1.json"


def _vertices(index: int = 1) -> list[dict[str, int]]:
    """Return one explicit closed rectangular ring."""
    column = (index - 1) % 5
    row = (index - 1) // 5
    south = 44_470_000 + row * 10_000
    west = -79_250_000 + column * 10_000
    north = south + 9_000
    east = west + 9_000
    return [
        {"latitudeMicrodegrees": south, "longitudeMicrodegrees": west},
        {"latitudeMicrodegrees": south, "longitudeMicrodegrees": east},
        {"latitudeMicrodegrees": north, "longitudeMicrodegrees": east},
        {"latitudeMicrodegrees": north, "longitudeMicrodegrees": west},
        {"latitudeMicrodegrees": south, "longitudeMicrodegrees": west},
    ]


def _simulated_member(index: int) -> dict[str, object]:
    """Return one explicit simulator-bound member."""
    column = (index - 1) % 5
    row = (index - 1) // 5
    north = (100 + column * 10) * (1 if column % 2 == 0 else -1)
    return {
        "identifier": f"drone-sim-{index:02d}",
        "participation": "SIMULATED_DRONE",
        "sectorId": f"sector-{index:02d}",
        "latitudeMicrodegrees": 44_474_500 + row * 10_000,
        "longitudeMicrodegrees": -79_245_500 + column * 10_000,
        "altitudeMetres": 82 + index,
        "headingDegrees": 0 if north > 0 else 180,
        "groundSpeedCentimetresPerSecond": 1110 + column * 111,
        "batteryPermille": 1000 - index * 10,
        "northMicrodegreesPerTick": north,
        "eastMicrodegreesPerTick": 0,
        "batteryDrainPermillePerTick": 1 + (index - 1) % 3,
    }


def _definition(**overrides: object) -> dict[str, object]:
    """Return a current-schema definition satisfying the prepared workload."""
    simulated = [_simulated_member(index) for index in range(1, 21)]
    declared = [
        {
            "identifier": "drone-vision-01",
            "participation": "DECLARED_ONLY",
            "role": "vision",
            "executionLabel": "DECLARED ONLY — NOT EXECUTED",
        },
        {
            "identifier": "drone-navigation-02",
            "participation": "DECLARED_ONLY",
            "role": "navigation",
            "executionLabel": "DECLARED ONLY — NOT EXECUTED",
        },
        {
            "identifier": "drone-comms-03",
            "participation": "DECLARED_ONLY",
            "role": "communications",
            "executionLabel": "DECLARED ONLY — NOT EXECUTED",
        },
    ]
    document: dict[str, object] = {
        "definitionVersion": 1,
        "identifier": SCENARIO_ID,
        "revision": REVISION,
        "title": "Wilderness Missing Person",
        "summary": "Twenty deterministic aircraft sweep twenty synthetic sectors.",
        "searchAreaSquareMetres": 16_891_440,
        "lastKnownLocation": {
            "label": "North ridge trail",
            "latitudeMicrodegrees": 44_493_100,
            "longitudeMicrodegrees": -79_228_400,
        },
        "searchPolygon": {"vertices": _vertices()},
        "sectors": [
            {"identifier": f"sector-{index:02d}", "vertices": _vertices(index)}
            for index in range(1, 21)
        ],
        "members": [*simulated, *declared],
        "tickIntervalMilliseconds": 1000,
        "connectivityThresholds": {
            "missesToDegraded": 3,
            "missesToOffline": 6,
            "heartbeatsToRecover": 2,
        },
        "ticksToSweep": 12,
        "absentHeartbeats": [
            {"droneId": "drone-sim-07", "tickOrdinal": tick} for tick in range(2, 8)
        ],
    }
    return document | overrides


def _encoded(document: object) -> bytes:
    """Return stable UTF-8 document bytes."""
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def _catalog(definition: bytes, **entry_overrides: object) -> bytes:
    """Return a catalog binding the prepared identity to the supplied bytes."""
    entry: dict[str, object] = {
        "identifier": SCENARIO_ID,
        "revision": REVISION,
        "definitionPath": DEFINITION_PATH,
        "definitionSha256": hashlib.sha256(definition).hexdigest(),
    }
    entry.update(entry_overrides)
    return _encoded({"catalogVersion": 1, "scenarios": [entry]})


def _temporary_root(case: unittest.TestCase, definition: bytes) -> Path:
    """Write one valid catalog pair under a test-owned root."""
    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    (root / "v1").mkdir()
    (root / DEFINITION_PATH).write_bytes(definition)
    (root / CATALOG_FILENAME).write_bytes(_catalog(definition))
    return root


@dataclass
class _MemorySource:
    """Return exact in-memory documents while recording requested names."""

    documents: dict[str, bytes]
    requested: list[str] = field(default_factory=list)

    def read(self, relative_name: str) -> bytes:
        """Return the named document."""
        self.requested.append(relative_name)
        return self.documents[relative_name]


def _memory_loader(definition: dict[str, object]) -> tuple[ScenarioCatalogLoader, _MemorySource]:
    """Return a loader over one valid in-memory catalog pair."""
    content = _encoded(definition)
    source = _MemorySource({CATALOG_FILENAME: _catalog(content), DEFINITION_PATH: content})
    return ScenarioCatalogLoader(source), source


class AcceptedCatalogTests(unittest.TestCase):
    def test_current_schema_loads_and_projects_only_the_twenty_simulated_members(self) -> None:
        # Arrange
        loader, source = _memory_loader(_definition())

        # Act
        loaded = loader.load(SCENARIO_ID, REVISION)
        projected = project_fleet_scenario(loaded.definition, "mission-r2")

        # Assert
        self.assertEqual([CATALOG_FILENAME, DEFINITION_PATH], source.requested)
        self.assertEqual(
            (SCENARIO_ID, REVISION), (loaded.definition.identifier, loaded.definition.revision)
        )
        self.assertEqual(20, len(projected.drones))
        self.assertEqual(
            tuple(f"drone-sim-{index:02d}" for index in range(1, 21)),
            tuple(drone.drone_id for drone in projected.drones),
        )
        self.assertEqual(
            {"drone-vision-01", "drone-navigation-02", "drone-comms-03"},
            {
                member.identifier
                for member in loaded.definition.members
                if isinstance(member, DeclaredOnlyMember)
            },
        )
        self.assertTrue(all(drone.drone_id.startswith("drone-sim-") for drone in projected.drones))
        self.assertNotIn("seed", projected.model_dump(by_alias=True))

    def test_projection_preserves_every_simulator_bound_value(self) -> None:
        # Arrange
        loader, _ = _memory_loader(_definition())
        accepted = loader.load(SCENARIO_ID, REVISION).definition
        declared = next(
            member
            for member in accepted.members
            if isinstance(member, SimulatedMember) and member.identifier == "drone-sim-07"
        )

        # Act
        projected = project_fleet_scenario(accepted, "mission-r2")
        drone = next(item for item in projected.drones if item.drone_id == declared.identifier)

        # Assert
        self.assertEqual("mission-r2", projected.mission_id)
        self.assertEqual(
            (
                declared.sector_id,
                declared.latitude_microdegrees,
                declared.longitude_microdegrees,
                declared.altitude_metres,
                declared.heading_degrees,
                declared.ground_speed_centimetres_per_second,
                declared.battery_permille,
                declared.north_microdegrees_per_tick,
                declared.east_microdegrees_per_tick,
                declared.battery_drain_permille_per_tick,
            ),
            (
                drone.sector_id,
                drone.latitude_microdegrees,
                drone.longitude_microdegrees,
                drone.altitude_metres,
                drone.heading_degrees,
                drone.ground_speed_centimetres_per_second,
                drone.battery_permille,
                drone.north_microdegrees_per_tick,
                drone.east_microdegrees_per_tick,
                drone.battery_drain_permille_per_tick,
            ),
        )
        self.assertEqual(1000, projected.tick_interval_milliseconds)
        self.assertEqual(12, projected.ticks_to_sweep)
        self.assertEqual(list(accepted.absent_heartbeats), projected.absent_heartbeats)

    def test_private_catalog_response_is_projected_from_the_loaded_definition(self) -> None:
        # Arrange
        loader, source = _memory_loader(_definition())

        # Act
        response = loader.catalog_response()
        scenario = response.scenarios[0]

        # Assert
        self.assertEqual([CATALOG_FILENAME, DEFINITION_PATH], source.requested)
        self.assertEqual("scenario-catalog/v1", response.catalog_version)
        self.assertEqual(
            (23, 20, 3),
            (scenario.declared_count, scenario.simulated_count, scenario.declared_only_count),
        )
        self.assertEqual(20, len(scenario.sectors))
        self.assertEqual(
            ["SIMULATED"] * 20 + ["DECLARED_ONLY"] * 3,
            [member.participation for member in scenario.members],
        )

    def test_escaped_quotes_and_brackets_inside_text_do_not_count_as_nesting(self) -> None:
        # Arrange
        summary = 'A quote " and literal [[{{ containers remain text.'
        loader, _ = _memory_loader(_definition(summary=summary))

        # Act
        loaded = loader.load(SCENARIO_ID, REVISION)

        # Assert
        self.assertEqual(summary, loaded.definition.summary)

    def test_unprepared_definition_cannot_enter_the_public_catalog_projection(self) -> None:
        # Arrange
        definition = _definition()
        members = cast("list[dict[str, object]]", definition["members"])
        loader, _ = _memory_loader(_definition(members=members[:-4] + members[-3:]))

        # Act
        with pytest.raises(ScenarioCatalogError) as raised:
            loader.catalog_response()

        # Assert
        self.assertIs(CatalogRefusal.CATALOG_RESPONSE_INVALID, raised.value.reason)


class CatalogResolutionTests(unittest.TestCase):
    def test_unknown_identity_and_revision_have_distinct_refusals(self) -> None:
        # Arrange
        loader, _ = _memory_loader(_definition())

        # Act
        refused: list[CatalogRefusal] = []
        for identity, revision in (("unknown-scenario", 1), (SCENARIO_ID, 2)):
            with pytest.raises(ScenarioCatalogError) as raised:
                loader.load(identity, revision)
            refused.append(raised.value.reason)

        # Assert
        self.assertEqual(
            [CatalogRefusal.SCENARIO_NOT_FOUND, CatalogRefusal.REVISION_NOT_FOUND], refused
        )

    def test_duplicate_catalog_identity_is_refused_before_selection(self) -> None:
        # Arrange
        definition = _encoded(_definition())
        entry = json.loads(_catalog(definition))["scenarios"][0]
        catalog = _encoded({"catalogVersion": 1, "scenarios": [entry, entry]})
        loader = ScenarioCatalogLoader(
            _MemorySource({CATALOG_FILENAME: catalog, DEFINITION_PATH: definition})
        )

        # Act
        with pytest.raises(ScenarioCatalogError) as raised:
            loader.load(SCENARIO_ID, REVISION)

        # Assert
        self.assertIs(CatalogRefusal.DUPLICATE_CATALOG_IDENTITY, raised.value.reason)

    def test_definition_identity_must_equal_the_catalog_identity(self) -> None:
        # Arrange
        loader, _ = _memory_loader(_definition(identifier="another-scenario"))

        # Act
        with pytest.raises(ScenarioCatalogError) as raised:
            loader.load(SCENARIO_ID, REVISION)

        # Assert
        self.assertIs(CatalogRefusal.DEFINITION_IDENTITY_MISMATCH, raised.value.reason)

    def test_digest_is_checked_before_invalid_definition_bytes_are_parsed(self) -> None:
        # Arrange
        invalid = b'{"definitionVersion": 1'
        catalog = _catalog(invalid, definitionSha256="0" * 64)
        loader = ScenarioCatalogLoader(
            _MemorySource({CATALOG_FILENAME: catalog, DEFINITION_PATH: invalid})
        )

        # Act
        with pytest.raises(ScenarioCatalogError) as raised:
            loader.load(SCENARIO_ID, REVISION)

        # Assert
        self.assertIs(CatalogRefusal.DIGEST_MISMATCH, raised.value.reason)


class RawDocumentTests(unittest.TestCase):
    def test_duplicate_keys_floats_utf16_and_excessive_depth_are_refused(self) -> None:
        # Arrange
        deep = ("[" * 17 + "]" * 17).encode()
        documents = (
            b'{"catalogVersion":1,"catalogVersion":1,"scenarios":[]}',
            b'{"catalogVersion":1.0,"scenarios":[]}',
            b'{"catalogVersion":1,"scenarios":[]}',
            '{"catalogVersion":1,"scenarios":[]}'.encode("utf-16"),
            deep,
        )

        # Act
        reasons: list[CatalogRefusal] = []
        for document in documents:
            loader = ScenarioCatalogLoader(_MemorySource({CATALOG_FILENAME: document}))
            with pytest.raises(ScenarioCatalogError) as raised:
                loader.catalog()
            reasons.append(raised.value.reason)

        # Assert
        self.assertEqual(
            [
                CatalogRefusal.DOCUMENT_INVALID,
                CatalogRefusal.DOCUMENT_INVALID,
                CatalogRefusal.DOCUMENT_INVALID,
                CatalogRefusal.DOCUMENT_ENCODING,
                CatalogRefusal.DOCUMENT_TOO_DEEP,
            ],
            reasons,
        )


class CrossFieldDefinitionTests(unittest.TestCase):
    def test_unclosed_or_degenerate_geometry_is_refused(self) -> None:
        # Arrange
        unclosed = _definition(searchPolygon={"vertices": _vertices()[:-1]})
        point = _vertices()[0]
        degenerate = _definition(searchPolygon={"vertices": [point, point, point, point]})

        # Act
        reasons: list[CatalogRefusal] = []
        for definition in (unclosed, degenerate):
            loader, _ = _memory_loader(definition)
            with pytest.raises(ScenarioCatalogError) as raised:
                loader.load(SCENARIO_ID, REVISION)
            reasons.append(raised.value.reason)

        # Assert
        self.assertEqual(
            [CatalogRefusal.POLYGON_NOT_CLOSED, CatalogRefusal.POLYGON_DEGENERATE], reasons
        )

    def test_duplicate_sector_member_or_assignment_is_refused(self) -> None:
        # Arrange
        baseline = _definition()
        sectors = list(cast("list[dict[str, object]]", baseline["sectors"]))
        sectors[1] = {**sectors[1], "identifier": "sector-01"}
        members = list(cast("list[dict[str, object]]", baseline["members"]))
        duplicate_member = [*members, dict(members[0])]
        duplicate_assignment = list(members)
        duplicate_assignment[1] = {**duplicate_assignment[1], "sectorId": "sector-01"}
        definitions = (
            _definition(sectors=sectors),
            _definition(members=duplicate_member),
            _definition(members=duplicate_assignment),
        )

        # Act
        reasons: list[CatalogRefusal] = []
        for definition in definitions:
            loader, _ = _memory_loader(definition)
            with pytest.raises(ScenarioCatalogError) as raised:
                loader.load(SCENARIO_ID, REVISION)
            reasons.append(raised.value.reason)

        # Assert
        self.assertEqual(
            [
                CatalogRefusal.DUPLICATE_SECTOR,
                CatalogRefusal.DUPLICATE_MEMBER,
                CatalogRefusal.DUPLICATE_SECTOR_ASSIGNMENT,
            ],
            reasons,
        )

    def test_unknown_sector_and_invalid_heartbeat_targets_are_refused(self) -> None:
        # Arrange
        baseline = _definition()
        members = list(cast("list[dict[str, object]]", baseline["members"]))
        members[0] = {**members[0], "sectorId": "sector-99"}
        definitions = (
            _definition(members=members),
            _definition(absentHeartbeats=[{"droneId": "drone-vision-01", "tickOrdinal": 2}]),
            _definition(
                absentHeartbeats=[
                    {"droneId": "drone-sim-07", "tickOrdinal": 2},
                    {"droneId": "drone-sim-07", "tickOrdinal": 2},
                ]
            ),
        )

        # Act
        reasons: list[CatalogRefusal] = []
        for definition in definitions:
            loader, _ = _memory_loader(definition)
            with pytest.raises(ScenarioCatalogError) as raised:
                loader.load(SCENARIO_ID, REVISION)
            reasons.append(raised.value.reason)

        # Assert
        self.assertEqual(
            [
                CatalogRefusal.UNKNOWN_SECTOR,
                CatalogRefusal.HEARTBEAT_MEMBER_NOT_SIMULATED,
                CatalogRefusal.DUPLICATE_HEARTBEAT,
            ],
            reasons,
        )


class RootedSourceTests(unittest.TestCase):
    def test_invalid_names_are_refused_before_an_absent_root_is_consulted(self) -> None:
        # Arrange
        source = RootedScenarioSource(Path("/root-that-does-not-exist"))
        names = ("/etc/hosts", "../catalog.json", "v1//definition.json", "v1\\definition.json")

        # Act
        reasons: list[CatalogRefusal] = []
        for name in names:
            with pytest.raises(ScenarioCatalogError) as raised:
                source.read(name)
            reasons.append(raised.value.reason)

        # Assert
        self.assertEqual([CatalogRefusal.PATH_INVALID] * len(names), reasons)

    def test_regular_files_are_bounded_and_returned_exactly(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        content = b"0123456789abcdef"
        (root / CATALOG_FILENAME).write_bytes(content)

        # Act
        accepted = RootedScenarioSource(root, maximum_bytes=len(content)).read(CATALOG_FILENAME)
        with pytest.raises(ScenarioCatalogError) as raised:
            RootedScenarioSource(root, maximum_bytes=len(content) - 1).read(CATALOG_FILENAME)

        # Assert
        self.assertEqual(content, accepted)
        self.assertIs(CatalogRefusal.DOCUMENT_TOO_LARGE, raised.value.reason)

    def test_missing_nonregular_and_escaping_files_are_refused(self) -> None:
        # Arrange
        enclosing = Path(self.enterContext(tempfile.TemporaryDirectory()))
        outside = enclosing / "outside.json"
        outside.write_bytes(b"{}")
        root = enclosing / "root"
        root.mkdir()
        (root / "directory").mkdir()
        (root / "escape.json").symlink_to(outside)
        source = RootedScenarioSource(root)

        # Act
        reasons: list[CatalogRefusal] = []
        for name in ("missing.json", "directory", "escape.json"):
            with pytest.raises(ScenarioCatalogError) as raised:
                source.read(name)
            reasons.append(raised.value.reason)

        # Assert
        self.assertEqual(
            [
                CatalogRefusal.FILE_MISSING,
                CatalogRefusal.FILE_NOT_REGULAR,
                CatalogRefusal.PATH_OUTSIDE_ROOT,
            ],
            reasons,
        )

    def test_a_named_pipe_is_refused_without_opening_it(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        os.mkfifo(root / "scenario.pipe")

        # Act
        with pytest.raises(ScenarioCatalogError) as raised:
            RootedScenarioSource(root).read("scenario.pipe")

        # Assert
        self.assertIs(CatalogRefusal.FILE_NOT_REGULAR, raised.value.reason)


class CommittedScenarioTests(unittest.TestCase):
    def test_committed_wilderness_pair_is_the_complete_prepared_workload(self) -> None:
        # Arrange
        repository_root = Path(__file__).resolve().parents[3]
        loader = ScenarioCatalogLoader(RootedScenarioSource(repository_root / "scenarios"))

        # Act
        loaded = loader.load(SCENARIO_ID, REVISION)
        projected = project_fleet_scenario(loaded.definition, "mission-production-e2e")
        simulated = [
            member for member in loaded.definition.members if isinstance(member, SimulatedMember)
        ]
        declared = [
            member for member in loaded.definition.members if isinstance(member, DeclaredOnlyMember)
        ]

        # Assert
        self.assertEqual(
            (23, 20, 3), (len(loaded.definition.members), len(simulated), len(declared))
        )
        self.assertEqual(20, len(loaded.definition.sectors))
        self.assertEqual(
            tuple(f"drone-sim-{index:02d}" for index in range(1, 21)),
            tuple(drone.drone_id for drone in projected.drones),
        )
        self.assertEqual(
            (
                ("drone-vision-01", "vision"),
                ("drone-navigation-02", "navigation"),
                ("drone-comms-03", "communications"),
            ),
            tuple((member.identifier, member.role) for member in declared),
        )
        self.assertTrue(
            all(member.execution_label == "DECLARED ONLY — NOT EXECUTED" for member in declared)
        )
        self.assertEqual(
            (1000, 12), (projected.tick_interval_milliseconds, projected.ticks_to_sweep)
        )
        self.assertEqual(
            [("drone-sim-07", tick) for tick in range(2, 8)],
            [(absence.drone_id, absence.tick_ordinal) for absence in projected.absent_heartbeats],
        )

    def test_committed_documents_contain_no_seed_member(self) -> None:
        # Arrange
        repository_root = Path(__file__).resolve().parents[3]
        paths = (
            repository_root / "scenarios" / CATALOG_FILENAME,
            repository_root / "scenarios" / DEFINITION_PATH,
        )

        # Act
        keys: set[str] = set()
        for path in paths:
            pending: list[object] = [json.loads(path.read_bytes())]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    keys.update(value)
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)

        # Assert
        self.assertNotIn("seed", keys)
