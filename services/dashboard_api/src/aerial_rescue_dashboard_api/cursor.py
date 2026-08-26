"""Opaque HMAC-SHA256 cursors bound to one API runtime and dashboard run."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from aerial_rescue_contracts.canonical import canonical_bytes

_KEY_BYTES = 32
_CURSOR_HEX_CHARACTERS = 64


@dataclass(frozen=True)
class CursorCodec:
    """Issue and reconstruct bounded opaque native SSE cursors."""

    runtime_id: str
    key: bytes

    def __post_init__(self) -> None:
        """Require the independently generated 256-bit cursor key."""
        if len(self.key) != _KEY_BYTES:
            message = "cursor HMAC key must contain exactly 32 bytes"
            raise ValueError(message)

    def issue(self, run_id: str, audit_ordinal: int) -> str:
        """Return the lowercase capability for one exact runtime/run/ordinal tuple."""
        if audit_ordinal < 0:
            message = "cursor audit ordinal cannot be negative"
            raise ValueError(message)
        covered = canonical_bytes(
            {
                "auditOrdinal": audit_ordinal,
                "cursorVersion": 1,
                "runId": run_id,
                "runtimeId": self.runtime_id,
            }
        )
        return hmac.new(self.key, covered, hashlib.sha256).hexdigest()

    def resolve(
        self,
        cursor: str,
        run_id: str,
        *,
        oldest_ordinal: int,
        latest_ordinal: int,
    ) -> int | None:
        """Resolve only a cursor inside the caller's bounded reconstruction window."""
        if oldest_ordinal < 0 or latest_ordinal < oldest_ordinal:
            message = "cursor reconstruction bounds are invalid"
            raise ValueError(message)
        if len(cursor) != _CURSOR_HEX_CHARACTERS or any(
            character not in "0123456789abcdef" for character in cursor
        ):
            return None
        for ordinal in range(oldest_ordinal, latest_ordinal + 1):
            if hmac.compare_digest(cursor, self.issue(run_id, ordinal)):
                return ordinal
        return None
