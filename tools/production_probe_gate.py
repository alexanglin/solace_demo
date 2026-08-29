"""Resolve the references the dashboard's production harness embeds as string literals.

The harness reaches the running stack by embedding Python programs, container module
names, and environment variable names as TypeScript string literals. ``tsc`` and ESLint
see ``string[]``, so ADR-0197's deletion of two scenario-service modules left every
reference in ``fleetStatusProbe`` naming something that no longer exists while every
gate stayed green.

This gate reads those literals and refuses one that names something the repository does
not contain. It resolves by reading source, never by importing it: a gate that imported
an application module to check a string would run that module's top-level code inside
the commit path. It starts no process either -- ADR-0025 confines ``subprocess`` to four
reviewed owners -- so the enumeration lives in the shell driver and arrives as
arguments. ADR-0204 records the decision and the shapes this gate can reconstruct.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

DIAGNOSTIC: Final = "PROBE"
PROBE_SUFFIX: Final = "Probe"
"""The declaration-name suffix that marks a constant as embedded Python (ADR-0204)."""

MODULE_FLAG: Final = "-m"
"""The container argument whose successor names the module the image will execute."""

_TYPESCRIPT_LANGUAGE: Final = Language(tree_sitter_typescript.language_typescript())


@dataclass(frozen=True, slots=True)
class EmbeddedProbe:
    """One Python program a harness source embeds as a string literal."""

    name: str
    source: str


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _string_value(node: Node | None, source: bytes) -> str | None:
    """Return the value of one TypeScript string literal, or ``None`` when it is not one."""
    if node is None or node.type != "string":
        return None
    try:
        value = ast.literal_eval(_node_text(node, source))
    except SyntaxError, ValueError:
        return None
    return value if isinstance(value, str) else None


def _joined_array(node: Node, source: bytes) -> Node | None:
    """Return the array of a ``[...].join(...)`` call expression, when it is one."""
    if node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    if function is None or function.type != "member_expression":
        return None
    prop = function.child_by_field_name("property")
    if prop is None or _node_text(prop, source) != "join":
        return None
    array = function.child_by_field_name("object")
    return array if array is not None and array.type == "array" else None


def _joined_value(node: Node, source: bytes) -> str | None:
    """Return the reconstructed value of a ``[...].join(sep)`` over string literals."""
    array = _joined_array(node, source)
    arguments = node.child_by_field_name("arguments")
    if array is None or arguments is None:
        return None
    separator = _string_value(next(iter(arguments.named_children), None), source)
    elements = [_string_value(child, source) for child in array.named_children]
    if separator is None or any(element is None for element in elements):
        return None
    return separator.join(cast("list[str]", elements))


def _declaration_value(node: Node, source: bytes) -> str | None:
    """Return the reconstructed Python of one probe declaration, or ``None``."""
    direct = _string_value(node, source)
    return direct if direct is not None else _joined_value(node, source)


def extract_probes(path: Path, root: Node, source: bytes, issues: list[str]) -> list[EmbeddedProbe]:
    """Return every embedded probe, recording a finding for one that cannot be read."""
    probes: list[EmbeddedProbe] = []
    for node in _walk(root):
        if node.type != "variable_declarator":
            continue
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")
        if name_node is None or name_node.type != "identifier":
            continue
        name = _node_text(name_node, source)
        if not name.endswith(PROBE_SUFFIX):
            continue
        value = None if value_node is None else _declaration_value(value_node, source)
        if value is None:
            issues.append(
                f"{path}: {name} is named as a probe, but its declaration is not a literal "
                f"the gate can reconstruct"
            )
            continue
        probes.append(EmbeddedProbe(name, value))
    return probes


def first_party_packages(source_roots: Sequence[Path]) -> dict[str, Path]:
    """Return each importable top-level package name and the source root that holds it."""
    packages: dict[str, Path] = {}
    for root in source_roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and (entry / "__init__.py").is_file():
                packages.setdefault(entry.name, root)
    return packages


def is_first_party(module: str, packages: Mapping[str, Path]) -> bool:
    """Whether this dotted module name belongs to a workspace package."""
    return module.split(".", maxsplit=1)[0] in packages


def _resolve(module: str, packages: Mapping[str, Path], sibling: str) -> Path | None:
    root = packages.get(module.split(".", maxsplit=1)[0])
    if root is None:
        return None
    base = root.joinpath(*module.split("."))
    candidates = (base.with_suffix(".py"), base / sibling)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def module_path(module: str, packages: Mapping[str, Path]) -> Path | None:
    """Return the file backing one first-party module, or ``None`` when it is absent."""
    return _resolve(module, packages, "__init__.py")


def runnable_module_path(module: str, packages: Mapping[str, Path]) -> Path | None:
    """Return the file ``python -m`` would execute, or ``None`` when there is none."""
    return _resolve(module, packages, "__main__.py")


def _assigned_names(targets: Iterable[ast.expr]) -> set[str]:
    return {
        node.id for target in targets for node in ast.walk(target) if isinstance(node, ast.Name)
    }


def _names_bound_by(statement: ast.stmt) -> set[str]:
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            names = {statement.name}
        case ast.Assign(targets=targets):
            names = _assigned_names(targets)
        case ast.AnnAssign(target=ast.Name(id=identifier)):
            names = {identifier}
        case ast.Import(names=aliases):
            names = {alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in aliases}
        case ast.ImportFrom(names=aliases):
            names = {alias.asname or alias.name for alias in aliases}
        case ast.If(body=body, orelse=orelse):
            names = _bound_names(body) | _bound_names(orelse)
        case ast.Try(body=body, orelse=orelse, finalbody=finalbody, handlers=handlers):
            nested = [*body, *orelse, *finalbody, *(node for h in handlers for node in h.body)]
            names = _bound_names(nested)
        case _:
            names = set()
    return names


def _bound_names(statements: Iterable[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        names |= _names_bound_by(statement)
    return names


def module_bindings(path: Path) -> frozenset[str]:
    """Return every name one module binds at its top level, conditional imports included."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except OSError, SyntaxError, ValueError:
        return frozenset()
    return frozenset(_bound_names(tree.body))


def _from_import_issues(
    label: str,
    node: ast.ImportFrom,
    packages: Mapping[str, Path],
) -> list[str]:
    module = node.module
    if module is None or not is_first_party(module, packages):
        return []
    resolved = module_path(module, packages)
    if resolved is None:
        return [
            f"{label} imports from {module}, but no module of that name exists under the "
            f"workspace source roots"
        ]
    bindings = module_bindings(resolved)
    return [
        f"{label} imports {alias.name} from {module}, which binds no such name"
        for alias in node.names
        if alias.name != "*" and alias.name not in bindings
    ]


def _plain_import_issues(
    label: str,
    node: ast.Import,
    packages: Mapping[str, Path],
) -> list[str]:
    return [
        f"{label} imports {alias.name}, but no module of that name exists under the "
        f"workspace source roots"
        for alias in node.names
        if is_first_party(alias.name, packages) and module_path(alias.name, packages) is None
    ]


def probe_issues(path: Path, probe: EmbeddedProbe, packages: Mapping[str, Path]) -> list[str]:
    """Return every unresolved first-party reference one embedded probe makes."""
    label = f"{path}: {probe.name}"
    try:
        tree = ast.parse(probe.source)
    except SyntaxError as error:
        return [f"{label} cannot be parsed as Python: {error.msg}"]
    issues: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            issues.extend(_from_import_issues(label, node, packages))
        elif isinstance(node, ast.Import):
            issues.extend(_plain_import_issues(label, node, packages))
    return issues


def _invocation_issue(
    path: Path,
    node: Node,
    source: bytes,
    packages: Mapping[str, Path],
) -> list[str]:
    module = _string_value(node, source)
    if module is None:
        return [f"{path}: a {MODULE_FLAG} container argument is not a literal module name"]
    if not is_first_party(module, packages):
        return []
    if runnable_module_path(module, packages) is not None:
        return []
    return [
        f"{path}: {MODULE_FLAG} names {module}, which is not a runnable module under the "
        f"workspace source roots"
    ]


def invocation_issues(
    path: Path,
    root: Node,
    source: bytes,
    packages: Mapping[str, Path],
) -> list[str]:
    """Return every unresolved first-party module named after a ``-m`` container argument."""
    issues: list[str] = []
    for node in _walk(root):
        if node.type != "array":
            continue
        elements = list(node.named_children)
        for index, element in enumerate(elements[:-1]):
            if _string_value(element, source) == MODULE_FLAG:
                issues.extend(_invocation_issue(path, elements[index + 1], source, packages))
    return issues


def evaluate_support(path: Path, text: str, packages: Mapping[str, Path]) -> list[str]:
    """Return every finding for one harness source."""
    source = text.encode("utf-8")
    tree = Parser(_TYPESCRIPT_LANGUAGE).parse(source)
    if tree.root_node.has_error:
        return [f"{path}: harness source cannot be parsed as TypeScript"]
    issues: list[str] = []
    for probe in extract_probes(path, tree.root_node, source, issues):
        issues.extend(probe_issues(path, probe, packages))
    issues.extend(invocation_issues(path, tree.root_node, source, packages))
    return issues


def evaluate(
    supports: Sequence[Path],
    source_roots: Sequence[Path],
    errors: list[str],
) -> list[str]:
    """Return every finding across the enumerated harness sources."""
    packages = first_party_packages(source_roots)
    issues: list[str] = []
    for path in supports:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"{path}: harness source could not be read")
            continue
        issues.extend(evaluate_support(path, text, packages))
    return issues


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="production-probe-gate",
        description="Resolve the dashboard harness's embedded probe references (ADR-0204).",
    )
    parser.add_argument("--support", action="append", default=[], type=Path)
    parser.add_argument("--source-root", action="append", default=[], type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print diagnostics and return a blocking status when a probe reference is unresolved."""
    arguments = _parse_arguments(argv)
    supports = cast("list[Path]", arguments.support)
    source_roots = cast("list[Path]", arguments.source_root)
    if not supports:
        return 0
    errors: list[str] = []
    issues = sorted(set(evaluate(supports, source_roots, errors) + errors))
    for issue in issues:
        print(f"{DIAGNOSTIC}: {issue}", file=sys.stderr)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
