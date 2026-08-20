from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class EnvironmentTemplateSecurityTests(QualityGateTestCase):
    def test_exported_literal_secret_is_rejected(self) -> None:
        # Arrange
        template = self.temporary_file(
            ".env.example",
            "export SOLACE_PASSWORD=wilderness-demo-password\n",
        )

        # Act
        result = self.run_hook("check-env-template.sh", template.parent, (str(template),))

        # Assert
        self.assert_hook_failed(result, "literal value")

    def test_url_userinfo_credentials_are_rejected(self) -> None:
        # Arrange
        template = self.temporary_file(
            ".env.example",
            "DATABASE_URL=postgresql://alice:LowEntropyPassword@db.example.invalid/rescue\n",
        )

        # Act
        result = self.run_hook("check-env-template.sh", template.parent, (str(template),))

        # Assert
        self.assert_hook_failed(result, "embedded credentials")

    def test_lowercase_secret_name_is_rejected(self) -> None:
        # Arrange
        template = self.temporary_file(
            ".env.example",
            "solace_password=wilderness-demo-password\n",
        )

        # Act
        result = self.run_hook("check-env-template.sh", template.parent, (str(template),))

        # Assert
        self.assert_hook_failed(result, "literal value")


if __name__ == "__main__":
    unittest.main()
