"""One-shot, network-free normalized-recording validator."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from aerial_rescue_recorder.recording import (
    MAX_RECORDING_BYTES,
    RecordingError,
    RecordingRefusal,
    write_validated_replay,
)


def _read_regular_file(path: Path) -> bytes:
    """Read one bounded regular file without following a symbolic link."""
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RecordingError(RecordingRefusal.INPUT_PATH) from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RecordingError(RecordingRefusal.INPUT_PATH)
        if details.st_size > MAX_RECORDING_BYTES:
            raise RecordingError(RecordingRefusal.SIZE)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAX_RECORDING_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > MAX_RECORDING_BYTES:
        raise RecordingError(RecordingRefusal.SIZE)
    return raw


def validate_file(input_path: Path, output_directory: Path) -> Path:
    """Validate one bounded input and atomically create the fixed replay bundle."""
    return write_validated_replay(_read_regular_file(input_path), output_directory)


def _parse(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_recorder.validator",
        description="Validate one normalized dashboard recording into an isolated replay bundle.",
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-directory", required=True)
    return parser.parse_args(arguments)


def main(
    arguments: Sequence[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Validate once, reporting only a structured and redacted outcome."""
    parsed = _parse(arguments)
    try:
        validate_file(Path(parsed.input), Path(parsed.output_directory))
    except RecordingError as failure:
        error.write(f"FAILED: {failure.refusal.value}\n")
        return 1
    out.write("validated replay ready\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
