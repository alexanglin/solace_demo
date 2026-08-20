from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest import mock

from tools import coverage_gate
from tools.quality_gate_tests.support import QualityGateTestCase

EMPTY_REPORT: dict[str, object] = {"files": {}}


def _workspace(root: Path, *, source: str, tests: bool) -> None:
    (root / "pyproject.toml").write_text(
        '[tool.aerial-rescue]\nrisk-tier = 2\n[tool.uv.workspace]\nmembers = ["services/*"]\n',
        encoding="utf-8",
    )
    member = root / "services" / "example"
    package = member / "src" / "example"
    package.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        "[tool.aerial-rescue]\nrisk-tier = 2\n", encoding="utf-8"
    )
    (package / "__init__.py").write_text(source, encoding="utf-8")
    (package / "py.typed").write_text("", encoding="utf-8")
    if tests:
        (member / "tests").mkdir()


def _member_verdict(verdicts: list[coverage_gate.MemberVerdict]) -> coverage_gate.MemberVerdict:
    return next(verdict for verdict in verdicts if verdict.name == "services/example")


class EmptyCoverageTests(unittest.TestCase):
    def test_an_active_member_with_no_measurable_source_fails_closed(self) -> None:
        # Arrange
        measurement = coverage_gate.CoverageMeasurement(
            statements=0,
            covered_statements=0,
            branches=0,
            covered_branches=0,
        )

        # Act
        verdict = coverage_gate.judge("packages/empty", 2, measurement)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("no measurable source", verdict.detail)


class ScaffoldedMemberTests(QualityGateTestCase):
    def test_a_scaffolded_member_is_reported_not_failed(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _workspace(root, source='"""Not started."""\n', tests=False)

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=EMPTY_REPORT):
            verdict = _member_verdict(coverage_gate.collect(root))

        # Assert
        self.assertEqual("SCAFFOLD", verdict.outcome)
        self.assertEqual(2, verdict.tier)
        self.assertIsNone(verdict.threshold)
        self.assertIn("not measured", verdict.detail)

    def test_a_scaffold_with_a_tests_directory_is_active_and_fails_closed(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _workspace(root, source='"""Not started."""\n', tests=True)

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=EMPTY_REPORT):
            verdict = _member_verdict(coverage_gate.collect(root))

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("no measurable source", verdict.detail)

    def test_a_scaffold_without_a_declared_tier_still_fails(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _workspace(root, source='"""Not started."""\n', tests=False)
        (root / "services" / "example" / "pyproject.toml").write_text(
            "[project]\n", encoding="utf-8"
        )

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=EMPTY_REPORT):
            verdict = _member_verdict(coverage_gate.collect(root))

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("risk-tier", verdict.detail)

    def test_the_first_statement_turns_a_scaffold_into_a_measured_member(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _workspace(root, source='"""Started."""\n\nVALUE = 1\n', tests=False)
        report = {
            "files": {
                "services/example/src/example/__init__.py": {
                    "summary": {
                        "num_statements": 1,
                        "covered_lines": 0,
                        "num_branches": 0,
                        "covered_branches": 0,
                    }
                }
            }
        }

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=report):
            verdict = _member_verdict(coverage_gate.collect(root))

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("statement 0.00%", verdict.detail)

    def test_main_prints_scaffolds_on_stdout_and_exits_zero(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _workspace(root, source='"""Not started."""\n', tests=False)
        report = {
            "files": {
                "tools/gate.py": {
                    "summary": {
                        "num_statements": 1,
                        "covered_lines": 1,
                        "num_branches": 0,
                        "covered_branches": 0,
                    }
                }
            }
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        # Act
        with (
            mock.patch.object(coverage_gate, "_coverage_json", return_value=report),
            mock.patch.object(Path, "cwd", return_value=root),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = coverage_gate.main()

        # Assert
        self.assertEqual(0, status)
        self.assertIn("SCAFFOLD services/example", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
