from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class UvVersionPinTests(unittest.TestCase):
    def test_uv_version_is_identical_in_configuration_docs_and_ci(self) -> None:
        # Arrange
        expected = "0.12.5"
        pyproject = REPOSITORY_ROOT / "pyproject.toml"
        contributing = REPOSITORY_ROOT / "CONTRIBUTING.md"
        workflow = REPOSITORY_ROOT / ".github" / "workflows" / "checks.yml"

        # Act
        project_data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        required = project_data["tool"]["uv"].get("required-version")
        contributing_source = contributing.read_text(encoding="utf-8")
        workflow_source = workflow.read_text(encoding="utf-8")

        # Assert
        self.assertEqual(f"=={expected}", required)
        self.assertIn(f"`uv` {expected}", contributing_source)
        self.assertIn(f'version: "{expected}"', workflow_source)

    def test_the_agent_mesh_project_pins_the_same_uv_version_as_the_root(self) -> None:
        """The two dependency domains must be resolved by one uv.

        ADR-0020 pins uv exactly and requires lock verification in both Python
        environments on an upgrade. Verifying only the root left the Agent Mesh project
        free to declare a different version, or none, and still pass.
        """
        # Arrange
        expected = "0.12.5"
        agent_pyproject = REPOSITORY_ROOT / "agent-mesh" / "pyproject.toml"

        # Act
        agent_data = tomllib.loads(agent_pyproject.read_text(encoding="utf-8"))

        # Assert
        self.assertEqual(f"=={expected}", agent_data["tool"]["uv"].get("required-version"))


if __name__ == "__main__":
    unittest.main()
