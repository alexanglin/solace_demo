"""Read-only exact-byte replay adapter over one isolated validator output."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Final

from aerial_rescue_dashboard_api.documents import (
    REPLAY_SCHEMA,
    validated_document,
)
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import ReplayPreparation, StorePort

MAXIMUM_REPLAY_BYTES: Final = 1024 * 1024


class ReplayFilePort:
    """Associate retained session identities with one immutable validator artifact."""

    def __init__(self, path: Path, store: StorePort) -> None:
        """Retain only the fixed artifact path and durable session lookup port."""
        self._path = path
        self._store = store

    async def readiness(self) -> tuple[str, ...]:
        """Require one validator-approved artifact without any live-service dependency."""
        try:
            self._bundle()
        except ApiError:
            return ("validated-replay-unavailable",)
        return ()

    async def prepare(self, scenario_id: str, scenario_revision: int) -> ReplayPreparation:
        """Return exact immutable bytes after validating their scenario identity."""
        raw, document = self._bundle()
        if document.get("scenarioId") != scenario_id:
            raise ApiError(ErrorCode.SCENARIO_NOT_FOUND)
        if document.get("scenarioRevision") != scenario_revision:
            raise ApiError(ErrorCode.SCENARIO_REVISION_MISMATCH)
        return ReplayPreparation(bundle_bytes=raw)

    async def bundle(self, session_id: str) -> bytes | None:
        """Serve exact validator bytes only for one durably retained replay session."""
        if not await self._store.replay_session_known(session_id):
            return None
        raw, _document = self._bundle()
        return raw

    def _bundle(self) -> tuple[bytes, dict[str, object]]:
        """Read a bounded regular artifact and validate it before every use."""
        try:
            details = self._path.lstat()
        except OSError as invalid:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from invalid
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAXIMUM_REPLAY_BYTES:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        try:
            raw = self._path.read_bytes()
        except OSError as invalid:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from invalid
        document = validated_document(
            REPLAY_SCHEMA,
            raw,
            maximum_bytes=MAXIMUM_REPLAY_BYTES,
        )
        return raw, dict(document)
