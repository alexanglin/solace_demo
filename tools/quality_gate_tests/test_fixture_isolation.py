"""Tests that fixture repositories are isolated from the caller's Git context."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from tools.quality_gate_tests.support import QualityGateTestCase


class FixtureIsolationTests(QualityGateTestCase):
    def leaked_git_context(self, decoy: Path) -> dict[str, str]:
        return {
            "GIT_DIR": str(decoy / ".git"),
            "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
            "GIT_WORK_TREE": str(decoy),
        }

    def test_a_fixture_repository_commits_while_a_git_context_is_exported(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        decoy = self.temporary_repository()
        (repository / "file.txt").write_text("content\n", encoding="utf-8")

        # Act
        with mock.patch.dict(os.environ, self.leaked_git_context(decoy)):
            self.commit_all(repository, "valid: message")

        # Assert
        head = self.git(repository, "rev-parse", "--verify", "HEAD").stdout.strip()
        self.assertEqual(40, len(head))

    def test_an_exported_git_context_receives_no_fixture_commit(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        decoy = self.temporary_repository()
        (repository / "file.txt").write_text("content\n", encoding="utf-8")

        # Act
        with mock.patch.dict(os.environ, self.leaked_git_context(decoy)):
            self.commit_all(repository, "valid: message")

        # Assert
        commits = self.git(decoy, "rev-list", "--count", "--all").stdout.strip()
        self.assertEqual("0", commits)


if __name__ == "__main__":
    unittest.main()
