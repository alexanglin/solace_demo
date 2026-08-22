"""Select the test files a change affects, from a project-owned import graph.

This is the dependency map `scripts/hooks/python/pytest-related.sh` waited for. It derives
each owned file's importable module name the way mypy and pytest do -- walk up while
`__init__.py` exists, and the first directory without one is the import root -- parses every
file with `ast`, resolves absolute and relative imports against that index, and inverts the
edges. The transitive closure of dependents of the changed files, restricted to test files,
is the affected set (docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md).

This module is pure: it parses the files it is handed and never launches a process. The
caller enumerates the tree with `git ls-files` and passes the listing as `--paths-from`, so
`subprocess` stays inside the owners ADR-0025 admits.

It fails safe. A changed path that is not a Python file in the graph, is a `conftest.py`, has
an ambiguous module name, or does not parse, widens the selection to the whole suite rather
than guessing at it. Under-selecting is the one failure a commit-stage selector must not have.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ALL_TESTS = ":all:"
"""Printed in place of a selection when the change cannot be narrowed safely.

A colon-wrapped word cannot collide with a repository path, so the calling script can
branch on it without quoting rules.
"""

DIAGNOSTIC = "AFFECTED"
INITIALISER = "__init__.py"
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
TEST_PREFIX = "test_"
WIDENING_FILENAMES = frozenset({"conftest.py"})
"""Files whose effect is on collection itself, so no import edge can describe it."""


class SelectionError(Exception):
    """The selector could not run, as distinct from finding nothing to select."""


@dataclass(frozen=True)
class ImportGraph:
    """Owned Python files, their module names, and who imports whom."""

    module_by_path: Mapping[PurePosixPath, str]
    path_by_module: Mapping[str, PurePosixPath]
    dependents: Mapping[PurePosixPath, frozenset[PurePosixPath]]
    opaque: frozenset[PurePosixPath]
    """Files the graph cannot reason about, so a change to one widens the selection."""


def python_paths(paths: Iterable[PurePosixPath]) -> tuple[PurePosixPath, ...]:
    """Return the Python files among these paths, sorted."""
    return tuple(sorted(path for path in paths if path.suffix in PYTHON_SUFFIXES))


def package_directories(paths: Iterable[PurePosixPath]) -> frozenset[PurePosixPath]:
    """Return every directory holding a package initialiser."""
    return frozenset(path.parent for path in paths if path.name == INITIALISER)


def module_name_for(path: PurePosixPath, packages: frozenset[PurePosixPath]) -> str:
    """Return the importable module name for this file.

    The walk stops at the first directory without an initialiser, which is the import
    root. A package initialiser is named for its package rather than for the file.
    """
    parts: list[str] = [] if path.name == INITIALISER else [path.stem]
    directory = path.parent
    while directory in packages:
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))


def is_test_path(path: PurePosixPath) -> bool:
    """Whether pytest would collect this file as a test module."""
    return path.suffix == ".py" and path.name.startswith(TEST_PREFIX)


def _resolve_relative(node: ast.ImportFrom, module: str) -> str | None:
    """Return the absolute name a `from . import x` refers to, or None if it escapes."""
    if node.level == 0:
        return node.module
    package = module.split(".")[:-1]
    if node.level > len(package) + 1:
        return None
    base = package[: len(package) - node.level + 1]
    if node.module is not None:
        base = [*base, node.module]
    return ".".join(base) or None


def referenced_modules(tree: ast.Module, module: str) -> tuple[str, ...]:
    """Return every module name this source refers to, relatives resolved to absolute.

    `from a.b import c` yields both `a.b` and `a.b.c`, because only the index can say
    whether `c` is a submodule or an attribute.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative(node, module)
            if base is None:
                continue
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    return tuple(names)


def _invert_modules(
    module_by_path: Mapping[PurePosixPath, str],
) -> tuple[dict[str, PurePosixPath], frozenset[PurePosixPath]]:
    """Return the name-to-path index, and the paths whose names are not unique.

    Two files sharing a module name make an import to that name unresolvable, so both
    are reported as opaque rather than one of them silently winning.
    """
    paths_by_module: dict[str, list[PurePosixPath]] = {}
    for path, module in sorted(module_by_path.items()):
        paths_by_module.setdefault(module, []).append(path)
    index = {module: paths[0] for module, paths in paths_by_module.items() if len(paths) == 1}
    ambiguous = frozenset(
        path for paths in paths_by_module.values() if len(paths) > 1 for path in paths
    )
    return index, ambiguous


def _parse_source(path: Path) -> ast.Module | None:
    """Return the parsed module, or None when it cannot be read or parsed."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def build_graph(root: Path, listing: Sequence[PurePosixPath]) -> ImportGraph:
    """Build the import graph for the Python files in this listing."""
    paths = python_paths(listing)
    packages = package_directories(paths)
    module_by_path = {path: module_name_for(path, packages) for path in paths}
    path_by_module, opaque = _invert_modules(module_by_path)
    dependents: dict[PurePosixPath, set[PurePosixPath]] = {path: set() for path in paths}
    unparsed: set[PurePosixPath] = set()
    for path, module in module_by_path.items():
        tree = _parse_source(root / path)
        if tree is None:
            unparsed.add(path)
            continue
        for name in referenced_modules(tree, module):
            target = path_by_module.get(name)
            if target is not None and target != path:
                dependents[target].add(path)
    return ImportGraph(
        module_by_path=module_by_path,
        path_by_module=path_by_module,
        dependents={path: frozenset(found) for path, found in dependents.items()},
        opaque=opaque | frozenset(unparsed),
    )


def _must_widen(graph: ImportGraph, path: PurePosixPath) -> bool:
    """Whether this changed path defeats narrowing and requires the whole suite."""
    return (
        path.suffix not in PYTHON_SUFFIXES
        or path.name in WIDENING_FILENAMES
        or path not in graph.module_by_path
        or path in graph.opaque
    )


def _closure(graph: ImportGraph, changed: Sequence[PurePosixPath]) -> frozenset[PurePosixPath]:
    """Return the changed files and everything that transitively imports them."""
    reached = set(changed)
    queue = deque(changed)
    while queue:
        for dependent in graph.dependents.get(queue.popleft(), frozenset()):
            if dependent not in reached:
                reached.add(dependent)
                queue.append(dependent)
    return frozenset(reached)


def selection(graph: ImportGraph, changed: Sequence[PurePosixPath]) -> tuple[str, ...]:
    """Return the test files these changes affect, or the widening sentinel."""
    if not changed:
        return ()
    if any(_must_widen(graph, path) for path in changed):
        return (ALL_TESTS,)
    return tuple(sorted(path.as_posix() for path in _closure(graph, changed) if is_test_path(path)))


def _read_listing(path: Path) -> tuple[PurePosixPath, ...]:
    """Return the paths in this listing file.

    `git ls-files -z` separates with NUL, which is the only separator a path cannot
    contain; a newline-separated listing is accepted too so the file can be written by
    hand. Splitting on both costs nothing and removes a way to hold this wrong.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"listing is unreadable: {error}"
        raise SelectionError(message) from error
    entries = text.replace("\0", "\n").splitlines()
    return tuple(PurePosixPath(entry) for entry in entries if entry)


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed command line."""
    parser = argparse.ArgumentParser(description="Select the tests a change affects.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--paths-from", required=True, type=Path, dest="paths_from")
    parser.add_argument("changed", nargs="*")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the affected test files, one per line, and return a blocking status."""
    arguments = _parse_arguments(argv)
    try:
        listing = _read_listing(arguments.paths_from)
    except SelectionError as error:
        print(f"{DIAGNOSTIC}: {error}", file=sys.stderr)
        return 1
    graph = build_graph(arguments.root, listing)
    for line in selection(graph, tuple(PurePosixPath(name) for name in arguments.changed)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
