"""Recorder-specific healthcheck adapter over the shared active freshness lease."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, TextIO

from aerial_rescue_observability.freshness import (
    EXPIRY_SECONDS,
    LEASE_VERSION,
    MAXIMUM_LEASE_BYTES,
    REFRESH_SECONDS,
    FreshnessLease,
    FreshnessLeaseError,
    LeaseRefusal,
    check_lease,
    epoch_seconds,
)

__all__ = (
    "EXPIRY_SECONDS",
    "LEASE_VERSION",
    "MAXIMUM_LEASE_BYTES",
    "REFRESH_SECONDS",
    "LeaseRefusal",
    "ReadinessLease",
    "ReadinessLeaseError",
    "check_lease",
    "main",
    "readiness_reasons",
)

PUBLIC_UNAVAILABLE_REASON: Final = "recorder-capture-unavailable"
ReadinessLease = FreshnessLease
ReadinessLeaseError = FreshnessLeaseError


def _epoch_seconds() -> int:
    """Return the shared whole-second wall clock through a patchable process seam."""
    return epoch_seconds()


def readiness_reasons(path: Path, *, now_epoch_seconds: int | None = None) -> tuple[str, ...]:
    """Map every private lease refusal to one stable non-sensitive public reason."""
    now = _epoch_seconds() if now_epoch_seconds is None else now_epoch_seconds
    refusal = check_lease(path, now_epoch_seconds=now)
    return () if refusal is None else (PUBLIC_UNAVAILABLE_REASON,)


def _parse(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_recorder.readiness",
        description="Check the bounded recorder freshness lease.",
    )
    parser.add_argument("--check", required=True)
    return parser.parse_args(arguments)


def main(
    arguments: Sequence[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Return a redacted process health status for Compose."""
    parsed = _parse(arguments)
    if readiness_reasons(Path(parsed.check)):
        error.write("not ready\n")
        return 1
    out.write("ready\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
