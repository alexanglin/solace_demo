from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class DashboardIntegrationStageTests(QualityGateTestCase):
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
        (dashboard / "package.json").write_text(
            json.dumps({"scripts": {"test:integration": "vitest run --config integration.ts"}})
            + "\n",
            encoding="utf-8",
        )
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        return repository

    def test_the_integration_stage_is_inert_before_the_dashboard_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook("dashboard-integration-full.sh", repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_an_active_dashboard_without_pnpm_fails_closed(self) -> None:
        # Arrange
        repository = self._dashboard_repository()

        # Act
        result = self.run_hook("dashboard-integration-full.sh", repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: pnpm")

    def test_the_integration_stage_runs_the_dedicated_package_inventory(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self.install_argument_recorder(
            repository, "pnpm", "pnpm-arguments.txt"
        )

        # Act
        result = self.run_hook("dashboard-integration-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertEqual(
            "--dir apps/dashboard run test:integration\n",
            arguments.read_text(encoding="utf-8"),
        )

    def test_the_integration_stage_is_an_unconditional_pre_push_and_ci_gate(self) -> None:
        # Arrange
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "checks.yml").read_text(
            encoding="utf-8"
        )

        # Act
        block = self._hook_block("dashboard-integration-full")

        # Assert
        self.assertIn("entry: scripts/hooks/dashboard/dashboard-integration-full.sh", block)
        self.assertIn("stages: [pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)
        self.assertIn("uses: pre-commit/action@", workflow)
        self.assertIn("extra_args: --all-files --hook-stage pre-push", workflow)


if __name__ == "__main__":
    unittest.main()
