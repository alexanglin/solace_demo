from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import typescript_coverage_gate


def _complete_summary() -> dict[str, dict[str, int]]:
    return {
        dimension: {"total": 1, "covered": 1, "skipped": 0, "pct": 100}
        for dimension in ("lines", "statements", "functions", "branches")
    }


class TypeScriptV8MetadataCompatibilityTests(unittest.TestCase):
    """Accept the pinned Vitest spelling for empty V8-only metadata."""

    def test_zero_count_metadata_with_a_complete_percentage_is_accepted(self) -> None:
        # Arrange
        temporary_directory = self.enterContext(tempfile.TemporaryDirectory())
        dashboard_root = Path(temporary_directory) / "apps" / "dashboard"
        source = dashboard_root / "src" / "main.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const ready = true;\n", encoding="utf-8")
        report = dashboard_root / "coverage" / "coverage-summary.json"
        report.parent.mkdir()
        aggregate: dict[str, object] = _complete_summary()
        aggregate["branchesTrue"] = {
            "total": 0,
            "covered": 0,
            "skipped": 0,
            "pct": 100,
        }
        report.write_text(
            json.dumps({"total": aggregate, str(source): _complete_summary()}),
            encoding="utf-8",
        )

        # Act
        findings = typescript_coverage_gate.evaluate_coverage(
            report,
            dashboard_root,
            [source],
        )

        # Assert
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
