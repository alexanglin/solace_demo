"""Production fleet-simulator composition and process entrypoint."""

from __future__ import annotations

import runpy
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest
from aerial_rescue_fleet_simulator import event_source
from aerial_rescue_fleet_simulator.main import (
    FleetConfiguration,
    FleetConfigurationError,
    _stamps,
    configuration,
    main,
)

pytestmark = [pytest.mark.unit]

CONTROL_VALUE: Final = "a" * 64
BROKER_VALUE: Final = "not-a-real-broker-password"


def _environment(root: Path) -> dict[str, str]:
    """Write bounded synthetic secrets and return the production environment shape."""
    broker = root / "broker"
    control = root / "control"
    broker.write_text(BROKER_VALUE, encoding="ascii")
    control.write_text(CONTROL_VALUE, encoding="ascii")
    return {
        "SOLACE_BROKER_URL": "tcps://broker:55443",
        "SOLACE_BROKER_VPN": "default",
        "TRUST_STORE": "/etc/aerial-rescue/certs",
        "SOLACE_BROKER_PASSWORD_FILE": str(broker),
        "FLEET_CONTROL_SECRET_FILE": str(control),
        "FLEET_COMMAND_INTAKE_MODE": "enabled",
    }


class FleetConfigurationTests(unittest.TestCase):
    def test_successor_run_continues_each_stable_drone_source_sequence(self) -> None:
        # Arrange
        producer = "drone-sim-reset-seam"
        stable_source = event_source(producer)

        # Act
        predecessor = _stamps("run-predecessor").next_stamp(producer)
        successor = _stamps("run-successor").next_stamp(producer)

        # Assert
        self.assertEqual(stable_source, event_source(producer))
        self.assertEqual(predecessor.sequence + 1, successor.sequence)
        self.assertNotEqual(predecessor.correlation_id, successor.correlation_id)

    def test_module_import_does_not_start_the_process(self) -> None:
        # Arrange
        module_name = "aerial_rescue_fleet_simulator.__main__"

        # Act
        namespace = runpy.run_module(module_name, run_name="fleet-import-check")

        # Assert
        self.assertEqual("fleet-import-check", namespace["__name__"])

    def test_module_execution_propagates_the_redacted_main_status(self) -> None:
        # Arrange
        module_name = "aerial_rescue_fleet_simulator.__main__"

        # Act
        with (
            patch("aerial_rescue_fleet_simulator.main.main", return_value=17) as process,
            pytest.raises(SystemExit) as stopped,
        ):
            runpy.run_module(module_name, run_name="__main__")

        # Assert
        self.assertEqual(17, stopped.value.code)
        process.assert_called_once_with()

    def test_configuration_reads_only_indirected_secrets_and_redacts_repr(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))

        # Act
        configured = configuration(environment)
        rendered = repr(configured)

        # Assert
        self.assertIsInstance(configured, FleetConfiguration)
        self.assertEqual("tcps://broker:55443", configured.broker_endpoint.url)
        self.assertEqual(CONTROL_VALUE, configured.control_secret)
        self.assertNotIn(CONTROL_VALUE, rendered)
        self.assertNotIn(BROKER_VALUE, rendered)

    def test_configuration_selects_publication_only_and_refuses_an_unknown_intake_mode(
        self,
    ) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))

        # Act
        publication_only = configuration(
            {**environment, "FLEET_COMMAND_INTAKE_MODE": "publication-only"}
        )
        with pytest.raises(FleetConfigurationError) as unknown:
            configuration({**environment, "FLEET_COMMAND_INTAKE_MODE": "noop"})

        # Assert
        self.assertFalse(publication_only.command_intake_enabled)
        self.assertEqual("FLEET_COMMAND_INTAKE_MODE", unknown.value.value)

    def test_configuration_refuses_missing_settings_and_weak_control_material(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        missing = {name: value for name, value in environment.items() if name != "TRUST_STORE"}
        Path(environment["FLEET_CONTROL_SECRET_FILE"]).write_text("short", encoding="ascii")

        # Act
        with pytest.raises(FleetConfigurationError) as absent:
            configuration(missing)
        with pytest.raises(FleetConfigurationError) as weak:
            configuration(environment)

        # Assert
        self.assertEqual("TRUST_STORE", absent.value.value)
        self.assertEqual("FLEET_CONTROL_SECRET_FILE", weak.value.value)
        self.assertNotIn("short", str(weak.value))

    def test_main_uses_the_internal_listener_and_reports_only_redacted_configuration_failure(
        self,
    ) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        errors = StringIO()

        # Act
        with patch("aerial_rescue_fleet_simulator.main.serve") as listener:
            succeeded = main(environment=environment, error=errors)
        failed = main(environment={}, error=errors)

        # Assert
        self.assertEqual(0, succeeded)
        self.assertEqual(1, failed)
        self.assertEqual(1, listener.call_count)
        self.assertEqual("FAILED: fleet simulator unavailable\n", errors.getvalue())

    def test_configuration_refuses_insecure_transport_and_unreadable_secret_file(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = _environment(Path(temporary.name))
        insecure = {**environment, "SOLACE_BROKER_URL": "http://broker:55555"}
        unreadable = {
            **environment,
            "SOLACE_BROKER_PASSWORD_FILE": str(Path(temporary.name) / "absent"),
        }

        # Act
        with pytest.raises(FleetConfigurationError) as transport:
            configuration(insecure)
        with pytest.raises(FleetConfigurationError) as material:
            configuration(unreadable)

        # Assert
        self.assertEqual("SOLACE_BROKER_URL", transport.value.value)
        self.assertEqual("SOLACE_BROKER_PASSWORD_FILE", material.value.value)
