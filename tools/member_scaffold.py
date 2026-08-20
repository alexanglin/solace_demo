"""Decide whether a workspace member is a scaffold with nothing to measure yet.

A scaffold is a declared member — it has a manifest — whose ``src/`` holds nothing but
``py.typed`` markers and docstring-only modules, and which has no ``tests/`` directory.
The coverage gate reports such a member instead of failing it and the mutation gate
skips it; the first executable statement or test file makes it active and every
fail-closed rule applies again (``docs/adr/0053``).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

SCAFFOLD_OUTCOME = "SCAFFOLD"
SCAFFOLD_DETAIL = "manifest and docstring-only package markers, no tests; not measured"
TYPED_MARKER = "py.typed"
CACHE_DIRECTORY = "__pycache__"


def is_docstring_only(source: Path) -> bool:
    """Return whether ``source`` parses to an empty body or a lone docstring."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except OSError, SyntaxError, ValueError:
        return False
    if not tree.body:
        return True
    if len(tree.body) != 1:
        return False
    node = tree.body[0]
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _source_files(member_root: Path) -> Iterator[Path]:
    source_root = member_root / "src"
    if not source_root.is_dir():
        return
    for path in sorted(source_root.rglob("*")):
        if CACHE_DIRECTORY in path.relative_to(source_root).parts:
            continue
        if path.is_file():
            yield path


def is_scaffold(member_root: Path) -> bool:
    """Return whether the member at ``member_root`` has nothing to measure yet."""
    if not (member_root / "pyproject.toml").is_file():
        return False
    if (member_root / "tests").exists():
        return False
    return all(
        path.name == TYPED_MARKER or (path.suffix == ".py" and is_docstring_only(path))
        for path in _source_files(member_root)
    )
