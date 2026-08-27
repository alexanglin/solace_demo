from __future__ import annotations

import json
import unittest

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class DashboardContractGenerationPolicyTests(QualityGateTestCase):
    """Repository wiring that keeps generated dashboard contract types current."""

    def test_the_manifest_exposes_exact_write_and_check_commands(self) -> None:
        # Arrange
        manifest_path = REPOSITORY_ROOT / "apps" / "dashboard" / "package.json"
        expected = {
            "contracts:generate": "node scripts/generate-dashboard-contracts.ts --write",
            "contracts:check": "node scripts/generate-dashboard-contracts.ts --check",
        }

        # Act
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = {name: manifest["scripts"].get(name) for name in expected}

        # Assert
        self.assertEqual(expected, actual)

    def test_the_dashboard_recipe_includes_the_full_contract_freshness_gate(self) -> None:
        # Arrange
        justfile = (REPOSITORY_ROOT / "justfile").read_text(encoding="utf-8")

        # Act
        recipe = justfile.split("check-dashboard:", maxsplit=1)[1].split(
            "\n# Run the complete dashboard browser", maxsplit=1
        )[0]

        # Assert
        self.assertIn(
            "pre-commit run --all-files --hook-stage pre-push dashboard-contracts-current-all",
            recipe,
        )


if __name__ == "__main__":
    unittest.main()
