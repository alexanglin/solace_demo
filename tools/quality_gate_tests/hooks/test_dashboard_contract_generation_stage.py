from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

HOOK = "dashboard-contracts-check.sh"
COMMIT_HOOK_ID = "dashboard-contracts-current"
PUSH_HOOK_ID = "dashboard-contracts-current-all"


class DashboardContractGenerationStageTests(QualityGateTestCase):
    """Check-only, offline freshness enforcement for generated browser contracts."""

    @staticmethod
    def _hook_block(hook_id: str) -> str:
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        marker = f"- id: {hook_id}"
        if marker not in configuration:
            return ""
        return configuration.split(marker, maxsplit=1)[1].split("\n      - id:", maxsplit=1)[0]

    def _dashboard_repository(self) -> Path:
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text("{}\n", encoding="utf-8")
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        return repository

    @staticmethod
    def _write_pnpm_recorder(path: Path) -> None:
        path.write_text(
            "#!/bin/sh\n"
            'printf \'%s|%s\\n\' "${COREPACK_ENABLE_NETWORK-unset}" "$*" '
            '">"$QUALITY_ARGUMENTS_FILE"\n'
            'exit "${QUALITY_PNPM_STATUS:-0}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _pnpm_environment(
        self,
        repository: Path,
        *,
        status: int = 0,
    ) -> tuple[Path, dict[str, str]]:
        executable_directory = repository / "bin"
        executable_directory.mkdir()
        self._write_pnpm_recorder(executable_directory / "pnpm")
        arguments = repository / "pnpm-arguments.txt"
        environment = {
            "PATH": f"{executable_directory}:/usr/bin:/bin",
            "QUALITY_ARGUMENTS_FILE": str(arguments),
            "QUALITY_PNPM_STATUS": str(status),
        }
        return arguments, environment

    def test_the_stage_is_inert_before_the_dashboard_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_the_stage_runs_only_the_offline_freshness_check(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._pnpm_environment(repository)

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertEqual(
            "0|--offline --dir apps/dashboard run contracts:check",
            arguments.read_text(encoding="utf-8").strip(),
        )

    def test_the_stage_preserves_a_failing_freshness_status(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        _, environment = self._pnpm_environment(repository, status=7)

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assertEqual(7, result.returncode)

    def test_the_commit_hook_is_triggered_by_every_contract_generation_input(self) -> None:
        # Arrange
        block = self._hook_block(COMMIT_HOOK_ID)
        declared = re.search(r"^\s*files: (\S+)\s*$", block, flags=re.MULTILINE)
        pattern = re.compile(declared.group(1) if declared else r"(?!)")
        changes = (
            "schemas/contract-manifest.toml",
            "schemas/v1/canonical.schema.json",
            "schemas/v1/dashboard/bootstrap.schema.json",
            "fixtures/golden/v1/dashboard/bootstrap/baseline.json",
            "apps/dashboard/scripts/generate-dashboard-contracts.ts",
            "apps/dashboard/src/contracts/generated/bootstrap.ts",
            "apps/dashboard/package.json",
            "apps/dashboard/pnpm-lock.yaml",
        )

        # Act
        matched = tuple(path for path in changes if pattern.search(path) is not None)

        # Assert
        self.assertEqual(changes, matched)
        self.assertIn("stages: [pre-commit]", block)
        self.assertIn(
            "entry: scripts/hooks/dashboard/dashboard-contracts-check.sh",
            block,
        )
        self.assertIn("pass_filenames: false", block)
        self.assertNotIn("always_run: true", block)

    def test_the_push_hook_runs_the_same_check_unconditionally(self) -> None:
        # Arrange
        block = self._hook_block(PUSH_HOOK_ID)

        # Act
        entry_count = block.count("entry: scripts/hooks/dashboard/dashboard-contracts-check.sh")

        # Assert
        self.assertEqual(1, entry_count)
        self.assertIn("stages: [pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)


if __name__ == "__main__":
    unittest.main()
