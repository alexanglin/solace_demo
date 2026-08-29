"""Fleet-owned adaptation of durable command processing to the SQLAlchemy store."""

from __future__ import annotations

import unittest
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast, override

import pytest
from aerial_rescue_domain.commands import CommandEvent
from aerial_rescue_domain.idempotency import SequenceVerdict
from aerial_rescue_fleet_simulator.durable_processing import (
    EffectResult,
    FleetUnitOfWork,
    ProcessingError,
    ProcessingRefusal,
)
from aerial_rescue_fleet_simulator.intake import AssignSectorCommand, IncomingCommand
from aerial_rescue_fleet_simulator.store_adapter import (
    StoreCommandTransaction,
    StoreFleetUnitOfWork,
)
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.processing.fleet import (
    CommandEffectOutcome,
    DroneStreamIdentity,
    DurableCommandEffect,
    FleetStoreError,
    FleetStoreRefusal,
    FleetTransaction,
    FleetTransactions,
)
from aerial_rescue_store.receipts import (
    CommandReceiptIdentity,
    ReceiptDecision,
    ReceiptOutcome,
)

if TYPE_CHECKING:
    from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder

pytestmark = [pytest.mark.unit]

MISSION = "m-2026-0001"
DRONE = "drone-vision-01"
PRODUCER = "urn:aerial-rescue:drone:drone-vision-01"
EFFECT_PAYLOAD = b'{"sectorId":"sector-north","state":"assigned"}'
FINAL_RESULT = b'{"state":"SUCCEEDED"}'
PROCESSED_AT = "2026-08-25T12:00:01.000Z"
IDENTITY = CommandReceiptIdentity(DRONE, "cmd-2026-0001", MISSION, "1" * 64)
COMMAND = AssignSectorCommand(
    command_id=IDENTITY.command_id,
    sector_id="sector-north",
    event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6e",
    correlation_id="corr-2026-0001",
    sequence=2,
)
PUBLICATION = StagedApplicationEvent(
    producer=PRODUCER,
    event_id="result-1",
    family="drone-command-result",
    topic=f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/command-result/{IDENTITY.command_id}",
    headers=b"{}",
    payload=FINAL_RESULT,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
    tracestate=None,
    correlation_id=COMMAND.correlation_id,
    causation_id=COMMAND.event_id,
    staged_at=PROCESSED_AT,
)
REFUSAL = BrokerRefusalCandidate(
    "fleet-simulator",
    None,
    "drone.command",
    "fleet-simulator-drone-command-drone-vision-01",
    "invalid-command",
    "2" * 64,
)


@dataclass
class FakeStoreTransaction:
    """Record the purpose-specific calls made through one store transaction."""

    capacity_refusal: FleetStoreRefusal | None = None
    receipt: ReceiptOutcome = field(
        default_factory=lambda: ReceiptOutcome(ReceiptDecision.CLAIMED, None, None, None)
    )
    admitted: list[tuple[DroneStreamIdentity, int]] = field(default_factory=list)
    claims: list[CommandReceiptIdentity] = field(default_factory=list)
    persisted: list[
        tuple[
            DroneStreamIdentity,
            DurableCommandEffect,
            tuple[StagedApplicationEvent, ...],
            bytes,
        ]
    ] = field(default_factory=list)

    async def admit_sequence(
        self,
        identity: DroneStreamIdentity,
        sequence: int,
    ) -> SequenceVerdict:
        """Record the mapped stream identity and accept its sequence."""
        self.admitted.append((identity, sequence))
        return SequenceVerdict.ADVANCES

    async def claim_receipt(self, identity: CommandReceiptIdentity) -> ReceiptOutcome:
        """Record and claim one new command receipt."""
        self.claims.append(identity)
        return self.receipt

    async def persist_outcome(
        self,
        stream: DroneStreamIdentity,
        effect: DurableCommandEffect,
        publications: tuple[StagedApplicationEvent, ...],
        final_result: bytes,
    ) -> None:
        """Record one atomic outcome or inject the selected capacity refusal."""
        if self.capacity_refusal is not None:
            raise FleetStoreError(self.capacity_refusal, stream.drone_id)
        self.persisted.append((stream, effect, publications, final_result))


class FakeStoreContext(AbstractAsyncContextManager[FakeStoreTransaction]):
    """Expose one fake store transaction and its commit-or-rollback result."""

    def __init__(self, transaction: FakeStoreTransaction, lifecycle: list[str]) -> None:
        """Retain the scripted transaction and its observable lifecycle."""
        self.transaction = transaction
        self.lifecycle = lifecycle

    @override
    async def __aenter__(self) -> FakeStoreTransaction:
        self.lifecycle.append("begin")
        return self.transaction

    @override
    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception, traceback
        self.lifecycle.append("rollback" if exception_type is not None else "commit")


class FakeTransactions:
    """Construct a context over one scripted store transaction."""

    def __init__(self, transaction: FakeStoreTransaction, lifecycle: list[str]) -> None:
        """Retain the transaction returned by each requested context."""
        self.transaction = transaction
        self.lifecycle = lifecycle

    def open(self) -> FakeStoreContext:
        return FakeStoreContext(self.transaction, self.lifecycle)


@dataclass
class _Refusals:
    """Record candidates delegated by the fleet unit of work."""

    candidates: list[BrokerRefusalCandidate] = field(default_factory=list)

    async def record(self, candidate: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return one deterministic committed refusal fact."""
        self.candidates.append(candidate)
        fact = StoredBrokerRefusal(
            candidate.consumer,
            candidate.source,
            candidate.family,
            candidate.channel,
            candidate.refusal_code,
            candidate.raw_digest,
            PROCESSED_AT,
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, fact)


class EffectCallback:
    """Return one deterministic simulated effect while retaining its binding."""

    def __init__(self, event: CommandEvent = CommandEvent.SUCCEED) -> None:
        """Start with no observed effect decisions."""
        self.event = event
        self.calls: list[tuple[str, IncomingCommand]] = []

    def __call__(self, drone_id: str, command: IncomingCommand) -> EffectResult:
        self.calls.append((drone_id, command))
        return EffectResult(self.event, 7, EFFECT_PAYLOAD)


async def _persist_one(work: StoreFleetUnitOfWork) -> None:
    """Exercise the complete adapter operation as one named scenario action."""
    async with work.begin() as transaction:
        await transaction.claim_receipt(IDENTITY)
        await transaction.admit_sequence(DRONE, COMMAND.sequence)
        effect = await transaction.apply_effect(COMMAND)
        await transaction.stage_critical(DRONE, PUBLICATION)
        await transaction.complete_receipt(
            IDENTITY,
            FINAL_RESULT,
            effect.applied_sequence,
            PROCESSED_AT,
        )


def _adapter(
    stored: FakeStoreTransaction,
    callback: EffectCallback | None = None,
) -> StoreCommandTransaction:
    """Return the fleet adapter over one fake purpose-specific store transaction."""
    return StoreCommandTransaction(
        cast("FleetTransaction", stored),
        callback or EffectCallback(),
    )


class StoreAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_drone_effect_and_results_into_one_store_outcome(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        stored = FakeStoreTransaction()
        callback = EffectCallback()
        refusals = _Refusals()
        work: FleetUnitOfWork = StoreFleetUnitOfWork(
            cast("FleetTransactions", FakeTransactions(stored, lifecycle)),
            callback,
            cast("BrokerRefusalRecorder", refusals),
        )

        # Act
        async with work.begin() as transaction:
            receipt = await transaction.claim_receipt(IDENTITY)
            verdict = await transaction.admit_sequence(DRONE, COMMAND.sequence)
            effect = await transaction.apply_effect(COMMAND)
            await transaction.stage_critical(DRONE, PUBLICATION)
            await transaction.complete_receipt(
                IDENTITY,
                FINAL_RESULT,
                effect.applied_sequence,
                PROCESSED_AT,
            )
        refused = await work.refuse(REFUSAL)

        # Assert
        self.assertEqual(
            (receipt.decision, verdict), (ReceiptDecision.CLAIMED, SequenceVerdict.ADVANCES)
        )
        self.assertEqual(callback.calls, [(DRONE, COMMAND)])
        self.assertEqual(lifecycle, ["begin", "commit"])
        self.assertEqual(len(stored.persisted), 1)
        stream, durable, publications, final = stored.persisted[0]
        self.assertEqual(stream, DroneStreamIdentity(DRONE, PRODUCER))
        self.assertEqual(
            durable,
            DurableCommandEffect(
                IDENTITY,
                CommandEffectOutcome.SUCCEEDED,
                EFFECT_PAYLOAD,
                7,
                PROCESSED_AT,
            ),
        )
        self.assertEqual((publications, final), ((PUBLICATION,), FINAL_RESULT))
        self.assertEqual(
            (BrokerRefusalDecision.STORED, [REFUSAL]),
            (refused.decision, refusals.candidates),
        )

    async def test_maps_store_capacity_to_transient_processing_refusal_and_rollback(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        stored = FakeStoreTransaction(capacity_refusal=FleetStoreRefusal.RECORD_CAPACITY)
        work = StoreFleetUnitOfWork(
            cast("FleetTransactions", FakeTransactions(stored, lifecycle)),
            EffectCallback(),
            cast("BrokerRefusalRecorder", _Refusals()),
        )

        # Act
        with pytest.raises(ProcessingError) as captured:
            await _persist_one(work)

        # Assert
        self.assertEqual(captured.value.refusal, ProcessingRefusal.CRITICAL_OUTBOX_CAPACITY)
        self.assertEqual(lifecycle, ["begin", "rollback"])
        self.assertEqual(stored.persisted, [])

    async def test_completed_duplicate_never_arms_the_adapter_for_an_effect(self) -> None:
        # Arrange
        prior = b'{"state":"SUCCEEDED"}'
        stored = FakeStoreTransaction(
            receipt=ReceiptOutcome(ReceiptDecision.DUPLICATE, prior, 7, PROCESSED_AT)
        )
        callback = EffectCallback()
        transaction = _adapter(stored, callback)

        # Act
        receipt = await transaction.claim_receipt(IDENTITY)
        with pytest.raises(ProcessingError) as captured:
            await transaction.apply_effect(COMMAND)

        # Assert
        self.assertEqual((receipt.decision, receipt.result), (ReceiptDecision.DUPLICATE, prior))
        self.assertEqual(captured.value.refusal, ProcessingRefusal.INVALID_EFFECT)
        self.assertEqual(callback.calls, [])

    async def test_drone_and_command_binding_mismatches_are_refused_before_store_writes(
        self,
    ) -> None:
        # Arrange
        wrong_drone = _adapter(FakeStoreTransaction())
        await wrong_drone.claim_receipt(IDENTITY)
        wrong_command = _adapter(FakeStoreTransaction())
        await wrong_command.claim_receipt(IDENTITY)

        # Act
        with pytest.raises(ProcessingError) as drone_error:
            await wrong_drone.admit_sequence("drone-other", COMMAND.sequence)
        with pytest.raises(ProcessingError) as command_error:
            await wrong_command.apply_effect(replace(COMMAND, command_id="cmd-other"))

        # Assert
        self.assertEqual(
            (drone_error.value.refusal, command_error.value.refusal),
            (ProcessingRefusal.INVALID_EFFECT, ProcessingRefusal.INVALID_EFFECT),
        )

    async def test_incomplete_and_unreportable_effects_are_refused_before_persistence(self) -> None:
        # Arrange
        incomplete = _adapter(FakeStoreTransaction())
        await incomplete.claim_receipt(IDENTITY)
        unreportable = _adapter(
            FakeStoreTransaction(),
            EffectCallback(CommandEvent.ACKNOWLEDGE),
        )
        await unreportable.claim_receipt(IDENTITY)
        unreportable_effect = await unreportable.apply_effect(COMMAND)

        # Act
        with pytest.raises(ProcessingError) as incomplete_error:
            await incomplete.complete_receipt(IDENTITY, FINAL_RESULT, 7, PROCESSED_AT)
        with pytest.raises(ProcessingError) as event_error:
            await unreportable.complete_receipt(
                IDENTITY,
                FINAL_RESULT,
                unreportable_effect.applied_sequence,
                PROCESSED_AT,
            )

        # Assert
        self.assertEqual(
            (incomplete_error.value.refusal, event_error.value.refusal),
            (ProcessingRefusal.INVALID_EFFECT, ProcessingRefusal.INVALID_EFFECT),
        )

    async def test_noncapacity_store_refusal_propagates_and_rolls_back(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        stored = FakeStoreTransaction(capacity_refusal=FleetStoreRefusal.EVENT_CONFLICT)
        work = StoreFleetUnitOfWork(
            cast("FleetTransactions", FakeTransactions(stored, lifecycle)),
            EffectCallback(),
            cast("BrokerRefusalRecorder", _Refusals()),
        )

        # Act
        with pytest.raises(FleetStoreError) as captured:
            await _persist_one(work)

        # Assert
        self.assertEqual(captured.value.refusal, FleetStoreRefusal.EVENT_CONFLICT)
        self.assertEqual(lifecycle, ["begin", "rollback"])


if __name__ == "__main__":
    unittest.main()
