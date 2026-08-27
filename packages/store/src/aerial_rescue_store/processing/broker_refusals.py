"""Commit one bounded broker refusal through the package-owned transaction boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalOutcome,
    BrokerRefusalSession,
    StoredBrokerRefusal,
    record,
)
from aerial_rescue_store.session import transaction

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class BrokerRefusalRecorder:
    """Persist each malformed delivery in a fresh commit-or-rollback transaction."""

    def __init__(
        self,
        factory: Callable[[], AsyncSession],
        observed_at: Callable[[], str],
    ) -> None:
        """Retain a lazy session factory and trusted observation clock without I/O."""
        self._factory = factory
        self._observed_at = observed_at

    async def record(self, candidate: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return only after the new or exact prior refusal fact is durably committed."""
        fact = StoredBrokerRefusal(
            consumer=candidate.consumer,
            source=candidate.source,
            family=candidate.family,
            channel=candidate.channel,
            refusal_code=candidate.refusal_code,
            raw_digest=candidate.raw_digest,
            observed_at=self._observed_at(),
        )
        async with transaction(self._factory) as session:
            return await record(cast("BrokerRefusalSession", session), fact)
