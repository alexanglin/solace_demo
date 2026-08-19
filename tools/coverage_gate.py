"""Per-member coverage gate.

Coverage is enforced per workspace member rather than as one global total: a single
``--cov-fail-under`` would let a well-tested domain package mask an untested adapter,
which is the outcome the gates exist to prevent (``docs/adr/0010``, ``docs/adr/0015``).

Each member declares its risk tier in its own ``pyproject.toml``. A member with no
declared tier fails rather than defaulting to the weakest one (``docs/adr/0017``).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

TIER_THRESHOLDS: dict[int, int | None] = {1: 100, 2: 95, 3: None}
"""Tier 3 carries a smoke-plus-one-failure-path obligation a percentage cannot express."""


@dataclass(frozen=True)
class CoverageMeasurement:
    """Independent statement and branch totals for one workspace member."""

    statements: int
    covered_statements: int
    branches: int
    covered_branches: int

    @staticmethod
    def _percent(covered: int, total: int) -> float:
        """Return 100 for a dimension with no measurable opportunities."""
        return 100.0 if total == 0 else covered * 100.0 / total

    @property
    def statement_percent(self) -> float:
        """Return statement coverage as a display percentage."""
        return self._percent(self.covered_statements, self.statements)

    @property
    def branch_percent(self) -> float:
        """Return branch coverage as a display percentage."""
        return self._percent(self.covered_branches, self.branches)


@dataclass(frozen=True)
class MemberVerdict:
    """The independently measured outcome for one workspace member."""

    name: str
    tier: int | None
    threshold: int | None
    measurement: CoverageMeasurement
    outcome: str
    detail: str


class WorkspaceConfigurationError(ValueError):
    """The root uv workspace member inventory is malformed."""


def read_tier(pyproject: Path) -> int | None:
    """Return the declared risk tier, or None when the member declares none."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    tier = data.get("tool", {}).get("aerial-rescue", {}).get("risk-tier")
    return tier if type(tier) is int else None


def _meets_threshold(covered: int, total: int, threshold: int) -> bool:
    """Compare coverage with integer arithmetic to avoid rounding at the boundary."""
    return total == 0 or covered * 100 >= threshold * total


def judge(
    name: str,
    tier: int | None,
    measurement: CoverageMeasurement,
) -> MemberVerdict:
    """Decide one member's outcome from its declared tier and measured coverage."""
    limit = TIER_THRESHOLDS.get(tier) if tier is not None else None
    threshold: int | None = None
    if tier is None or tier not in TIER_THRESHOLDS:
        outcome = "FAIL"
        detail = "declares no [tool.aerial-rescue] risk-tier; docs/adr/0017 has no default"
    elif measurement.statements == 0:
        threshold = limit
        outcome = "FAIL"
        detail = "no measurable source; an active member cannot pass vacuously"
    elif limit is None:
        outcome = "FAIL"
        detail = (
            f"statement {measurement.statement_percent:.2f}%, "
            f"branch {measurement.branch_percent:.2f}%; "
            "the required tier-3 smoke/failure-path gate is not implemented"
        )
    else:
        threshold = limit
        meets = _meets_threshold(
            measurement.covered_statements,
            measurement.statements,
            limit,
        ) and _meets_threshold(
            measurement.covered_branches,
            measurement.branches,
            limit,
        )
        outcome = "PASS" if meets else "FAIL"
        detail = (
            f"statement {measurement.statement_percent:.2f}%, "
            f"branch {measurement.branch_percent:.2f}%; required {limit}% each"
        )
    return MemberVerdict(name, tier, threshold, measurement, outcome, detail)


def _coverage_json() -> dict[str, object]:
    """Return the coverage report as JSON, or an empty report when no data exists."""
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {"files": {}}
    try:
        parsed: dict[str, object] = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"files": {}}
    return parsed


def measure(report: dict[str, object], member: str) -> CoverageMeasurement:
    """Aggregate independent statement and branch totals under one member."""
    files = report.get("files", {})
    if not isinstance(files, dict):
        return CoverageMeasurement(0, 0, 0, 0)
    prefixes = ("tools/",) if member == "." else (f"{member}/src/",)
    statements = 0
    covered_statements = 0
    branches = 0
    covered_branches = 0
    for path, entry in files.items():
        if (
            not isinstance(path, str)
            or not path.startswith(prefixes)
            or not isinstance(entry, dict)
        ):
            continue
        summary = entry.get("summary", {})
        if not isinstance(summary, dict):
            continue
        statements += int(summary.get("num_statements", 0))
        covered_statements += int(summary.get("covered_lines", 0))
        branches += int(summary.get("num_branches", 0))
        covered_branches += int(summary.get("covered_branches", 0))
    return CoverageMeasurement(
        statements=statements,
        covered_statements=covered_statements,
        branches=branches,
        covered_branches=covered_branches,
    )


def _workspace_members(root: Path, pyproject: Path) -> tuple[Path, ...]:
    """Expand the workspace's declared member globs into unique directories."""
    raw = cast(object, tomllib.loads(pyproject.read_text(encoding="utf-8")))
    data = cast(dict[object, object], raw)
    tool = data.get("tool")
    uv = tool.get("uv") if isinstance(tool, dict) else None
    workspace = uv.get("workspace") if isinstance(uv, dict) else None
    if workspace is None:
        return ()
    members = workspace.get("members") if isinstance(workspace, dict) else None
    if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
        message = "[tool.uv.workspace].members must be a list of glob strings"
        raise WorkspaceConfigurationError(message)
    paths = {
        path for pattern in cast(list[str], members) for path in root.glob(pattern) if path.is_dir()
    }
    return tuple(sorted(paths))


def _missing_manifest_verdict(root: Path, member: Path, report: dict[str, object]) -> MemberVerdict:
    """Return a blocking verdict for a declared member without a manifest."""
    name = member.relative_to(root).as_posix()
    return MemberVerdict(
        name=name,
        tier=None,
        threshold=None,
        measurement=measure(report, name),
        outcome="FAIL",
        detail=f"missing {name}/pyproject.toml for declared workspace member",
    )


def collect(root: Path) -> list[MemberVerdict]:
    """Judge every workspace member under ``root``."""
    report = _coverage_json()
    verdicts = []
    root_pyproject = root / "pyproject.toml"
    if root_pyproject.is_file():
        root_measurement = measure(report, ".")
        verdicts.append(judge(".", read_tier(root_pyproject), root_measurement))
    for member_path in _workspace_members(root, root_pyproject):
        pyproject = member_path / "pyproject.toml"
        if not pyproject.is_file():
            verdicts.append(_missing_manifest_verdict(root, member_path, report))
            continue
        member = member_path.relative_to(root).as_posix()
        verdicts.append(judge(member, read_tier(pyproject), measure(report, member)))
    return verdicts


def main() -> int:
    """Print one line per member and fail if any member fails its tier threshold."""
    verdicts = collect(Path.cwd())
    for verdict in verdicts:
        stream = sys.stderr if verdict.outcome == "FAIL" else sys.stdout
        print(
            f"{verdict.outcome:6} {verdict.name:28} tier="
            f"{verdict.tier if verdict.tier is not None else '?'} {verdict.detail}",
            file=stream,
        )
    return 1 if any(v.outcome == "FAIL" for v in verdicts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
