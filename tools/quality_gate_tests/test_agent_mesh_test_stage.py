"""The Agent Mesh test stage fails closed and runs in the Agent Mesh project.

The gate this covers exists because nothing else executes that domain's tests
(docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md). A gate that
skipped when its lockfile or interpreter were missing would be worse than no gate, since
it would report success for a suite that never ran.
"""

from __future__ import annotations

from pathlib import Path

from tools.quality_gate_tests.support import QualityGateTestCase

HOOK = "agent-mesh-test-full.sh"


def _agent_project(repository: Path, *, lockfile: bool, tests: bool) -> None:
    """Create as much of an Agent Mesh project as a case needs."""
    project = repository / "agent-mesh"
    project.mkdir(parents=True, exist_ok=True)
    (project / "pyproject.toml").write_text('[project]\nname = "a"\n', encoding="utf-8")
    if lockfile:
        (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    if tests:
        (project / "tests").mkdir()


class AgentMeshTestStageTests(QualityGateTestCase):
    def test_the_stage_is_inert_before_the_agent_mesh_project_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_owned_agent_source_without_a_manifest_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "agent-mesh" / "plugins" / "owned.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/pyproject.toml")

    def test_a_manifest_without_a_lockfile_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=False, tests=False)

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/uv.lock")

    def test_a_locked_project_without_uv_on_the_path_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=True, tests=False)

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv")

    def test_a_project_without_a_test_directory_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=True, tests=False)
        _, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/tests")

    def test_the_suite_runs_inside_the_agent_mesh_project_rather_than_the_root(self) -> None:
        """`uv run --project` would leave pytest rooted at the repository root.

        It would then load the root pytest configuration and collect the application tree
        under the 3.13 interpreter, so the absence of `--project` here is the assertion.
        """
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=True, tests=True)
        recorded, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        self.run_hook(HOOK, repository, environment=environment)

        # Assert
        arguments = recorded.read_text(encoding="utf-8")
        self.assertNotIn("--project", arguments)


if __name__ == "__main__":
    import unittest

    unittest.main()
