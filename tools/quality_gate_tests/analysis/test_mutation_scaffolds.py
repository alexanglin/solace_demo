from __future__ import annotations

import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest

from tools import mutation_gate
from tools.quality_gate_tests.support import MutationGateTestCase

USAGE_EXIT_CODE = 2
TIER_ONE = """[tool.aerial-rescue]
risk-tier = 1

[tool.mutmut]
source_paths = ["src"]
pytest_add_cli_args_test_selection = ["tests"]
on_dependency_change = "rerun"
cache_invalidation_files = [
    "tests/**/*.py",
    "pyproject.toml",
    "../../pyproject.toml",
    "../../uv.lock",
    "../../packages/*/pyproject.toml",
    "../../packages/*/src/**/*.py",
    "../../services/*/pyproject.toml",
    "../../services/*/src/**/*.py",
]
"""


def _write_member(root: Path, member: str, *, source: str, tests: bool) -> None:
    member_root = root / member
    package = member_root / "src" / "example"
    package.mkdir(parents=True)
    (member_root / "pyproject.toml").write_text(TIER_ONE, encoding="utf-8")
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (package / "py.typed").write_text("", encoding="utf-8")
    if tests:
        (member_root / "tests").mkdir()
        (member_root / "tests" / "test_example.py").write_text("", encoding="utf-8")


class MutationScaffoldTests(MutationGateTestCase):
    def _workspace(self) -> Path:
        root = self.temporary_directory()
        (root / "pyproject.toml").write_text(
            '[tool.uv.workspace]\nmembers = ["services/*"]\n',
            encoding="utf-8",
        )
        _write_member(root, "services/gateway", source='"""Not started."""\n', tests=False)
        _write_member(
            root,
            "services/core",
            source='"""Started."""\n\n\ndef rule() -> int:\n    return 1\n',
            tests=True,
        )
        self.write_mutation_metadata(
            root,
            "services/core",
            self.mutation_statuses(killed=10, survived=0),
        )
        self.write_survivor_registry(root)
        return root

    def _run(self, root: Path, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(Path, "cwd", return_value=root),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = mutation_gate.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_the_listing_omits_scaffolds_and_names_them_on_stderr(self) -> None:
        # Arrange
        root = self._workspace()

        # Act
        status, stdout, stderr = self._run(root, "--list-tier-one")

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(["services/core"], stdout.split())
        self.assertIn("SCAFFOLD services/gateway", stderr)

    def test_preflight_of_a_scaffold_reports_and_succeeds_without_running(self) -> None:
        # Arrange
        root = self._workspace()

        # Act
        status, stdout, stderr = self._run(root, "--preflight", "services/gateway")

        # Assert
        self.assertEqual(0, status)
        self.assertIn("SCAFFOLD services/gateway", stdout)
        self.assertEqual("", stderr)

    def test_evaluation_reports_scaffolds_and_judges_the_active_members(self) -> None:
        # Arrange
        root = self._workspace()

        # Act
        status, stdout, stderr = self._run(root, "--evaluate")

        # Assert
        self.assertEqual(0, status)
        self.assertIn("SCAFFOLD services/gateway", stdout)
        self.assertIn("PASS   services/core", stdout)
        self.assertEqual("", stderr)

    def test_a_scaffold_that_gains_a_function_without_tests_fails_preflight(self) -> None:
        # Arrange
        root = self._workspace()
        (root / "services" / "gateway" / "src" / "example" / "__init__.py").write_text(
            '"""Started."""\n\n\ndef rule() -> int:\n    return 1\n',
            encoding="utf-8",
        )

        # Act
        status, stdout, stderr = self._run(root, "--preflight", "services/gateway")

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertIn("co-located tests/ is required", stderr)

    def test_partition_separates_active_from_scaffolded_tier_one_members(self) -> None:
        # Arrange
        root = self._workspace()

        # Act
        active, scaffolded = mutation_gate.partition_tier_one_members(root)

        # Assert
        self.assertEqual(("services/core",), active)
        self.assertEqual(("services/gateway",), scaffolded)

    def test_preflight_of_an_undeclared_member_fails(self) -> None:
        # Arrange
        root = self._workspace()

        # Act
        status, stdout, stderr = self._run(root, "--preflight", "services/unknown")

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertEqual("FAIL: services/unknown is not a tier-one member\n", stderr)

    def test_evaluation_prints_each_member_error_on_stderr(self) -> None:
        # Arrange
        root = self._workspace()
        (root / "services" / "core" / "mutants" / "src" / "example.py.meta").unlink()

        # Act
        status, stdout, stderr = self._run(root, "--evaluate")

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SCAFFOLD services/gateway", stdout)
        self.assertIn("FAIL   services/core", stderr)
        self.assertIn(
            "       services/core: no mutation results; an active tier-one member cannot pass\n",
            stderr,
        )

    def test_evaluation_prints_registry_scope_errors_on_stderr(self) -> None:
        # Arrange
        root = self._workspace()
        self.write_survivor_registry(
            root,
            records=(("services/other", "src.other.x_rule__mutmut_1"),),
        )

        # Act
        status, stdout, stderr = self._run(root, "--evaluate")

        # Assert
        self.assertEqual(1, status)
        self.assertIn("PASS   services/core", stdout)
        self.assertEqual(
            "services/other: survivor record for src.other.x_rule__mutmut_1 "
            "names a non-tier-one member\n",
            stderr,
        )

    def test_a_workspace_configuration_error_fails_the_gate(self) -> None:
        # Arrange
        root = self.temporary_directory()

        # Act
        status, stdout, stderr = self._run(root, "--list-tier-one")

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", stdout)
        self.assertTrue(
            stderr.startswith(
                "FAIL: mutation gate configuration: cannot read workspace manifest "
                f"{root / 'pyproject.toml'}: "
            ),
            stderr,
        )

    def test_main_requires_exactly_one_action(self) -> None:
        # Arrange
        root = self._workspace()

        # Act
        with pytest.raises(SystemExit) as raised:
            self._run(root)

        # Assert
        self.assertEqual(USAGE_EXIT_CODE, raised.value.code)

    def test_the_module_exits_with_the_gate_status_when_run_as_a_script(self) -> None:
        # Arrange
        root = self._workspace()
        stdout = io.StringIO()

        # Act
        with (
            mock.patch.object(Path, "cwd", return_value=root),
            mock.patch.object(sys, "argv", ["mutation_gate", "--list-tier-one"]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(Path(mutation_gate.__file__)), run_name="__main__")

        # Assert
        self.assertEqual(0, raised.value.code)
        self.assertEqual(["services/core"], stdout.getvalue().split())


if __name__ == "__main__":
    unittest.main()
