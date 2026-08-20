from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class EnvironmentTemplateContractTests(QualityGateTestCase):
    def test_non_assignment_content_is_rejected(self) -> None:
        # Arrange
        template = self.temporary_file(
            ".env.example",
            "export SOLACE_PASSWORD = <required>\n",
        )

        # Act
        result = self.run_hook("check-env-template.sh", template.parent, (str(template),))

        # Assert
        self.assert_hook_failed(result, "invalid dotenv assignment")

    def test_noncredential_name_containing_key_is_allowed(self) -> None:
        # Arrange
        template = self.temporary_file(".env.example", "MONKEY_SPECIES=macaque\n")

        # Act
        result = self.run_hook("check-env-template.sh", template.parent, (str(template),))

        # Assert
        self.assert_hook_succeeded(result)


if __name__ == "__main__":
    unittest.main()
