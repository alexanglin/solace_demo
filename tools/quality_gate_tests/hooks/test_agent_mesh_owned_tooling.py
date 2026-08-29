"""Owned Agent Mesh tooling is visible to every project quality gate."""

from __future__ import annotations

import tomllib

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

WARNING_FILTER_FIELD_COUNT = 4


class AgentMeshOwnedToolingTests(QualityGateTestCase):
    def test_owned_tool_source_arms_the_agent_mesh_stage_without_a_manifest(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "agent-mesh" / "tools" / "owned.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")

        # Act
        result = self.run_hook("agent-mesh-test-full.sh", repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: agent-mesh/pyproject.toml")

    def test_agent_mesh_stage_enforces_complete_validator_coverage(self) -> None:
        # Arrange
        script = REPOSITORY_ROOT / "scripts" / "hooks" / "agent-mesh-test-full.sh"

        # Act
        source = script.read_text(encoding="utf-8")

        # Assert
        self.assertIn("--cov=tools.agent_mesh_config_validator", source)
        self.assertIn("--cov=aerial_rescue_event_mesh_gateway", source)
        self.assertIn("--cov-branch", source)
        self.assertIn("--cov-fail-under=100", source)

    def test_every_static_gate_includes_both_owned_runtime_packages(self) -> None:
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
        self.assertTrue(
            all("agent-mesh/aerial_rescue_event_mesh_gateway" in source for source in sources),
            paths,
        )
        self.assertTrue(
            all("agent-mesh/aerial_rescue_runtime_compat" in source for source in sources),
            paths,
        )

    def test_runtime_image_installs_both_owned_packages_in_the_leaf_overlay(self) -> None:
        # Arrange
        dockerfile = REPOSITORY_ROOT / "deploy" / "agent-mesh" / "Dockerfile"

        # Act
        source = dockerfile.read_text(encoding="utf-8")

        # Assert
        self.assertIn(
            "COPY agent-mesh/aerial_rescue_event_mesh_gateway/ "
            "/opt/aerial-rescue-runtime/aerial_rescue_event_mesh_gateway/",
            source,
        )
        self.assertIn(
            "COPY agent-mesh/aerial_rescue_runtime_compat/ "
            "/opt/aerial-rescue-runtime/aerial_rescue_runtime_compat/",
            source,
        )
        self.assertIn("ENV PYTHONPATH=/opt/aerial-rescue-runtime", source)

    def test_agent_mesh_manifest_declares_validator_risk_and_quality_tools(self) -> None:
        # Arrange
        manifest = REPOSITORY_ROOT / "agent-mesh" / "pyproject.toml"

        # Act
        source = manifest.read_text(encoding="utf-8")
        configuration = tomllib.loads(source)

        # Assert
        self.assertEqual(2, configuration["tool"]["aerial-rescue"]["risk-tier"])
        self.assertIn("pytest-cov==7.1.0", source)
        self.assertIn("coverage==7.15.4", source)
        self.assertIn("bandit==1.9.4", source)

    def test_upstream_warning_filters_are_scoped_away_from_owned_modules(self) -> None:
        # Arrange
        manifest = REPOSITORY_ROOT / "agent-mesh" / "pyproject.toml"

        # Act
        configuration = tomllib.loads(manifest.read_text(encoding="utf-8"))
        filters = configuration["tool"]["pytest"]["ini_options"]["filterwarnings"]
        ignored = tuple(rule for rule in filters if rule.startswith("ignore:"))

        # Assert
        self.assertTrue(ignored)
        self.assertTrue(
            all(len(rule.split(":")) >= WARNING_FILTER_FIELD_COUNT for rule in ignored),
            ignored,
        )
        self.assertTrue(all(rule.split(":", maxsplit=3)[3] for rule in ignored), ignored)


if __name__ == "__main__":
    import unittest

    unittest.main()
