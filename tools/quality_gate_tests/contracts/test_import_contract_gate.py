from __future__ import annotations

import contextlib
import io
import runpy
import tomllib
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import pytest

from tools import import_contract_gate
from tools.import_contract_gate import FORBIDDEN_IMPORT_ROOTS, check_source
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

DOMAIN_MANIFEST = REPOSITORY_ROOT / "packages" / "domain" / "pyproject.toml"
RULES_MODULE = PurePosixPath("packages/domain/src/aerial_rescue_domain/rules.py")


def _domain_tree(root: Path, source: str) -> None:
    """Write one domain module under ``root`` at the gate's scanned location."""
    module = root / RULES_MODULE
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")


def _run_main(root: Path) -> tuple[int, str]:
    """Run the gate's entry point inside ``root`` and return its status and stderr."""
    stderr = io.StringIO()
    with mock.patch.object(Path, "cwd", return_value=root), contextlib.redirect_stderr(stderr):
        status = import_contract_gate.main()
    return status, stderr.getvalue()


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

    def test_a_diagnostic_renders_as_a_compiler_style_line(self) -> None:
        # Arrange
        diagnostic = check_source(RULES_MODULE, "import os\nimport httpx\n")[0]

        # Act
        rendered = diagnostic.render()

        # Assert
        self.assertEqual(
            f"{RULES_MODULE}:2:1: LAYER001 domain code must not import 'httpx'",
            rendered,
        )

    def test_a_missing_or_relative_module_name_has_no_root(self) -> None:
        # Arrange
        modules = (None, ".models")

        # Act
        roots = tuple(import_contract_gate._root_name(module) for module in modules)

        # Assert
        self.assertEqual((None, None), roots)


class ImportContractMainTests(QualityGateTestCase):
    def test_main_passes_when_no_domain_source_root_exists(self) -> None:
        # Arrange
        root = self.temporary_directory()

        # Act
        status, stderr = _run_main(root)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", stderr)

    def test_main_passes_a_clean_domain_tree_silently(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _domain_tree(root, "from dataclasses import dataclass\nfrom .models import Mission\n")

        # Act
        status, stderr = _run_main(root)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", stderr)

    def test_main_reports_every_forbidden_import_on_stderr_and_fails(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _domain_tree(root, "import httpx\nfrom fastapi import FastAPI\n")

        # Act
        status, stderr = _run_main(root)

        # Assert
        self.assertEqual(1, status)
        self.assertEqual(
            [
                f"{RULES_MODULE}:1:1: LAYER001 domain code must not import 'httpx'",
                f"{RULES_MODULE}:2:1: LAYER001 domain code must not import 'fastapi'",
            ],
            stderr.splitlines(),
        )

    def test_the_module_entry_point_exits_with_the_gate_status(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _domain_tree(root, "import sqlalchemy\n")
        stderr = io.StringIO()

        # Act
        with (
            mock.patch.object(Path, "cwd", return_value=root),
            contextlib.redirect_stderr(stderr),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(import_contract_gate.__file__, run_name="__main__")

        # Assert
        self.assertEqual(1, raised.value.code)
        self.assertIn("LAYER001 domain code must not import 'sqlalchemy'", stderr.getvalue())


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
