from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from tools import typescript_coverage_gate, typescript_policy_gate
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

TIER_ONE_SOURCE_PATHS = (
    "src/api/mutation-client.ts",
    "src/contracts/bootstrap.ts",
    "src/contracts/schema-registry.ts",
    "src/domain/canonical.ts",
    "src/domain/reducer.ts",
)
METRICS = ("statements", "branches", "functions", "lines")
MetricCounts = tuple[int, int, int, int]


def _dashboard(test_case: unittest.TestCase) -> tuple[Path, dict[str, Path]]:
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    dashboard_root = Path(directory.name) / "apps" / "dashboard"
    sources: dict[str, Path] = {}
    for relative in (*TIER_ONE_SOURCE_PATHS, "src/components/view.tsx"):
        path = dashboard_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export const value = true;\n", encoding="utf-8")
        sources[relative] = path
    return dashboard_root, sources


def _summary(overrides: Mapping[str, MetricCounts] | None = None) -> dict[str, object]:
    counts: dict[str, MetricCounts] = {metric: (100, 100, 0, 100) for metric in METRICS}
    if overrides is not None:
        counts.update(overrides)
    return {
        metric: {
            "total": values[0],
            "covered": values[1],
            "skipped": values[2],
            "pct": values[3],
        }
        for metric, values in counts.items()
    }


def _metric_count(entry: Mapping[str, object], metric: str, member: str) -> int:
    measurement = cast("Mapping[str, object]", entry[metric])
    return cast("int", measurement[member])


def _total(entries: Mapping[Path, Mapping[str, object]]) -> dict[str, object]:
    totals: dict[str, object] = {}
    for metric in METRICS:
        total = sum(_metric_count(entry, metric, "total") for entry in entries.values())
        covered = sum(_metric_count(entry, metric, "covered") for entry in entries.values())
        totals[metric] = {
            "total": total,
            "covered": covered,
            "skipped": 0,
            "pct": (covered * 10_000 // total) / 100 if total else 100,
        }
    return totals


def _write_report(
    dashboard_root: Path,
    entries: Mapping[Path, Mapping[str, object]],
    name: str = "coverage-summary.json",
) -> Path:
    report = dashboard_root / "coverage" / name
    report.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {"total": _total(entries)}
    document.update({str(path.resolve()): dict(summary) for path, summary in entries.items()})
    report.write_text(json.dumps(document), encoding="utf-8")
    return report


class TypeScriptTierOneCoverageTests(QualityGateTestCase):
    def test_the_tier_one_inventory_names_only_the_five_trust_boundary_modules(self) -> None:
        # Arrange
        expected = TIER_ONE_SOURCE_PATHS

        # Act
        actual = getattr(typescript_coverage_gate, "TIER_ONE_SOURCE_PATHS", ())

        # Assert
        self.assertEqual(expected, actual)

    def test_each_tier_one_file_requires_complete_statements_and_branches(self) -> None:
        # Arrange
        dashboard_root, sources = _dashboard(self)
        cases = {
            (relative, metric): {
                path: _summary({metric: (100, 99, 0, 99)} if path == sources[relative] else None)
                for path in sources.values()
            }
            for relative in TIER_ONE_SOURCE_PATHS
            for metric in ("statements", "branches")
        }
        reports = {
            case: _write_report(
                dashboard_root,
                entries,
                f"{case[0].replace('/', '-')}-{case[1]}.json",
            )
            for case, entries in cases.items()
        }

        # Act
        findings = {
            case: typescript_coverage_gate.evaluate_coverage(
                report,
                dashboard_root,
                list(sources.values()),
                enforce_tier_one=True,
            )
            for case, report in reports.items()
        }

        # Assert
        for (relative, metric), reported in findings.items():
            with self.subTest(relative=relative, metric=metric):
                self.assertTrue(
                    any(
                        relative in finding and metric in finding and "below 100 percent" in finding
                        for finding in reported
                    ),
                    reported,
                )

    def test_a_missing_tier_one_module_fails_even_when_the_remaining_inventory_is_complete(
        self,
    ) -> None:
        # Arrange
        dashboard_root, sources = _dashboard(self)
        missing_relative = TIER_ONE_SOURCE_PATHS[0]
        missing = sources.pop(missing_relative)
        missing.unlink()
        entries = {path: _summary() for path in sources.values()}
        report = _write_report(dashboard_root, entries)

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report,
            dashboard_root,
            list(sources.values()),
            enforce_tier_one=True,
        )

        # Assert
        self.assertTrue(
            any(
                missing_relative in finding and "Tier 1 source is missing" in finding
                for finding in findings
            ),
            findings,
        )

    def test_a_tier_one_file_missing_from_the_report_fails_with_its_identity(self) -> None:
        # Arrange
        dashboard_root, sources = _dashboard(self)
        missing_relative = TIER_ONE_SOURCE_PATHS[1]
        entries = {
            path: _summary() for relative, path in sources.items() if relative != missing_relative
        }
        report = _write_report(dashboard_root, entries)

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report,
            dashboard_root,
            list(sources.values()),
            enforce_tier_one=True,
        )

        # Assert
        self.assertTrue(
            any(
                missing_relative in finding
                and "Tier 1 source is missing from the coverage report" in finding
                for finding in findings
            ),
            findings,
        )

    def test_a_tier_one_file_with_no_statements_fails_nonvacuously(self) -> None:
        # Arrange
        dashboard_root, sources = _dashboard(self)
        empty_relative = TIER_ONE_SOURCE_PATHS[2]
        entries = {
            path: _summary(
                {"statements": (0, 0, 0, 100)} if path == sources[empty_relative] else None
            )
            for path in sources.values()
        }
        report = _write_report(dashboard_root, entries)

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report,
            dashboard_root,
            list(sources.values()),
            enforce_tier_one=True,
        )

        # Assert
        self.assertTrue(
            any(
                empty_relative in finding
                and "Tier 1 statements have no measurable coverage" in finding
                for finding in findings
            ),
            findings,
        )

    def test_tier_two_source_may_rely_on_the_global_ninety_five_percent_gate(self) -> None:
        # Arrange
        dashboard_root, sources = _dashboard(self)
        tier_two = sources["src/components/view.tsx"]
        entries = {
            path: _summary(
                {
                    "statements": (100, 95, 0, 95),
                    "branches": (100, 95, 0, 95),
                    "functions": (100, 95, 0, 95),
                    "lines": (100, 95, 0, 95),
                }
                if path == tier_two
                else None
            )
            for path in sources.values()
        }
        report = _write_report(dashboard_root, entries)

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report,
            dashboard_root,
            list(sources.values()),
            enforce_tier_one=True,
        )

        # Assert
        self.assertEqual([], findings)

    def test_command_line_switch_enables_the_fixed_tier_one_policy(self) -> None:
        # Arrange
        dashboard_root, sources = _dashboard(self)
        tier_one = sources[TIER_ONE_SOURCE_PATHS[0]]
        entries = {
            path: _summary({"branches": (100, 99, 0, 99)} if path == tier_one else None)
            for path in sources.values()
        }
        report = _write_report(dashboard_root, entries)
        arguments = [
            "--report",
            str(report),
            "--dashboard-root",
            str(dashboard_root),
            "--enforce-dashboard-tier-one",
        ]
        for source in sources.values():
            arguments.extend(("--source", str(source)))
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            status = typescript_coverage_gate.main(arguments)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("Tier 1 branches", stderr.getvalue())

    def test_repository_configuration_keeps_one_global_pass_and_the_exact_tier_one_gate(
        self,
    ) -> None:
        # Arrange
        manifest = json.loads(self.read_repository_text("apps/dashboard/package.json"))
        wrapper = self.read_repository_text("scripts/hooks/dashboard/dashboard-test-full.sh")
        parameters = self.read_repository_text("docs/operating-parameters.md")
        decision = self.read_repository_text(
            "docs/adr/0130-enforce-dashboard-tier-one-coverage-per-file.md"
        )
        scripts = manifest["scripts"]
        coverage_command = scripts["test:coverage"]
        errors: list[str] = []

        # Act
        policy_findings = typescript_policy_gate.evaluate_manifest(
            REPOSITORY_ROOT / "apps/dashboard/package.json", errors
        )
        tier_one = getattr(typescript_coverage_gate, "TIER_ONE_SOURCE_PATHS", ())

        # Assert
        self.assertEqual([], policy_findings + errors)
        self.assertEqual(1, coverage_command.count("vitest run --coverage"))
        self.assertEqual(TIER_ONE_SOURCE_PATHS, tier_one)
        self.assertIn("--enforce-dashboard-tier-one", wrapper)
        self.assertIn("| TypeScript Tier 1 statement coverage", parameters)
        self.assertIn("| TypeScript Tier 1 branch coverage", parameters)
        self.assertGreaterEqual(parameters.count("100% independently"), 2)
        for relative in TIER_ONE_SOURCE_PATHS:
            with self.subTest(relative=relative):
                self.assertIn(f"`{relative}`", decision)


if __name__ == "__main__":
    unittest.main()
