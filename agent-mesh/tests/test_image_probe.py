"""Deterministic behavior for the Agent Mesh runtime-image probe."""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tools import image_probe

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]


class _EntryPoint:
    def __init__(self, *, name: str, loaded: object) -> None:
        self.name = name
        self._loaded = loaded

    def load(self) -> object:
        return self._loaded


class ImageProbeCheckTests(unittest.TestCase):
    def test_versions_accept_only_the_complete_exact_pin_mapping(self) -> None:
        # Arrange
        installed = dict(image_probe.EXPECTED_VERSIONS)

        # Act
        with patch.object(importlib.metadata, "version", side_effect=installed.__getitem__):
            result = image_probe._versions()

        # Assert
        self.assertEqual(
            "versions: solace-agent-mesh==1.28.7, "
            "sam-event-mesh-gateway==1.1.0, sam-event-mesh-tool==0.1.1, "
            "solace-ai-connector==3.3.12, solace-pubsubplus==1.11.0",
            result,
        )

    def test_missing_and_drifted_distributions_are_precise_probe_errors(self) -> None:
        # Arrange
        drifted = dict(image_probe.EXPECTED_VERSIONS)
        drifted["sam-event-mesh-gateway"] = "9.9.9"

        # Act
        with (
            patch.object(importlib.metadata, "version", side_effect=drifted.__getitem__),
            pytest.raises(image_probe.ProbeError) as wrong_version,
        ):
            image_probe._versions()
        with (
            patch.object(
                importlib.metadata,
                "version",
                side_effect=importlib.metadata.PackageNotFoundError,
            ),
            pytest.raises(image_probe.ProbeError) as missing,
        ):
            image_probe._versions()

        # Assert
        self.assertIn("9.9.9", str(wrong_version.value))
        self.assertIn("is not installed in the image", str(missing.value))

    def test_gateway_entry_point_requires_one_exact_owned_upstream_registration(self) -> None:
        # Arrange
        accepted = _EntryPoint(
            name=image_probe.GATEWAY_ENTRY_POINT,
            loaded={"class_name": image_probe.GATEWAY_CLASS},
        )
        wrong = _EntryPoint(
            name=image_probe.GATEWAY_ENTRY_POINT,
            loaded={"class_name": "WrongGateway"},
        )

        # Act
        with patch.object(importlib.metadata, "entry_points", return_value=(accepted,)):
            result = image_probe._gateway_entry_point()
        with (
            patch.object(importlib.metadata, "entry_points", return_value=(wrong,)),
            pytest.raises(image_probe.ProbeError) as rejected,
        ):
            image_probe._gateway_entry_point()

        # Assert
        self.assertIn(image_probe.GATEWAY_CLASS, result)
        self.assertIn("expected", str(rejected.value))

    def test_tool_import_failure_is_redacted_and_missing_class_is_refused(self) -> None:
        # Arrange
        hostile = ImportError("tenant-secret-must-not-escape")
        missing_class = SimpleNamespace()

        # Act
        with (
            patch.object(importlib, "import_module", side_effect=hostile),
            pytest.raises(image_probe.ProbeError) as import_failure,
        ):
            image_probe._tool_module()
        with (
            patch.object(importlib, "import_module", return_value=missing_class),
            pytest.raises(image_probe.ProbeError) as symbol_failure,
        ):
            image_probe._tool_module()

        # Assert
        diagnostics = f"{import_failure.value} {symbol_failure.value}"
        self.assertNotIn("tenant-secret-must-not-escape", diagnostics)
        self.assertIn(image_probe.TOOL_CLASS, diagnostics)

    def test_runtime_symbol_check_reports_the_total_and_refuses_one_absence(self) -> None:
        # Arrange
        complete = SimpleNamespace(
            **{symbol: object() for _, symbol in image_probe.RUNTIME_SYMBOLS}
        )
        missing = SimpleNamespace()

        # Act
        with patch.object(importlib, "import_module", return_value=complete):
            result = image_probe._runtime_symbols()
        with (
            patch.object(importlib, "import_module", return_value=missing),
            pytest.raises(image_probe.ProbeError) as rejected,
        ):
            image_probe._runtime_symbols()

        # Assert
        self.assertEqual(f"runtime symbols: {len(image_probe.RUNTIME_SYMBOLS)} resolved", result)
        self.assertIn(image_probe.RUNTIME_SYMBOLS[0][0], str(rejected.value))

    def test_runtime_symbols_include_the_owned_direct_gateway_extension(self) -> None:
        # Arrange
        expected = {
            (
                "aerial_rescue_event_mesh_gateway.app",
                "AerialRescueEventMeshGatewayApp",
            ),
            (
                "aerial_rescue_event_mesh_gateway.component",
                "AerialRescueEventMeshGatewayComponent",
            ),
        }

        # Act
        declared = set(image_probe.RUNTIME_SYMBOLS)

        # Assert
        self.assertLessEqual(expected, declared)

    def test_direct_output_api_check_attests_methods_and_receipt_properties(self) -> None:
        # Arrange
        missing = SimpleNamespace()

        # Act
        result = image_probe._direct_output_api()
        with (
            patch.object(importlib, "import_module", return_value=missing),
            pytest.raises(image_probe.ProbeError) as rejected,
        ):
            image_probe._direct_output_api()

        # Assert
        self.assertEqual("direct output API: 12 methods and 3 receipt properties resolved", result)
        self.assertIn("direct output SDK surface is absent", str(rejected.value))


class ImageProbeCommandTests(unittest.TestCase):
    def test_main_accumulates_multiple_expected_failures_and_continues(self) -> None:
        # Arrange
        first = Mock(side_effect=image_probe.ProbeError("first safe failure"))
        first.__name__ = "first"
        passing = Mock(return_value="middle evidence")
        passing.__name__ = "passing"
        last = Mock(side_effect=image_probe.ProbeError("last safe failure"))
        last.__name__ = "last"
        stdout = io.StringIO()
        stderr = io.StringIO()

        # Act
        with (
            patch.object(image_probe, "CHECKS", (first, passing, last)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = image_probe.main()

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("PASS middle evidence\n", stdout.getvalue())
        self.assertIn("FAIL first: first safe failure", stderr.getvalue())
        self.assertIn("FAIL last: last safe failure", stderr.getvalue())
        self.assertEqual(1, passing.call_count)

    def test_unexpected_check_failure_is_redacted_counted_and_does_not_stop_later_checks(
        self,
    ) -> None:
        # Arrange
        broken = Mock(side_effect=RuntimeError("tenant-secret-must-not-escape"))
        broken.__name__ = "broken"
        passing = Mock(return_value="later evidence")
        passing.__name__ = "passing"
        stdout = io.StringIO()
        stderr = io.StringIO()

        # Act
        with (
            patch.object(image_probe, "CHECKS", (broken, passing)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = image_probe.main()

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("PASS later evidence\n", stdout.getvalue())
        self.assertEqual("FAIL broken: unexpected probe failure\n", stderr.getvalue())
        self.assertNotIn("tenant-secret-must-not-escape", stderr.getvalue())
        self.assertEqual(1, passing.call_count)


if __name__ == "__main__":
    unittest.main()
