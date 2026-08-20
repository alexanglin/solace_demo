from __future__ import annotations

import contextlib
import io
import subprocess
import tomllib
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

import pytest

from tools import executable_resolution
from tools.aaa_checker import checker
from tools.quality_gate_tests import support
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class ExecutableResolutionTests(QualityGateTestCase):
    def test_required_executable_returns_a_resolved_absolute_path(self) -> None:
        # Arrange
        executable = self.temporary_directory() / "bin" / "git"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)

        # Act
        with mock.patch("tools.executable_resolution.shutil.which", return_value=str(executable)):
            resolved = executable_resolution.required_executable("git")

        # Assert
        self.assertEqual(str(executable.resolve()), resolved)
        self.assertTrue(Path(resolved).is_absolute())

    def test_required_executable_fails_closed_when_missing(self) -> None:
        # Arrange
        expected_message = "required executable is unavailable: git"

        # Act
        with (
            mock.patch("tools.executable_resolution.shutil.which", return_value=None),
            pytest.raises(RuntimeError) as captured,
        ):
            executable_resolution.required_executable("git")

        # Assert
        self.assertEqual(expected_message, str(captured.value))

    def test_required_executable_fails_closed_for_a_dangling_symlink(self) -> None:
        # Arrange
        link = self.temporary_directory() / "git"
        link.symlink_to(link.parent / "absent")
        expected_message = "required executable is unavailable: git"

        # Act
        with (
            mock.patch("tools.executable_resolution.shutil.which", return_value=str(link)),
            pytest.raises(RuntimeError) as captured,
        ):
            executable_resolution.required_executable("git")

        # Assert
        self.assertEqual(expected_message, str(captured.value))
        self.assertIsInstance(captured.value.__cause__, FileNotFoundError)

    def test_required_executable_fails_closed_for_a_directory(self) -> None:
        # Arrange
        directory = self.temporary_directory() / "git"
        directory.mkdir()
        expected_message = "required executable is unavailable: git"

        # Act
        with (
            mock.patch("tools.executable_resolution.shutil.which", return_value=str(directory)),
            pytest.raises(RuntimeError) as captured,
        ):
            executable_resolution.required_executable("git")

        # Assert
        self.assertEqual(expected_message, str(captured.value))
        self.assertIsNone(captured.value.__cause__)

    def test_aaa_repository_discovery_uses_the_resolved_git_path(self) -> None:
        # Arrange
        result = subprocess.CompletedProcess[bytes]((), 0, stdout=b"", stderr=b"")

        # Act
        with (
            mock.patch.object(
                checker,
                "required_executable",
                return_value="/usr/bin/git",
            ) as resolver,
            mock.patch(
                "tools.aaa_checker.checker.subprocess.run",
                return_value=result,
            ) as run,
        ):
            paths = checker.repository_source_paths(REPOSITORY_ROOT)

        # Assert
        self.assertEqual((), paths)
        resolver.assert_called_once_with("git")
        self.assertEqual("/usr/bin/git", run.call_args.args[0][0])

    def test_aaa_repository_discovery_fails_closed_without_git(self) -> None:
        # Arrange
        output = io.StringIO()

        # Act
        with (
            mock.patch.object(
                checker,
                "required_executable",
                side_effect=RuntimeError("required executable is unavailable: git"),
            ),
            contextlib.redirect_stderr(output),
        ):
            status = checker.main(())

        # Assert
        self.assertEqual(2, status)
        self.assertIn("AAA014", output.getvalue())
        self.assertIn("required executable is unavailable: git", output.getvalue())

    def test_shared_git_helper_uses_the_resolved_git_path(self) -> None:
        # Arrange
        result = subprocess.CompletedProcess[str]((), 0, stdout="", stderr="")

        # Act
        with (
            mock.patch.object(
                support,
                "required_executable",
                return_value="/usr/bin/git",
            ) as resolver,
            mock.patch(
                "tools.quality_gate_tests.support.subprocess.run",
                return_value=result,
            ) as run,
        ):
            completed = self.git(self.temporary_directory() / "repository", "status")

        # Assert
        self.assertIs(result, completed)
        resolver.assert_called_once_with("git")
        self.assertEqual("/usr/bin/git", run.call_args.args[0][0])


class RuffSubprocessPolicyTests(unittest.TestCase):
    def test_subprocess_waiver_scope_is_exact_and_has_no_s607(self) -> None:
        # Arrange
        configuration = cast(
            dict[str, object],
            tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")),
        )
        tool = cast(dict[str, object], configuration["tool"])
        ruff = cast(dict[str, object], tool["ruff"])
        lint = cast(dict[str, object], ruff["lint"])
        ignores = cast(list[str], lint["ignore"])
        per_file = cast(dict[str, list[str]], lint["per-file-ignores"])
        expected_global_ignores = {"D203", "D213", "PT009"}
        expected_s603 = {
            "tools/aaa_checker/checker.py",
            "tools/coverage_gate.py",
            "tools/quality_gate_tests/support.py",
            "tools/quality_gate_tests/test_diagram_integrity.py",
        }
        inline_markers = tuple(f"noqa: S{code}" for code in (603, 607))

        # Act
        global_subprocess_waivers = {"S603", "S607"}.intersection(ignores)
        s603_paths = {path for path, rules in per_file.items() if "S603" in rules}
        s607_paths = {path for path, rules in per_file.items() if "S607" in rules}
        inline_waivers = tuple(
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for root in ("tools", "packages", "services")
            for path in (REPOSITORY_ROOT / root).rglob("*.py")
            if any(marker in path.read_text(encoding="utf-8") for marker in inline_markers)
        )

        # Assert
        self.assertEqual(expected_global_ignores, set(ignores))
        self.assertEqual(set(), global_subprocess_waivers)
        self.assertEqual(expected_s603, s603_paths)
        self.assertEqual(set(), s607_paths)
        self.assertEqual((), inline_waivers)


if __name__ == "__main__":
    unittest.main()
