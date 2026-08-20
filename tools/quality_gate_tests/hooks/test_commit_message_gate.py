from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class CommitMessageGateTests(QualityGateTestCase):
    def test_ci_runs_the_commit_message_stage_for_the_complete_range(self) -> None:
        # Arrange
        workflow_path = ".github/workflows/checks.yml"

        # Act
        source = self.read_repository_text(workflow_path)

        # Assert
        self.assertIn("scripts/hooks/repo/check-commit-messages.sh", source)
        self.assertIn("QUALITY_DIFF_BASE", source)
        self.assertIn("QUALITY_DIFF_HEAD", source)

    def test_commit_message_gate_fails_when_pre_commit_is_missing(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "file.txt").write_text("content\n", encoding="utf-8")
        self.commit_all(repository, "valid: message")

        # Act
        result = self.run_hook("check-commit-messages.sh", repository)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: pre-commit", result.stderr)


if __name__ == "__main__":
    unittest.main()
