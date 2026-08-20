from __future__ import annotations

import unittest

from tools import coverage_gate


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


if __name__ == "__main__":
    unittest.main()
