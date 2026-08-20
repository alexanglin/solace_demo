from __future__ import annotations

import unittest
from pathlib import PurePosixPath

from tools.import_contract_gate import check_source


class ImportContractGateTests(unittest.TestCase):
    def test_domain_code_cannot_import_framework_or_adapter_dependencies(self) -> None:
        # Arrange
        source = "import fastapi\nfrom sqlalchemy import select\n"

        # Act
        diagnostics = check_source(
            PurePosixPath("packages/domain/src/aerial_rescue_domain/rules.py"),
            source,
        )

        # Assert
        self.assertEqual(2, len(diagnostics))
        self.assertEqual({"fastapi", "sqlalchemy"}, {item.module for item in diagnostics})

    def test_domain_code_can_import_standard_library_and_domain_siblings(self) -> None:
        # Arrange
        source = "from dataclasses import dataclass\nfrom .models import Mission\n"

        # Act
        diagnostics = check_source(
            PurePosixPath("packages/domain/src/aerial_rescue_domain/rules.py"),
            source,
        )

        # Assert
        self.assertEqual((), diagnostics)


if __name__ == "__main__":
    unittest.main()
