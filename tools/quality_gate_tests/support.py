from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.executable_resolution import required_executable

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIRECTORY = REPOSITORY_ROOT / "scripts" / "hooks"


def hermetic_git_environment() -> dict[str, str]:
    """Return the ambient environment without the caller's inherited Git context.

    Git exports ``GIT_DIR``, ``GIT_INDEX_FILE``, and related variables while it runs a
    hook. Inheriting them aims a fixture command at the repository that invoked the hook
    instead of the temporary repository under test, so they are removed here.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


class QualityGateTestCase(unittest.TestCase):
    """Shared process and temporary-repository fixtures for quality-gate tests."""

    def temporary_directory(self) -> Path:
        """Return a directory that remains available through the test's Assert phase."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def temporary_repository(self) -> Path:
        """Return an initialized repository with a deterministic test identity."""
        repository = self.temporary_directory()
        self.git(repository, "init", "--quiet")
        self.git(repository, "config", "user.email", "tests@example.invalid")
        self.git(repository, "config", "user.name", "Quality Gate Tests")
        return repository

    def temporary_file(self, name: str, content: str) -> Path:
        path = self.temporary_directory() / name
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def read_repository_text(relative_path: str) -> str:
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    @staticmethod
    def git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        git_executable = required_executable("git")
        return subprocess.run(
            (git_executable, "-C", str(repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
            env=hermetic_git_environment(),
        )

    @classmethod
    def commit_all(cls, repository: Path, message: str) -> None:
        cls.git(repository, "add", ".")
        cls.git(repository, "commit", "--quiet", "-m", message)

    @staticmethod
    def run_hook(
        hook_name: str,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        hook_environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
        if environment is not None:
            hook_environment.update(environment)
        return subprocess.run(
            ("/bin/sh", str(HOOKS_DIRECTORY / hook_name), *arguments),
            cwd=repository,
            env=hook_environment,
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def write_argument_recorder(path: Path) -> None:
        path.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)

    def install_argument_recorder(
        self,
        repository: Path,
        executable_name: str,
        output_name: str,
    ) -> tuple[Path, dict[str, str]]:
        executable_directory = repository / "bin"
        executable_directory.mkdir(exist_ok=True)
        self.write_argument_recorder(executable_directory / executable_name)
        output = repository / output_name
        environment = {
            "PATH": f"{executable_directory}:/usr/bin:/bin",
            "QUALITY_ARGUMENTS_FILE": str(output),
        }
        return output, environment

    def assert_hooks_failed(
        self,
        hook_names: tuple[str, ...],
        results: tuple[subprocess.CompletedProcess[str], ...],
        expected_error: str,
    ) -> None:
        for hook_name, result in zip(hook_names, results, strict=True):
            with self.subTest(hook=hook_name):
                self.assertNotEqual(0, result.returncode)
                self.assertIn(expected_error, result.stderr)

    def assert_hook_failed(
        self,
        result: subprocess.CompletedProcess[str],
        expected_error: str,
    ) -> None:
        self.assertNotEqual(0, result.returncode)
        self.assertIn(expected_error, result.stderr)

    def assert_hook_succeeded(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(0, result.returncode, result.stderr)

    def assert_hooks_succeeded(
        self,
        hook_names: tuple[str, ...],
        results: tuple[subprocess.CompletedProcess[str], ...],
    ) -> None:
        for hook_name, result in zip(hook_names, results, strict=True):
            with self.subTest(hook=hook_name):
                self.assertEqual(0, result.returncode, result.stderr)


class MutationGateTestCase(QualityGateTestCase):
    """Fixture writers shared by mutation-result and survivor-registry tests."""

    @staticmethod
    def mutation_statuses(*, killed: int, survived: int) -> dict[str, int | None]:
        statuses: dict[str, int | None] = {
            f"src.example.x_rule__mutmut_{index}": 1 for index in range(1, killed + 1)
        }
        statuses.update(
            {
                f"src.example.x_rule__mutmut_{index}": 0
                for index in range(killed + 1, killed + survived + 1)
            }
        )
        return statuses

    @staticmethod
    def write_mutation_metadata(
        root: Path,
        member: str,
        statuses: dict[str, int | None],
        *,
        module: str = "example",
    ) -> None:
        metadata = root / member / "mutants" / "src" / f"{module}.py.meta"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            json.dumps(
                {
                    "exit_code_by_key": statuses,
                    "hash_by_function_name": {},
                    "type_check_error_by_key": {},
                    "durations_by_key": {},
                    "estimated_durations_by_key": {},
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def write_survivor_registry(
        root: Path,
        *,
        records: tuple[tuple[str, str], ...] = (),
        expires_on: str = "2026-09-18",
    ) -> None:
        lines = ["format = 1"]
        for member, mutant in records:
            lines.extend(
                (
                    "",
                    "[[survivors]]",
                    f"member = {json.dumps(member)}",
                    f"mutant = {json.dumps(mutant)}",
                    'reason = "Equivalent boundary-preserving mutation reviewed manually."',
                    'reviewed_by = "Alex Anglin"',
                    'reviewed_on = "2026-08-19"',
                    f"expires_on = {json.dumps(expires_on)}",
                )
            )
        (root / "mutation-survivors.toml").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
