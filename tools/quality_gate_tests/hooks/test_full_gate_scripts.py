from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class FullGateScriptTests(QualityGateTestCase):
    def test_full_gate_hooks_use_fail_closed_project_scripts(self) -> None:
        # Arrange
        expected_entries = {
            "agent-mesh-test-full": "scripts/hooks/agent-mesh-test-full.sh",
            "bandit-full": "scripts/hooks/python/bandit-full.sh",
            "dashboard-build": "scripts/hooks/dashboard/dashboard-build.sh",
            "dashboard-test-full": "scripts/hooks/dashboard/dashboard-test-full.sh",
            "dependency-audit": "scripts/hooks/deps/dependency-audit.sh",
            "python-quality-full": "scripts/hooks/python/python-quality-full.sh",
            "trivy-config-full": "scripts/hooks/deploy/trivy-config-full.sh",
        }
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        hook_blocks = {
            hook_id: configuration.split(f"- id: {hook_id}", maxsplit=1)[1].split(
                "\n      - id:", maxsplit=1
            )[0]
            for hook_id in expected_entries
        }

        # Assert
        for hook_id, entry in expected_entries.items():
            with self.subTest(hook=hook_id):
                self.assertIn(f"entry: {entry}", hook_blocks[hook_id])
                self.assertIn("always_run: true", hook_blocks[hook_id])
                self.assertIn("pass_filenames: false", hook_blocks[hook_id])

    def test_python_full_gates_fail_when_uv_is_missing(self) -> None:
        # Arrange
        hook_names = ("bandit-full.sh", "dependency-audit.sh", "python-quality-full.sh")
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(name, repository) for name in hook_names)

        # Assert
        self.assert_hooks_failed(hook_names, results, "MISSING: uv")

    def test_dashboard_full_gates_fail_when_pnpm_is_missing(self) -> None:
        # Arrange
        hook_names = ("dashboard-test-full.sh", "dashboard-build.sh")
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text("{}\n", encoding="utf-8")
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(name, repository) for name in hook_names)

        # Assert
        self.assert_hooks_failed(hook_names, results, "MISSING: pnpm")

    def test_full_gate_scripts_are_inert_before_their_component_exists(self) -> None:
        # Arrange
        hook_names = (
            "agent-mesh-test-full.sh",
            "bandit-full.sh",
            "dependency-audit.sh",
            "python-quality-full.sh",
            "dashboard-test-full.sh",
            "dashboard-build.sh",
            "trivy-config-full.sh",
        )
        repository = self.temporary_repository()

        # Act
        results = tuple(self.run_hook(name, repository) for name in hook_names)

        # Assert
        self.assert_hooks_succeeded(hook_names, results)


if __name__ == "__main__":
    unittest.main()
