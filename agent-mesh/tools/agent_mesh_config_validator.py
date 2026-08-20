"""Offline semantic validation for pinned Solace Agent Mesh configuration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True, order=True)
class ValidationIssue:
    """One deterministic, value-redacted configuration diagnostic."""

    path: Path
    location: str
    rule: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """The validation outcome for one configuration file."""

    path: Path
    app_count: int
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        """Return whether the file has no validation issues."""
        return not self.issues


def validate_paths(
    paths: Sequence[Path],
    *,
    config_root: Path,
    env_template: Path,
) -> tuple[ValidationResult, ...]:
    """Validate configuration paths without starting the Agent Mesh runtime."""
    raise NotImplementedError


def run(
    arguments: Sequence[str],
    *,
    project_root: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run the deterministic command interface against one Agent Mesh project."""
    raise NotImplementedError


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the validator from the current Agent Mesh project directory."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
