"""Bounded atomic freshness lease shared by two local readiness consumers."""

from __future__ import annotations

import os
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final, cast

from aerial_rescue_contracts import canonical

LEASE_VERSION: Final = "recorder-readiness/v1"
REFRESH_SECONDS: Final = 2
EXPIRY_SECONDS: Final = 10
MAXIMUM_LEASE_BYTES: Final = 256
_DIRECTORY_UNAVAILABLE: Final = "readiness directory is unavailable"


class LeaseRefusal(Enum):
    """Why a freshness lease does not prove a current active process."""

    MISSING = "lease is absent"
    PATH = "lease is not one regular non-symlink file"
    SIZE = "lease exceeds its byte bound"
    DOCUMENT = "lease is not one exact canonical document"
    VERSION = "lease version is unsupported"
    TIMESTAMP = "lease timestamp is not a valid past integer epoch"
    STALE = "lease is older than the accepted freshness bound"


class FreshnessLeaseError(RuntimeError):
    """The active process could not publish its bounded freshness claim."""


def epoch_seconds() -> int:
    """Return whole wall-clock seconds for a cross-process freshness witness."""
    return int(time.time())


def monotonic_seconds() -> int:
    """Return whole monotonic seconds for local refresh scheduling only."""
    return int(time.monotonic())


@dataclass
class FreshnessLease:
    """Atomically maintain one bounded freshness document on a shared tmpfs."""

    path: Path
    epoch_source: Callable[[], int] = epoch_seconds
    monotonic_source: Callable[[], int] = monotonic_seconds
    _last_refresh: int | None = field(default=None, init=False)

    def activate(self) -> None:
        """Replace stale process residue after the owning runtime has become operational."""
        self.close()
        self._write()
        self._last_refresh = self.monotonic_source()

    def refresh_if_due(self) -> None:
        """Refresh at most once per accepted interval."""
        if self._last_refresh is None:
            message = "freshness lease is not active"
            raise FreshnessLeaseError(message)
        now = self.monotonic_source()
        elapsed = now - self._last_refresh
        if 0 <= elapsed < REFRESH_SECONDS:
            return
        self._write()
        self._last_refresh = now

    def close(self) -> None:
        """Withdraw this process's claim without following any replacement symlink."""
        with suppress(IsADirectoryError, NotADirectoryError):
            self.path.unlink(missing_ok=True)
        self._last_refresh = None

    def _write(self) -> None:
        """Write canonical bytes then atomically replace the public lease name."""
        parent = self.path.parent
        try:
            details = parent.lstat()
        except OSError as invalid:
            raise FreshnessLeaseError(_DIRECTORY_UNAVAILABLE) from invalid
        if not stat.S_ISDIR(details.st_mode):
            raise FreshnessLeaseError(_DIRECTORY_UNAVAILABLE)
        raw = canonical.canonical_bytes(
            {
                "readinessVersion": LEASE_VERSION,
                "updatedAtEpochSeconds": self.epoch_source(),
            }
        )
        descriptor, temporary_name = tempfile.mkstemp(dir=parent, prefix=".recorder-ready-")
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o644)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)


def check_lease(path: Path, *, now_epoch_seconds: int) -> LeaseRefusal | None:
    """Return no refusal only for one fresh, strict, canonical regular lease file."""
    raw_or_refusal = _read_lease(path)
    if isinstance(raw_or_refusal, LeaseRefusal):
        return raw_or_refusal
    raw = raw_or_refusal
    try:
        decoded = canonical.decode(raw)
        if canonical.canonical_bytes(decoded) != raw:
            return LeaseRefusal.DOCUMENT
    except canonical.CanonicalizationError:
        return LeaseRefusal.DOCUMENT
    return _document_refusal(decoded, now_epoch_seconds)


def _document_refusal(value: object, now_epoch_seconds: int) -> LeaseRefusal | None:
    """Apply the exact lease shape and integer freshness rules."""
    if not isinstance(value, Mapping) or set(value) != {
        "readinessVersion",
        "updatedAtEpochSeconds",
    }:
        return LeaseRefusal.DOCUMENT
    document = cast("Mapping[str, object]", value)
    if document["readinessVersion"] != LEASE_VERSION:
        return LeaseRefusal.VERSION
    updated = document["updatedAtEpochSeconds"]
    if (
        not isinstance(updated, int)
        or isinstance(updated, bool)
        or updated < 0
        or updated > now_epoch_seconds
    ):
        return LeaseRefusal.TIMESTAMP
    if now_epoch_seconds - updated > EXPIRY_SECONDS:
        return LeaseRefusal.STALE
    return None


def _read_lease(path: Path) -> bytes | LeaseRefusal:
    """Read a bounded regular lease without following a symbolic link."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return LeaseRefusal.MISSING
    except OSError:
        return LeaseRefusal.PATH
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            return LeaseRefusal.PATH
        if details.st_size > MAXIMUM_LEASE_BYTES:
            return LeaseRefusal.SIZE
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAXIMUM_LEASE_BYTES + 1)
    except OSError:
        return LeaseRefusal.PATH
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return LeaseRefusal.SIZE if len(raw) > MAXIMUM_LEASE_BYTES else raw
