"""Fail-closed ADR-0147 inventory and ADR-0186 process-authority boundary."""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SuiteEntry:
    """One reviewed live file and its exact module-level resource markers."""

    path: Path
    markers: frozenset[str]


AUTHORIZED_SUITE = (
    SuiteEntry(
        Path("tests/integration/test_durable_store_live.py"),
        frozenset({"integration", "docker"}),
    ),
    SuiteEntry(
        Path("tests/security/test_broker_authorization.py"),
        frozenset({"security", "docker", "broker"}),
    ),
    SuiteEntry(
        Path("tests/integration/test_fleet_simulator_live.py"),
        frozenset({"integration", "docker", "broker"}),
    ),
    SuiteEntry(
        Path("tests/integration/test_guaranteed_delivery_live.py"),
        frozenset({"integration", "docker", "broker"}),
    ),
    SuiteEntry(
        Path("tests/integration/test_command_dispatch_live.py"),
        frozenset({"integration", "docker", "broker"}),
    ),
    SuiteEntry(
        Path("tests/integration/test_backlog_recovery_live.py"),
        frozenset({"performance", "integration", "docker", "broker"}),
    ),
    SuiteEntry(
        Path("tests/integration/test_application_data_plane_live.py"),
        frozenset({"integration", "docker", "broker"}),
    ),
)
"""The ordered verification-policy table accepted by ADR-0147."""

_FORBIDDEN_MARKERS = frozenset({"ollama", "paid", "net"})
_APPLICATION_DATA_PLANE = Path("tests/integration/test_application_data_plane_live.py")
_PROCESS_AUTHORITY_MODULES = frozenset({"docker", "subprocess", "testcontainers"})


def _pytestmark_value(module: ast.Module) -> ast.expr | None:
    values: list[ast.expr] = []
    for statement in module.body:
        match statement:
            case ast.Assign(targets=targets, value=value) if any(
                isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
            ):
                values.append(value)
            case ast.AnnAssign(target=ast.Name(id="pytestmark"), value=value) if value is not None:
                values.append(value)
    return values[0] if len(values) == 1 else None


def _marker_names(value: ast.expr) -> frozenset[str]:
    return frozenset(
        node.attr
        for node in ast.walk(value)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _markers(path: Path) -> frozenset[str] | None:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError, SyntaxError, UnicodeError:
        return None
    value = _pytestmark_value(module)
    return None if value is None else _marker_names(value)


def _process_authority_findings(path: Path, label: str) -> tuple[str, ...]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError, SyntaxError, UnicodeError:
        return ()
    imported_roots = {
        name.name.partition(".")[0]
        for node in ast.walk(module)
        if isinstance(node, ast.Import)
        for name in node.names
    }
    imported_roots.update(
        node.module.partition(".")[0]
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    if imported_roots & _PROCESS_AUTHORITY_MODULES:
        return (f"{label}: application live proof cannot own Docker process authority",)
    forbidden_calls = {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    if forbidden_calls & {
        ("asyncio", "create_subprocess_exec"),
        ("asyncio", "create_subprocess_shell"),
        ("os", "popen"),
        ("os", "system"),
    }:
        return (f"{label}: application live proof cannot own Docker process authority",)
    return ()


def _entry_findings(root: Path, entry: SuiteEntry) -> tuple[str, ...]:
    path = root / entry.path
    label = entry.path.as_posix()
    if path.is_symlink() or not path.is_file():
        return (f"{label}: authorized live file is missing or is not a regular file",)
    markers = _markers(path)
    if markers is None:
        return (f"{label}: module-level pytestmark inventory is missing or invalid",)
    findings: list[str] = []
    forbidden = markers & _FORBIDDEN_MARKERS
    if forbidden:
        findings.append(f"{label}: forbidden live marker inventory: {', '.join(sorted(forbidden))}")
    if markers != entry.markers:
        expected = ", ".join(sorted(entry.markers))
        observed = ", ".join(sorted(markers)) or "<empty>"
        findings.append(
            f"{label}: marker inventory differs; expected [{expected}], observed [{observed}]"
        )
    if entry.path == _APPLICATION_DATA_PLANE:
        findings.extend(_process_authority_findings(path, label))
    return tuple(findings)


def _inventory_findings(root: Path) -> tuple[str, ...]:
    integration = root / "tests" / "integration"
    observed = {
        path.relative_to(root).as_posix()
        for path in integration.glob("test_*_live.py")
        if path.is_file() or path.is_symlink()
    }
    expected = {
        entry.path.as_posix()
        for entry in AUTHORIZED_SUITE
        if entry.path.parent == Path("tests/integration")
    }
    return tuple(
        f"{path}: additional live file is outside ADR-0147's ordered allowlist"
        for path in sorted(observed - expected)
    )


def _allowlist_findings() -> tuple[str, ...]:
    paths = tuple(entry.path.as_posix() for entry in AUTHORIZED_SUITE)
    if len(paths) == len(set(paths)):
        return ()
    return ("ADR-0147 authorized live file inventory contains a duplicate path",)


def validate_repository(root: Path) -> tuple[str, ...]:
    """Return stable redacted findings for the fixed live-suite repository state."""
    findings = [*_allowlist_findings(), *_inventory_findings(root)]
    for entry in AUTHORIZED_SUITE:
        findings.extend(_entry_findings(root, entry))
    return tuple(sorted(set(findings)))


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the current checkout without accepting selection arguments."""
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments:
        sys.stderr.write("usage: python -m tools.live_integration_policy\n")
        return 2
    findings = validate_repository(Path.cwd())
    for finding in findings:
        sys.stderr.write(f"FAILED: {finding}\n")
    if findings:
        return 1
    sys.stdout.write("ADR-0147 live integration inventory is complete and closed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
