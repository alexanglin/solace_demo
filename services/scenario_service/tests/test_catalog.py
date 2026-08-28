from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_scenario_service.catalog import FilesystemScenarioCatalog
from aerial_rescue_scenario_service.http_runtime import ControlError, ControlRefusal
from aerial_rescue_scenario_service.wire import ScenarioCatalogResponse, parse_wire_document

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
GOLDEN_DEFINITION: Final = REPOSITORY_ROOT / "fixtures/golden/v1/scenario/definition/baseline.json"
CATALOG_SCHEMA_ID: Final = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json"
)
PRODUCTION_ROOT: Final = REPOSITORY_ROOT / "scenarios"
SCENARIO_ID: Final = "wilderness-missing-person"


def _catalog_bytes(definition: bytes, **entry_changes: object) -> bytes:
    entry: dict[str, object] = {
        "identifier": SCENARIO_ID,
        "revision": 1,
        "definitionPath": "v1/wilderness-missing-person.r1.json",
        "definitionSha256": hashlib.sha256(definition).hexdigest(),
    }
    entry.update(entry_changes)
    return canonical.canonical_bytes({"catalogVersion": 1, "scenarios": [entry]})


def _write_catalog(root: Path, definition: bytes, catalog: bytes | None = None) -> None:
    (root / "v1").mkdir()
    (root / "v1/wilderness-missing-person.r1.json").write_bytes(definition)
    (root / "catalog.v1.json").write_bytes(catalog or _catalog_bytes(definition))


class FilesystemScenarioCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_catalog_loads_the_exact_twenty_plus_three_workload(self) -> None:
        # Arrange
        definition_path = PRODUCTION_ROOT / "v1/wilderness-missing-person.r1.json"
        definition_bytes = definition_path.read_bytes()
        catalog_document = cast(
            "dict[str, object]",
            canonical.decode((PRODUCTION_ROOT / "catalog.v1.json").read_bytes()),
        )
        catalog = FilesystemScenarioCatalog(PRODUCTION_ROOT)

        # Act
        await catalog.startup()
        loaded = await catalog.load(SCENARIO_ID, 1)
        ready = catalog.ready
        await catalog.shutdown()

        # Assert
        entries = cast("list[dict[str, object]]", catalog_document["scenarios"])
        simulated = tuple(
            member for member in loaded.members if member.participation == "SIMULATED_DRONE"
        )
        declared = tuple(
            member for member in loaded.members if member.participation == "DECLARED_ONLY"
        )
        self.assertTrue(ready)
        self.assertEqual((len(simulated), len(declared)), (20, 3))
        self.assertEqual(len(loaded.sectors), 20)
        self.assertEqual(
            tuple(member.identifier for member in declared),
            ("drone-vision-01", "drone-navigation-02", "drone-comms-03"),
        )
        self.assertEqual(
            tuple(
                heartbeat.tick_ordinal
                for heartbeat in loaded.absent_heartbeats
                if heartbeat.drone_id == "drone-sim-07"
            ),
            (2, 3, 4, 5, 6, 7),
        )
        self.assertEqual(
            entries[0]["definitionSha256"], hashlib.sha256(definition_bytes).hexdigest()
        )

    async def test_the_committed_catalog_projects_into_the_dashboard_document_after_startup(
        self,
    ) -> None:
        # Arrange
        definition = cast(
            "dict[str, object]",
            canonical.decode(
                (PRODUCTION_ROOT / "v1/wilderness-missing-person.r1.json").read_bytes()
            ),
        )
        catalog = FilesystemScenarioCatalog(PRODUCTION_ROOT)
        with pytest.raises(ControlError) as before_startup:
            catalog.catalog_response()

        # Act
        await catalog.startup()
        response = catalog.catalog_response()
        encoded = canonical.canonical_bytes(response.model_dump(mode="json", by_alias=True))
        parsed = parse_wire_document(CATALOG_SCHEMA_ID, encoded)
        await catalog.shutdown()
        with pytest.raises(ControlError) as after_shutdown:
            catalog.catalog_response()

        # Assert
        scenario = response.scenarios[0]
        self.assertEqual(
            ("scenario-catalog/v1", 1, SCENARIO_ID, 1, definition["title"], definition["summary"]),
            (
                response.catalog_version,
                len(response.scenarios),
                scenario.identifier,
                scenario.revision,
                scenario.title,
                scenario.summary,
            ),
        )
        self.assertEqual(
            (23, 20, 3, 20, 23),
            (
                scenario.declared_count,
                scenario.simulated_count,
                scenario.declared_only_count,
                len(scenario.sectors),
                len(scenario.members),
            ),
        )
        self.assertIsInstance(parsed, ScenarioCatalogResponse)
        self.assertEqual(
            (ControlRefusal.SCENARIO_NOT_FOUND, ControlRefusal.SCENARIO_NOT_FOUND),
            (before_startup.value.refusal, after_shutdown.value.refusal),
        )

    async def test_a_definition_that_fails_geometry_validation_fails_readiness_closed(
        self,
    ) -> None:
        # Arrange
        document = cast("dict[str, object]", canonical.decode(GOLDEN_DEFINITION.read_bytes()))
        polygon = cast("dict[str, list[object]]", document["searchPolygon"])
        polygon["vertices"] = [polygon["vertices"][0]] * len(polygon["vertices"])
        root = Path(self.enterContext(TemporaryDirectory()))
        _write_catalog(root, canonical.canonical_bytes(document))
        catalog = FilesystemScenarioCatalog(root)

        # Act
        await catalog.startup()
        ready = catalog.ready
        with pytest.raises(ControlError) as refused:
            await catalog.load(SCENARIO_ID, 1)

        # Assert
        self.assertFalse(ready)
        self.assertEqual(ControlRefusal.SCENARIO_NOT_FOUND, refused.value.refusal)

    async def test_startup_validates_digest_and_loads_an_exact_catalog_identity(self) -> None:
        # Arrange
        definition = GOLDEN_DEFINITION.read_bytes()
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        _write_catalog(root, definition)
        catalog = FilesystemScenarioCatalog(root)

        # Act
        before = catalog.ready
        await catalog.startup()
        loaded = await catalog.load(SCENARIO_ID, 1)
        during = catalog.ready
        await catalog.shutdown()
        after = catalog.ready
        temporary.cleanup()

        # Assert
        self.assertEqual((before, during, after), (False, True, False))
        self.assertEqual(loaded.identifier, SCENARIO_ID)
        self.assertEqual(loaded.revision, 1)
        self.assertEqual(loaded.members[0].identifier, "drone-sim-01")

    async def test_unknown_identity_and_wrong_revision_have_distinct_refusals(self) -> None:
        # Arrange
        definition = GOLDEN_DEFINITION.read_bytes()
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        _write_catalog(root, definition)
        catalog = FilesystemScenarioCatalog(root)
        await catalog.startup()

        # Act
        with pytest.raises(ControlError) as missing:
            await catalog.load("another-scenario", 1)
        with pytest.raises(ControlError) as revision:
            await catalog.load(SCENARIO_ID, 2)
        await catalog.shutdown()
        temporary.cleanup()

        # Assert
        self.assertEqual(missing.value.refusal, ControlRefusal.SCENARIO_NOT_FOUND)
        self.assertEqual(revision.value.refusal, ControlRefusal.SCENARIO_REVISION_MISMATCH)

    async def test_digest_mismatch_keeps_liveness_separate_from_readiness(self) -> None:
        # Arrange
        definition = GOLDEN_DEFINITION.read_bytes()
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        wrong_digest = "0" * 64
        _write_catalog(root, definition, _catalog_bytes(definition, definitionSha256=wrong_digest))
        catalog = FilesystemScenarioCatalog(root)

        # Act
        await catalog.startup()
        with pytest.raises(ControlError) as captured:
            await catalog.load(SCENARIO_ID, 1)
        temporary.cleanup()

        # Assert
        self.assertFalse(catalog.ready)
        self.assertEqual(captured.value.refusal, ControlRefusal.SCENARIO_REVISION_MISMATCH)
        self.assertNotIn(str(root), captured.value.detail)

    async def test_duplicate_catalog_identity_fails_closed_without_filesystem_order(self) -> None:
        # Arrange
        definition = GOLDEN_DEFINITION.read_bytes()
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        entry = {
            "identifier": SCENARIO_ID,
            "revision": 1,
            "definitionPath": "v1/wilderness-missing-person.r1.json",
            "definitionSha256": hashlib.sha256(definition).hexdigest(),
        }
        duplicate = canonical.canonical_bytes(
            {"catalogVersion": 1, "scenarios": [entry, dict(entry)]}
        )
        _write_catalog(root, definition, duplicate)
        catalog = FilesystemScenarioCatalog(root)

        # Act
        await catalog.startup()
        with pytest.raises(ControlError) as captured:
            await catalog.load(SCENARIO_ID, 1)
        temporary.cleanup()

        # Assert
        self.assertFalse(catalog.ready)
        self.assertEqual(captured.value.refusal, ControlRefusal.SCENARIO_REVISION_MISMATCH)

    async def test_symlinked_definition_and_oversized_catalog_are_refused_before_parsing(
        self,
    ) -> None:
        # Arrange
        definition = GOLDEN_DEFINITION.read_bytes()
        symlink_temporary = TemporaryDirectory()
        symlink_root = Path(symlink_temporary.name)
        outside = symlink_root / "outside.json"
        outside.write_bytes(definition)
        (symlink_root / "v1").mkdir()
        (symlink_root / "v1/wilderness-missing-person.r1.json").symlink_to(outside)
        (symlink_root / "catalog.v1.json").write_bytes(_catalog_bytes(definition))
        symlinked = FilesystemScenarioCatalog(symlink_root)
        oversized_temporary = TemporaryDirectory()
        oversized_root = Path(oversized_temporary.name)
        oversized_root.mkdir(exist_ok=True)
        (oversized_root / "catalog.v1.json").write_bytes(b"x" * (256 * 1024 + 1))
        oversized = FilesystemScenarioCatalog(oversized_root)

        # Act
        await symlinked.startup()
        await oversized.startup()
        with pytest.raises(ControlError) as symlink_refusal:
            await symlinked.load(SCENARIO_ID, 1)
        with pytest.raises(ControlError) as oversized_refusal:
            await oversized.load(SCENARIO_ID, 1)
        symlink_temporary.cleanup()
        oversized_temporary.cleanup()

        # Assert
        self.assertEqual(
            (symlink_refusal.value.refusal, oversized_refusal.value.refusal),
            (ControlRefusal.SCENARIO_REVISION_MISMATCH, ControlRefusal.SCENARIO_NOT_FOUND),
        )

    async def test_definition_identity_must_match_the_catalog_entry(self) -> None:
        # Arrange
        document = cast("dict[str, object]", canonical.decode(GOLDEN_DEFINITION.read_bytes()))
        changed = dict(document)
        changed["identifier"] = "another-scenario"
        definition = canonical.canonical_bytes(changed)
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        _write_catalog(root, definition)
        catalog = FilesystemScenarioCatalog(root)

        # Act
        await catalog.startup()
        with pytest.raises(ControlError) as captured:
            await catalog.load(SCENARIO_ID, 1)
        temporary.cleanup()

        # Assert
        self.assertFalse(catalog.ready)
        self.assertEqual(captured.value.refusal, ControlRefusal.SCENARIO_REVISION_MISMATCH)

    async def test_malformed_catalog_shapes_depth_and_root_fail_readiness_closed(self) -> None:
        # Arrange
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        malformed_root = root / "malformed"
        malformed_root.mkdir()
        (malformed_root / "catalog.v1.json").write_bytes(b"not-json")
        empty_root = root / "empty"
        empty_root.mkdir()
        (empty_root / "catalog.v1.json").write_bytes(
            canonical.canonical_bytes({"catalogVersion": 1, "scenarios": []})
        )
        deep_value: object = "leaf"
        for _ordinal in range(18):
            deep_value = {"next": deep_value}
        deep_root = root / "deep"
        deep_root.mkdir()
        (deep_root / "catalog.v1.json").write_bytes(canonical.canonical_bytes(deep_value))
        catalogs = (
            FilesystemScenarioCatalog(malformed_root),
            FilesystemScenarioCatalog(empty_root),
            FilesystemScenarioCatalog(deep_root),
            FilesystemScenarioCatalog(Path("\0")),
        )

        # Act
        refusals: list[ControlRefusal] = []
        for catalog in catalogs:
            await catalog.startup()
            with pytest.raises(ControlError) as captured:
                await catalog.load(SCENARIO_ID, 1)
            refusals.append(captured.value.refusal)
        temporary.cleanup()

        # Assert
        self.assertEqual(refusals, [ControlRefusal.SCENARIO_NOT_FOUND] * len(catalogs))
        self.assertFalse(any(catalog.ready for catalog in catalogs))

    async def test_missing_nonregular_and_schema_invalid_definitions_fail_closed(self) -> None:
        # Arrange
        definition = GOLDEN_DEFINITION.read_bytes()
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        invalid_root = root / "invalid"
        invalid_root.mkdir()
        (invalid_root / "v1").mkdir()
        invalid_document = cast("dict[str, object]", canonical.decode(definition))
        invalid_document.pop("title")
        invalid_bytes = canonical.canonical_bytes(invalid_document)
        (invalid_root / "v1/wilderness-missing-person.r1.json").write_bytes(invalid_bytes)
        (invalid_root / "catalog.v1.json").write_bytes(_catalog_bytes(invalid_bytes))
        missing_root = root / "missing"
        missing_root.mkdir()
        (missing_root / "catalog.v1.json").write_bytes(_catalog_bytes(definition))
        directory_root = root / "directory"
        directory_root.mkdir()
        (directory_root / "v1").mkdir()
        (directory_root / "v1/wilderness-missing-person.r1.json").mkdir()
        (directory_root / "catalog.v1.json").write_bytes(_catalog_bytes(definition))
        catalogs = tuple(
            FilesystemScenarioCatalog(candidate)
            for candidate in (invalid_root, missing_root, directory_root)
        )

        # Act
        refusals: list[ControlRefusal] = []
        for catalog in catalogs:
            await catalog.startup()
            with pytest.raises(ControlError) as captured:
                await catalog.load(SCENARIO_ID, 1)
            refusals.append(captured.value.refusal)
        temporary.cleanup()

        # Assert
        self.assertEqual(
            refusals,
            [ControlRefusal.SCENARIO_REVISION_MISMATCH] * len(catalogs),
        )
