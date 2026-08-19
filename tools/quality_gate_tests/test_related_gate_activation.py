from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class RelatedGateActivationTests(QualityGateTestCase):
    def test_root_source_without_manifest_fails_the_related_test_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        source = repository / "tools" / "owned.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")

        # Act
        result = self.run_hook("pytest-related.sh", repository, (str(source),))

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: pyproject.toml", result.stderr)


if __name__ == "__main__":
    unittest.main()
