"""Atomic recorder unit of work over inbox, source-event, and append-only audit repositories."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, cast

from aerial_rescue_store.audit import AuditRecord, OrdinalSession, append
from aerial_rescue_store.inbox import (
    InboxIdentity,
    InboxOutcome,
    InboxSession,
    claim,
    complete,
)
from aerial_rescue_store.processing.source_events import (
    SourceEventDecision,
    SourceEventSession,
    StoredSourceEvent,
)
from aerial_rescue_store.processing.source_events import (
    record as record_source_event,
)
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class RecordingTransaction:
    """Purpose-specific recorder operations sharing one injected SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the session whose caller-owned transaction makes the effects atomic."""
        self._session = session

    async def claim_inbox(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim one broker identity or return its exact prior durable result."""
        return await claim(cast("InboxSession", self._session), identity)

    async def record_source_event(self, event: StoredSourceEvent) -> SourceEventDecision:
        """Store one complete source event without overwriting immutable identity."""
        return await record_source_event(cast("SourceEventSession", self._session), event)

    async def append_audit(self, record: AuditRecord) -> int:
        """Append one audit record at the ordinal issued in this transaction."""
        return await append(cast("OrdinalSession", self._session), record)

    async def complete_inbox(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete this transaction's exact inbox claim with its durable result."""
        await complete(cast("InboxSession", self._session), identity, result, processed_at)


class RecordingTransactions:
    """Construct fresh recorder transactions over one injected session factory."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the lazy factory without opening a connection."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[RecordingTransaction]:
        """Return a unit of work that commits all recorder effects or rolls all back."""
        return _open(self._factory)


@asynccontextmanager
async def _open(
    factory: Callable[[], AsyncSession],
) -> AsyncIterator[RecordingTransaction]:
    """Adapt the shared transaction boundary to recorder-purpose operations."""
    async with transaction(factory) as session:
        yield RecordingTransaction(session)
