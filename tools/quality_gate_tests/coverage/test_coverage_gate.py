from __future__ import annotations

import contextlib
import io
import json
import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest

from tools import coverage_gate
from tools.quality_gate_tests.support import QualityGateTestCase

COVERAGE_COMMAND = [sys.executable, "-m", "coverage", "json", "-o", "-"]
EMPTY_REPORT: dict[str, object] = {"files": {}}
FULLY_COVERED_WITHOUT_BRANCHES = coverage_gate.CoverageMeasurement(
    statements=4, covered_statements=4, branches=0, covered_branches=0
)
NOTHING_MEASURED = coverage_gate.CoverageMeasurement(
    statements=0, covered_statements=0, branches=0, covered_branches=0
)


def _completed(returncode: int, stdout: str) -> subprocess.CompletedProcess[str]:
    """Return a finished coverage process without launching one."""
    return subprocess.CompletedProcess(COVERAGE_COMMAND, returncode, stdout=stdout, stderr="")


def _file_entry(statements: int, covered: int) -> dict[str, object]:
    """Return one branch-free file entry in coverage's JSON report shape."""
    summary = {
        "num_statements": statements,
        "covered_lines": covered,
        "num_branches": 0,
        "covered_branches": 0,
    }
    return {"summary": summary}


class CoverageGateTests(unittest.TestCase):
    def test_branch_coverage_cannot_be_masked_by_line_coverage(self) -> None:
        # Arrange
        measurement = coverage_gate.CoverageMeasurement(
            statements=100,
            covered_statements=100,
            branches=10,
            covered_branches=9,
        )

        # Act
        verdict = coverage_gate.judge("packages/example", 2, measurement)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("branch 90.00%", verdict.detail)

    def test_a_tier_one_member_requires_complete_line_and_branch_coverage(self) -> None:
        # Arrange
        measurement = coverage_gate.CoverageMeasurement(
            statements=10,
            covered_statements=10,
            branches=4,
            covered_branches=4,
        )

        # Act
        verdict = coverage_gate.judge("packages/domain", 1, measurement)

        # Assert
        self.assertEqual("PASS", verdict.outcome)
        self.assertIn("statement 100.00%", verdict.detail)
        self.assertIn("branch 100.00%", verdict.detail)

    def test_a_member_without_branches_has_complete_branch_coverage(self) -> None:
        # Arrange
        measurement = coverage_gate.CoverageMeasurement(
            statements=4,
            covered_statements=4,
            branches=0,
            covered_branches=0,
        )

        # Act
        verdict = coverage_gate.judge("packages/contracts", 1, measurement)

        # Assert
        self.assertEqual("PASS", verdict.outcome)
        self.assertIn("branch 100.00%", verdict.detail)

    def test_a_member_without_a_declared_tier_fails_before_measurement(self) -> None:
        # Arrange
        measurement = FULLY_COVERED_WITHOUT_BRANCHES

        # Act
        verdict = coverage_gate.judge("packages/untiered", None, measurement)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIsNone(verdict.threshold)
        self.assertIn("declares no [tool.aerial-rescue] risk-tier", verdict.detail)


class CoverageJsonTests(unittest.TestCase):
    def test_a_failed_coverage_command_yields_an_empty_report(self) -> None:
        # Arrange
        process = _completed(1, "")

        # Act
        with mock.patch("tools.coverage_gate.subprocess.run", return_value=process) as run:
            report = coverage_gate._coverage_json()

        # Assert
        self.assertEqual(EMPTY_REPORT, report)
        run.assert_called_once_with(COVERAGE_COMMAND, capture_output=True, text=True, check=False)

    def test_an_unparsable_coverage_report_yields_an_empty_report(self) -> None:
        # Arrange
        process = _completed(0, "No data to report.")

        # Act
        with mock.patch("tools.coverage_gate.subprocess.run", return_value=process):
            report = coverage_gate._coverage_json()

        # Assert
        self.assertEqual(EMPTY_REPORT, report)

    def test_a_parsable_coverage_report_is_returned_as_parsed(self) -> None:
        # Arrange
        expected = {"files": {"tools/gate.py": _file_entry(1, 1)}}
        process = _completed(0, json.dumps(expected))

        # Act
        with mock.patch("tools.coverage_gate.subprocess.run", return_value=process):
            report = coverage_gate._coverage_json()

        # Assert
        self.assertEqual(expected, report)


class MeasureTests(unittest.TestCase):
    def test_a_report_whose_files_are_not_a_mapping_measures_nothing(self) -> None:
        # Arrange
        report: dict[str, object] = {"files": ["tools/gate.py"]}

        # Act
        measurement = coverage_gate.measure(report, ".")

        # Assert
        self.assertEqual(NOTHING_MEASURED, measurement)

    def test_a_file_whose_summary_is_not_a_mapping_is_skipped(self) -> None:
        # Arrange
        report: dict[str, object] = {
            "files": {
                "tools/corrupt.py": {"summary": "unreadable"},
                "tools/gate.py": _file_entry(2, 1),
            }
        }
        expected = coverage_gate.CoverageMeasurement(
            statements=2, covered_statements=1, branches=0, covered_branches=0
        )

        # Act
        measurement = coverage_gate.measure(report, ".")

        # Assert
        self.assertEqual(expected, measurement)


class EntryPointTests(QualityGateTestCase):
    def test_the_module_entry_point_exits_with_the_gate_verdict(self) -> None:
        # Arrange
        root = self.temporary_directory()
        (root / "pyproject.toml").write_text(
            "[tool.aerial-rescue]\nrisk-tier = 2\n",
            encoding="utf-8",
        )
        report = {"files": {"tools/gate.py": _file_entry(1, 1)}}
        process = _completed(0, json.dumps(report))
        stdout = io.StringIO()

        # Act
        with (
            mock.patch("tools.coverage_gate.subprocess.run", return_value=process),
            mock.patch.object(Path, "cwd", return_value=root),
            contextlib.redirect_stdout(stdout),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(coverage_gate.__file__, run_name="__main__")

        # Assert
        self.assertEqual(0, raised.value.code)
        self.assertIn("PASS   .", stdout.getvalue())
        self.assertIn("required 95% each", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
