from __future__ import annotations

import hashlib
import subprocess
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

CHECK_ONE = REPOSITORY_ROOT / "scripts" / "hooks" / "docs" / "check-diagrams.sh"
CHECK_ALL = REPOSITORY_ROOT / "scripts" / "hooks" / "docs" / "check-diagrams-all.sh"


class DiagramIntegrityTests(QualityGateTestCase):
    def test_nested_source_without_png_fails_the_full_check(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "docs" / "architecture" / "workflows" / "nested.dot"
        source.parent.mkdir(parents=True)
        source.write_text("digraph G {}\n", encoding="utf-8")

        # Act
        result = self._run(CHECK_ALL, repository)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING", result.stderr)

    def test_changed_png_fails_even_when_the_source_hash_matches(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "docs" / "architecture" / "overview.dot"
        source.parent.mkdir(parents=True)
        source.write_text("digraph G {}\n", encoding="utf-8")
        png = source.with_suffix(".png")
        png.write_bytes(b"\x89PNG\r\n\x1a\noriginal")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        (Path(f"{source}.sha256")).write_text(f"{source_hash}\n", encoding="utf-8")
        png.write_bytes(b"\x89PNG\r\n\x1a\nchanged")

        # Act
        result = self._run(CHECK_ONE, repository, str(source))

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("PNG", result.stderr)

    @staticmethod
    def _run(
        hook: Path,
        repository: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("/bin/sh", str(hook), *arguments),
            cwd=repository,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
