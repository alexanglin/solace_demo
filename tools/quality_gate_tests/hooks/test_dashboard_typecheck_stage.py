from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

DASHBOARD_GATES = (
    "dashboard-typecheck-full.sh",
    "dashboard-quality-full.sh",
    "check-typescript-policy.sh",
)


class DashboardTypeCheckStageTests(QualityGateTestCase):
    """The dashboard type, lint, and configuration gates, before any dashboard exists."""

    @staticmethod
    def _hook_block(hook_id: str) -> str:
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        return configuration.split(f"- id: {hook_id}", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

    def _dashboard_repository(self) -> Path:
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text("{}\n", encoding="utf-8")
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        return repository

    def test_every_new_dashboard_gate_is_inert_before_the_dashboard_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        results = tuple(self.run_hook(name, repository) for name in DASHBOARD_GATES)

        # Assert
        self.assert_hooks_succeeded(DASHBOARD_GATES, results)

    def test_dashboard_source_without_a_manifest_fails_every_new_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "apps" / "dashboard" / "src" / "owned.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const VALUE = 1;\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(name, repository) for name in DASHBOARD_GATES)

        # Assert
        self.assert_hooks_failed(DASHBOARD_GATES, results, "MISSING: apps/dashboard/package.json")

    def test_the_whole_tree_dashboard_gates_fail_when_pnpm_is_missing(self) -> None:
        # Arrange
        hook_names = ("dashboard-typecheck-full.sh", "dashboard-quality-full.sh")
        repository = self._dashboard_repository()

        # Act
        results = tuple(self.run_hook(name, repository) for name in hook_names)

        # Assert
        self.assert_hooks_failed(hook_names, results, "MISSING: pnpm")

    def test_the_type_gate_runs_the_dashboard_typecheck_script(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "pnpm",
            "pnpm-arguments.txt",
        )

        # Act
        result = self.run_hook(
            "dashboard-typecheck-full.sh",
            repository,
            environment=environment,
        )

        # Assert
        self.assert_hook_succeeded(result)
        recorded = arguments_file.read_text(encoding="utf-8")
        self.assertIn("--dir apps/dashboard run typecheck", recorded)

    def test_the_quality_gate_checks_both_lint_and_formatting(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "pnpm",
            "pnpm-arguments.txt",
        )

        # Act
        result = self.run_hook(
            "dashboard-quality-full.sh",
            repository,
            environment=environment,
        )

        # Assert
        self.assert_hook_succeeded(result)
        recorded = arguments_file.read_text(encoding="utf-8")
        self.assertIn("--dir apps/dashboard run lint", recorded)
        self.assertIn("--dir apps/dashboard run format:check", recorded)

    def test_both_type_check_stages_run_the_same_script(self) -> None:
        """The commit-stage trigger and the pre-push gate cannot drift into two verdicts."""
        # Arrange
        expected = "entry: scripts/hooks/dashboard/dashboard-typecheck-full.sh"

        # Act
        blocks = (self._hook_block("tsc"), self._hook_block("dashboard-typecheck-full"))

        # Assert
        self.assertIn(expected, blocks[0])
        self.assertIn(expected, blocks[1])

    def test_the_commit_stage_type_gate_triggers_on_configuration_and_manifest_changes(
        self,
    ) -> None:
        """`tsc` is whole-project, so its pattern is a trigger; a narrow one is a hole.

        A tsconfig edit or a bumped type-declaration package changes the verdict for every
        file while touching no TypeScript source at all.
        """
        # Arrange
        block = self._hook_block("tsc")
        declared = re.search(r"files: (\S+)", block)
        # A pattern matching nothing, so a missing `files:` fails in the Assert phase
        # rather than passing vacuously.
        pattern = re.compile(declared.group(1) if declared else r"(?!)")
        changes = (
            "apps/dashboard/tsconfig.json",
            "apps/dashboard/tsconfig.app.json",
            "apps/dashboard/package.json",
            "apps/dashboard/pnpm-lock.yaml",
            "apps/dashboard/src/App.tsx",
        )

        # Act
        matched = tuple(path for path in changes if pattern.search(path) is not None)

        # Assert
        self.assertEqual(changes, matched)

    def test_the_whole_tree_dashboard_gates_run_unconditionally_at_pre_push(self) -> None:
        # Arrange
        hook_ids = ("dashboard-typecheck-full", "dashboard-quality-full", "typescript-policy")

        # Act
        blocks = {hook_id: self._hook_block(hook_id) for hook_id in hook_ids}

        # Assert
        for hook_id, block in blocks.items():
            with self.subTest(hook=hook_id):
                self.assertIn("always_run: true", block)
                self.assertIn("pass_filenames: false", block)


if __name__ == "__main__":
    unittest.main()
