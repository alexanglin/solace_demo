"""Failure-path coverage for the closed dashboard filesystem boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api import files as files_module

_ROOT = Path(__file__).parents[3]
_MAXIMUM: Final = 262_144
_ROOT_REFUSAL_COUNT: Final = 6
_CATALOG_REFUSAL_COUNT: Final = 4


def _settings(tmp_path: Path) -> files_module.DashboardFileSettings:
    assets = tmp_path / "assets"
    replays = tmp_path / "replays"
    assets.mkdir()
    replays.mkdir()
    (assets / "index-12345678.js").write_bytes(b"export {};")
    return files_module.DashboardFileSettings(
        _ROOT / "scenarios",
        assets,
        replays,
        _MAXIMUM,
    )


def _definition(*, participation: str = "SIMULATED_DRONE") -> dict[str, object]:
    return {
        "identifier": "scenario-synthetic-0001",
        "revision": 1,
        "title": "Synthetic scenario",
        "summary": "Synthetic summary",
        "searchAreaSquareMetres": 100,
        "lastKnownLocation": {},
        "searchPolygon": {},
        "sectors": [],
        "members": [
            {
                "identifier": "drone-synthetic-0001",
                "participation": participation,
            }
        ],
    }


def _catalog_root(
    root: Path,
    definitions: tuple[tuple[str, dict[str, object]], ...],
    *,
    digest_override: str | None = None,
) -> Path:
    root.mkdir()
    entries: list[dict[str, object]] = []
    for relative, definition in definitions:
        raw = canonical.canonical_bytes(definition)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        entries.append(
            {
                "definitionPath": relative,
                "definitionSha256": digest_override or hashlib.sha256(raw).hexdigest(),
            }
        )
    (root / "catalog.v1.json").write_bytes(canonical.canonical_bytes({"scenarios": entries}))
    return root


def _replay_body(session_id: str) -> bytes:
    source = (_ROOT / "fixtures/golden/v1/dashboard/replay-bundle/baseline.json").read_bytes()
    document = dict(files_module._mapping(canonical.decode(source)))
    document["sessionId"] = session_id
    integrity = dict(files_module._mapping(document["integrity"]))
    integrity.pop("checksum", None)
    covered = dict(document)
    covered["integrity"] = integrity
    integrity["checksum"] = hashlib.sha256(canonical.canonical_bytes(covered)).hexdigest()
    document["integrity"] = integrity
    return canonical.canonical_bytes(document)


@pytest.mark.parametrize("maximum", [0, -1, True])
def test_file_settings_refuse_nonpositive_or_boolean_bounds(
    tmp_path: Path,
    maximum: int,
) -> None:
    # Arrange
    roots = (tmp_path / "scenarios", tmp_path / "assets", tmp_path / "replays")

    # Act
    with pytest.raises(files_module.DashboardFileError) as captured:
        files_module.DashboardFileSettings(*roots, maximum)

    # Assert
    assert str(captured.value) == "dashboard local material is invalid"


@pytest.mark.asyncio
async def test_repository_refuses_reads_outside_an_open_validated_epoch(tmp_path: Path) -> None:
    # Arrange
    repository = files_module.FilesystemDashboardData(_settings(tmp_path))

    # Act
    with pytest.raises(files_module.DashboardFileError) as catalog_before:
        _ = repository.catalog_bytes
    with pytest.raises(files_module.DashboardFileError) as entrypoint_before:
        _ = repository.entrypoint
    await repository.startup()
    unsupported_asset = await repository.asset("index-12345678.txt")
    escaped_asset = await repository.asset("../index-12345678.js")
    with pytest.raises(files_module.DashboardFileError) as missing_scenario:
        repository.scenario("missing-scenario", 1)
    with pytest.raises(files_module.DashboardFileError) as missing_recording:
        await repository.replay("missing-session")
    with pytest.raises(files_module.DashboardFileError) as missing_replay_selection:
        repository.replay_for_scenario("missing-scenario", 1)
    await repository.shutdown()
    with pytest.raises(files_module.DashboardFileError) as catalog_after:
        _ = repository.catalog_bytes

    # Assert
    refusals = (
        catalog_before,
        entrypoint_before,
        missing_scenario,
        missing_recording,
        missing_replay_selection,
        catalog_after,
    )
    assert all(
        str(captured.value) == "dashboard local material is invalid" for captured in refusals
    )
    assert unsupported_asset is None
    assert escaped_asset is None


def test_root_and_entrypoint_discovery_refuse_ambiguous_or_unsafe_material(
    tmp_path: Path,
) -> None:
    # Arrange
    missing = tmp_path / "missing"
    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="ascii")
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    empty = tmp_path / "empty"
    empty.mkdir()
    ambiguous = tmp_path / "ambiguous"
    ambiguous.mkdir()
    (ambiguous / "index-12345678.js").write_bytes(b"one")
    (ambiguous / "index-87654321.js").write_bytes(b"two")

    # Act
    captured: list[pytest.ExceptionInfo[files_module.DashboardFileError]] = []
    for root in (missing, regular, linked):
        with pytest.raises(files_module.DashboardFileError) as refusal:
            files_module._root(root)
        captured.append(refusal)
    for root, maximum in ((empty, 1), (ambiguous, 8), (empty, 0)):
        with pytest.raises(files_module.DashboardFileError) as refusal:
            files_module.discover_asset_entrypoint(root, maximum)
        captured.append(refusal)

    # Assert
    assert len(captured) == _ROOT_REFUSAL_COUNT
    assert all(str(item.value) == "dashboard local material is invalid" for item in captured)


def test_regular_file_guard_and_bounded_reader_fail_closed(tmp_path: Path) -> None:
    # Arrange
    root = tmp_path.resolve()
    valid = root / "valid.js"
    oversized = root / "oversized.js"
    missing = root / "missing.js"
    nested = root / "nested"
    nested.mkdir()
    nested_file = nested / "index.js"
    linked = root / "linked.js"
    valid.write_bytes(b"ok")
    oversized.write_bytes(b"large")
    nested_file.write_bytes(b"ok")
    linked.symlink_to(valid)

    # Act
    outcomes = (
        files_module._safe_regular(root, valid, 2),
        files_module._safe_regular(root, oversized, 2),
        files_module._safe_regular(root, missing, 2),
        files_module._safe_regular(root, nested_file, 2),
        files_module._safe_regular(root, nested, 2),
        files_module._safe_regular(root, linked, 2),
    )
    with pytest.raises(files_module.DashboardFileError) as oversized_read:
        files_module._read(oversized, 2)
    with pytest.raises(files_module.DashboardFileError) as directory_read:
        files_module._read(nested, 2)

    # Assert
    assert outcomes == (True, False, False, False, False, False)
    assert str(oversized_read.value) == "dashboard local material is invalid"
    assert str(directory_read.value) == "dashboard local material is invalid"


def test_catalog_rejects_untrusted_paths_digest_and_duplicate_identity(tmp_path: Path) -> None:
    # Arrange
    absolute = _catalog_root(
        tmp_path / "absolute",
        ((str(tmp_path / "outside.json"), _definition()),),
    )
    traversal = _catalog_root(
        tmp_path / "traversal",
        (("nested/../definition.json", _definition()),),
    )
    bad_digest = _catalog_root(
        tmp_path / "digest",
        (("definition.json", _definition()),),
        digest_override="0" * 64,
    )
    duplicate = _catalog_root(
        tmp_path / "duplicate",
        (("one.json", _definition()), ("two.json", _definition())),
    )

    # Act
    refusals: list[pytest.ExceptionInfo[files_module.DashboardFileError]] = []
    for root in (absolute, traversal, bad_digest, duplicate):
        with pytest.raises(files_module.DashboardFileError) as captured:
            files_module._expanded_catalog(root, _MAXIMUM)
        refusals.append(captured)

    # Assert
    assert len(refusals) == _CATALOG_REFUSAL_COUNT
    assert all(str(item.value) == "dashboard local material is invalid" for item in refusals)


def test_scenario_projection_and_closed_value_helpers_reject_unknown_shapes() -> None:
    # Arrange
    definition = _definition(participation="UNKNOWN")

    # Act
    with pytest.raises(files_module.DashboardFileError) as participation:
        files_module._public_scenario(definition)
    with pytest.raises(files_module.DashboardFileError) as mapping:
        files_module._mapping([])
    with pytest.raises(files_module.DashboardFileError) as sequence:
        files_module._sequence({}, "member")
    with pytest.raises(files_module.DashboardFileError) as text:
        files_module._text({"member": 1}, "member")
    with pytest.raises(files_module.DashboardFileError) as integer:
        files_module._integer({"member": True}, "member")

    # Assert
    refusals = (participation, mapping, sequence, text, integer)
    assert all(
        str(captured.value) == "dashboard local material is invalid" for captured in refusals
    )


def test_replay_index_refuses_filename_mismatch_and_duplicate_scenario(tmp_path: Path) -> None:
    # Arrange
    mismatch = tmp_path / "mismatch"
    mismatch.mkdir()
    (mismatch / "wrong-session.json").write_bytes(_replay_body("replay-session-synthetic-0001"))
    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    for session_id in ("replay-session-synthetic-0001", "replay-session-synthetic-0002"):
        (duplicate / f"{session_id}.json").write_bytes(_replay_body(session_id))

    # Act
    with pytest.raises(files_module.DashboardFileError) as mismatched_name:
        files_module._replay_index(mismatch, _MAXIMUM)
    with pytest.raises(files_module.DashboardFileError) as duplicate_identity:
        files_module._replay_index(duplicate, _MAXIMUM)

    # Assert
    assert str(mismatched_name.value) == "dashboard local material is invalid"
    assert str(duplicate_identity.value) == "dashboard local material is invalid"
