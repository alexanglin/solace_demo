from __future__ import annotations

import json
import unittest

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class DashboardVerificationPolicyTests(QualityGateTestCase):
    def test_the_manifest_keeps_strict_coverage_and_a_dedicated_integration_command(self) -> None:
        # Arrange
        manifest_path = REPOSITORY_ROOT / "apps" / "dashboard" / "package.json"

        # Act
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scripts = manifest["scripts"]

        # Assert
        self.assertEqual(
            "vitest run --config vitest.integration.config.ts",
            scripts["test:integration"],
        )
        self.assertIn("vitest run --coverage", scripts["test:coverage"])
        for dimension in ("statements", "branches", "functions", "lines"):
            with self.subTest(dimension=dimension):
                self.assertIn(
                    f"--coverage.thresholds.{dimension}=95",
                    scripts["test:coverage"],
                )
        self.assertEqual(66, manifest["config"]["playwrightExpectedTests"])

    def test_the_base_vitest_configuration_emits_inventory_adjudication_evidence(self) -> None:
        # Arrange
        configuration_path = REPOSITORY_ROOT / "apps" / "dashboard" / "vitest.config.ts"

        # Act
        configuration = configuration_path.read_text(encoding="utf-8")

        # Assert
        self.assertIn('provider: "v8"', configuration)
        self.assertIn('reporter: ["text", "json-summary"]', configuration)
        self.assertIn('include: ["src/**/*.{ts,tsx}"]', configuration)
        self.assertIn('"src/contracts/generated/**"', configuration)
        self.assertIn('"src/**/*.d.ts"', configuration)
        self.assertIn('"src/**/*.test.{ts,tsx}"', configuration)
        self.assertIn(
            'include: ["src/**/*.test.{ts,tsx}", "src/**/*.spec.{ts,tsx}"]',
            configuration,
        )
        self.assertIn("passWithNoTests: false", configuration)

    def test_the_integration_configuration_selects_only_the_dedicated_inventory(self) -> None:
        # Arrange
        configuration_path = REPOSITORY_ROOT / "apps" / "dashboard" / "vitest.integration.config.ts"

        # Act
        configuration = configuration_path.read_text(encoding="utf-8")

        # Assert
        self.assertIn('include: ["src/**/*.integration.test.{ts,tsx}"]', configuration)
        self.assertIn("passWithNoTests: false", configuration)
        self.assertIn(
            'import { dashboardVitestConfiguration } from "./vitest.config.ts"',
            configuration,
        )
        self.assertIn("...dashboardVitestConfiguration.test", configuration)
        self.assertNotIn("mergeConfig", configuration)

    def test_the_dashboard_recipe_runs_coverage_integration_and_browser_evidence(self) -> None:
        # Arrange
        justfile = (REPOSITORY_ROOT / "justfile").read_text(encoding="utf-8")

        # Act
        recipe = justfile.split("check-dashboard:", maxsplit=1)[1].split(
            "\n# Run the complete dashboard browser", maxsplit=1
        )[0]

        # Assert
        self.assertIn("dashboard-test-full", recipe)
        self.assertIn("dashboard-integration-full", recipe)
        self.assertIn("dashboard-typecheck-full", recipe)
        self.assertIn("dashboard-quality-full", recipe)


if __name__ == "__main__":
    unittest.main()
