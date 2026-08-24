"""Fail-closed dashboard coverage adjudication fixed by ADR-0103.

Vitest produces evidence; it does not own the verdict.  This module validates the
coverage summary and the enumerated hand-written source inventory without invoking a
process, then applies ADR-0019's integer coverage comparison independently to every
TypeScript dimension.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, cast

from tools.typescript_policy_gate import COVERAGE_DIMENSIONS, COVERAGE_THRESHOLD_PERCENT

DIAGNOSTIC_PREFIX: Final = "TYPESCRIPT COVERAGE: "
MAX_PERCENT: Final = 100
SUMMARY_FIELDS: Final = frozenset({"total", "covered", "skipped", "pct"})
ZERO_OPPORTUNITY_DIMENSIONS: Final = frozenset({"branches", "functions"})
TEST_MARKERS: Final = (".test.", ".spec.")
GENERATED_CONTRACT_PREFIX: Final = ("src", "contracts", "generated")
DECLARATION_SUFFIXES: Final = (".d.ts", ".d.mts", ".d.cts")
JAVASCRIPT_SUFFIXES: Final = frozenset({".js", ".jsx", ".mjs", ".cjs"})
TYPESCRIPT_SUFFIXES: Final = frozenset({".ts", ".tsx"})
V8_TOTAL_METADATA_FIELD: Final = "branchesTrue"
IGNORE_DIRECTIVE: Final = re.compile(
    r"\b(?:c8|v8|istanbul)\s+ignore\b|"
    r"\bnode:coverage\s+(?:ignore|disable|enable)\b",
    re.IGNORECASE,
)


class _DuplicateJsonKeyError(ValueError):
    """Signal a repeated key at any depth in an untrusted JSON document."""


class _NonstandardJsonNumberError(ValueError):
    """Signal a non-standard numeric constant accepted by Python's decoder."""


@dataclass(frozen=True, slots=True)
class _Metric:
    total: int
    covered: int
    skipped: int


_Summary = dict[str, _Metric]


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateJsonKeyError(key)
        document[key] = value
    return document


def _reject_nonstandard_number(value: str) -> NoReturn:
    del value
    raise _NonstandardJsonNumberError


def _absolute(path: Path) -> Path:
    return path.resolve(strict=False)


def _relative(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _display(path: Path, root: Path) -> str:
    relative = _relative(path, root)
    return relative.as_posix() if relative is not None else str(path)


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    return (
        "tests" in parts
        or any(marker in relative.name for marker in TEST_MARKERS)
        or relative.name.endswith(DECLARATION_SUFFIXES)
        or parts[: len(GENERATED_CONTRACT_PREFIX)] == GENERATED_CONTRACT_PREFIX
    )


def _read_source(path: Path, root: Path, issues: set[str]) -> str | None:
    label = _display(path, root)
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        issues.add(f"{label}: production source is not readable as UTF-8")
        return None


def _source_candidate(
    source: Path, dashboard_root: Path, issues: set[str]
) -> tuple[Path, Path] | None:
    declared = source.absolute()
    if declared.is_symlink():
        issues.add(f"{source}: production source may not be a symbolic link")
        return None
    path = _absolute(source)
    relative = _relative(path, dashboard_root)
    if relative is None:
        issues.add(f"{path}: source is outside the dashboard root")
        return None
    if not path.is_file():
        issues.add(f"{relative.as_posix()}: production source must be a regular file")
        return None
    return path, relative


def _production_source(
    path: Path, relative: Path, dashboard_root: Path, issues: set[str]
) -> Path | None:
    if _is_excluded(relative):
        return None
    label = relative.as_posix()
    if not relative.parts or relative.parts[0] != "src":
        issues.add(f"{label}: source is outside the dashboard src inventory")
        return None
    if path.suffix in JAVASCRIPT_SUFFIXES:
        issues.add(f"{label}: JavaScript/JSX is not an accepted TypeScript production source")
        return None
    if path.suffix not in TYPESCRIPT_SUFFIXES:
        issues.add(f"{label}: file is not an accepted TypeScript production source")
        return None
    content = _read_source(path, dashboard_root, issues)
    if content is not None and IGNORE_DIRECTIVE.search(content) is not None:
        issues.add(f"{label}: contains a coverage-ignore directive")
    return path


def _production_inventory(
    dashboard_root: Path,
    sources: Sequence[Path],
    issues: set[str],
) -> set[Path]:
    candidates = (
        candidate
        for source in sources
        if (candidate := _source_candidate(source, dashboard_root, issues)) is not None
    )
    production = {
        production_source
        for path, relative in candidates
        if (production_source := _production_source(path, relative, dashboard_root, issues))
        is not None
    }
    if not production:
        issues.add("dashboard has no hand-written production source inventory")
    return production


def _report_candidate(
    report_path: Path,
    dashboard_root: Path,
    issues: set[str],
) -> tuple[Path, str] | None:
    declared = report_path.absolute()
    if declared.is_symlink():
        issues.add(f"coverage report {report_path} may not be a symbolic link")
        return None
    path = _absolute(report_path)
    relative = _relative(path, dashboard_root)
    if relative is None:
        issues.add(f"coverage report {path} is outside the dashboard root")
        return None
    label = relative.as_posix()
    if not path.exists():
        issues.add(f"coverage report {label} does not exist")
        return None
    if not path.is_file():
        issues.add(f"coverage report {label} must be a regular file")
        return None
    return path, label


def _decode_report(path: Path, label: str, issues: set[str]) -> Mapping[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
        parsed = cast(
            "object",
            json.loads(
                text,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_nonstandard_number,
            ),
        )
    except _DuplicateJsonKeyError as cause:
        issues.add(f"coverage report contains duplicate JSON key {cause.args[0]!r}")
        return None
    except OSError, UnicodeError, json.JSONDecodeError, ValueError:
        issues.add(f"coverage report {label} is invalid JSON")
        return None
    if not isinstance(parsed, dict):
        issues.add(f"coverage report {label} must contain a JSON object")
        return None
    return cast("Mapping[str, object]", parsed)


def _load_report(
    report_path: Path,
    dashboard_root: Path,
    issues: set[str],
) -> Mapping[str, object] | None:
    candidate = _report_candidate(report_path, dashboard_root, issues)
    if candidate is None:
        return None
    return _decode_report(*candidate, issues)


def _source_inventory_candidate(
    inventory_path: Path,
    dashboard_root: Path,
    issues: set[str],
) -> Path | None:
    declared = inventory_path.absolute()
    if declared.is_symlink():
        issues.add("source inventory may not be a symbolic link")
        return None
    path = _absolute(inventory_path)
    if _relative(path, dashboard_root) is None:
        issues.add("source inventory is outside the dashboard root")
        return None
    if not path.is_file():
        issues.add("source inventory must be a regular file")
        return None
    return path


def _decode_source_inventory(content: bytes, issues: set[str]) -> list[Path]:
    if not content:
        return []
    if not content.endswith(b"\0"):
        issues.add("source inventory must be NUL-terminated")
        return []
    encoded_paths = content[:-1].split(b"\0")
    if any(not encoded_path for encoded_path in encoded_paths):
        issues.add("source inventory contains an empty path")
        return []
    try:
        return [Path(encoded_path.decode("utf-8")) for encoded_path in encoded_paths]
    except UnicodeDecodeError:
        issues.add("source inventory paths must be valid UTF-8")
        return []


def _load_source_inventory(
    inventory_path: Path,
    dashboard_root: Path,
    issues: set[str],
) -> list[Path]:
    path = _source_inventory_candidate(inventory_path, dashboard_root, issues)
    if path is None:
        return []
    try:
        content = path.read_bytes()
    except OSError:
        issues.add("source inventory is not readable")
        return []
    return _decode_source_inventory(content, issues)


def _integer_count(value: object, label: str, issues: set[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        issues.add(f"{label} must be an integer")
        return None
    if value < 0:
        issues.add(f"{label} must be non-negative")
        return None
    return value


def _reported_percentage(value: object, label: str, issues: set[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        issues.add(f"{label} must be a finite numeric percentage")
        return None
    percentage = float(value)
    if not math.isfinite(percentage) or not 0 <= percentage <= MAX_PERCENT:
        issues.add(f"{label} must be a finite numeric percentage between 0 and 100")
        return None
    return percentage


def _expected_percentage(total: int, covered: int) -> float:
    if total == 0:
        return 100.0
    return (covered * 10_000 // total) / 100


def _metric_mapping(value: object, label: str, issues: set[str]) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        issues.add(f"{label} must contain a JSON object")
        return None
    metric = cast("Mapping[str, object]", value)
    keys = set(metric)
    for missing in sorted(SUMMARY_FIELDS - keys):
        issues.add(f"{label}.{missing} is required")
    for unknown in sorted(keys - SUMMARY_FIELDS):
        issues.add(f"{label}.{unknown} is unknown")
    return metric if keys == SUMMARY_FIELDS else None


def _metric_counts(
    metric: Mapping[str, object], label: str, issues: set[str]
) -> tuple[int, int, int] | None:
    total = _integer_count(metric["total"], f"{label}.total", issues)
    covered = _integer_count(metric["covered"], f"{label}.covered", issues)
    skipped = _integer_count(metric["skipped"], f"{label}.skipped", issues)
    if total is None or covered is None or skipped is None:
        return None
    if covered > total:
        issues.add(f"{label}.covered exceeds total")
    if skipped != 0:
        issues.add(f"{label}.skipped counts must be zero")
    if covered > total or skipped != 0:
        return None
    return total, covered, skipped


def _parse_metric(value: object, label: str, issues: set[str]) -> _Metric | None:
    metric = _metric_mapping(value, label, issues)
    if metric is None:
        return None
    counts = _metric_counts(metric, label, issues)
    percentage = _reported_percentage(metric["pct"], f"{label}.pct", issues)
    if counts is None or percentage is None:
        return None
    total, covered, skipped = counts
    expected_percentage = _expected_percentage(total, covered)
    if percentage != expected_percentage:
        issues.add(f"{label}.pct {percentage:g} disagrees with recomputed {expected_percentage:g}")
    return _Metric(total=total, covered=covered, skipped=skipped)


def _validate_v8_total_metadata(value: object, label: str, issues: set[str]) -> None:
    metadata = _metric_mapping(value, label, issues)
    if metadata is None:
        return
    counts = [
        _integer_count(metadata[field], f"{label}.{field}", issues)
        for field in ("total", "covered", "skipped")
    ]
    if all(count is not None for count in counts) and any(count != 0 for count in counts):
        issues.add(f"{label} counts must all be zero")
    if metadata["pct"] != "Unknown":
        issues.add(f'{label}.pct must be "Unknown"')


def _parse_summary(
    value: object,
    label: str,
    issues: set[str],
    *,
    allow_v8_total_metadata: bool = False,
) -> _Summary | None:
    if not isinstance(value, dict):
        issues.add(f"{label} must contain a JSON object")
        return None
    summary = cast("Mapping[str, object]", value)
    expected = set(COVERAGE_DIMENSIONS)
    allowed = expected | ({V8_TOTAL_METADATA_FIELD} if allow_v8_total_metadata else set())
    keys = set(summary)
    for missing in sorted(expected - keys):
        issues.add(f"{label}.{missing} is required")
    for unknown in sorted(keys - allowed):
        issues.add(f"{label}.{unknown} is unknown")
    if allow_v8_total_metadata and V8_TOTAL_METADATA_FIELD in summary:
        _validate_v8_total_metadata(
            summary[V8_TOTAL_METADATA_FIELD],
            f"{label}.{V8_TOTAL_METADATA_FIELD}",
            issues,
        )
    parsed = {
        metric: measurement
        for metric in COVERAGE_DIMENSIONS
        if metric in summary
        and (measurement := _parse_metric(summary[metric], f"{label}.{metric}", issues)) is not None
    }
    return parsed if expected <= keys <= allowed and len(parsed) == len(expected) else None


def _report_entry_path(key: str, dashboard_root: Path, issues: set[str]) -> Path | None:
    declared = Path(key)
    path = _absolute(declared) if declared.is_absolute() else _absolute(dashboard_root / declared)
    relative = _relative(path, dashboard_root)
    if relative is None:
        issues.add(f"coverage report file {path} is outside the dashboard root")
        return None
    return path


def _report_entries(
    document: Mapping[str, object],
    dashboard_root: Path,
    issues: set[str],
) -> tuple[_Summary | None, dict[Path, _Summary]]:
    if "total" not in document:
        issues.add("coverage report total summary is required")
        total_summary = None
    else:
        total_summary = _parse_summary(
            document["total"],
            "total",
            issues,
            allow_v8_total_metadata=True,
        )
    entries: dict[Path, _Summary] = {}
    for key in sorted(key for key in document if key != "total"):
        path = _report_entry_path(key, dashboard_root, issues)
        if path is None:
            continue
        if path in entries:
            issues.add(
                f"{_display(path, dashboard_root)}: duplicate normalized coverage report file"
            )
            continue
        summary = _parse_summary(document[key], _display(path, dashboard_root), issues)
        if summary is not None:
            entries[path] = summary
    return total_summary, entries


def _inventory_issues(
    production: set[Path],
    entries: Mapping[Path, _Summary],
    dashboard_root: Path,
    issues: set[str],
) -> None:
    for path in sorted(production - entries.keys()):
        issues.add(f"{_display(path, dashboard_root)}: missing from the coverage report")
    for path in sorted(entries.keys() - production):
        issues.add(f"{_display(path, dashboard_root)}: unexpected coverage report file")


def _recomputed_summary(entries: Mapping[Path, _Summary]) -> _Summary:
    return {
        metric: _Metric(
            total=sum(summary[metric].total for summary in entries.values()),
            covered=sum(summary[metric].covered for summary in entries.values()),
            skipped=sum(summary[metric].skipped for summary in entries.values()),
        )
        for metric in COVERAGE_DIMENSIONS
    }


def _total_issues(
    declared: _Summary | None,
    entries: Mapping[Path, _Summary],
    issues: set[str],
) -> None:
    if declared is None:
        return
    recomputed = _recomputed_summary(entries)
    for metric in COVERAGE_DIMENSIONS:
        if declared[metric] != recomputed[metric]:
            issues.add(f"total.{metric} does not match recomputed file counts")


def _coverage_issues(
    production: set[Path],
    entries: Mapping[Path, _Summary],
    issues: set[str],
) -> None:
    measured_entries = {path: entries[path] for path in production if path in entries}
    recomputed = _recomputed_summary(measured_entries)
    for metric in COVERAGE_DIMENSIONS:
        measurement = recomputed[metric]
        if measurement.total == 0:
            if metric not in ZERO_OPPORTUNITY_DIMENSIONS:
                issues.add(f"{metric}: no measurable production source coverage")
            continue
        if measurement.covered * 100 < measurement.total * COVERAGE_THRESHOLD_PERCENT:
            issues.add(
                f"{metric}: coverage {measurement.covered}/{measurement.total} is below "
                f"{COVERAGE_THRESHOLD_PERCENT} percent"
            )


def evaluate_coverage(
    report_path: Path,
    dashboard_root: Path,
    sources: Sequence[Path],
) -> list[str]:
    """Return deterministic findings for one dashboard coverage report and inventory."""
    issues: set[str] = set()
    root = _absolute(dashboard_root)
    production = _production_inventory(root, sources, issues)
    document = _load_report(report_path, root, issues)
    if document is None:
        return sorted(issues)
    declared_total, entries = _report_entries(document, root, issues)
    _inventory_issues(production, entries, root, issues)
    _total_issues(declared_total, entries, issues)
    _coverage_issues(production, entries, issues)
    return sorted(issues)


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="typescript-coverage-gate",
        description="Adjudicate dashboard V8 coverage according to ADR-0103.",
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--dashboard-root", required=True, type=Path)
    parser.add_argument("--source", action="append", default=[], type=Path)
    parser.add_argument("--source-inventory", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print findings to stderr and return a blocking status when evidence is incomplete."""
    arguments = _parse_arguments(argv)
    report = cast("Path", arguments.report)
    dashboard_root = cast("Path", arguments.dashboard_root)
    sources = cast("list[Path]", arguments.source)
    inventory = cast("Path | None", arguments.source_inventory)
    inventory_issues: set[str] = set()
    if inventory is not None:
        sources.extend(
            _load_source_inventory(inventory, _absolute(dashboard_root), inventory_issues)
        )
    findings = sorted(inventory_issues | set(evaluate_coverage(report, dashboard_root, sources)))
    for finding in findings:
        print(f"{DIAGNOSTIC_PREFIX}{finding}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
