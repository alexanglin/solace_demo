from __future__ import annotations

import unittest

from tools import coverage_gate


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


if __name__ == "__main__":
    unittest.main()
