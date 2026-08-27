"""Atomic evidence-service source ingestion over migrated SQLAlchemy repositories."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, cast

from aerial_rescue_store.inbox import InboxIdentity, InboxOutcome, InboxSession, claim, complete
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import (
    SourceEvidenceWriteSession,
    StoredSourceEvidenceFact,
    record_source_evidence,
)
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class SourceProcessingTransaction:
    """Source inbox and immutable provenance operations in one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the caller-owned SQLAlchemy session."""
        self._session = session

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim new source work or return its exact prior result."""
        return await claim(cast("InboxSession", self._session), identity)

    async def record_source(
        self,
        event: StoredSourceEvent,
        facts: tuple[StoredSourceEvidenceFact, ...],
    ) -> None:
        """Persist the exact event and complete initial sensor fact set."""
        await record_source_evidence(
            cast("SourceEvidenceWriteSession", self._session),
            event,
            facts,
        )

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the inbox claim with the exact durable source result."""
        await complete(cast("InboxSession", self._session), identity, result, processed_at)


class SourceProcessingTransactions:
    """Construct fresh atomic source-ingestion transactions lazily."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the lazy SQLAlchemy session factory."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[SourceProcessingTransaction]:
        """Return one source transaction without opening it early."""
        return _open(self._factory)


@asynccontextmanager
async def _open(
    factory: Callable[[], AsyncSession],
) -> AsyncIterator[SourceProcessingTransaction]:
    """Adapt the shared commit-or-rollback boundary to source ingestion."""
    async with transaction(factory) as session:
        yield SourceProcessingTransaction(session)
