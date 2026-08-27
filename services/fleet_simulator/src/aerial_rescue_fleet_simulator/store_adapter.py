"""Adapt fleet command use cases to the purpose-specific SQLAlchemy store boundary.

The store package owns SQLAlchemy statements and transactions.  This fleet-owned adapter
maps service concepts into those durable records, retains one command's publications until
the complete outcome can be persisted atomically, and translates only the two accepted edge
capacity refusals into the use case's transient result.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Protocol, cast

from aerial_rescue_domain.commands import CommandEvent
from aerial_rescue_domain.idempotency import SequenceVerdict
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    ApplicationOutboxSession,
    StagedApplicationEvent,
    pending,
    reconciliation,
    record_publication,
)
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.fleet import (
    CommandEffectOutcome,
    DroneStreamIdentity,
    DurableCommandEffect,
    FleetStoreError,
    FleetStoreRefusal,
    FleetTransaction,
    FleetTransactions,
)
from aerial_rescue_store.receipts import CommandReceiptIdentity, ReceiptDecision, ReceiptOutcome
from aerial_rescue_store.session import StoreSessionFactory, transaction

from aerial_rescue_fleet_simulator import event_source
from aerial_rescue_fleet_simulator.durable_processing import (
    EffectResult,
    ProcessingError,
    ProcessingRefusal,
)
from aerial_rescue_fleet_simulator.intake import IncomingCommand

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class EffectCallback(Protocol):
    """Produce one deterministic effect without opening a second durable boundary."""

    def __call__(self, drone_id: str, command: IncomingCommand) -> EffectResult:
        """Return the reportable outcome, exact payload, and drone-local effect sequence."""


_STORE_OUTCOME = {
    CommandEvent.SUCCEED: CommandEffectOutcome.SUCCEEDED,
    CommandEvent.FAIL: CommandEffectOutcome.FAILED,
}
_CAPACITY_REFUSALS = frozenset(
    {
        FleetStoreRefusal.RECORD_CAPACITY,
        FleetStoreRefusal.BYTE_CAPACITY,
    }
)


class StoreCommandTransaction:
    """Map one fleet command onto one caller-owned store transaction."""

    def __init__(self, store: FleetTransaction, effect_callback: EffectCallback) -> None:
        """Retain one open store transaction and its pure effect decision."""
        self._store = store
        self._effect_callback = effect_callback
        self._claimed: CommandReceiptIdentity | None = None
        self._effect: EffectResult | None = None
        self._publications: list[StagedApplicationEvent] = []

    async def admit_sequence(self, drone_id: str, sequence: int) -> SequenceVerdict:
        """Map a fleet drone identifier to its producer-scoped durable stream."""
        self._require_claimed_drone(drone_id)
        return await self._store.admit_sequence(_stream(drone_id), sequence)

    async def claim_receipt(self, identity: CommandReceiptIdentity) -> ReceiptOutcome:
        """Claim one effect identity or return its exact completed result."""
        receipt = await self._store.claim_receipt(identity)
        if receipt.decision is ReceiptDecision.CLAIMED:
            self._claimed = identity
        return receipt

    async def apply_effect(self, command: IncomingCommand) -> EffectResult:
        """Invoke the injected deterministic effect after receipt and sequence admission."""
        claimed = self._require_claimed()
        if command.command_id != claimed.command_id:
            raise ProcessingError(ProcessingRefusal.INVALID_EFFECT, command.command_id)
        effect = self._effect_callback(claimed.drone_id, command)
        self._effect = effect
        return effect

    async def stage_critical(self, drone_id: str, event: StagedApplicationEvent) -> None:
        """Retain one result until the store can persist the complete atomic outcome."""
        self._require_claimed_drone(drone_id)
        self._publications.append(event)

    async def complete_receipt(
        self,
        identity: CommandReceiptIdentity,
        result: bytes,
        applied_sequence: int,
        processed_at: str,
    ) -> None:
        """Persist effect, publications, and receipt completion in the open transaction."""
        claimed = self._require_claimed()
        effect = self._effect
        if claimed != identity or effect is None or effect.applied_sequence != applied_sequence:
            raise ProcessingError(ProcessingRefusal.INVALID_EFFECT, identity.command_id)
        try:
            outcome = _STORE_OUTCOME[effect.event]
        except KeyError as error:
            raise ProcessingError(ProcessingRefusal.INVALID_EFFECT, effect.event.name) from error
        durable = DurableCommandEffect(
            identity=identity,
            outcome=outcome,
            effect_payload=effect.effect_payload,
            applied_sequence=effect.applied_sequence,
            applied_at=processed_at,
        )
        try:
            await self._store.persist_outcome(
                _stream(identity.drone_id),
                durable,
                tuple(self._publications),
                result,
            )
        except FleetStoreError as error:
            if error.refusal not in _CAPACITY_REFUSALS:
                raise
            raise ProcessingError(ProcessingRefusal.CRITICAL_OUTBOX_CAPACITY) from error

    def _require_claimed(self) -> CommandReceiptIdentity:
        """Return the transaction's new receipt or fail before an unbound effect."""
        if self._claimed is None:
            raise ProcessingError(ProcessingRefusal.INVALID_EFFECT, "receipt")
        return self._claimed

    def _require_claimed_drone(self, drone_id: str) -> None:
        """Require every mapped operation to name the claimed receipt's drone."""
        if self._require_claimed().drone_id != drone_id:
            raise ProcessingError(ProcessingRefusal.INVALID_EFFECT, drone_id)


class StoreFleetUnitOfWork:
    """Construct fleet command transactions over the SQLAlchemy store adapter."""

    def __init__(
        self,
        transactions: FleetTransactions,
        effect_callback: EffectCallback,
        refusals: BrokerRefusalRecorder,
    ) -> None:
        """Retain the lazy store factory and effect decision without opening either."""
        self._transactions = transactions
        self._effect_callback = effect_callback
        self._refusals = refusals

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed command evidence in its own transaction."""
        return await self._refusals.record(fact)

    def begin(self) -> AbstractAsyncContextManager[StoreCommandTransaction]:
        """Return a fresh commit-or-rollback command transaction."""
        return _begin(self._transactions, self._effect_callback)


class StoreCriticalOutbox:
    """Read and update exact Fleet outbox rows in independent transactions."""

    def __init__(self, factory: StoreSessionFactory) -> None:
        """Retain the lazy SQLAlchemy session factory without opening a connection."""
        self._factory = factory

    async def pending(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Read one bounded committed batch for the selected drone producer."""
        async with transaction(self._factory) as session:
            return await pending(
                cast("ApplicationOutboxSession", session),
                event_source(drone_id),
            )

    async def reconciliation(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Read ambiguous rows as evidence only, never as blind retry work."""
        async with transaction(self._factory) as session:
            return await reconciliation(
                cast("ApplicationOutboxSession", session),
                event_source(drone_id),
            )

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Commit one confirmation or ambiguity compare-and-set independently."""
        async with transaction(self._factory) as session:
            await record_publication(
                cast("ApplicationOutboxSession", session),
                identity,
                OutboxState.STAGED,
                event,
                confirmed_at,
            )


def _stream(drone_id: str) -> DroneStreamIdentity:
    """Bind one simulated drone to the CloudEvent producer that owns its sequence."""
    return DroneStreamIdentity(drone_id, event_source(drone_id))


@asynccontextmanager
async def _begin(
    transactions: FleetTransactions,
    effect_callback: EffectCallback,
) -> AsyncIterator[StoreCommandTransaction]:
    """Keep the store transaction open across the complete fleet use case."""
    async with transactions.open() as store:
        yield StoreCommandTransaction(store, effect_callback)
