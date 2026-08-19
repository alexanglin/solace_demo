"""Syntax-aware Arrange-Act-Assert checker for Python and the JavaScript family.

Enforces the contract in ``docs/TESTING.md`` for every project-owned executable test,
per ``docs/adr/0018-enforced-arrange-act-assert.md``. There is no per-test suppression:
an unsupported or dynamic test registration fails closed rather than being skipped.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from tools.executable_resolution import required_executable

PYTHON_PHASE_MARKERS = ("# Arrange", "# Act", "# Assert")
STATE_MACHINE_DECORATORS = frozenset({"rule", "invariant"})
FUSED_ASSERTION_METHODS = frozenset(
    {"assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"}
)
JAVASCRIPT_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"})
JAVASCRIPT_PHASE_MARKERS = ("// Arrange", "// Act", "// Assert")
TEST_IMPORTS = {
    "vitest": frozenset({"test", "it"}),
    "@playwright/test": frozenset({"test"}),
}
ASSERTION_IMPORTS = {
    "vitest": frozenset({"expect", "expectTypeOf", "assert"}),
    "@playwright/test": frozenset({"expect"}),
}
TEST_MODIFIERS = frozenset({"only", "skip", "concurrent", "fails", "runIf", "skipIf"})
_TYPESCRIPT_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One compiler-style finding at a specific source position."""

    path: PurePath
    line: int
    column: int
    code: str
    message: str

    def render(self) -> str:
        """Format the finding as a compiler-style diagnostic line."""
        return f"{self.path}:{self.line}:{self.column} {self.code} {self.message}"


def check_text(path: PurePath, source: str) -> tuple[Diagnostic, ...]:
    """Check one source text, dispatching on the path suffix."""
    if path.suffix == ".py":
        return _check_python(path, source)
    if path.suffix in JAVASCRIPT_SUFFIXES:
        return _check_javascript(path, source)
    return ()


def check_paths(paths: Iterable[Path]) -> tuple[Diagnostic, ...]:
    """Check every supported source file among the given paths, in sorted order."""
    diagnostics: list[Diagnostic] = []
    for path in sorted(
        (candidate for candidate in paths if candidate.suffix in {".py", *JAVASCRIPT_SUFFIXES}),
        key=lambda candidate: candidate.as_posix(),
    ):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            diagnostics.append(
                Diagnostic(
                    path=path,
                    line=1,
                    column=1,
                    code="AAA014",
                    message=f"source cannot be read as UTF-8: {error}.",
                )
            )
            continue
        diagnostics.extend(check_text(path, source))
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                item.path.as_posix(),
                item.line,
                item.column,
                item.code,
                item.message,
            ),
        )
    )


def repository_source_paths(root: Path) -> tuple[Path, ...]:
    """Every tracked or unignored Python and JavaScript-family source file under root."""
    git_executable = required_executable("git")
    result = subprocess.run(
        (
            git_executable,
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ),
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        message = f"git source discovery failed: {detail or 'unknown error'}"
        raise RuntimeError(message)
    return tuple(
        root / relative
        for raw in result.stdout.split(b"\0")
        if raw
        for relative in (Path(raw.decode("utf-8")),)
        if relative.suffix in {".py", *JAVASCRIPT_SUFFIXES}
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Check the given paths, or the whole repository, and return an exit code."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments:
        paths = tuple(Path(argument) for argument in arguments)
    else:
        try:
            paths = repository_source_paths(Path.cwd())
        except RuntimeError as error:
            print(f"AAA014 {error}", file=sys.stderr)
            return 2

    diagnostics = check_paths(paths)
    for diagnostic in diagnostics:
        print(diagnostic.render(), file=sys.stderr)
    return 1 if diagnostics else 0


def _check_python(path: PurePath, source: str) -> tuple[Diagnostic, ...]:
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return (
            Diagnostic(
                path=path,
                line=error.lineno or 1,
                column=error.offset or 1,
                code="AAA013",
                message=f"Python source cannot be parsed: {error.msg}.",
            ),
        )

    comments = tuple(
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and token.string in PYTHON_PHASE_MARKERS
    )
    tests = tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_python_test(node)
    )
    diagnostics: list[Diagnostic] = []

    for node in tests:
        diagnostics.extend(_check_python_test(path, node, comments))

    diagnostics.extend(_check_dynamic_python_tests(path, tree))
    diagnostics.extend(_check_stray_python_markers(path, comments, tests))
    if not tests and _is_canonical_test_file(path):
        diagnostics.append(_missing_executable_test_diagnostic(path))
    return _sorted_diagnostics(diagnostics)


def _is_python_test(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.name.startswith("test"):
        return True
    return any(
        _decorator_name(decorator) in STATE_MACHINE_DECORATORS for decorator in node.decorator_list
    )


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _check_python_test(
    path: PurePath,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    comments: tuple[tokenize.TokenInfo, ...],
) -> list[Diagnostic]:
    body_indent = min(
        (statement.col_offset for statement in node.body),
        default=node.col_offset + 4,
    )
    direct, diagnostics = _direct_python_markers(path, node, comments, body_indent)
    ordered, marker_diagnostics = _ordered_python_markers(path, node, direct)
    diagnostics.extend(marker_diagnostics)
    if ordered is None:
        return diagnostics

    lines = (
        ordered[0].start[0],
        ordered[1].start[0],
        ordered[2].start[0],
    )
    diagnostics.extend(_python_phase_diagnostics(path, node, lines, body_indent))
    diagnostics.extend(_python_assertion_diagnostics(path, node, lines[2], body_indent))
    return diagnostics


def _direct_python_markers(
    path: PurePath,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    comments: tuple[tokenize.TokenInfo, ...],
    body_indent: int,
) -> tuple[tuple[tokenize.TokenInfo, ...], list[Diagnostic]]:
    contained = tuple(
        comment
        for comment in comments
        if node.lineno < comment.start[0] <= (node.end_lineno or node.lineno)
    )
    diagnostics: list[Diagnostic] = []
    direct: list[tokenize.TokenInfo] = []
    for comment in contained:
        if comment.start[1] != body_indent or _marker_overlaps_statement(comment, node.body):
            diagnostics.append(
                _at_token(
                    path,
                    comment,
                    "AAA008",
                    f"{node.name}: phase markers must be standalone at the direct test-body level.",
                )
            )
            continue
        direct.append(comment)
    return tuple(direct), diagnostics


def _ordered_python_markers(
    path: PurePath,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    direct: tuple[tokenize.TokenInfo, ...],
) -> tuple[tuple[tokenize.TokenInfo, ...] | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    by_marker = {
        marker: tuple(comment for comment in direct if comment.string == marker)
        for marker in PYTHON_PHASE_MARKERS
    }
    for marker, matches in by_marker.items():
        if not matches:
            diagnostics.append(
                _at_node(path, node, "AAA001", f"{node.name}: missing '{marker}' phase.")
            )
        elif len(matches) > 1:
            diagnostics.append(
                _at_token(
                    path,
                    matches[1],
                    "AAA002",
                    f"{node.name}: duplicate '{marker}' phase.",
                )
            )

    if not all(len(by_marker[marker]) == 1 for marker in PYTHON_PHASE_MARKERS):
        return None, diagnostics

    ordered = tuple(by_marker[marker][0] for marker in PYTHON_PHASE_MARKERS)
    lines = tuple(marker.start[0] for marker in ordered)
    if lines != tuple(sorted(lines)):
        diagnostics.append(
            _at_token(
                path,
                ordered[0],
                "AAA003",
                f"{node.name}: phases must appear in Arrange, Act, Assert order.",
            )
        )
        return None, diagnostics
    return ordered, diagnostics


def _python_phase_diagnostics(
    path: PurePath,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: tuple[int, int, int],
    body_indent: int,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    optional_docstring = node.body[0] if node.body and _is_docstring(node.body[0]) else None
    executable = tuple(
        statement
        for statement in node.body
        if statement is not optional_docstring and _is_executable_statement(statement)
    )
    before_arrange = tuple(statement for statement in executable if statement.lineno < lines[0])
    if before_arrange:
        diagnostics.append(
            _at_node(
                path,
                min(before_arrange, key=lambda statement: statement.lineno),
                "AAA005",
                f"{node.name}: executable code appears before '# Arrange'.",
            )
        )

    phase_ranges = (
        ("Arrange", lines[0], lines[1]),
        ("Act", lines[1], lines[2]),
        ("Assert", lines[2], (node.end_lineno or lines[2]) + 1),
    )
    for phase, start, end in phase_ranges:
        statements = tuple(statement for statement in executable if start < statement.lineno < end)
        if statements:
            continue
        diagnostics.append(
            _at_line(
                path,
                start,
                body_indent + 1,
                "AAA004",
                f"{node.name}: {phase} phase has no executable statement.",
            )
        )
    return diagnostics


def _python_assertion_diagnostics(
    path: PurePath,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    assert_line: int,
    body_indent: int,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    assertion_nodes = tuple(_python_assertions(node))
    for assertion in assertion_nodes:
        if assertion.lineno >= assert_line:
            continue
        diagnostics.append(
            _at_node(
                path,
                assertion,
                "AAA006",
                f"{node.name}: assertions are permitted only in the Assert phase.",
            )
        )

    if not any(assertion.lineno > assert_line for assertion in assertion_nodes):
        diagnostics.append(
            _at_line(
                path,
                assert_line,
                body_indent + 1,
                "AAA007",
                f"{node.name}: Assert phase contains no recognized outcome assertion.",
            )
        )
    return diagnostics


def _marker_overlaps_statement(
    marker: tokenize.TokenInfo,
    statements: list[ast.stmt],
) -> bool:
    line = marker.start[0]
    return any(
        statement.lineno <= line <= (statement.end_lineno or statement.lineno)
        for statement in statements
    )


def _is_executable_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return False
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return statement.value.value is not Ellipsis
    return True


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _python_assertions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[ast.Assert | ast.Call]:
    for candidate in _walk_python_test_scope(node):
        if isinstance(candidate, ast.Assert):
            yield candidate
            continue
        if not isinstance(candidate, ast.Call):
            continue
        name = _call_name(candidate.func)
        if name is None or name in FUSED_ASSERTION_METHODS:
            continue
        if name.startswith("assert"):
            yield candidate


def _walk_python_test_scope(node: ast.AST) -> Iterable[ast.AST]:
    """Walk one test body without treating nested callable bodies as executed assertions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_python_test_scope(child)


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _dynamic_test_assignment(statement: ast.stmt) -> tuple[ast.Name, ast.expr] | None:
    """Return a dynamic test assignment target and value, when present."""
    target: ast.expr | None = None
    value: ast.expr | None = None
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target = statement.targets[0]
        value = statement.value
    elif isinstance(statement, ast.AnnAssign):
        target = statement.target
        value = statement.value
    if (
        isinstance(target, ast.Name)
        and target.id.startswith("test")
        and isinstance(value, (ast.Attribute, ast.Call, ast.Lambda, ast.Name))
    ):
        return target, value
    return None


def _check_dynamic_python_tests(path: PurePath, tree: ast.Module) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for statement in _registration_scope_statements(tree):
        assignment = _dynamic_test_assignment(statement)
        if assignment is None:
            continue
        target, _value = assignment
        diagnostics.append(
            _at_node(
                path,
                statement,
                "AAA009",
                f"{target.id}: dynamic test registration cannot be verified; "
                "use an explicit test body.",
            )
        )
    return diagnostics


def _registration_scope_statements(node: ast.AST) -> Iterable[ast.stmt]:
    """Walk module and class control flow while excluding every callable body."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.stmt):
            yield child
        yield from _registration_scope_statements(child)


def _check_stray_python_markers(
    path: PurePath,
    comments: tuple[tokenize.TokenInfo, ...],
    tests: tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for comment in comments:
        if any(
            test.lineno < comment.start[0] <= (test.end_lineno or test.lineno) for test in tests
        ):
            continue
        diagnostics.append(
            _at_token(
                path,
                comment,
                "AAA010",
                "AAA phase marker appears outside a recognized executable test.",
            )
        )
    return diagnostics


@dataclass(frozen=True, slots=True)
class _JavascriptImports:
    tests: frozenset[str]
    matcher_assertions: frozenset[str]
    direct_assertions: frozenset[str]


@dataclass(slots=True)
class _JavascriptImportBuilder:
    tests: set[str]
    matcher_assertions: set[str]
    direct_assertions: set[str]


@dataclass(frozen=True, slots=True)
class _JavascriptTestContext:
    path: PurePath
    label: str
    body: Node
    source: bytes
    imports: _JavascriptImports


def _check_javascript(path: PurePath, source: str) -> tuple[Diagnostic, ...]:
    source_bytes = source.encode("utf-8")
    language = _TSX_LANGUAGE if path.suffix in {".jsx", ".tsx"} else _TYPESCRIPT_LANGUAGE
    parser = Parser(language)
    tree = parser.parse(source_bytes)
    if tree.root_node.has_error:
        problem = next(
            (node for node in _walk_tree(tree.root_node) if node.is_error or node.is_missing),
            tree.root_node,
        )
        return (
            _at_js_node(
                path,
                problem,
                "AAA013",
                "JavaScript or TypeScript source cannot be parsed.",
            ),
        )

    imports = _javascript_imports(tree.root_node, source_bytes)
    diagnostics: list[Diagnostic] = []
    test_bodies: list[Node] = []
    recognized_tests = 0

    for node in _walk_tree(tree.root_node):
        recognized, label, body, call_diagnostics = _javascript_test_registration(
            path, node, source_bytes, imports
        )
        recognized_tests += int(recognized)
        diagnostics.extend(call_diagnostics)
        if body is None or label is None:
            continue
        test_bodies.append(body)
        diagnostics.extend(_check_javascript_test(path, label, body, source_bytes, imports))

    diagnostics.extend(
        _check_stray_javascript_markers(path, tree.root_node, source_bytes, test_bodies)
    )
    if recognized_tests == 0 and _is_canonical_test_file(path):
        diagnostics.append(_missing_executable_test_diagnostic(path))
    return _sorted_diagnostics(diagnostics)


def _javascript_test_registration(
    path: PurePath,
    node: Node,
    source: bytes,
    imports: _JavascriptImports,
) -> tuple[bool, str | None, Node | None, list[Diagnostic]]:
    if node.type != "call_expression":
        return False, None, None, []
    function = node.child_by_field_name("function")
    if function is None:
        return False, None, None, []

    callee = _compact(_node_text(function, source))
    classification = _classify_test_callee(callee, imports)
    if classification == "ignore":
        return False, None, None, []
    if classification == "unknown":
        return (
            False,
            None,
            None,
            [
                _at_js_node(
                    path,
                    node,
                    "AAA013",
                    f"{callee}: test registration must use an explicit Vitest or "
                    "Playwright import.",
                )
            ],
        )
    if classification == "unsupported":
        return (
            True,
            None,
            None,
            [
                _at_js_node(
                    path,
                    node,
                    "AAA009",
                    f"{callee}: dynamic or bodyless test registration cannot be verified.",
                )
            ],
        )
    body, diagnostics = _javascript_callback_body(path, node, callee)
    return True, callee, body, diagnostics


def _javascript_callback_body(
    path: PurePath,
    call: Node,
    callee: str,
) -> tuple[Node | None, list[Diagnostic]]:
    callback = _inline_test_callback(call)
    if callback is None:
        return None, [
            _at_js_node(
                path,
                call,
                "AAA009",
                f"{callee}: use an inline block callback so AAA structure can be verified.",
            )
        ]
    body = callback.child_by_field_name("body")
    if body is not None and body.type == "statement_block":
        return body, []
    return None, [
        _at_js_node(
            path,
            callback,
            "AAA009",
            f"{callee}: expression-bodied tests cannot be verified; use a block body.",
        )
    ]


def _javascript_imports(root: Node, source: bytes) -> _JavascriptImports:
    imports = _JavascriptImportBuilder(set(), set(), set())
    for statement in (node for node in root.named_children if node.type == "import_statement"):
        _record_javascript_import_statement(statement, source, imports)

    return _JavascriptImports(
        tests=frozenset(imports.tests),
        matcher_assertions=frozenset(imports.matcher_assertions),
        direct_assertions=frozenset(imports.direct_assertions),
    )


def _record_javascript_import_statement(
    statement: Node,
    source: bytes,
    imports: _JavascriptImportBuilder,
) -> None:
    module_node = statement.child_by_field_name("source")
    module = _javascript_import_module(module_node, source)
    if module not in TEST_IMPORTS:
        return
    for node in _walk_tree(statement):
        _record_javascript_import_node(module, node, source, imports)


def _record_javascript_import_node(
    module: str,
    node: Node,
    source: bytes,
    imports: _JavascriptImportBuilder,
) -> None:
    if node.type == "import_specifier":
        imported_node = node.child_by_field_name("name")
        alias_node = node.child_by_field_name("alias")
        if imported_node is not None:
            imported = _node_text(imported_node, source)
            local = _node_text(alias_node or imported_node, source)
            _record_javascript_import(module, imported, local, imports)
    elif node.type == "namespace_import":
        identifier = next(
            (child for child in node.named_children if child.type == "identifier"), None
        )
        if identifier is not None:
            _record_javascript_namespace(module, _node_text(identifier, source), imports)


def _javascript_import_module(node: Node | None, source: bytes) -> str | None:
    if node is None:
        return None
    fragment = next(
        (child for child in node.named_children if child.type == "string_fragment"), None
    )
    return _node_text(fragment, source) if fragment is not None else None


def _record_javascript_import(
    module: str,
    imported: str,
    local: str,
    imports: _JavascriptImportBuilder,
) -> None:
    if imported in TEST_IMPORTS[module]:
        imports.tests.add(local)
    if imported == "assert" and imported in ASSERTION_IMPORTS[module]:
        imports.direct_assertions.add(local)
    elif imported in ASSERTION_IMPORTS[module]:
        imports.matcher_assertions.add(local)


def _record_javascript_namespace(
    module: str,
    namespace: str,
    imports: _JavascriptImportBuilder,
) -> None:
    imports.tests.update(f"{namespace}.{member}" for member in TEST_IMPORTS[module])
    imports.matcher_assertions.update(
        f"{namespace}.{member}" for member in ASSERTION_IMPORTS[module] if member != "assert"
    )
    if "assert" in ASSERTION_IMPORTS[module]:
        imports.direct_assertions.add(f"{namespace}.assert")


def _classify_test_callee(callee: str, imports: _JavascriptImports) -> str:
    for base in sorted(imports.tests, key=len, reverse=True):
        classification = _classify_imported_test_callee(callee, base)
        if classification is not None:
            return classification

    if _looks_like_unimported_test(callee):
        return "unknown"
    return "ignore"


def _classify_imported_test_callee(callee: str, base: str) -> str | None:
    if callee == f"{base}.todo":
        return "unsupported"
    if callee == base:
        return "test"
    if not callee.startswith(f"{base}."):
        return None
    remainder = callee[len(base) + 1 :]
    if remainder.startswith("each(") and remainder.endswith(")"):
        return "test"
    if all(part in TEST_MODIFIERS for part in remainder.split(".")):
        return "test"
    return "ignore"


def _looks_like_unimported_test(callee: str) -> bool:
    for base in ("test", "it"):
        if callee in (base, f"{base}.todo"):
            return True
        if callee.startswith(f"{base}."):
            remainder = callee[len(base) + 1 :]
            return remainder.startswith("each(") or all(
                part in TEST_MODIFIERS for part in remainder.split(".")
            )
    return False


def _inline_test_callback(call: Node) -> Node | None:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return None
    callbacks = tuple(
        child
        for child in arguments.named_children
        if child.type in {"arrow_function", "function_expression"}
    )
    return callbacks[-1] if callbacks else None


def _check_javascript_test(
    path: PurePath,
    label: str,
    body: Node,
    source: bytes,
    imports: _JavascriptImports,
) -> list[Diagnostic]:
    context = _JavascriptTestContext(path, label, body, source, imports)
    direct, diagnostics = _direct_javascript_markers(path, label, body, source)
    ordered, marker_diagnostics = _ordered_javascript_markers(path, label, body, source, direct)
    diagnostics.extend(marker_diagnostics)
    if ordered is None:
        return diagnostics

    offsets = (
        ordered[0].start_byte,
        ordered[1].start_byte,
        ordered[2].start_byte,
    )
    diagnostics.extend(_javascript_phase_diagnostics(path, label, body, ordered, offsets))
    diagnostics.extend(_javascript_assertion_diagnostics(context, ordered[2]))
    return diagnostics


def _direct_javascript_markers(
    path: PurePath,
    label: str,
    body: Node,
    source: bytes,
) -> tuple[tuple[Node, ...], list[Diagnostic]]:
    contained = tuple(
        node
        for node in _walk_tree(body)
        if node.type == "comment" and _node_text(node, source) in JAVASCRIPT_PHASE_MARKERS
    )
    diagnostics: list[Diagnostic] = []
    direct: list[Node] = []
    for marker_node in contained:
        if marker_node.parent != body or not _node_text(marker_node, source).startswith("//"):
            diagnostics.append(
                _at_js_node(
                    path,
                    marker_node,
                    "AAA008",
                    f"{label}: phase markers must be standalone at the direct test-body level.",
                )
            )
            continue
        direct.append(marker_node)
    return tuple(direct), diagnostics


def _ordered_javascript_markers(
    path: PurePath,
    label: str,
    body: Node,
    source: bytes,
    direct: tuple[Node, ...],
) -> tuple[tuple[Node, ...] | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    by_marker = {
        marker_text: tuple(node for node in direct if _node_text(node, source) == marker_text)
        for marker_text in JAVASCRIPT_PHASE_MARKERS
    }
    for marker_text, matches in by_marker.items():
        if not matches:
            diagnostics.append(
                _at_js_node(
                    path,
                    body,
                    "AAA001",
                    f"{label}: missing '{marker_text}' phase.",
                )
            )
        elif len(matches) > 1:
            diagnostics.append(
                _at_js_node(
                    path,
                    matches[1],
                    "AAA002",
                    f"{label}: duplicate '{marker_text}' phase.",
                )
            )

    if not all(len(by_marker[marker_text]) == 1 for marker_text in JAVASCRIPT_PHASE_MARKERS):
        return None, diagnostics

    ordered = tuple(by_marker[marker_text][0] for marker_text in JAVASCRIPT_PHASE_MARKERS)
    offsets = tuple(marker.start_byte for marker in ordered)
    if offsets != tuple(sorted(offsets)):
        diagnostics.append(
            _at_js_node(
                path,
                ordered[0],
                "AAA003",
                f"{label}: phases must appear in Arrange, Act, Assert order.",
            )
        )
        return None, diagnostics
    return ordered, diagnostics


def _javascript_phase_diagnostics(
    path: PurePath,
    label: str,
    body: Node,
    ordered: tuple[Node, ...],
    offsets: tuple[int, int, int],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    statements = tuple(
        child for child in body.named_children if child.type not in {"comment", "empty_statement"}
    )
    before_arrange = tuple(
        statement for statement in statements if statement.start_byte < offsets[0]
    )
    if before_arrange:
        diagnostics.append(
            _at_js_node(
                path,
                before_arrange[0],
                "AAA005",
                f"{label}: executable code appears before '// Arrange'.",
            )
        )

    phase_ranges = (
        ("Arrange", offsets[0], offsets[1], ordered[0]),
        ("Act", offsets[1], offsets[2], ordered[1]),
        ("Assert", offsets[2], body.end_byte, ordered[2]),
    )
    for phase, start, end, marker in phase_ranges:
        if any(start < statement.start_byte < end for statement in statements):
            continue
        diagnostics.append(
            _at_js_node(
                path,
                marker,
                "AAA004",
                f"{label}: {phase} phase has no executable statement.",
            )
        )
    return diagnostics


def _javascript_assertion_diagnostics(
    context: _JavascriptTestContext,
    assert_marker: Node,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    assert_offset = assert_marker.start_byte
    assertions = tuple(_javascript_assertions(context.body, context.source, context.imports))
    for assertion in assertions:
        if assertion.start_byte >= assert_offset:
            continue
        diagnostics.append(
            _at_js_node(
                context.path,
                assertion,
                "AAA006",
                f"{context.label}: assertions are permitted only in the Assert phase.",
            )
        )
    if not any(assertion.start_byte > assert_offset for assertion in assertions):
        diagnostics.append(
            _at_js_node(
                context.path,
                assert_marker,
                "AAA007",
                f"{context.label}: Assert phase contains no recognized outcome assertion.",
            )
        )
    return diagnostics


def _javascript_assertions(
    body: Node,
    source: bytes,
    imports: _JavascriptImports,
) -> Iterable[Node]:
    for node in _walk_tree(body):
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        if function is None:
            continue
        callee = _compact(_node_text(function, source))
        if _is_javascript_assertion(callee, imports):
            yield node


def _is_javascript_assertion(callee: str, imports: _JavascriptImports) -> bool:
    for base in sorted(imports.direct_assertions, key=len, reverse=True):
        if callee == base or callee.startswith(f"{base}."):
            return True

    for base in sorted(imports.matcher_assertions, key=len, reverse=True):
        starts_matcher = callee.startswith((f"{base}(", f"{base}."))
        if starts_matcher and ")." in callee:
            return True

    first = re.match(r"[A-Za-z_$][\w$]*", callee)
    return bool(first and first.group(0).startswith("assert"))


def _check_stray_javascript_markers(
    path: PurePath,
    root: Node,
    source: bytes,
    test_bodies: list[Node],
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for node in _walk_tree(root):
        if node.type != "comment" or _node_text(node, source) not in JAVASCRIPT_PHASE_MARKERS:
            continue
        if any(body.start_byte < node.start_byte < body.end_byte for body in test_bodies):
            continue
        diagnostics.append(
            _at_js_node(
                path,
                node,
                "AAA010",
                "AAA phase marker appears outside a recognized executable test.",
            )
        )
    return diagnostics


def _walk_tree(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from _walk_tree(child)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _is_canonical_test_file(path: PurePath) -> bool:
    stem = path.stem
    if path.suffix == ".py":
        return stem.startswith("test_") or stem.endswith("_test")
    return stem.endswith((".test", ".spec"))


def _missing_executable_test_diagnostic(path: PurePath) -> Diagnostic:
    return Diagnostic(
        path=path,
        line=1,
        column=1,
        code="AAA013",
        message="canonical test file contains no recognized executable test case.",
    )


def _at_js_node(path: PurePath, node: Node, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        path=path,
        line=node.start_point.row + 1,
        column=node.start_point.column + 1,
        code=code,
        message=message,
    )


def _at_node(path: PurePath, node: ast.AST, code: str, message: str) -> Diagnostic:
    return Diagnostic(
        path=path,
        line=getattr(node, "lineno", 1),
        column=getattr(node, "col_offset", 0) + 1,
        code=code,
        message=message,
    )


def _at_token(
    path: PurePath,
    token: tokenize.TokenInfo,
    code: str,
    message: str,
) -> Diagnostic:
    return _at_line(path, token.start[0], token.start[1] + 1, code, message)


def _at_line(
    path: PurePath,
    line: int,
    column: int,
    code: str,
    message: str,
) -> Diagnostic:
    return Diagnostic(path=path, line=line, column=column, code=code, message=message)


def _sorted_diagnostics(diagnostics: Iterable[Diagnostic]) -> tuple[Diagnostic, ...]:
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (item.line, item.column, item.code, item.message),
        )
    )
