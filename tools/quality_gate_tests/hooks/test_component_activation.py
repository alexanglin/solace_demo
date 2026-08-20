from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class ComponentActivationTests(QualityGateTestCase):
    def test_root_python_source_without_manifest_fails_every_root_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "tools" / "owned.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        hooks = (
            "bandit-full.sh",
            "check-locks.sh",
            "dependency-audit.sh",
            "mypy-full.sh",
            "pytest-full.sh",
            "python-quality-full.sh",
        )

        # Act
        results = tuple(self.run_hook(hook, repository) for hook in hooks)

        # Assert
        self.assert_hooks_failed(hooks, results, "MISSING: pyproject.toml")

    def test_agent_source_without_manifest_fails_every_agent_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "agent-mesh" / "plugins" / "owned.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        hooks = (
            "agent-mesh-test-full.sh",
            "bandit-full.sh",
            "check-locks.sh",
            "dependency-audit.sh",
            "mypy-full.sh",
            "python-quality-full.sh",
        )

        # Act
        results = tuple(self.run_hook(hook, repository) for hook in hooks)

        # Assert
        self.assert_hooks_failed(hooks, results, "MISSING: agent-mesh/pyproject.toml")

    def test_dashboard_source_without_manifest_fails_every_dashboard_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "apps" / "dashboard" / "src" / "owned.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const value = 1;\n", encoding="utf-8")
        hooks = (
            "check-locks.sh",
            "dashboard-build.sh",
            "dashboard-test-full.sh",
            "dependency-audit.sh",
        )

        # Act
        results = tuple(self.run_hook(hook, repository) for hook in hooks)

        # Assert
        self.assert_hooks_failed(hooks, results, "MISSING: apps/dashboard/package.json")

    def test_deploy_compose_without_environment_template_fails_the_compose_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        compose = repository / "deploy" / "compose.yaml"
        compose.parent.mkdir(parents=True)
        compose.write_text("services: {}\n", encoding="utf-8")

        # Act
        result = self.run_hook("check-compose-policy.sh", repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: .env.example")


if __name__ == "__main__":
    unittest.main()
