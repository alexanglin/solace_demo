from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from tools import typescript_coverage_gate
from tools.quality_gate_tests.coverage.support import MetricCounts, coverage_summary

METRICS = ("statements", "branches", "functions", "lines")

COVERAGE_IGNORE_DIRECTIVES = (
    "/* c8 ignore next */",
    "/* c8 ignore next 3 */",
    "/* c8 ignore start */",
    "/* c8 ignore stop */",
    "/* istanbul ignore if */",
    "/* istanbul ignore else */",
    "/* istanbul ignore next */",
    "/* istanbul ignore file */",
    "/* v8 ignore next */",
    "/* v8 ignore next 3 */",
    "/* v8 ignore start */",
    "/* v8 ignore stop */",
    "/* node:coverage ignore next */",
    "/* node:coverage ignore next 3 */",
    "/* node:coverage disable */",
    "/* node:coverage enable */",
)


def _dashboard(test_case: unittest.TestCase) -> Path:
    """Arrange one disposable dashboard root."""
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    dashboard_root = Path(directory.name) / "apps" / "dashboard"
    dashboard_root.mkdir(parents=True)
    return dashboard_root


def _source(
    dashboard_root: Path,
    relative_path: str = "src/App.tsx",
    content: str = "export const value = 1;\n",
) -> Path:
    """Arrange one authored source path."""
    path = dashboard_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _summary(overrides: Mapping[str, MetricCounts] | None = None) -> dict[str, object]:
    """Arrange one Vitest JSON-summary measurement."""
    return coverage_summary(METRICS, (100, 95, 0, 95), overrides)


def _aggregate_summary(file_count: int) -> dict[str, object]:
    """Arrange the exact aggregate for equally measured fixture files."""
    if file_count == 0:
        return _summary({metric: (0, 0, 0, 100) for metric in METRICS})
    return _summary({metric: (100 * file_count, 95 * file_count, 0, 95) for metric in METRICS})


def _write_report(
    path: Path,
    entries: Mapping[Path, Mapping[str, object]],
    *,
    total: Mapping[str, object] | None = None,
) -> Path:
    """Arrange one Vitest coverage-summary.json report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "total": dict(total) if total is not None else _aggregate_summary(len(entries))
    }
    document.update({str(source.resolve()): dict(summary) for source, summary in entries.items()})
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _conforming_coverage(test_case: unittest.TestCase) -> tuple[Path, Path, Path]:
    """Arrange a dashboard, its default source, and a conforming report."""
    dashboard_root = _dashboard(test_case)
    source = _source(dashboard_root)
    report = _write_report(
        dashboard_root / "coverage" / "coverage-summary.json",
        {source: _summary()},
    )
    return dashboard_root, source, report


def _metric_reports(
    dashboard_root: Path,
    source: Path,
    metrics: tuple[str, ...],
    counts: MetricCounts,
    *,
    filename_prefix: str,
) -> dict[str, Path]:
    """Arrange one independently varied report for each requested metric."""
    return {
        metric: _write_report(
            dashboard_root / "coverage" / f"{filename_prefix}-{metric}.json",
            {source: _summary({metric: counts})},
        )
        for metric in metrics
    }


def _v8_aggregate_report(
    test_case: unittest.TestCase,
    *,
    total: int,
    covered: int,
    percentage: object,
) -> tuple[Path, Path, Path]:
    """Arrange one report with Vitest's optional branchesTrue aggregate metadata."""
    dashboard_root = _dashboard(test_case)
    source = _source(dashboard_root)
    aggregate = _summary()
    aggregate["branchesTrue"] = {
        "total": total,
        "covered": covered,
        "skipped": 0,
        "pct": percentage,
    }
    report = _write_report(
        dashboard_root / "coverage" / "coverage-summary.json",
        {source: _summary()},
        total=aggregate,
    )
    return dashboard_root, source, report


def _main_arguments(
    report: Path,
    dashboard_root: Path,
    *,
    sources: tuple[Path, ...] = (),
    inventory: Path | None = None,
) -> list[str]:
    """Arrange the command-line boundary without hiding its invocation."""
    arguments = ["--report", str(report), "--dashboard-root", str(dashboard_root)]
    for source in sources:
        arguments.extend(("--source", str(source)))
    if inventory is not None:
        arguments.extend(("--source-inventory", str(inventory)))
    return arguments


def _captured_main_arguments(
    report: Path,
    dashboard_root: Path,
    *,
    sources: tuple[Path, ...] = (),
    inventory: Path | None = None,
) -> tuple[io.StringIO, list[str]]:
    """Arrange the stderr capture and arguments for one explicit main invocation."""
    return io.StringIO(), _main_arguments(
        report,
        dashboard_root,
        sources=sources,
        inventory=inventory,
    )


def _captured_inventory_arguments(
    report: Path,
    dashboard_root: Path,
    content: bytes,
) -> tuple[io.StringIO, list[str]]:
    """Arrange one inventory file plus stderr and arguments for an explicit invocation."""
    inventory = dashboard_root / "coverage" / "source-inventory.bin"
    inventory.write_bytes(content)
    return _captured_main_arguments(report, dashboard_root, inventory=inventory)


class TypeScriptCoverageGateTests(unittest.TestCase):
    def test_exactly_ninety_five_percent_in_every_metric_passes(self) -> None:
        # Arrange
        dashboard_root, source, report = _conforming_coverage(self)

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertEqual([], findings)

    def test_each_metric_is_enforced_independently_below_ninety_five_percent(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        reports = _metric_reports(
            dashboard_root,
            source,
            METRICS,
            (100, 94, 0, 100),
            filename_prefix="coverage",
        )

        # Act
        findings_by_metric: dict[str, list[str]] = {}
        for metric, report in reports.items():
            findings_by_metric[metric] = typescript_coverage_gate.evaluate_coverage(
                report, dashboard_root, [source]
            )

        # Assert
        for metric, findings in findings_by_metric.items():
            with self.subTest(metric=metric):
                self.assertTrue(
                    any(metric in finding and "95" in finding for finding in findings),
                    findings,
                )

    def test_an_empty_production_inventory_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [])

        # Assert
        self.assertTrue(
            any("no hand-written production source" in finding for finding in findings),
            findings,
        )

    def test_a_missing_coverage_report_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        missing_report = dashboard_root / "coverage" / "coverage-summary.json"

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            missing_report, dashboard_root, [source]
        )

        # Assert
        self.assertTrue(
            any(
                "coverage report" in finding and "does not exist" in finding for finding in findings
            ),
            findings,
        )

    def test_malformed_coverage_reports_fail_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        invalid_json = dashboard_root / "coverage" / "invalid-json.json"
        invalid_json.parent.mkdir(parents=True)
        invalid_json.write_text("{", encoding="utf-8")
        non_object = dashboard_root / "coverage" / "non-object.json"
        non_object.write_text("[]", encoding="utf-8")

        # Act
        findings_by_case = {
            "invalid JSON": typescript_coverage_gate.evaluate_coverage(
                invalid_json, dashboard_root, [source]
            ),
            "JSON object": typescript_coverage_gate.evaluate_coverage(
                non_object, dashboard_root, [source]
            ),
        }

        # Assert
        for expected, findings in findings_by_case.items():
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in finding for finding in findings), findings)

    def test_a_duplicate_json_key_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        report = dashboard_root / "coverage" / "coverage-summary.json"
        report.parent.mkdir(parents=True)
        summary = json.dumps(_summary())
        report.write_text(
            f'{{"total": {summary}, "total": {summary}, {json.dumps(str(source))}: {summary}}}',
            encoding="utf-8",
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(any("duplicate JSON key" in finding for finding in findings), findings)

    def test_a_production_source_missing_from_the_report_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(
            any(
                "src/App.tsx" in finding and "missing from the coverage report" in finding
                for finding in findings
            ),
            findings,
        )

    def test_an_unexpected_report_file_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        unexpected = _source(dashboard_root, "src/NotEnumerated.ts")
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: _summary(), unexpected: _summary()},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(
            any(
                "src/NotEnumerated.ts" in finding and "unexpected" in finding
                for finding in findings
            ),
            findings,
        )

    def test_a_report_file_outside_the_dashboard_root_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        outside = dashboard_root.parent / "outside.ts"
        outside.write_text("export const outside = true;\n", encoding="utf-8")
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: _summary(), outside: _summary()},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(
            any("outside the dashboard root" in finding for finding in findings), findings
        )

    def test_the_declared_total_must_equal_the_recomputed_file_total(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        reported_total = _summary({metric: (100, 100, 0, 100) for metric in METRICS})
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: _summary()},
            total=reported_total,
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(
            any("total" in finding and "recomputed" in finding for finding in findings),
            findings,
        )

    def test_invalid_metric_counts_fail_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        cases: dict[str, tuple[MetricCounts, str]] = {
            "boolean": ((True, 1, 0, 100), "integer"),
            "negative": ((-1, 0, 0, 100), "non-negative"),
            "covered above total": ((1, 2, 0, 100), "exceed"),
            "skipped": ((1, 1, 1, 100), "skipped"),
        }
        reports = {
            name: _write_report(
                dashboard_root / "coverage" / f"{name.replace(' ', '-')}.json",
                {source: _summary({"statements": counts})},
                total=_summary(),
            )
            for name, (counts, _) in cases.items()
        }

        # Act
        findings_by_case = {
            name: typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])
            for name, report in reports.items()
        }

        # Assert
        for name, (_, expected) in cases.items():
            with self.subTest(name=name):
                self.assertTrue(
                    any(expected in finding for finding in findings_by_case[name]),
                    findings_by_case[name],
                )

    def test_zero_measurable_statements_or_lines_fails_closed(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        reports = _metric_reports(
            dashboard_root,
            source,
            ("statements", "lines"),
            (0, 0, 0, 100),
            filename_prefix="zero",
        )

        # Act
        findings_by_metric = {
            metric: typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])
            for metric, report in reports.items()
        }

        # Assert
        for metric, findings in findings_by_metric.items():
            with self.subTest(metric=metric):
                self.assertTrue(
                    any(metric in finding and "no measurable" in finding for finding in findings),
                    findings,
                )

    def test_zero_opportunity_branches_and_functions_pass(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        summary = _summary(
            {
                "branches": (0, 0, 0, 100),
                "functions": (0, 0, 0, 100),
            }
        )
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: summary},
            total=summary,
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertEqual([], findings)

    def test_zero_opportunity_dimensions_still_require_an_exact_aggregate(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        source = _source(dashboard_root)
        file_summary = _summary(
            {
                "branches": (0, 0, 0, 100),
                "functions": (0, 0, 0, 100),
            }
        )
        inconsistent_total = _summary(
            {
                "branches": (1, 1, 0, 100),
                "functions": (1, 1, 0, 100),
            }
        )
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: file_summary},
            total=inconsistent_total,
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(
            any("total.branches" in finding and "recomputed" in finding for finding in findings),
            findings,
        )
        self.assertTrue(
            any("total.functions" in finding and "recomputed" in finding for finding in findings),
            findings,
        )

    def test_the_exact_v8_aggregate_metadata_is_accepted(self) -> None:
        # Arrange
        dashboard_root, source, report = _v8_aggregate_report(
            self,
            total=0,
            covered=0,
            percentage="Unknown",
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertEqual([], findings)

    def test_malformed_v8_aggregate_metadata_fails_closed(self) -> None:
        # Arrange
        dashboard_root, source, report = _v8_aggregate_report(
            self,
            total=1,
            covered=1,
            percentage=100,
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, [source])

        # Assert
        self.assertTrue(
            any("total.branchesTrue" in finding for finding in findings),
            findings,
        )

    def test_test_generated_and_declaration_sources_are_excluded(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        production = _source(dashboard_root)
        excluded = [
            _source(dashboard_root, "src/App.test.tsx", "/* c8 ignore file */\n"),
            _source(dashboard_root, "src/App.spec.ts", "/* istanbul ignore file */\n"),
            _source(dashboard_root, "tests/e2e/dashboard.spec.ts", "/* v8 ignore next */\n"),
            _source(
                dashboard_root,
                "src/contracts/generated/dashboard.ts",
                "/* node:coverage disable */\n",
            ),
            _source(dashboard_root, "src/vite-env.d.ts", "/* c8 ignore start */\n"),
        ]
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {production: _summary()},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report, dashboard_root, [production, *excluded]
        )

        # Assert
        self.assertEqual([], findings)

    def test_excluded_files_reported_as_covered_are_unexpected(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        production = _source(dashboard_root)
        excluded = [
            _source(dashboard_root, "src/App.test.tsx"),
            _source(dashboard_root, "src/contracts/generated/dashboard.ts"),
            _source(dashboard_root, "src/vite-env.d.ts"),
        ]
        entries = {path: _summary() for path in [production, *excluded]}
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            entries,
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report, dashboard_root, [production, *excluded]
        )

        # Assert
        for path in excluded:
            with self.subTest(path=path.name):
                self.assertTrue(
                    any(
                        str(path.relative_to(dashboard_root)) in finding and "unexpected" in finding
                        for finding in findings
                    ),
                    findings,
                )

    def test_javascript_and_jsx_production_sources_are_refused(self) -> None:
        # Arrange
        dashboard_root, production, report = _conforming_coverage(self)
        javascript = _source(dashboard_root, "src/legacy.js")
        jsx = _source(dashboard_root, "src/legacy-view.jsx")

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report, dashboard_root, [production, javascript, jsx]
        )

        # Assert
        for path in (javascript, jsx):
            with self.subTest(path=path.name):
                self.assertTrue(
                    any(
                        path.name in finding and "TypeScript production source" in finding
                        for finding in findings
                    ),
                    findings,
                )

    def test_module_extensions_are_never_silently_omitted(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        production = _source(dashboard_root)
        refused_typescript = [
            _source(dashboard_root, "src/module.mts"),
            _source(dashboard_root, "src/common-module.cts"),
        ]
        refused_javascript = [
            _source(dashboard_root, "src/module.mjs"),
            _source(dashboard_root, "src/common-module.cjs"),
        ]
        declarations = [
            _source(dashboard_root, "src/module.d.mts"),
            _source(dashboard_root, "src/common-module.d.cts"),
        ]
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {production: _summary()},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report,
            dashboard_root,
            [production, *refused_typescript, *refused_javascript, *declarations],
        )

        # Assert
        for path in refused_typescript:
            with self.subTest(path=path.name):
                self.assertTrue(
                    any(
                        path.name in finding and "not an accepted TypeScript" in finding
                        for finding in findings
                    ),
                    findings,
                )
        for path in refused_javascript:
            with self.subTest(path=path.name):
                self.assertTrue(
                    any(path.name in finding and "JavaScript" in finding for finding in findings),
                    findings,
                )
        for path in declarations:
            with self.subTest(path=path.name):
                self.assertFalse(any(path.name in finding for finding in findings), findings)

    def test_symlink_and_nonregular_production_sources_are_refused(self) -> None:
        # Arrange
        dashboard_root, production, report = _conforming_coverage(self)
        target = _source(dashboard_root, "support/target.ts")
        symlink = dashboard_root / "src" / "linked.ts"
        symlink.symlink_to(target)
        nonregular = dashboard_root / "src" / "nonregular.ts"
        nonregular.mkdir()

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report, dashboard_root, [production, symlink, nonregular]
        )

        # Assert
        self.assertTrue(
            any("linked.ts" in finding and "symbolic link" in finding for finding in findings),
            findings,
        )
        self.assertTrue(
            any("nonregular.ts" in finding and "regular file" in finding for finding in findings),
            findings,
        )

    def test_every_known_coverage_ignore_directive_is_refused(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        sources = [
            _source(
                dashboard_root,
                f"src/ignore-{index}.ts",
                f"{directive}\nexport const value{index} = {index};\n",
            )
            for index, directive in enumerate(COVERAGE_IGNORE_DIRECTIVES)
        ]
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: _summary() for source in sources},
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(report, dashboard_root, sources)

        # Assert
        for source, directive in zip(sources, COVERAGE_IGNORE_DIRECTIVES, strict=True):
            with self.subTest(directive=directive):
                self.assertTrue(
                    any(
                        source.name in finding and "coverage-ignore directive" in finding
                        for finding in findings
                    ),
                    findings,
                )

    def test_diagnostics_are_deduplicated_and_sorted_independent_of_source_order(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        first = _source(
            dashboard_root,
            "src/Zulu.ts",
            "/* c8 ignore next */\nexport const zulu = true;\n",
        )
        second = _source(
            dashboard_root,
            "src/Alpha.ts",
            "/* v8 ignore next */\nexport const alpha = true;\n",
        )
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {first: _summary(), second: _summary()},
        )

        # Act
        forward = typescript_coverage_gate.evaluate_coverage(
            report, dashboard_root, [first, second, first]
        )
        reverse = typescript_coverage_gate.evaluate_coverage(
            report, dashboard_root, [second, first]
        )

        # Assert
        self.assertEqual(reverse, forward)
        self.assertEqual(sorted(set(forward)), forward)

    def test_main_returns_zero_for_a_conforming_report(self) -> None:
        # Arrange
        dashboard_root, source, report = _conforming_coverage(self)
        stderr, arguments = _captured_main_arguments(report, dashboard_root, sources=(source,))

        # Act
        with contextlib.redirect_stderr(stderr):
            status = typescript_coverage_gate.main(arguments)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", stderr.getvalue())

    def test_main_reads_a_nul_delimited_inventory_losslessly(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        sources = [
            _source(dashboard_root, "src/café.ts"),
            _source(dashboard_root, "src/line\nbreak.ts"),
        ]
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {source: _summary() for source in sources},
        )
        inventory_content = b"".join(os.fsencode(source) + b"\0" for source in sources)
        stderr, arguments = _captured_inventory_arguments(
            report,
            dashboard_root,
            inventory_content,
        )
        stderr_context = contextlib.redirect_stderr(stderr)

        # Act
        with stderr_context:
            status = typescript_coverage_gate.main(arguments)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", stderr.getvalue())

    def test_an_invalid_nul_inventory_fails_closed_without_echoing_its_value(self) -> None:
        # Arrange
        dashboard_root, _source_path, report = _conforming_coverage(self)
        stderr, arguments = _captured_inventory_arguments(
            report,
            dashboard_root,
            b"not-nul-terminated SECRET_SENTINEL",
        )

        # Act
        with contextlib.redirect_stderr(stderr):
            status = typescript_coverage_gate.main(arguments)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("NUL-terminated", stderr.getvalue())
        self.assertNotIn("SECRET_SENTINEL", stderr.getvalue())

    def test_main_prints_sorted_diagnostics_and_returns_one_on_failure(self) -> None:
        # Arrange
        dashboard_root = _dashboard(self)
        first = _source(dashboard_root, "src/Zulu.ts")
        second = _source(dashboard_root, "src/Alpha.ts")
        report = _write_report(
            dashboard_root / "coverage" / "coverage-summary.json",
            {},
        )
        stderr, arguments = _captured_main_arguments(
            report,
            dashboard_root,
            sources=(first, second),
        )

        # Act
        with contextlib.redirect_stderr(stderr):
            status = typescript_coverage_gate.main(arguments)
        diagnostics = stderr.getvalue().splitlines()

        # Assert
        self.assertEqual(1, status)
        self.assertGreater(len(diagnostics), 1)
        self.assertEqual(sorted(set(diagnostics)), diagnostics)
        self.assertTrue(
            all(diagnostic.startswith("TYPESCRIPT COVERAGE: ") for diagnostic in diagnostics),
            diagnostics,
        )


if __name__ == "__main__":
    unittest.main()
