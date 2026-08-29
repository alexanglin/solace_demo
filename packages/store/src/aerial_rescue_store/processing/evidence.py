"""Atomic evidence processing over the package-owned SQLAlchemy repositories."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, cast

from aerial_rescue_domain.outbox import OutboxEvent, OutboxState

from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    ApplicationOutboxSession,
    StagedApplicationEvent,
    pending,
    reconciliation,
    record_publication,
    stage,
)
from aerial_rescue_store.audit import AuditRecord, OrdinalSession, append
from aerial_rescue_store.evidence import (
    EvidenceSequenceSession,
    EvidenceSession,
    StoredEvidenceDecision,
    StoredEvidenceItem,
    latest_sequence,
    record_decision,
    record_item,
)
from aerial_rescue_store.inbox import (
    InboxIdentity,
    InboxOutcome,
    InboxSession,
    claim,
    complete,
)
from aerial_rescue_store.processing.source_evidence import (
    SourceEvidenceSession,
    StoredSourceEvidence,
    load_source_evidence,
)
from aerial_rescue_store.proposals import (
    ProposalSession,
    StoredProposal,
)
from aerial_rescue_store.proposals import load as load_proposal
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class EvidenceProcessingTransaction:
    """Evidence operations sharing one caller-owned SQLAlchemy transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Retain the one session whose commit makes every effect atomic."""
        self._session = session

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim new broker work or return its exact committed prior result."""
        return await claim(cast("InboxSession", self._session), identity)

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Load the immutable proposal authority inside this transaction."""
        return await load_proposal(cast("ProposalSession", self._session), proposal_id)

    async def load_source(
        self,
        mission_id: str,
        source_event_id: str,
    ) -> StoredSourceEvidence | None:
        """Load complete canonical source bytes and ordered provenance facts."""
        return await load_source_evidence(
            cast("SourceEvidenceSession", self._session),
            mission_id,
            source_event_id,
        )

    async def record_item(self, item: StoredEvidenceItem) -> None:
        """Persist one immutable evidence item."""
        await record_item(cast("EvidenceSession", self._session), item)

    async def record_decision(self, decision: StoredEvidenceDecision) -> None:
        """Persist one append-only evidence decision."""
        await record_decision(cast("EvidenceSession", self._session), decision)

    async def append_audit(self, record: AuditRecord) -> int:
        """Append one authoritative audit row in the same transaction."""
        return await append(cast("OrdinalSession", self._session), record)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Stage one exact application publication before commit."""
        await stage(cast("ApplicationOutboxSession", self._session), event)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the inbox claim with the exact durable processing result."""
        await complete(cast("InboxSession", self._session), identity, result, processed_at)


class EvidenceProcessingTransactions:
    """Construct fresh evidence transactions over one lazy session factory."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the factory without opening a connection or starting a transaction."""
        self._factory = factory

    def open(self) -> AbstractAsyncContextManager[EvidenceProcessingTransaction]:
        """Return a transaction that commits every evidence effect or none of them."""
        return _open(self._factory)


class EvidenceApplicationOutbox:
    """Run recovery reads and per-row outcomes in independent transactions."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the lazy session factory used after a service restart."""
        self._factory = factory

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Read one bounded committed batch and release its transaction."""
        async with transaction(self._factory) as session:
            return await pending(cast("ApplicationOutboxSession", session), producer)

    async def reconciliation(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Read ambiguous rows as evidence only; never make them publishable."""
        async with transaction(self._factory) as session:
            return await reconciliation(cast("ApplicationOutboxSession", session), producer)

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Commit one publication outcome without changing any neighboring row."""
        async with transaction(self._factory) as session:
            await record_publication(
                cast("ApplicationOutboxSession", session),
                identity,
                OutboxState.STAGED,
                event,
                confirmed_at,
            )


class EvidenceSequenceReader:
    """Recover the next producer sequence from committed decision/audit pairs."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        """Retain the lazy SQLAlchemy session factory."""
        self._factory = factory

    async def starting_sequence(self) -> int:
        """Return zero for an empty store or two after the latest decision sequence."""
        async with transaction(self._factory) as session:
            latest = await latest_sequence(cast("EvidenceSequenceSession", session))
        return 0 if latest is None else latest + 2


@asynccontextmanager
async def _open(
    factory: Callable[[], AsyncSession],
) -> AsyncIterator[EvidenceProcessingTransaction]:
    """Adapt the shared commit-or-rollback boundary to evidence operations."""
    async with transaction(factory) as session:
        yield EvidenceProcessingTransaction(session)
