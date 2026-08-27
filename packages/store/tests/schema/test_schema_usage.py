"""Established repositories use the complete shared metadata, never lightweight clauses."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from aerial_rescue_store import approvals, audit, idempotency, outbox
from aerial_rescue_store.database.schema import (
    APPROVAL,
    AUDIT_RECORD,
    AUDIT_SEQUENCE,
    COMMAND_OUTBOX,
    IDEMPOTENCY_CLAIM,
)

_RUNTIME_DDL = re.compile(
    r"^\s*(?:"
    r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|SEQUENCE|TYPE)"
    r"|ALTER\s+(?:TABLE|INDEX|SEQUENCE|TYPE)"
    r"|DROP\s+(?:TABLE|INDEX|SEQUENCE|TYPE)"
    r"|TRUNCATE\s+TABLE"
    r")\b",
    re.IGNORECASE,
)


def _import_violations(node: ast.Import, *, service: bool) -> tuple[str, ...]:
    """Return driver or service-owned persistence imports from one plain import."""
    names = {alias.name for alias in node.names}
    violations = []
    if "asyncpg" in names:
        violations.append("asyncpg-import")
    if service and "sqlalchemy" in names:
        violations.append("sqlalchemy-import")
    return tuple(violations)


def _from_import_violations(
    node: ast.ImportFrom, *, service: bool, migration: bool
) -> tuple[str, ...]:
    """Return forbidden driver, service SQLAlchemy, or raw-SQL imports."""
    module = node.module or ""
    violations = []
    if module == "asyncpg" or module.startswith("asyncpg."):
        violations.append("asyncpg-import")
    if service and (module == "sqlalchemy" or module.startswith("sqlalchemy.")):
        violations.append("sqlalchemy-import")
    if (
        module == "sqlalchemy"
        and not migration
        and any(alias.name == "text" for alias in node.names)
    ):
        violations.append("raw-sql-import")
    return tuple(violations)


def _node_violations(node: ast.AST, *, service: bool, migration: bool) -> tuple[str, ...]:
    """Return every persistence-boundary violation expressed by one syntax node."""
    if isinstance(node, ast.Import):
        return _import_violations(node, service=service)
    if isinstance(node, ast.ImportFrom):
        return _from_import_violations(node, service=service, migration=migration)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        in {
            "create_all",
            "drop_all",
            "exec_driver_sql",
        }
    ):
        return ("runtime-ddl-or-driver-sql",)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and not migration
        and _RUNTIME_DDL.match(node.value) is not None
    ):
        return ("runtime-ddl-string",)
    return ()


class RepositoryMetadataTests(unittest.TestCase):
    def test_boundary_scanner_detects_every_forbidden_persistence_escape(self) -> None:
        # Arrange
        cases = (
            ("import asyncpg", False, False, ("asyncpg-import",)),
            ("from sqlalchemy import text", False, False, ("raw-sql-import",)),
            ("from sqlalchemy import select", True, False, ("sqlalchemy-import",)),
            ('ddl = "CREATE TABLE bypass (id INTEGER)"', False, False, ("runtime-ddl-string",)),
            ("metadata.create_all()", False, False, ("runtime-ddl-or-driver-sql",)),
            ('ddl = "CREATE TABLE migration_owned (id INTEGER)"', False, True, ()),
        )

        # Act
        actual = []
        for source, service, migration, _expected in cases:
            tree = ast.parse(source)
            actual.append(
                tuple(
                    code
                    for node in ast.walk(tree)
                    for code in _node_violations(
                        node,
                        service=service,
                        migration=migration,
                    )
                )
            )

        # Assert
        self.assertEqual([expected for _source, _service, _migration, expected in cases], actual)

    def test_application_sources_cannot_bypass_the_store_owned_sqlalchemy_boundary(self) -> None:
        # Arrange
        repository = Path(__file__).resolve().parents[4]
        store_sources = tuple((repository / "packages/store/src").rglob("*.py"))
        service_sources = tuple((repository / "services").glob("*/src/**/*.py"))

        # Act
        violations: list[str] = []
        for path in (*store_sources, *service_sources):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(repository).as_posix()
            for node in ast.walk(tree):
                violations.extend(
                    f"{relative}:{getattr(node, 'lineno', 1)}:{code}"
                    for code in _node_violations(
                        node,
                        service=relative.startswith("services/"),
                        migration="migrations" in path.parts,
                    )
                )

        # Assert
        self.assertEqual([], violations)

    def test_every_established_repository_uses_its_migrated_table_object(self) -> None:
        # Arrange
        expected = (
            AUDIT_SEQUENCE,
            AUDIT_RECORD,
            APPROVAL,
            IDEMPOTENCY_CLAIM,
            COMMAND_OUTBOX,
        )

        # Act
        actual = (
            audit._SEQUENCE_ROWS,
            audit._RECORD_ROWS,
            approvals._APPROVAL_ROWS,
            idempotency._CLAIM_ROWS,
            outbox._OUTBOX_ROWS,
        )

        # Assert
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
