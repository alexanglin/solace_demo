from __future__ import annotations

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class DashboardEslintGeneratedPolicyTests(QualityGateTestCase):
    def test_only_the_freshness_checked_standalone_validator_is_ignored(self) -> None:
        # Arrange
        configuration_path = REPOSITORY_ROOT / "apps" / "dashboard" / "eslint.config.mjs"

        # Act
        configuration = configuration_path.read_text(encoding="utf-8")

        # Assert
        self.assertIn('"src/contracts/generated/runtime/validators.mjs"', configuration)
        self.assertNotIn('"src/contracts/generated/**"', configuration)
        self.assertNotIn('"src/**"', configuration)
        self.assertIn("eslint.configs.recommended", configuration)
        self.assertIn("tseslint.configs.strictTypeChecked", configuration)
        self.assertIn('files: ["**/*.{ts,tsx}"]', configuration)
