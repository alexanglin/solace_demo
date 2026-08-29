"""Stable committed-fixture discovery for source and generated mutation-test copies."""

from __future__ import annotations

from pathlib import Path

_MISSING_ROOT = "committed golden fixture root not found"


def repository_root(source: Path) -> Path:
    """Find the repository root above a source or mutmut-copy test path."""
    for candidate in source.resolve().parents:
        if (candidate / "fixtures" / "golden" / "v1").is_dir():
            return candidate
    raise RuntimeError(_MISSING_ROOT)
