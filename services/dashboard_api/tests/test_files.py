"""Closed local dashboard catalog, asset, and replay repository tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.files import (
    DashboardFileError,
    DashboardFileSettings,
    FilesystemDashboardData,
    discover_asset_entrypoint,
)

_ROOT = Path(__file__).parents[3]
_DECLARED_FLEET_SIZE = 23
_SIMULATED_FLEET_SIZE = 20


@pytest.mark.asyncio
async def test_startup_expands_the_hash_bound_scenario_and_discovers_one_hashed_entrypoint(
    tmp_path: Path,
) -> None:
    # Arrange
    assets = tmp_path / "assets"
    replays = tmp_path / "replays"
    assets.mkdir()
    replays.mkdir()
    (assets / "index-12345678.js").write_bytes(b"export const ready=true;")
    repository = FilesystemDashboardData(
        DashboardFileSettings(_ROOT / "scenarios", assets, replays, 262_144)
    )

    # Act
    discovered = discover_asset_entrypoint(assets, 262_144)
    await repository.startup()
    catalog = cast("dict[str, object]", canonical.decode(repository.catalog_bytes))
    scenario = repository.scenario("wilderness-missing-person", 1)
    asset = await repository.asset("index-12345678.js")
    await repository.shutdown()

    # Assert
    assert discovered == "index-12345678.js"
    assert repository.entrypoint == "index-12345678.js"
    assert catalog["catalogVersion"] == "scenario-catalog/v1"
    assert scenario["declaredCount"] == _DECLARED_FLEET_SIZE
    assert scenario["simulatedCount"] == _SIMULATED_FLEET_SIZE
    assert asset is not None
    assert asset.media_type == "application/javascript"


@pytest.mark.asyncio
async def test_replay_loader_rejects_noncanonical_or_symlinked_material(
    tmp_path: Path,
) -> None:
    # Arrange
    assets = tmp_path / "assets"
    replays = tmp_path / "replays"
    assets.mkdir()
    replays.mkdir()
    (assets / "index-12345678.js").write_bytes(b"export {};")
    (replays / "session-synthetic-0001.json").write_bytes(b'{ "bundleVersion": "bad" }')
    repository = FilesystemDashboardData(
        DashboardFileSettings(_ROOT / "scenarios", assets, replays, 262_144)
    )
    # Act
    with pytest.raises(DashboardFileError) as captured:
        await repository.startup()
    await repository.shutdown()

    # Assert
    assert str(captured.value) == "dashboard local material is invalid"


@pytest.mark.asyncio
async def test_startup_indexes_one_validated_replay_per_scenario_revision(tmp_path: Path) -> None:
    # Arrange
    assets = tmp_path / "assets"
    replays = tmp_path / "replays"
    assets.mkdir()
    replays.mkdir()
    (assets / "index-12345678.js").write_bytes(b"export {};")
    fixture = (_ROOT / "fixtures/golden/v1/dashboard/replay-bundle/baseline.json").read_bytes()
    replay = canonical.canonical_bytes(canonical.decode(fixture))
    (replays / "replay-session-synthetic-0001.json").write_bytes(replay)
    repository = FilesystemDashboardData(
        DashboardFileSettings(_ROOT / "scenarios", assets, replays, 262_144)
    )

    # Act
    await repository.startup()
    selected = repository.replay_for_scenario("wilderness-missing-person", 1)
    await repository.shutdown()

    # Assert
    assert selected == replay
