"""The commit stage runs the affected tests and the push stage runs all of them.

Together these hold both halves of the split
[ADR-0012](../../../docs/adr/0012-git-hooks-with-ci-as-authority.md) decided and
[ADR-0066](../../../docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md)
implements. A selector that silently ran nothing would look exactly like a fast one, so
inertness and fail-closed behaviour are asserted rather than assumed.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

ROOT_HOOK = "pytest-related.sh"
AGENT_HOOK = "pytest-agent-mesh-related.sh"

FULL_SUITE_HOOKS = (
    "pytest-full.sh",
    "agent-mesh-test-full.sh",
    "dashboard-test-full.sh",
)


def _root_project(repository: Path, *, lockfile: bool) -> None:
    """Create as much of the root Python project as a case needs."""
    (repository / "pyproject.toml").write_text('[project]\nname = "a"\n', encoding="utf-8")
    source = repository / "tools" / "owned.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    if lockfile:
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _agent_project(repository: Path, *, lockfile: bool, tests: bool) -> None:
    """Create as much of the Agent Mesh project as a case needs."""
    project = repository / "agent-mesh"
    (project / "plugins").mkdir(parents=True, exist_ok=True)
    (project / "plugins" / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text('[project]\nname = "a"\n', encoding="utf-8")
    if lockfile:
        (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    if tests:
        (project / "tests").mkdir()


class RootSelectionStageTests(QualityGateTestCase):
    def test_the_stage_is_inert_before_the_root_project_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(ROOT_HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_a_manifest_without_a_lockfile_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _root_project(repository, lockfile=False)
        _, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        result = self.run_hook(ROOT_HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv.lock")

    def test_a_project_without_uv_on_the_path_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _root_project(repository, lockfile=True)

        # Act
        result = self.run_hook(ROOT_HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv")

    def test_the_stage_hands_the_staged_paths_to_the_selector(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _root_project(repository, lockfile=True)
        recorded, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        self.run_hook(ROOT_HOOK, repository, ("tools/owned.py",), environment=environment)

        # Assert
        arguments = recorded.read_text(encoding="utf-8")
        self.assertIn("-m tools.affected_tests", arguments)
        self.assertIn("tools/owned.py", arguments)


class AgentSelectionStageTests(QualityGateTestCase):
    def test_the_stage_is_inert_before_the_agent_mesh_project_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(AGENT_HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_owned_agent_source_without_a_manifest_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "agent-mesh" / "plugins" / "owned.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")

        # Act
        result = self.run_hook(AGENT_HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/pyproject.toml")

    def test_a_manifest_without_a_lockfile_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=False, tests=False)

        # Act
        result = self.run_hook(AGENT_HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/uv.lock")

    def test_a_locked_project_without_uv_on_the_path_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=True, tests=False)

        # Act
        result = self.run_hook(AGENT_HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv")

    def test_a_project_without_a_test_directory_fails_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=True, tests=False)
        _, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        result = self.run_hook(AGENT_HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/tests")

    def test_selection_is_scoped_to_the_domain_rather_than_the_repository(self) -> None:
        """The domain carries its own `tools` package, which the root package would shadow."""
        # Arrange
        repository = self.temporary_repository()
        _agent_project(repository, lockfile=True, tests=True)
        recorded, environment = self.install_argument_recorder(repository, "uv", "uv-arguments.txt")

        # Act
        self.run_hook(
            AGENT_HOOK, repository, ("agent-mesh/tests/test_a.py",), environment=environment
        )

        # Assert
        self.assertIn("--root agent-mesh", recorded.read_text(encoding="utf-8"))


class SelectionRegistrationTests(QualityGateTestCase):
    """The selectors are themselves gated: deleting a hook entry must fail a test."""

    def test_both_commit_stage_selectors_are_registered_with_the_staged_paths(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        agent_entry = "entry: scripts/hooks/agent-mesh/pytest-agent-mesh-related.sh"
        expected = {
            "pytest-unit-fast": "entry: scripts/hooks/python/pytest-related.sh",
            "pytest-agent-mesh-fast": agent_entry,
        }

        # Act
        blocks = {
            identifier: configuration.split(f"- id: {identifier}", maxsplit=1)[1].split(
                "\n      - id:", maxsplit=1
            )[0]
            for identifier in expected
        }

        # Assert
        for identifier, entry in expected.items():
            with self.subTest(hook=identifier):
                self.assertIn(entry, blocks[identifier])
                self.assertIn("pass_filenames: true", blocks[identifier])

    def test_the_commit_stage_selector_covers_more_than_python_files(self) -> None:
        """A hook script or a workflow is an input to the suites and no import names it."""
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        block = configuration.split("- id: pytest-unit-fast", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

        # Assert
        self.assertNotIn("types_or: [python, pyi]", block)
        self.assertIn("exclude:", block)

    def test_a_selection_whose_tests_all_carry_an_excluded_marker_passes(self) -> None:
        # Arrange
        stage = (REPOSITORY_ROOT / "scripts" / "hooks" / "python" / "pytest-related.sh").read_text(
            encoding="utf-8"
        )

        # Act
        tolerated = ('"$status" -eq 5' in stage, "every selected test carries an excluded" in stage)

        # Assert
        self.assertEqual((True, True), tolerated)

    def test_every_other_status_from_the_selected_run_is_propagated(self) -> None:
        # Arrange
        stage = (REPOSITORY_ROOT / "scripts" / "hooks" / "python" / "pytest-related.sh").read_text(
            encoding="utf-8"
        )

        # Act
        propagated = ('return "$status"' in stage, "exit $?" in stage)

        # Assert
        self.assertEqual((True, True), propagated)

    def test_the_root_selector_keeps_the_agent_mesh_tree_out_of_its_graph(self) -> None:
        # Arrange
        expected = ":(exclude)agent-mesh/*"

        # Act
        script = (REPOSITORY_ROOT / "scripts" / "hooks" / "python" / ROOT_HOOK).read_text(
            encoding="utf-8"
        )

        # Assert
        self.assertIn(expected, script)


class FullSuiteAtPushTests(QualityGateTestCase):
    """Every unit test runs at the push stage, in all three toolchains.

    Narrowing the commit stage is only safe while this holds, so it is asserted here
    rather than left to the reader of the configuration file.
    """

    def test_the_push_stage_runs_every_unit_suite_unconditionally(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        identifiers = ("pytest-full", "agent-mesh-test-full", "dashboard-test-full")

        # Act
        blocks = {
            identifier: configuration.split(f"- id: {identifier}", maxsplit=1)[1].split(
                "\n      - id:", maxsplit=1
            )[0]
            for identifier in identifiers
        }

        # Assert
        for identifier, block in blocks.items():
            with self.subTest(hook=identifier):
                self.assertIn("stages: [pre-push]", block)
                self.assertIn("always_run: true", block)
                self.assertIn("pass_filenames: false", block)

    def test_no_push_stage_suite_selects_a_subset_of_its_tests(self) -> None:
        # Arrange
        scripts = {name: self._hook_source(name) for name in FULL_SUITE_HOOKS}

        # Act
        selecting = {name: "affected_tests" in source for name, source in scripts.items()}

        # Assert
        for name, selects in selecting.items():
            with self.subTest(script=name):
                self.assertFalse(selects)

    @staticmethod
    def _hook_source(name: str) -> str:
        """Return the text of a hook script, wherever it sits under scripts/hooks/."""
        matches = sorted((REPOSITORY_ROOT / "scripts" / "hooks").glob(f"**/{name}"))
        return matches[0].read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
