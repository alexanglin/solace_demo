from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import HOOKS_DIRECTORY, QualityGateTestCase

# Subcommands git sends through `core.pager` when its output is a terminal. A pager
# waiting for a keystroke is indistinguishable from a slow gate until the job is killed,
# so the rule is structural rather than a timeout somewhere.
PAGEABLE_SUBCOMMANDS = (
    "blame",
    "branch",
    "diff",
    "grep",
    "log",
    "reflog",
    "shortlog",
    "show",
    "tag",
    "whatchanged",
)

_GIT_INVOCATION = re.compile(r"(?<![\w./-])git\s")
_CONSUMES_OUTPUT = ("$(", "`", ">", "|")
_SUPPRESSES_PAGER = ("--no-pager", "core.pager")


def _logical_lines(script: Path) -> list[str]:
    """Return the script's commands with backslash continuations joined into one line.

    The invocation this rule exists for spans two physical lines, so a scanner reading
    them separately sees `exec git -c ...` on one and `diff --check` on the next and
    matches neither.
    """
    joined = script.read_text(encoding="utf-8").replace("\\\n", " ")
    return [line.strip() for line in joined.splitlines()]


def _starts_a_pager(line: str) -> bool:
    """Whether this command runs a pageable git subcommand with the terminal inherited."""
    if line.startswith("#") or not _GIT_INVOCATION.search(line):
        return False
    words = re.findall(r"[\w-]+", line)
    if not any(subcommand in words for subcommand in PAGEABLE_SUBCOMMANDS):
        return False
    if any(marker in line for marker in _CONSUMES_OUTPUT):
        return False
    return not any(marker in line for marker in _SUPPRESSES_PAGER)


class TerminalSafetyTests(QualityGateTestCase):
    def test_the_pushed_range_gate_completes_when_its_output_is_a_terminal(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        tracked = repository / "tracked.txt"
        tracked.write_text("root\n", encoding="utf-8")
        self.commit_all(repository, "root")
        base = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        tracked.write_text("second\n", encoding="utf-8")
        self.commit_all(repository, "second")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()

        # Act
        result = self.run_hook_on_terminal(
            "check-commit-range.sh",
            repository,
            environment={"QUALITY_DIFF_BASE": base, "QUALITY_DIFF_HEAD": head},
        )

        # Assert
        self.assertFalse(
            result.timed_out,
            f"the gate never exited with its output on a terminal: {result.output!r}",
        )
        self.assertEqual(0, result.returncode, result.output)

    def test_no_hook_script_lets_git_start_a_pager(self) -> None:
        # Arrange
        scripts = sorted(HOOKS_DIRECTORY.rglob("*.sh"))

        # Act
        offenders = [
            f"{script.relative_to(HOOKS_DIRECTORY)}: {line}"
            for script in scripts
            for line in _logical_lines(script)
            if _starts_a_pager(line)
        ]

        # Assert
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
