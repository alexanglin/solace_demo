from __future__ import annotations

import tomllib
import unittest
from pathlib import PurePosixPath

from tools.import_contract_gate import FORBIDDEN_IMPORT_ROOTS, check_source
from tools.quality_gate_tests.support import REPOSITORY_ROOT

DOMAIN_MANIFEST = REPOSITORY_ROOT / "packages" / "domain" / "pyproject.toml"


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

    def test_domain_code_cannot_import_an_http_client(self) -> None:
        # Arrange
        source = "import httpx\n"

        # Act
        diagnostics = check_source(
            PurePosixPath("packages/domain/src/aerial_rescue_domain/rules.py"),
            source,
        )

        # Assert
        self.assertEqual(("httpx",), tuple(item.module for item in diagnostics))

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


class ImportBanParityTests(unittest.TestCase):
    def test_the_gate_and_the_domain_ruff_ban_forbid_the_same_roots(self) -> None:
        # Arrange
        manifest = tomllib.loads(DOMAIN_MANIFEST.read_text(encoding="utf-8"))
        banned_api = manifest["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]

        # Act
        ruff_roots = {key.partition(".")[0] for key in banned_api}

        # Assert
        self.assertEqual(set(FORBIDDEN_IMPORT_ROOTS), ruff_roots)


if __name__ == "__main__":
    unittest.main()
