"""Fail-closed activation and registration for Agent Mesh configuration validation."""

from __future__ import annotations

from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

HOOK = "check-agent-mesh-configs.sh"


def _config(repository: Path) -> Path:
    """Create one owned Agent Mesh configuration file."""
    path = repository / "agent-mesh" / "configs" / "agent.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("apps: []\n", encoding="utf-8")
    return path


class AgentMeshConfigStageTests(QualityGateTestCase):
    def test_the_stage_is_inert_before_the_first_configuration(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_an_empty_tree_or_non_configuration_file_does_not_arm_the_stage(self) -> None:
        # Arrange
        repositories = tuple(self.temporary_repository() for _ in range(2))
        (repositories[0] / "agent-mesh" / "configs").mkdir(parents=True)
        documentation = repositories[1] / "agent-mesh" / "configs" / "README.md"
        documentation.parent.mkdir(parents=True)
        documentation.write_text("# No owned configuration yet\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(HOOK, repository) for repository in repositories)

        # Assert
        self.assertTrue(all(result.returncode == 0 for result in results), results)

    def test_the_first_configuration_arms_fail_closed_prerequisites(self) -> None:
        # Arrange
        repositories = tuple(self.temporary_repository() for _ in range(3))
        for repository in repositories:
            _config(repository)
        project = repositories[1] / "agent-mesh"
        project.mkdir(exist_ok=True)
        (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        locked = repositories[2] / "agent-mesh"
        locked.mkdir(exist_ok=True)
        (locked / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (locked / "uv.lock").write_text("version = 1\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(HOOK, repository) for repository in repositories)

        # Assert
        self.assert_hook_failed(results[0], "MISSING: agent-mesh/pyproject.toml")
        self.assert_hook_failed(results[1], "MISSING: agent-mesh/uv.lock")
        self.assert_hook_failed(results[2], "MISSING: uv")

    def test_a_locked_project_without_the_validator_fails(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _config(repository)
        project = repository / "agent-mesh"
        (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        _, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/tools/agent_mesh_config_validator.py")

    def test_the_stage_runs_the_frozen_validator_from_agent_mesh(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _config(repository)
        project = repository / "agent-mesh"
        (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        validator = project / "tools" / "agent_mesh_config_validator.py"
        validator.parent.mkdir()
        validator.write_text("VALUE = 1\n", encoding="utf-8")
        recorded, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertEqual(
            "run --frozen python -m tools.agent_mesh_config_validator",
            recorded.read_text(encoding="utf-8").strip(),
        )

    def test_the_hook_is_blocking_at_both_stages_without_filename_arguments(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        block = configuration.split("- id: agent-mesh-configs", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

        # Assert
        self.assertIn("stages: [pre-commit, pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)
        self.assertIn("entry: scripts/hooks/agent-mesh/check-agent-mesh-configs.sh", block)

    def test_every_static_gate_includes_agent_mesh_tools(self) -> None:
        # Arrange
        paths = (
            "scripts/hooks/quality-components.sh",
            "scripts/hooks/python/bandit-full.sh",
            "scripts/hooks/python/cognitive-complexity-full.sh",
            "scripts/hooks/repo/duplication-full.sh",
        )

        # Act
        sources = tuple((REPOSITORY_ROOT / path).read_text(encoding="utf-8") for path in paths)

        # Assert
        self.assertTrue(all("agent-mesh/tools" in source for source in sources), paths)


if __name__ == "__main__":
    import unittest

    unittest.main()
