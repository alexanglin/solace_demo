from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from tools import coverage_gate

EMPTY_REPORT: dict[str, object] = {"files": {}}
MEMBERS_ERROR = "[tool.uv.workspace].members must be a list of glob strings"
TIER_TWO_MANIFEST = "[tool.aerial-rescue]\nrisk-tier = 2\n"


def _collect_without_coverage(root: Path) -> list[coverage_gate.MemberVerdict]:
    """Collect the verdicts under ``root`` against an empty coverage report."""
    with mock.patch.object(coverage_gate, "_coverage_json", return_value=EMPTY_REPORT):
        return coverage_gate.collect(root)


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

    def test_a_member_inventory_that_is_not_a_list_is_a_configuration_error(self) -> None:
        # Arrange
        root = self._root()
        self._write_root_manifest(root, 'members = "components/*"')

        # Act
        with pytest.raises(coverage_gate.WorkspaceConfigurationError) as raised:
            _collect_without_coverage(root)

        # Assert
        self.assertEqual(MEMBERS_ERROR, str(raised.value))

    def test_a_member_inventory_with_a_non_string_entry_is_a_configuration_error(self) -> None:
        # Arrange
        root = self._root()
        self._write_root_manifest(root, 'members = ["components/*", 1]')

        # Act
        with pytest.raises(coverage_gate.WorkspaceConfigurationError) as raised:
            _collect_without_coverage(root)

        # Assert
        self.assertEqual(MEMBERS_ERROR, str(raised.value))

    def test_a_root_without_a_manifest_cannot_be_collected(self) -> None:
        # Arrange
        root = self._root()

        # Act
        with pytest.raises(FileNotFoundError) as raised:
            _collect_without_coverage(root)

        # Assert
        self.assertEqual(str(root / "pyproject.toml"), raised.value.filename)

    def test_an_active_member_is_judged_against_its_declared_tier(self) -> None:
        # Arrange
        root = self._root()
        self._write_root_manifest(root, 'members = ["components/*"]')
        member = root / "components" / "example"
        (member / "tests").mkdir(parents=True)
        (member / "pyproject.toml").write_text(TIER_TWO_MANIFEST, encoding="utf-8")
        report = self._report("components/example/src/example.py")

        # Act
        with mock.patch.object(coverage_gate, "_coverage_json", return_value=report):
            verdicts = coverage_gate.collect(root)

        # Assert
        example = next(item for item in verdicts if item.name == "components/example")
        self.assertEqual("PASS", example.outcome)
        self.assertEqual(coverage_gate.TIER_THRESHOLDS[2], example.threshold)

    def _root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    @staticmethod
    def _write_root_manifest(root: Path, members: str) -> None:
        (root / "pyproject.toml").write_text(
            f"{TIER_TWO_MANIFEST}[tool.uv.workspace]\n{members}\n",
            encoding="utf-8",
        )

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
