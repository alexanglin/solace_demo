"""Production scenario configuration byte-bound enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerial_rescue_scenario_service.main import (
    ScenarioConfigurationError,
    ScenarioConfigurationRefusal,
    configuration,
)

pytestmark = [pytest.mark.unit]


def test_configuration_refuses_an_oversized_private_secret_before_decoding(
    tmp_path: Path,
) -> None:
    # Arrange
    scenario_secret = tmp_path / "scenario.secret"
    fleet_secret = tmp_path / "fleet.secret"
    scenario_root = tmp_path / "scenarios"
    scenario_secret.write_bytes(b"x" * 4097)
    fleet_secret.write_text("c" * 64, encoding="ascii")
    scenario_root.mkdir()
    environment = {
        "SCENARIO_CONTROL_SECRET_FILE": str(scenario_secret),
        "FLEET_CONTROL_SECRET_FILE": str(fleet_secret),
        "SCENARIO_ROOT": str(scenario_root),
    }

    # Act
    with pytest.raises(ScenarioConfigurationError) as refused:
        configuration(environment)

    # Assert
    assert refused.value.refusal is ScenarioConfigurationRefusal.MATERIAL_INVALID
    assert refused.value.value == "SCENARIO_CONTROL_SECRET_FILE"
    assert "x" * 64 not in str(refused.value)
