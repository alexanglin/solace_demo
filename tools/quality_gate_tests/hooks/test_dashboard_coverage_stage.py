from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import QualityGateTestCase


class DashboardCoverageStageTests(QualityGateTestCase):
    def _dashboard_repository(self) -> Path:
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        source = dashboard / "src" / "App.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("export const value = 1;\n", encoding="utf-8")
        (dashboard / "package.json").write_text(
            json.dumps({"scripts": {"test:coverage": "vitest run --coverage"}}) + "\n",
            encoding="utf-8",
        )
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        return repository

    @staticmethod
    def _write_runtime(path: Path, body: str) -> None:
        path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        path.chmod(0o755)

    def _runtime_environment(
        self,
        repository: Path,
        *,
        create_report: bool = True,
        pnpm_status: int = 0,
        include_uv: bool = True,
    ) -> tuple[Path, dict[str, str]]:
        executable_directory = repository / "bin"
        executable_directory.mkdir()
        arguments = repository / "arguments.txt"
        arguments.touch()
        report_body = (
            """
for argument in "$@"; do
  case "$argument" in
    --coverage.reportsDirectory=*)
      report_directory=${argument#*=}
      mkdir -p "$report_directory"
      printf '{"total":{}}\n' >"$report_directory/coverage-summary.json"
      ;;
  esac
done
"""
            if create_report
            else ""
        )
        self._write_runtime(
            executable_directory / "pnpm",
            f'printf \'pnpm %s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\n'
            f"{report_body}exit {pnpm_status}\n",
        )
        if include_uv:
            self._write_runtime(
                executable_directory / "uv",
                'printf \'uv %s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\n'
                "previous=''\n"
                'for argument in "$@"; do\n'
                '  if [ "$previous" = "--source-inventory" ]; then\n'
                '    cp "$argument" "$QUALITY_INVENTORY_FILE"\n'
                "  fi\n"
                "  previous=$argument\n"
                "done\n"
                "exit 0\n",
            )
        inventory = repository / "source-inventory.bin"
        inventory.touch()
        environment = {
            "PATH": f"{executable_directory}:/usr/bin:/bin",
            "QUALITY_ARGUMENTS_FILE": str(arguments),
            "QUALITY_INVENTORY_FILE": str(inventory),
        }
        return arguments, environment

    def test_the_coverage_stage_is_inert_before_the_dashboard_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_owned_source_without_a_manifest_fails_closed(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "apps" / "dashboard" / "src" / "App.tsx"
        source.parent.mkdir(parents=True)
        source.write_text("export const value = 1;\n", encoding="utf-8")

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: apps/dashboard/package.json")

    def test_an_active_dashboard_without_uv_fails_closed(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        _, environment = self._runtime_environment(repository, include_uv=False)

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv")

    def test_the_stage_routes_temporary_evidence_and_source_inventory_to_the_typed_gate(
        self,
    ) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(repository)

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository, environment=environment)
        recorded = arguments.read_text(encoding="utf-8")
        report_match = re.search(r"--report (\S+/coverage-summary\.json)", recorded)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertIn(
            "pnpm --dir apps/dashboard run test:coverage --coverage.reportsDirectory=",
            recorded,
        )
        self.assertNotIn("run test:coverage -- --coverage.reportsDirectory=", recorded)
        self.assertIn("--coverage.reportsDirectory=", recorded)
        self.assertIn("uv run --frozen python -m tools.typescript_coverage_gate", recorded)
        self.assertIn("--dashboard-root apps/dashboard", recorded)
        self.assertIn("--source-inventory", recorded)
        self.assertNotIn("--source apps/dashboard/src/App.tsx", recorded)
        self.assertIsNotNone(report_match)
        if report_match is not None:
            self.assertTrue(Path(report_match.group(1)).is_absolute())
            self.assertFalse(Path(report_match.group(1)).parent.exists())

    def test_source_inventory_is_nul_delimited_and_includes_every_module_extension(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        source_paths = [
            repository / "apps" / "dashboard" / "src" / "line\nbreak.ts",
            repository / "apps" / "dashboard" / "src" / "module.mts",
            repository / "apps" / "dashboard" / "src" / "common.cts",
            repository / "apps" / "dashboard" / "src" / "module.mjs",
            repository / "apps" / "dashboard" / "src" / "common.cjs",
        ]
        for source_path in source_paths:
            source_path.write_text("export const value = 1;\n", encoding="utf-8")
        _, environment = self._runtime_environment(repository)

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository, environment=environment)
        inventory = (repository / "source-inventory.bin").read_bytes()
        inventory_paths = inventory.removesuffix(b"\0").split(b"\0") if inventory else []

        # Assert
        self.assert_hook_succeeded(result)
        self.assertTrue(inventory.endswith(b"\0"), inventory)
        for source_path in source_paths:
            expected = os.fsencode(source_path.relative_to(repository).as_posix())
            with self.subTest(source=source_path.name):
                self.assertIn(expected, inventory_paths)

    def test_a_symbolic_link_coverage_parent_is_refused_before_vitest_runs(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        outside = repository / "outside"
        outside.mkdir()
        (repository / "apps" / "dashboard" / "coverage").symlink_to(
            outside,
            target_is_directory=True,
        )
        arguments, environment = self._runtime_environment(repository)

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "symbolic link")
        self.assertEqual("", arguments.read_text(encoding="utf-8"))
        self.assertEqual([], list(outside.iterdir()))

    def test_a_missing_summary_after_a_successful_test_run_fails_closed(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(repository, create_report=False)

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: dashboard coverage summary")
        self.assertNotIn("uv run", arguments.read_text(encoding="utf-8"))

    def test_a_failing_coverage_run_preserves_its_status(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        _, environment = self._runtime_environment(repository, pnpm_status=7)

        # Act
        result = self.run_hook("dashboard-test-full.sh", repository, environment=environment)

        # Assert
        self.assertEqual(7, result.returncode)


if __name__ == "__main__":
    unittest.main()
