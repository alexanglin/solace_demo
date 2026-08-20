from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class DuplicationScopeTests(unittest.TestCase):
    def test_gate_scans_owned_programming_languages_not_repeated_manifests(self) -> None:
        # Arrange
        script = REPOSITORY_ROOT / "scripts" / "hooks" / "repo" / "duplication-full.sh"

        # Act
        content = script.read_text(encoding="utf-8")

        # Assert
        self.assertIn("--format python,javascript,jsx,typescript,tsx,bash", content)


if __name__ == "__main__":
    unittest.main()
