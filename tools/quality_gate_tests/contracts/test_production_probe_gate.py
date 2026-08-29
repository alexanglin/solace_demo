from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from tools import production_probe_gate
from tools.quality_gate_tests.support import QualityGateTestCase

CONFORMING_MODULE = """\
\"\"\"A resolvable first-party module.\"\"\"

from __future__ import annotations

CONSTANT = 1


class Settings:
    pass


def build() -> None:
    pass
"""

CONFORMING_PROBE = """\
const fleetStatusProbe = [
  "import os",
  "from example_package.client import Settings, build",
  "print(Settings, build, os)",
].join("\\n");
"""


class ProductionProbeGateTests(QualityGateTestCase):
    """The gate that resolves probe references the dashboard harness embeds as literals."""

    def _tree(self, probe: str, module: str = CONFORMING_MODULE) -> tuple[Path, Path]:
        root = self.temporary_directory()
        package = root / "src" / "example_package"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "client.py").write_text(module, encoding="utf-8")
        support = root / "harness.ts"
        support.write_text(probe, encoding="utf-8")
        return support, root / "src"

    def _findings(
        self,
        probe: str,
        module: str = CONFORMING_MODULE,
        compose: Path | None = None,
    ) -> list[str]:
        support, source_root = self._tree(probe, module)
        errors: list[str] = []
        return production_probe_gate.evaluate([support], [source_root], compose, errors) + errors

    def _compose(self, document: str) -> Path:
        path = self.temporary_directory() / "compose.yaml"
        path.write_text(document, encoding="utf-8")
        return path

    def test_a_probe_whose_every_reference_resolves_passes(self) -> None:
        # Arrange
        probe = CONFORMING_PROBE

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_probe_naming_a_deleted_module_is_refused(self) -> None:
        # Arrange
        probe = CONFORMING_PROBE.replace("example_package.client", "example_package.fleet_client")

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("example_package.fleet_client", findings[0])
        self.assertIn("no module", findings[0])

    def test_a_probe_naming_a_renamed_symbol_is_refused(self) -> None:
        # Arrange
        probe = CONFORMING_PROBE.replace("Settings, build", "FleetClientConfig, build")

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("FleetClientConfig", findings[0])
        self.assertIn("example_package.client", findings[0])

    def test_every_unresolved_symbol_of_one_import_is_named_separately(self) -> None:
        # Arrange
        probe = CONFORMING_PROBE.replace("Settings, build", "Missing, Absent")

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(2, len(findings), findings)
        self.assertTrue(any("Missing" in finding for finding in findings), findings)
        self.assertTrue(any("Absent" in finding for finding in findings), findings)

    def test_a_plain_import_of_a_deleted_first_party_module_is_refused(self) -> None:
        # Arrange
        probe = 'const aProbe = "import example_package.gone";\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("example_package.gone", findings[0])

    def test_standard_library_and_third_party_imports_are_left_to_the_lockfiles(self) -> None:
        # Arrange
        probe = 'const aProbe = "import os\\nimport httpx\\nfrom pathlib import Path";\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_name_bound_by_a_conditional_import_resolves(self) -> None:
        # Arrange
        module = "from __future__ import annotations\n\nif True:\n    from os import sep\n"
        probe = 'const aProbe = "from example_package.client import sep";\n'

        # Act
        findings = self._findings(probe, module)

        # Assert
        self.assertEqual([], findings)

    def test_a_package_import_resolves_through_its_init_module(self) -> None:
        # Arrange
        probe = 'const aProbe = "import example_package";\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_probe_that_is_not_python_is_refused(self) -> None:
        # Arrange
        probe = 'const aProbe = "def (";\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("cannot be parsed as Python", findings[0])

    def test_a_probe_declared_in_an_unreadable_shape_is_refused_rather_than_skipped(self) -> None:
        # Arrange
        probe = "const aProbe = buildProbe();\n"

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("aProbe", findings[0])
        self.assertIn("reconstruct", findings[0])

    def test_a_declaration_that_is_not_named_as_a_probe_is_not_read_as_one(self) -> None:
        # Arrange
        probe = 'const fleetStatusQuery = "from example_package.gone import Nothing";\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_harness_source_that_cannot_be_parsed_is_refused(self) -> None:
        # Arrange
        probe = "const aProbe = [ ;;; \n"

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("cannot be parsed", findings[0])

    def test_a_missing_harness_file_is_an_error_rather_than_a_silent_pass(self) -> None:
        # Arrange
        root = self.temporary_directory()
        errors: list[str] = []

        # Act
        findings = production_probe_gate.evaluate(
            [root / "absent.ts"], [root / "src"], None, errors
        )

        # Assert
        self.assertEqual([], findings)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("absent.ts", errors[0])

    def test_a_string_literal_python_cannot_read_is_refused_rather_than_skipped(self) -> None:
        # Arrange
        probe = 'const aProbe = "\\u{1F600}";\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("reconstruct", findings[0])

    def test_a_probe_bound_to_a_value_that_is_not_a_call_is_refused(self) -> None:
        # Arrange
        probe = "const aProbe = 42;\n"

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("reconstruct", findings[0])

    def test_a_probe_built_by_a_method_other_than_join_is_refused(self) -> None:
        # Arrange
        probe = 'const aProbe = ["import os"].concat("x");\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("reconstruct", findings[0])

    def test_a_join_over_an_element_that_is_not_a_literal_is_refused(self) -> None:
        # Arrange
        probe = 'const aProbe = ["import os", other].join("\\n");\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("reconstruct", findings[0])

    def test_a_destructured_binding_is_not_read_as_a_probe_declaration(self) -> None:
        # Arrange
        probe = "const { aProbe } = harness;\n"

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_source_root_entry_that_is_not_a_package_is_not_importable(self) -> None:
        # Arrange
        root = self.temporary_directory()
        (root / "loose.py").write_text("", encoding="utf-8")
        (root / "not_a_package").mkdir()

        # Act
        packages = production_probe_gate.first_party_packages([root, root / "absent"])

        # Assert
        self.assertEqual({}, packages)

    def test_a_module_outside_every_source_root_resolves_to_no_path(self) -> None:
        # Arrange
        packages: dict[str, Path] = {}

        # Act
        resolved = production_probe_gate.module_path("httpx.client", packages)

        # Assert
        self.assertIsNone(resolved)

    def test_names_bound_by_annotation_plain_import_and_try_all_resolve(self) -> None:
        # Arrange
        module = (
            "from __future__ import annotations\n\n"
            "import os\n\n"
            "LIMIT: int = 1\n\n"
            "try:\n    from json import dumps\n"
            "except ImportError:\n    dumps = None\n"
            "finally:\n    ready = True\n"
        )
        probe = 'const aProbe = "from example_package.client import LIMIT, dumps, os, ready";\n'

        # Act
        findings = self._findings(probe, module)

        # Assert
        self.assertEqual([], findings)

    def test_a_first_party_module_that_is_not_parseable_binds_nothing(self) -> None:
        # Arrange
        probe = 'const aProbe = "from example_package.client import build";\n'

        # Act
        findings = self._findings(probe, "def (\n")

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("binds no such name", findings[0])

    def test_a_container_module_argument_that_resolves_passes(self) -> None:
        # Arrange
        probe = 'const args = ["-m", "example_package.client"];\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_container_module_argument_naming_a_deleted_module_is_refused(self) -> None:
        # Arrange
        probe = 'const args = ["-m", "example_package.exporter"];\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("example_package.exporter", findings[0])
        self.assertIn("runnable", findings[0])

    def test_a_container_module_argument_naming_an_unrunnable_package_is_refused(self) -> None:
        # Arrange
        probe = 'const args = ["-m", "example_package"];\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("example_package", findings[0])
        self.assertIn("runnable", findings[0])

    def test_a_package_holding_a_main_module_is_runnable(self) -> None:
        # Arrange
        support, source_root = self._tree('const args = ["-m", "example_package"];\n')
        (source_root / "example_package" / "__main__.py").write_text("", encoding="utf-8")
        errors: list[str] = []

        # Act
        findings = production_probe_gate.evaluate([support], [source_root], None, errors) + errors

        # Assert
        self.assertEqual([], findings)

    def test_a_container_module_argument_outside_the_workspace_is_left_alone(self) -> None:
        # Arrange
        probe = 'const args = ["-m", "pip", "-m", "http.server"];\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_container_module_argument_that_is_not_a_literal_is_refused(self) -> None:
        # Arrange
        probe = 'const args = ["-m", moduleName];\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("not a literal module name", findings[0])

    def test_a_trailing_module_flag_names_no_module_to_resolve(self) -> None:
        # Arrange
        probe = 'const args = ["run", "-m"];\n'

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_an_environment_name_a_service_sets_passes(self) -> None:
        # Arrange
        compose = self._compose(
            "services:\n  scenario-service:\n    environment:\n"
            "      FLEET_CONTROL_BEARER_FILE: /run/secrets/fleet-control-bearer\n"
        )
        probe = "const aProbe = \"import os\\nprint(os.environ['FLEET_CONTROL_BEARER_FILE'])\";\n"

        # Act
        findings = self._findings(probe, CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual([], findings)

    def test_an_environment_name_no_service_sets_is_refused(self) -> None:
        # Arrange
        compose = self._compose(
            "services:\n  scenario-service:\n    environment:\n"
            "      FLEET_CONTROL_BEARER_FILE: /run/secrets/fleet-control-bearer\n"
        )
        probe = "const aProbe = \"import os\\nprint(os.environ['FLEET_CONTROL_SECRET_FILE'])\";\n"

        # Act
        findings = self._findings(probe, CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("FLEET_CONTROL_SECRET_FILE", findings[0])
        self.assertIn("no service in", findings[0])

    def test_every_way_of_reading_an_environment_name_is_resolved(self) -> None:
        # Arrange
        compose = self._compose("services:\n  one:\n    environment:\n      KNOWN: value\n")
        probe = (
            'const aProbe = ["import os", "os.environ[\'A\']",'
            ' "os.environ.get(\'B\')", "os.getenv(\'C\')"].join("\\n");\n'
        )

        # Act
        findings = self._findings(probe, CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual(3, len(findings), findings)
        self.assertEqual(
            ["A", "B", "C"],
            sorted(name for name in ("A", "B", "C") if any(name in f for f in findings)),
        )

    def test_a_service_declaring_its_environment_as_a_list_is_understood(self) -> None:
        # Arrange
        compose = self._compose(
            "services:\n  one:\n    environment:\n      - KNOWN=value\n      - BARE\n"
        )
        probe = "const aProbe = \"import os\\nos.environ['KNOWN']\\nos.environ['BARE']\";\n"

        # Act
        findings = self._findings(probe, CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual([], findings)

    def test_environment_names_are_not_checked_when_no_compose_file_is_given(self) -> None:
        # Arrange
        probe = "const aProbe = \"import os\\nprint(os.environ['ANYTHING'])\";\n"

        # Act
        findings = self._findings(probe)

        # Assert
        self.assertEqual([], findings)

    def test_a_compose_file_that_is_not_readable_is_an_error(self) -> None:
        # Arrange
        compose = self.temporary_directory() / "absent.yaml"

        # Act
        findings = self._findings('const aProbe = "import os";\n', CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("absent.yaml", findings[0])

    def test_a_compose_file_that_is_not_valid_yaml_is_an_error(self) -> None:
        # Arrange
        compose = self._compose("services: [unclosed\n")

        # Act
        findings = self._findings('const aProbe = "import os";\n', CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("compose.yaml", findings[0])

    def test_a_compose_file_declaring_no_service_environment_names_nothing(self) -> None:
        # Arrange
        compose = self._compose("services:\n  one:\n    image: example\n")
        probe = "const aProbe = \"import os\\nos.environ['ANY']\";\n"

        # Act
        findings = self._findings(probe, CONFORMING_MODULE, compose)

        # Assert
        self.assertEqual(1, len(findings), findings)
        self.assertIn("ANY", findings[0])

    def test_a_compose_entry_that_is_not_a_service_mapping_declares_nothing(self) -> None:
        # Arrange
        compose = self._compose("services:\n  one: null\n  two:\n    environment:\n      A: b\n")
        errors: list[str] = []

        # Act
        names = production_probe_gate.compose_environment_names(compose, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(frozenset({"A"}), names)

    def test_the_repository_compose_file_sets_the_names_the_harness_reads(self) -> None:
        # Arrange
        compose = production_probe_gate.REPOSITORY_ROOT / "deploy" / "compose.yaml"
        errors: list[str] = []

        # Act
        names = production_probe_gate.compose_environment_names(compose, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertIn("FLEET_CONTROL_BEARER_FILE", names)

    def test_the_gate_is_inert_when_it_is_handed_no_harness_source(self) -> None:
        # Arrange
        argv: list[str] = []

        # Act
        status = production_probe_gate.main(argv)

        # Assert
        self.assertEqual(0, status)

    def test_the_gate_blocks_and_names_the_diagnostic_when_a_reference_is_unresolved(self) -> None:
        # Arrange
        support, source_root = self._tree(
            CONFORMING_PROBE.replace("example_package.client", "example_package.fleet_client")
        )
        captured = io.StringIO()

        # Act
        with contextlib.redirect_stderr(captured):
            status = production_probe_gate.main(
                ["--support", str(support), "--source-root", str(source_root)]
            )

        # Assert
        self.assertEqual(1, status)
        self.assertIn(f"{production_probe_gate.DIAGNOSTIC}:", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
