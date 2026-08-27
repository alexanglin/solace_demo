"""Bounded, rotation-aware reads from the broker-retained event log."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast

EVENT_LOG_POLL_SECONDS: Final = 1.0
MAXIMUM_ROTATION_GAP_POLLS: Final = 30


@dataclass(frozen=True, slots=True)
class EventLogFollowBounds:
    """The complete polling and temporary-rotation-gap bounds."""

    poll_seconds: float = EVENT_LOG_POLL_SECONDS
    maximum_gap_polls: int = MAXIMUM_ROTATION_GAP_POLLS

    def __post_init__(self) -> None:
        """Refuse a busy poll or an unbounded negative gap."""
        if self.poll_seconds <= 0 or self.maximum_gap_polls < 0:
            raise EventLogSourceError(EventLogSourceRefusal.INVALID_BOUND)


class EventLogSourceRefusal(StrEnum):
    """Secret-independent failures exposed by the retained-log boundary."""

    UNAVAILABLE = "retained broker event log is unavailable"
    ROTATION_GAP = "retained broker event log rotation gap exceeded its bound"
    INVALID_BOUND = "retained broker event log read bound is invalid"


class EventLogSourceError(OSError):
    """A redacted filesystem refusal which never retains a host path."""

    refusal: EventLogSourceRefusal

    def __init__(self, refusal: EventLogSourceRefusal) -> None:
        """Retain only the closed refusal category."""
        super().__init__(refusal.value)
        self.refusal = refusal


class BinaryOpener(Protocol):
    """The injected filesystem capability used for one read-only open."""

    def __call__(self, path: Path, mode: str, /) -> BinaryIO:
        """Open ``path`` in binary read mode."""


def _open_binary(path: Path, mode: str) -> BinaryIO:
    """Adapt ``Path.open`` to the source's narrow binary opener."""
    return cast("BinaryIO", path.open(mode))


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    """Return the stable device/inode identity used to detect rename rotation."""
    return (metadata.st_dev, metadata.st_ino)


class RetainedEventLogSource:
    """Follow one regular append-only log across rename and copy-truncate rotation."""

    def __init__(
        self,
        path: Path,
        *,
        running: Callable[[], bool],
        wait: Callable[[float], None] = time.sleep,
        open_file: BinaryOpener = _open_binary,
        bounds: EventLogFollowBounds | None = None,
    ) -> None:
        """Retain inert capabilities; the first bounded read performs the open."""
        self._path = path
        self._running = running
        self._wait = wait
        self._open_file = open_file
        self._bounds = EventLogFollowBounds() if bounds is None else bounds
        self._stream: BinaryIO | None = None
        self._stream_identity: tuple[int, int] | None = None
        self._pending = bytearray()
        self._gap_polls = 0

    def _open(self) -> None:
        """Open the current log and retain no path on a typed refusal."""
        try:
            stream = self._open_file(self._path, "rb")
            metadata = os.fstat(stream.fileno())
        except OSError as error:
            raise EventLogSourceError(EventLogSourceRefusal.UNAVAILABLE) from error
        self._stream = stream
        self._stream_identity = _identity(metadata)
        self._gap_polls = 0

    def _close_stream(self) -> None:
        """Close only the currently owned descriptor."""
        stream = self._stream
        self._stream = None
        self._stream_identity = None
        if stream is not None:
            stream.close()

    def close(self) -> None:
        """Release the descriptor and discard any incomplete shutdown fragment."""
        self._pending.clear()
        self._close_stream()

    def _return_pending(self) -> bytes:
        """Move the current bounded fragment to the caller."""
        payload = bytes(self._pending)
        self._pending.clear()
        return payload

    def _path_metadata(self) -> os.stat_result | None:
        """Read current path metadata, bounding a temporary rename gap."""
        try:
            metadata = self._path.stat()
        except FileNotFoundError as error:
            if self._gap_polls >= self._bounds.maximum_gap_polls:
                raise EventLogSourceError(EventLogSourceRefusal.ROTATION_GAP) from error
            self._gap_polls += 1
            self._wait(self._bounds.poll_seconds)
            return None
        except OSError as error:
            raise EventLogSourceError(EventLogSourceRefusal.UNAVAILABLE) from error
        self._gap_polls = 0
        return metadata

    def _refresh_at_eof(self) -> bytes | None:
        """Rewind truncation, rebind rotation, or pace one unchanged EOF poll."""
        stream = self._stream
        if stream is None or self._stream_identity is None:
            return None
        metadata = self._path_metadata()
        if metadata is None:
            return None
        if _identity(metadata) != self._stream_identity:
            fragment = self._return_pending() if self._pending else None
            self._close_stream()
            return fragment
        try:
            position = stream.tell()
            if metadata.st_size < position:
                stream.seek(0)
                return self._return_pending() if self._pending else None
        except OSError as error:
            raise EventLogSourceError(EventLogSourceRefusal.UNAVAILABLE) from error
        self._wait(self._bounds.poll_seconds)
        return None

    def _read_available(self, limit: int) -> bytes | None:
        """Return a complete chunk, an empty partial marker, or current EOF."""
        stream = self._stream
        if stream is None:
            raise EventLogSourceError(EventLogSourceRefusal.UNAVAILABLE)
        try:
            chunk = stream.readline(limit - len(self._pending))
        except OSError as error:
            raise EventLogSourceError(EventLogSourceRefusal.UNAVAILABLE) from error
        if not chunk:
            return None
        self._pending.extend(chunk)
        if self._pending.endswith(b"\n") or len(self._pending) == limit:
            return self._return_pending()
        return b""

    def readline(self, limit: int = -1) -> bytes:
        """Return one complete bounded line, or EOF only after requested shutdown."""
        if limit <= 0:
            raise EventLogSourceError(EventLogSourceRefusal.INVALID_BOUND)
        while self._running():
            if self._stream is None:
                self._open()
            payload = self._read_available(limit)
            if payload is not None:
                if payload:
                    return payload
                continue
            fragment = self._refresh_at_eof()
            if fragment is not None:
                return fragment
        return b""
