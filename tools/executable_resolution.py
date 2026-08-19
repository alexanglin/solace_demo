"""Fail-closed resolution for required subprocess executables."""

from __future__ import annotations

import shutil
from pathlib import Path


def required_executable(name: str) -> str:
    """Return an absolute executable path or raise when it is unavailable."""
    candidate = shutil.which(name)
    if candidate is None:
        message = f"required executable is unavailable: {name}"
        raise RuntimeError(message)
    try:
        resolved = Path(candidate).resolve(strict=True)
    except OSError as error:
        message = f"required executable is unavailable: {name}"
        raise RuntimeError(message) from error
    if not resolved.is_file():
        message = f"required executable is unavailable: {name}"
        raise RuntimeError(message)
    return str(resolved)
