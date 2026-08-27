"""Production scenario-service composition and resource ownership."""

from __future__ import annotations

import importlib
import runpy
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from aerial_rescue_scenario_service.main import (
    ScenarioConfiguration,
    ScenarioConfigurationError,
    configuration,
    main,
)

pytestmark = [pytest.mark.unit]

SCENARIO_VALUE: Final = "b" * 64
FLEET_VALUE: Final = "c" * 64


def _environment(root: Path) -> dict[str, str]:
    """Write bounded synthetic secrets and return the production environment shape."""
    scenario = root / "scenario"
    fleet = root / "fleet"
    scenario_root = root / "scenarios"
    scenario.write_text(SCENARIO_VALUE, encoding="ascii")
    fleet.write_text(FLEET_VALUE, encoding="ascii")
    scenario_root.mkdir()
    return {
        "SCENARIO_CONTROL_SECRET_FILE": str(scenario),
        "FLEET_CONTROL_SECRET_FILE": str(fleet),
        "SCENARIO_ROOT": str(scenario_root),
    }


class ScenarioConfigurationTests(unittest.TestCase):
    def test_module_import_does_not_start_the_process(self) -> None:
        # Arrange
        module_name = "aerial_rescue_scenario_service.__main__"

        # Act
        namespace = runpy.run_module(module_name, run_name="scenario-import-check")

        # Assert
        self.assertEqual("scenario-import-check", namespace["__name__"])

    def test_module_execution_propagates_the_redacted_main_status(self) -> None:
        # Arrange
        module_name = "aerial_rescue_scenario_service.__main__"

        # Act
        with (
            patch("aerial_rescue_scenario_service.main.main", return_value=19) as process,
            pytest.raises(SystemExit) as stopped,
        ):
            runpy.run_module(module_name, run_name="__main__")

        # Assert
        self.assertEqual(19, stopped.value.code)
        process.assert_called_once_with()

    def test_configuration_uses_distinct_indirected_hop_secrets_and_redacts_repr(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))

        # Act
        configured = configuration(environment)
        rendered = repr(configured)

        # Assert
        self.assertIsInstance(configured, ScenarioConfiguration)
        self.assertEqual(SCENARIO_VALUE, configured.control_secret)
        self.assertEqual(FLEET_VALUE, configured.fleet_control_secret)
        self.assertNotIn(SCENARIO_VALUE, rendered)
        self.assertNotIn(FLEET_VALUE, rendered)

    def test_configuration_refuses_shared_hop_secrets_and_non_directory_scenario_root(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        Path(environment["FLEET_CONTROL_SECRET_FILE"]).write_text(SCENARIO_VALUE, encoding="ascii")
        not_directory = {
            **environment,
            "SCENARIO_ROOT": environment["SCENARIO_CONTROL_SECRET_FILE"],
        }

        # Act
        with pytest.raises(ScenarioConfigurationError) as shared:
            configuration(environment)
        Path(environment["FLEET_CONTROL_SECRET_FILE"]).write_text(FLEET_VALUE, encoding="ascii")
        with pytest.raises(ScenarioConfigurationError) as root:
            configuration(not_directory)

        # Assert
        self.assertEqual("FLEET_CONTROL_SECRET_FILE", shared.value.value)
        self.assertEqual("SCENARIO_ROOT", root.value.value)
        self.assertNotIn(SCENARIO_VALUE, str(shared.value))

    def test_main_closes_http_resources_after_brokerless_listener_exit(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))

        # Act
        with (
            patch("aerial_rescue_scenario_service.main.httpx.Client") as http_client,
            patch("aerial_rescue_scenario_service.main.serve") as listener,
        ):
            status = main(environment=environment)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(1, listener.call_count)
        http_client.return_value.close.assert_called_once_with()

    def test_main_opens_no_broker_when_http_construction_fails(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))

        # Act
        with (
            patch(
                "aerial_rescue_scenario_service.main.httpx.Client",
                side_effect=RuntimeError("synthetic HTTP construction failure"),
            ),
            pytest.raises(RuntimeError, match="synthetic HTTP construction failure"),
        ):
            main(environment=environment)

        # Assert
        self.assertNotIn("SOLACE_BROKER_URL", environment)

    def test_missing_insecure_and_unreadable_configuration_fail_with_setting_names_only(
        self,
    ) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        missing = {
            name: value
            for name, value in environment.items()
            if name != "SCENARIO_CONTROL_SECRET_FILE"
        }
        absent_file = {
            **environment,
            "SCENARIO_CONTROL_SECRET_FILE": str(Path(temporary.name) / "absent"),
        }

        # Act
        with pytest.raises(ScenarioConfigurationError) as absent:
            configuration(missing)
        with pytest.raises(ScenarioConfigurationError) as material:
            configuration(absent_file)
        error = StringIO()
        status = main(environment={}, error=error)

        # Assert
        self.assertEqual("SCENARIO_CONTROL_SECRET_FILE", absent.value.value)
        self.assertEqual("SCENARIO_CONTROL_SECRET_FILE", material.value.value)
        self.assertEqual(1, status)
        self.assertEqual("FAILED: scenario service unavailable\n", error.getvalue())

    def test_production_module_exposes_no_broker_opener_or_principal(self) -> None:
        # Arrange
        module = importlib.import_module("aerial_rescue_scenario_service.main")

        # Act
        exported = vars(module)

        # Assert
        self.assertNotIn("open_lifecycle_session", exported)
        self.assertNotIn("open_guaranteed_publishing_session", exported)
        self.assertNotIn("Principal", exported)

    def test_configuration_refuses_non_regular_non_ascii_and_weak_material(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        directory_secret = {
            **environment,
            "SCENARIO_CONTROL_SECRET_FILE": environment["SCENARIO_ROOT"],
        }
        Path(environment["SCENARIO_CONTROL_SECRET_FILE"]).write_bytes(b"\xff")

        # Act
        with pytest.raises(ScenarioConfigurationError) as regular:
            configuration(directory_secret)
        with pytest.raises(ScenarioConfigurationError) as ascii_only:
            configuration(environment)
        Path(environment["SCENARIO_CONTROL_SECRET_FILE"]).write_text(
            SCENARIO_VALUE, encoding="ascii"
        )
        Path(environment["SCENARIO_CONTROL_SECRET_FILE"]).write_text("weak", encoding="ascii")
        with pytest.raises(ScenarioConfigurationError) as weak:
            configuration(environment)
        Path(environment["SCENARIO_CONTROL_SECRET_FILE"]).write_text(
            SCENARIO_VALUE, encoding="ascii"
        )
        root_file = Path(temporary.name) / "scenario-file"
        root_file.write_text("not a directory", encoding="ascii")
        with pytest.raises(ScenarioConfigurationError) as root:
            configuration({**environment, "SCENARIO_ROOT": str(root_file)})

        # Assert
        self.assertEqual("SCENARIO_CONTROL_SECRET_FILE", regular.value.value)
        self.assertEqual("SCENARIO_CONTROL_SECRET_FILE", ascii_only.value.value)
        self.assertEqual("SCENARIO_CONTROL_SECRET_FILE", weak.value.value)
        self.assertEqual("SCENARIO_ROOT", root.value.value)
