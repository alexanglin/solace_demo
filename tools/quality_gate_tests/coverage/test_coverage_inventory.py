from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import coverage_gate


class CoverageInventoryTests(unittest.TestCase):
    def test_the_root_tooling_project_is_a_measured_member(self) -> None:
        # Arrange
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "pyproject.toml").write_text(
            "[tool.aerial-rescue]\nrisk-tier = 2\n",
            encoding="utf-8",
        )
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

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=report):
            verdicts = coverage_gate.collect(root)

        # Assert
        self.assertEqual(["."], [verdict.name for verdict in verdicts])
        self.assertEqual("PASS", verdicts[0].outcome)

    def test_tier_three_fails_until_its_required_test_inventory_is_mechanical(self) -> None:
        # Arrange
        measurement = coverage_gate.CoverageMeasurement(
            statements=1,
            covered_statements=1,
            branches=0,
            covered_branches=0,
        )

        # Act
        verdict = coverage_gate.judge("services/configuration", 3, measurement)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("smoke/failure-path gate is not implemented", verdict.detail)


if __name__ == "__main__":
    unittest.main()
