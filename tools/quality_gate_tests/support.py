from __future__ import annotations

import dataclasses
import json
import os
import pty
import select
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tools.executable_resolution import required_executable

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIRECTORY = REPOSITORY_ROOT / "scripts" / "hooks"

TERMINAL_TIMEOUT_SECONDS = 15.0
"""How long a script attached to a terminal may run before it is treated as wedged."""

_TERMINAL_KILL_SECONDS = 5.0
_TERMINAL_POLL_SECONDS = 0.1
_TERMINAL_READ_BYTES = 4096


@dataclasses.dataclass(frozen=True)
class TerminalResult:
    """The outcome of a script whose output was attached to a pseudo-terminal."""

    returncode: int | None
    output: str

    @property
    def timed_out(self) -> bool:
        """Whether the script had to be killed instead of exiting on its own."""
        return self.returncode is None


def _drain_terminal(
    controller: int,
    process: subprocess.Popen[bytes],
    deadline: float,
) -> bytes:
    """Read the terminal until the script closes it or the deadline passes."""
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        readable, _, _ = select.select([controller], [], [], _TERMINAL_POLL_SECONDS)
        if not readable:
            if process.poll() is not None:
                break
            continue
        try:
            chunk = os.read(controller, _TERMINAL_READ_BYTES)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _settle_terminal_process(process: subprocess.Popen[bytes]) -> int | None:
    """Return the exit status, killing the whole session when the script never exits.

    The session, not the process: a script that wedges does so because something it
    started is waiting for a keystroke, and killing only the script would leave that
    child holding the terminal open.
    """
    if process.poll() is None:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=_TERMINAL_KILL_SECONDS)
        return None
    return process.returncode


def hook_script(name: str) -> Path:
    """Return the hook script with this basename, wherever it sits under scripts/hooks/.

    The scripts are grouped into concern subdirectories, and the three named by accepted
    records keep their original path (docs/adr/0033-bound-directory-fan-out.md). A
    script's basename is its stable identity, so resolving by basename here keeps a
    regrouping from rewriting every caller. A name that resolves to no script, or to more
    than one, is an error rather than a silent skip.
    """
    direct = HOOKS_DIRECTORY / name
    if direct.is_file():
        return direct
    matches = sorted(HOOKS_DIRECTORY.glob(f"*/{name}"))
    if len(matches) != 1:
        message = f"hook script is not uniquely resolvable under scripts/hooks/: {name}"
        raise RuntimeError(message)
    return matches[0]


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
    def run_script(
        script: Path,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one project-owned shell script inside ``repository`` with a minimal environment."""
        script_environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
        if environment is not None:
            script_environment.update(environment)
        return subprocess.run(
            ("/bin/sh", str(script), *arguments),
            cwd=repository,
            env=script_environment,
            check=False,
            capture_output=True,
            text=True,
        )

    @classmethod
    def run_hook(
        cls,
        hook_name: str,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return cls.run_script(hook_script(hook_name), repository, arguments, environment)

    @staticmethod
    def run_script_on_terminal(
        script: Path,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
        timeout_seconds: float = TERMINAL_TIMEOUT_SECONDS,
    ) -> TerminalResult:
        """Run one project-owned shell script with its output attached to a terminal.

        ``run_script`` captures output through pipes, so a command whose behaviour
        depends on stdout being a terminal takes its pipe path here and its terminal
        path nowhere. pre-commit allocates a pseudo-terminal so hook output keeps its
        colour, and the runner's terminal is degraded, so the terminal path is the one
        the verification authority actually takes. ``TERM`` is fixed to the degraded
        value rather than inherited, so the check does not depend on the terminal the
        contributor happens to be sitting at.
        """
        script_environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TERM": "dumb"}
        if environment is not None:
            script_environment.update(environment)
        controller, follower = pty.openpty()
        try:
            try:
                process = subprocess.Popen(
                    ("/bin/sh", str(script), *arguments),
                    cwd=repository,
                    env=script_environment,
                    stdin=subprocess.DEVNULL,
                    stdout=follower,
                    stderr=follower,
                    start_new_session=True,
                )
            finally:
                os.close(follower)
            output = _drain_terminal(controller, process, time.monotonic() + timeout_seconds)
            returncode = _settle_terminal_process(process)
        finally:
            os.close(controller)
        return TerminalResult(returncode, output.decode("utf-8", errors="replace"))

    @classmethod
    def run_hook_on_terminal(
        cls,
        hook_name: str,
        repository: Path,
        arguments: tuple[str, ...] = (),
        environment: dict[str, str] | None = None,
    ) -> TerminalResult:
        return cls.run_script_on_terminal(
            hook_script(hook_name), repository, arguments, environment
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
