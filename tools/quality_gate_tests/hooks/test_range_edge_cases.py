from __future__ import annotations

import unittest
from pathlib import Path

from tools.quality_gate_tests.support import QualityGateTestCase


class RangeEdgeCaseTests(QualityGateTestCase):
    def test_new_branch_commit_check_excludes_existing_remote_history(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        tracked = repository / "tracked.txt"
        tracked.write_text("root\n", encoding="utf-8")
        self.commit_all(repository, "Initial commit")
        root_commit = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        self.git(repository, "update-ref", "refs/remotes/origin/main", root_commit)
        tracked.write_text("feature\n", encoding="utf-8")
        self.commit_all(repository, "feat: add feature")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        executable_directory = repository / "bin"
        executable_directory.mkdir()
        inspected_messages = repository / "messages.txt"
        self._write_pre_commit_recorder(executable_directory / "pre-commit", inspected_messages)

        # Act
        result = self.run_hook(
            "check-commit-messages.sh",
            repository,
            environment={
                "PATH": f"{executable_directory}:/usr/bin:/bin",
                "PRE_COMMIT_FROM_REF": "0" * 40,
                "PRE_COMMIT_TO_REF": head,
                "PRE_COMMIT_REMOTE_NAME": "origin",
            },
        )

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "feat: add feature", inspected_messages.read_text(encoding="utf-8").strip()
        )

    def test_an_unrelated_remote_ref_does_not_admit_pre_convention_history(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        tracked = repository / "tracked.txt"
        tracked.write_text("root\n", encoding="utf-8")
        self.commit_all(repository, "Initial commit")
        tracked.write_text("feature\n", encoding="utf-8")
        self.commit_all(repository, "feat: add feature")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        self.git(repository, "checkout", "--orphan", "unrelated")
        unrelated = repository / "unrelated.txt"
        unrelated.write_text("unrelated\n", encoding="utf-8")
        self.commit_all(repository, "chore: unrelated root")
        orphan = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        self.git(repository, "update-ref", "refs/remotes/origin/unrelated", orphan)
        self.git(repository, "checkout", "--force", head)
        executable_directory = repository / "bin"
        executable_directory.mkdir()
        inspected_messages = repository / "messages.txt"
        self._write_pre_commit_recorder(executable_directory / "pre-commit", inspected_messages)

        # Act
        result = self.run_hook(
            "check-commit-messages.sh",
            repository,
            environment={
                "PATH": f"{executable_directory}:/usr/bin:/bin",
                "PRE_COMMIT_FROM_REF": "0" * 40,
                "PRE_COMMIT_TO_REF": head,
                "PRE_COMMIT_REMOTE_NAME": "origin",
            },
        )

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Initial commit", inspected_messages.read_text(encoding="utf-8"))

    def test_quality_range_cannot_borrow_one_pre_commit_endpoint(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        tracked = repository / "tracked.txt"
        tracked.write_text("root\n", encoding="utf-8")
        self.commit_all(repository, "root")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()

        # Act
        result = self.run_hook(
            "check-commit-range.sh",
            repository,
            environment={
                "QUALITY_DIFF_BASE": head,
                "PRE_COMMIT_TO_REF": head,
            },
        )

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("both", result.stderr.lower())

    def test_explicit_committed_range_ignores_uncommitted_whitespace(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        tracked = repository / "tracked.txt"
        tracked.write_text("root\n", encoding="utf-8")
        self.commit_all(repository, "root")
        base = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        tracked.write_text("clean commit\n", encoding="utf-8")
        self.commit_all(repository, "clean")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        tracked.write_text("uncommitted whitespace \n", encoding="utf-8")

        # Act
        result = self.run_hook(
            "check-commit-range.sh",
            repository,
            environment={"QUALITY_DIFF_BASE": base, "QUALITY_DIFF_HEAD": head},
        )

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)

    @staticmethod
    def _write_pre_commit_recorder(path: Path, output: Path) -> None:
        path.write_text(
            "#!/bin/sh\n"
            "message_file=''\n"
            'while [ "$#" -gt 0 ]; do\n'
            "  if [ \"$1\" = '--commit-msg-filename' ]; then shift; message_file=$1; fi\n"
            "  shift\n"
            "done\n"
            f'head -n 1 "$message_file" >>{output}\n',
            encoding="utf-8",
        )
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
