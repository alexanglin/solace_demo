from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import coverage_gate


class CoverageWorkspaceDiscoveryTests(unittest.TestCase):
    def test_collect_uses_the_workspace_member_globs(self) -> None:
        # Arrange
        root = self._root()
        (root / "pyproject.toml").write_text(
            "[tool.aerial-rescue]\n"
            "risk-tier = 2\n"
            "[tool.uv.workspace]\n"
            'members = ["components/*"]\n',
            encoding="utf-8",
        )
        member = root / "components" / "example"
        member.mkdir(parents=True)
        (member / "pyproject.toml").write_text(
            "[tool.aerial-rescue]\nrisk-tier = 2\n",
            encoding="utf-8",
        )
        report = self._report("components/example/src/example.py")

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=report):
            verdicts = coverage_gate.collect(root)

        # Assert
        self.assertEqual([".", "components/example"], [item.name for item in verdicts])

    def test_workspace_source_without_member_manifest_fails(self) -> None:
        # Arrange
        root = self._root()
        (root / "pyproject.toml").write_text(
            "[tool.aerial-rescue]\n"
            "risk-tier = 2\n"
            "[tool.uv.workspace]\n"
            'members = ["components/*"]\n',
            encoding="utf-8",
        )
        source = root / "components" / "missing" / "src" / "missing.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        report = self._report("components/missing/src/missing.py")

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=report):
            verdicts = coverage_gate.collect(root)

        # Assert
        missing = next(item for item in verdicts if item.name == "components/missing")
        self.assertEqual("FAIL", missing.outcome)
        self.assertIn("pyproject.toml", missing.detail)

    def test_boolean_risk_tier_is_not_accepted_as_integer_one(self) -> None:
        # Arrange
        root = self._root()
        pyproject = root / "pyproject.toml"
        pyproject.write_text("[tool.aerial-rescue]\nrisk-tier = true\n", encoding="utf-8")

        # Act
        tier = coverage_gate.read_tier(pyproject)

        # Assert
        self.assertIsNone(tier)

    def _root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    @staticmethod
    def _report(path: str) -> dict[str, object]:
        return {
            "files": {
                "tools/gate.py": {
                    "summary": {
                        "num_statements": 1,
                        "covered_lines": 1,
                        "num_branches": 0,
                        "covered_branches": 0,
                    }
                },
                path: {
                    "summary": {
                        "num_statements": 1,
                        "covered_lines": 1,
                        "num_branches": 0,
                        "covered_branches": 0,
                    }
                },
            }
        }


if __name__ == "__main__":
    unittest.main()
