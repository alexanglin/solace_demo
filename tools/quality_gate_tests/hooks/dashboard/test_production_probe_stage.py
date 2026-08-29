from __future__ import annotations

import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

HOOK = "check-production-probes.sh"
HOOK_ID = "production-probe-references"


class ProductionProbeStageTests(QualityGateTestCase):
    """Offline resolution of the harness's embedded probe references, at both stages."""

    @staticmethod
    def _hook_block(hook_id: str) -> str:
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        marker = f"- id: {hook_id}"
        if marker not in configuration:
            return ""
        return configuration.split(marker, maxsplit=1)[1].split("\n      - id:", maxsplit=1)[0]

    def _harness_repository(self) -> Path:
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        (dashboard / "tests" / "production" / "support").mkdir(parents=True)
        (dashboard / "tests" / "soak" / "support").mkdir(parents=True)
        (dashboard / "package.json").write_text("{}\n", encoding="utf-8")
        harness = dashboard / "tests" / "production" / "support" / "harness.ts"
        harness.write_text('const aProbe = "import os";\n', encoding="utf-8")
        soak = dashboard / "tests" / "soak" / "support" / "soak-policy.ts"
        soak.write_text("export const limit = 1;\n", encoding="utf-8")
        for member in ("packages/domain", "services/recorder"):
            (repository / member / "src").mkdir(parents=True)
        (repository / "deploy").mkdir()
        (repository / "deploy" / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (repository / "tools").mkdir()
        (repository / "tools" / "production_probe_gate.py").write_text("", encoding="utf-8")
        return repository

    def test_the_stage_is_inert_before_the_dashboard_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_the_stage_refuses_when_the_gate_module_is_absent(self) -> None:
        # Arrange
        repository = self._harness_repository()
        (repository / "tools" / "production_probe_gate.py").unlink()
        _arguments, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: tools/production_probe_gate.py")

    def test_the_stage_refuses_when_uv_is_not_installed(self) -> None:
        # Arrange
        repository = self._harness_repository()

        # Act
        result = self.run_hook(HOOK, repository, environment={"PATH": "/usr/bin:/bin"})

        # Assert
        self.assert_hook_failed(result, "MISSING: uv is not installed")

    def test_the_stage_refuses_when_the_compose_file_is_absent(self) -> None:
        # Arrange
        repository = self._harness_repository()
        (repository / "deploy" / "compose.yaml").unlink()
        _arguments, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: deploy/compose.yaml")

    def test_the_stage_routes_every_harness_source_and_workspace_root_to_the_gate(self) -> None:
        # Arrange
        repository = self._harness_repository()
        arguments, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        recorded = arguments.read_text(encoding="utf-8")
        self.assertIn("python -m tools.production_probe_gate", recorded)
        self.assertIn("--compose deploy/compose.yaml", recorded)
        self.assertIn("--support apps/dashboard/tests/production/support/harness.ts", recorded)
        self.assertIn("--support apps/dashboard/tests/soak/support/soak-policy.ts", recorded)
        self.assertIn("--source-root packages/domain/src", recorded)
        self.assertIn("--source-root services/recorder/src", recorded)

    def test_the_stage_routes_no_source_that_is_not_harness_typescript(self) -> None:
        # Arrange
        repository = self._harness_repository()
        (repository / "apps" / "dashboard" / "tests" / "production" / "notes.md").write_text(
            "not source\n", encoding="utf-8"
        )
        arguments, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")

        # Act
        self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assertNotIn("notes.md", arguments.read_text(encoding="utf-8"))

    def test_the_hook_runs_at_both_blocking_stages_regardless_of_the_changed_files(self) -> None:
        # Arrange
        block = self._hook_block(HOOK_ID)

        # Act
        registered = block.splitlines()

        # Assert
        self.assertNotEqual([], registered)
        self.assertIn(f"        entry: scripts/hooks/dashboard/{HOOK}", registered)
        self.assertIn("        stages: [pre-commit, pre-push]", registered)
        self.assertIn("        always_run: true", registered)
        self.assertIn("        pass_filenames: false", registered)
        self.assertNotIn("files:", block)


if __name__ == "__main__":
    unittest.main()
