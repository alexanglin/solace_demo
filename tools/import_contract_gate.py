"""Fail when the pure domain package imports an infrastructure dependency."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "asyncpg",
        "fastapi",
        "litellm",
        "ollama",
        "solace",
        "solace_agent_mesh",
        "sqlalchemy",
    }
)


@dataclass(frozen=True, order=True)
class ImportDiagnostic:
    """One forbidden domain import."""

    path: PurePosixPath
    line: int
    column: int
    module: str

    def render(self) -> str:
        """Render a stable compiler-style diagnostic."""
        return (
            f"{self.path}:{self.line}:{self.column}: LAYER001 "
            f"domain code must not import {self.module!r}"
        )


def _root_name(module: str | None) -> str | None:
    """Return the top-level name for an absolute import."""
    if module is None or module.startswith("."):
        return None
    return module.partition(".")[0]


def check_source(path: PurePosixPath, source: str) -> tuple[ImportDiagnostic, ...]:
    """Return every forbidden import in one domain source file."""
    tree = ast.parse(source, filename=str(path))
    diagnostics: list[ImportDiagnostic] = []
    for node in ast.walk(tree):
        modules: tuple[str | None, ...]
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = (node.module,)
        else:
            continue
        for module in modules:
            root = _root_name(module)
            if root in FORBIDDEN_IMPORT_ROOTS:
                diagnostics.append(
                    ImportDiagnostic(
                        path=path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        module=root,
                    )
                )
    return tuple(sorted(diagnostics))


def main() -> int:
    """Check every Python file under the pure domain source root."""
    root = Path.cwd()
    domain_root = root / "packages" / "domain" / "src"
    if not domain_root.exists():
        return 0
    diagnostics: list[ImportDiagnostic] = []
    for path in sorted(domain_root.rglob("*.py")):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        diagnostics.extend(check_source(relative, path.read_text(encoding="utf-8")))
    for diagnostic in sorted(diagnostics):
        print(diagnostic.render(), file=sys.stderr)
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
