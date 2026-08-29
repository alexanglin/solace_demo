"""Durable command effects and critical-result staging before settlement."""

from __future__ import annotations

import hashlib
import unittest
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import cast, override

import pytest
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import Envelope, envelope_document
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.idempotency import SequenceVerdict
from aerial_rescue_fleet_simulator.durable_processing import (
    CommandContext,
    CommandDelivery,
    CommandProcessingOutcome,
    EffectResult,
    ProcessingError,
    ProcessingRefusal,
    handle_command,
)
from aerial_rescue_fleet_simulator.results import ResultStamp
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.receipts import (
    CommandReceiptIdentity,
    ReceiptDecision,
    ReceiptError,
    ReceiptOutcome,
    ReceiptRefusal,
)

pytestmark = [pytest.mark.unit]

MISSION = "m-2026-0001"
DRONE = "drone-vision-01"
COMMAND = "cmd-2026-0001"
EVENT = "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6e"
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01"


def _command(*, drone: str = DRONE, sequence: int = 2, command_id: str = COMMAND) -> bytes:
    return canonical_bytes(
        envelope_document(
            Envelope(
                id=EVENT,
                source="urn:aerial-rescue:service:command-gateway",
                type="aerial-rescue.v1.drone.command.assign-sector",
                subject=MISSION,
                time="2026-08-25T12:00:00.000Z",
                dataschema=(
                    "https://aerial-rescue.invalid/schemas/v1/payload/"
                    "drone-command-assign-sector.schema.json"
                ),
                sequence=f"{sequence:015d}",
                correlation_id="corr-2026-0001",
                traceparent=TRACEPARENT,
                data={
                    "missionId": MISSION,
                    "droneId": drone,
                    "commandId": command_id,
                    "sectorId": "sector-north",
                },
            )
        )
    )


def _topic(drone: str = DRONE) -> str:
    return f"aerial-rescue/v1/{MISSION}/drone/{drone}/command/assign-sector"


def _rescue_command(*, sequence: int = 3, proposal_digest: str = "1" * 64) -> bytes:
    """Return one fully authority-bound rescue-escalation delivery."""
    return canonical_bytes(
        envelope_document(
            Envelope(
                id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a70",
                source="urn:aerial-rescue:command-gateway:gateway-synthetic-01",
                type="aerial-rescue.v1.drone.command.escalate-rescue",
                subject=MISSION,
                time="2026-08-25T12:00:00.000Z",
                dataschema=(
                    "https://aerial-rescue.invalid/schemas/v1/payload/"
                    "drone-command-escalate-rescue.schema.json"
                ),
                sequence=f"{sequence:015d}",
                correlation_id="corr-2026-0002",
                traceparent=TRACEPARENT,
                data={
                    "missionId": MISSION,
                    "droneId": DRONE,
                    "commandId": "cmd-rescue-0001",
                    "approvalId": "approval-0001",
                    "proposalId": "proposal-0001",
                    "proposalDigest": proposal_digest,
                    "proposalVersion": 1,
                    "evidenceDecisionId": "decision-0001",
                    "evidenceDecisionDigest": "2" * 64,
                    "evidenceDecisionVersion": 1,
                    "latitudeMicrodegrees": 45_123_456,
                    "longitudeMicrodegrees": -75_123_456,
                },
            )
        )
    )


def _rescue_topic() -> str:
    """Return the addressed rescue-escalation topic."""
    return f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/command/escalate-rescue"


class FakeSettlement:
    def __init__(self, order: list[str], *, refuse_accept: bool = False) -> None:
        """Record settlement ordering and an optional accepted-settlement failure."""
        self.order = order
        self.refuse_accept = refuse_accept

    async def accept(self) -> None:
        self.order.append("settle-accepted")
        if self.refuse_accept:
            message = "settlement refused"
            raise RuntimeError(message)

    async def fail(self) -> None:
        self.order.append("settle-failed")

    async def reject(self) -> None:
        self.order.append("settle-rejected")


class FakeStamps:
    def __init__(self) -> None:
        """Start a deterministic producer sequence."""
        self.next_sequence = 10

    def next_result_stamp(
        self,
        drone_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> ResultStamp:
        """Return the next deterministic result stamp."""
        del drone_id
        self.next_sequence += 1
        return ResultStamp(
            event_id=f"result-{self.next_sequence}",
            occurred_at="2026-08-25T12:00:01.000Z",
            sequence=self.next_sequence,
            correlation_id=correlation_id,
            causation_id=causation_id,
            traceparent=TRACEPARENT,
        )

    def processed_at(self) -> str:
        return "2026-08-25T12:00:01.000Z"


@dataclass
class Script:
    """The transaction decisions one test controls."""

    sequence: SequenceVerdict = SequenceVerdict.ADVANCES
    receipt: ReceiptOutcome | None = None
    overflow: bool = False
    effect: CommandEvent = CommandEvent.SUCCEED
    receipt_failure: Exception | None = None


class FakeTransaction:
    def __init__(self, order: list[str], script: Script) -> None:
        """Record transaction effects under one script."""
        self.order = order
        self.script = script
        self.applied = 0
        self.staged: list[StagedApplicationEvent] = []
        self.completed: list[tuple[CommandReceiptIdentity, bytes, int, str]] = []

    async def admit_sequence(self, drone_id: str, sequence: int) -> SequenceVerdict:
        self.order.append(f"sequence-{drone_id}-{sequence}")
        return self.script.sequence

    async def claim_receipt(self, identity: CommandReceiptIdentity) -> ReceiptOutcome:
        """Return the scripted receipt decision."""
        del identity
        self.order.append("claim")
        if self.script.receipt_failure is not None:
            raise self.script.receipt_failure
        return self.script.receipt or ReceiptOutcome(ReceiptDecision.CLAIMED, None, None, None)

    async def apply_effect(self, command: object) -> EffectResult:
        del command
        self.order.append("effect")
        self.applied += 1
        return EffectResult(
            self.script.effect,
            applied_sequence=7,
            effect_payload=b'{"sectorId":"sector-north","state":"assigned"}',
        )

    async def stage_critical(self, drone_id: str, event: StagedApplicationEvent) -> None:
        if self.script.overflow:
            raise ProcessingError(ProcessingRefusal.CRITICAL_OUTBOX_CAPACITY)
        self.order.append(f"stage-{drone_id}")
        self.staged.append(event)

    async def complete_receipt(
        self,
        identity: CommandReceiptIdentity,
        result: bytes,
        applied_sequence: int,
        processed_at: str,
    ) -> None:
        self.order.append("complete")
        self.completed.append((identity, result, applied_sequence, processed_at))


class FakeTransactionContext(AbstractAsyncContextManager[FakeTransaction]):
    def __init__(self, transaction: FakeTransaction, order: list[str]) -> None:
        """Bind one fake transaction to the observable effect order."""
        self.transaction = transaction
        self.order = order

    @override
    async def __aenter__(self) -> FakeTransaction:
        """Open the fake transaction."""
        self.order.append("begin")
        return self.transaction

    @override
    async def __aexit__(self, exception_type: object, exception: object, traceback: object) -> None:
        """Record rollback or commit from the context outcome."""
        del exception, traceback
        self.order.append("rollback" if exception_type is not None else "commit")


class FakeUnitOfWork:
    def __init__(
        self,
        script: Script,
        order: list[str],
        refusal_failure: Exception | None = None,
    ) -> None:
        """Construct one reusable fake transaction."""
        self.transaction = FakeTransaction(order, script)
        self.order = order
        self.refusal_failure = refusal_failure
        self.refusals: list[BrokerRefusalCandidate] = []

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Record separate refusal commit ordering or inject its failure."""
        self.order.append("refusal-commit")
        if self.refusal_failure is not None:
            raise self.refusal_failure
        self.refusals.append(fact)
        stored = StoredBrokerRefusal(
            fact.consumer,
            fact.source,
            fact.family,
            fact.channel,
            fact.refusal_code,
            fact.raw_digest,
            "2026-08-25T12:00:01.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)

    def begin(self) -> FakeTransactionContext:
        return FakeTransactionContext(self.transaction, self.order)


def _delivery(
    settlement: FakeSettlement,
    *,
    drone: str = DRONE,
    sequence: int = 2,
) -> CommandDelivery:
    return CommandDelivery(_topic(drone), _command(drone=drone, sequence=sequence), settlement)


def _rescue_delivery(settlement: FakeSettlement, *, sequence: int = 3) -> CommandDelivery:
    """Return one exact rescue-escalation delivery."""
    return CommandDelivery(_rescue_topic(), _rescue_command(sequence=sequence), settlement)


def _context(work: FakeUnitOfWork) -> CommandContext:
    """Return one durable command context over the supplied unit of work."""
    return CommandContext(MISSION, frozenset({DRONE}), SendBudget(5), FakeStamps(), work)


class DurableProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_effect_receipt_and_both_results_commit_before_settlement(self) -> None:
        # Arrange
        order: list[str] = []
        work = FakeUnitOfWork(Script(), order)
        settlement = FakeSettlement(order)

        # Act
        result = await handle_command(_delivery(settlement), _context(work))

        # Assert
        self.assertEqual(result.outcome, CommandProcessingOutcome.APPLIED)
        self.assertEqual(work.transaction.applied, 1)
        self.assertEqual(len(work.transaction.staged), 2)
        self.assertEqual(len(work.transaction.completed), 1)
        self.assertLess(order.index("commit"), order.index("settle-accepted"))
        self.assertLess(order.index("effect"), order.index("complete"))

    async def test_exact_duplicate_reuses_the_durable_result_without_an_effect(self) -> None:
        # Arrange
        order: list[str] = []
        prior = b'{"prior":true}'
        script = Script(
            sequence=SequenceVerdict.DUPLICATE,
            receipt=ReceiptOutcome(
                ReceiptDecision.DUPLICATE,
                prior,
                applied_sequence=7,
                processed_at="2026-08-25T12:00:01.000Z",
            ),
        )
        work = FakeUnitOfWork(script, order)

        # Act
        result = await handle_command(_delivery(FakeSettlement(order)), _context(work))

        # Assert
        self.assertEqual(
            (result.outcome, result.result),
            (CommandProcessingOutcome.DUPLICATE, prior),
        )
        self.assertEqual((work.transaction.applied, work.transaction.staged), (0, []))
        self.assertNotIn(f"sequence-{DRONE}-2", order)
        self.assertEqual(order[-2:], ["commit", "settle-accepted"])

    async def test_stale_new_claim_rolls_back_without_a_durable_receipt_or_effect(self) -> None:
        # Arrange
        order: list[str] = []
        work = FakeUnitOfWork(Script(sequence=SequenceVerdict.STALE), order)

        # Act
        result = await handle_command(_delivery(FakeSettlement(order), sequence=1), _context(work))

        # Assert
        self.assertEqual(result.outcome, CommandProcessingOutcome.SUPERSEDED)
        self.assertIn("claim", order)
        self.assertNotIn("effect", order)
        self.assertNotIn("commit", order)
        self.assertEqual(order[-2:], ["rollback", "settle-accepted"])

    async def test_rescue_duplicate_stale_and_conflicting_bytes_never_repeat_an_effect(
        self,
    ) -> None:
        # Arrange
        prior = b'{"priorRescueResult":true}'
        duplicate_order: list[str] = []
        stale_order: list[str] = []
        conflict_order: list[str] = []
        duplicate = FakeUnitOfWork(
            Script(
                receipt=ReceiptOutcome(
                    ReceiptDecision.DUPLICATE,
                    prior,
                    applied_sequence=11,
                    processed_at="2026-08-25T12:00:01.000Z",
                )
            ),
            duplicate_order,
        )
        stale = FakeUnitOfWork(Script(sequence=SequenceVerdict.STALE), stale_order)
        digest_conflict = ReceiptError(ReceiptRefusal.DIGEST_CONFLICT, "cmd-rescue-0001")
        conflicting = FakeUnitOfWork(
            Script(receipt_failure=digest_conflict),
            conflict_order,
        )

        # Act
        replayed = await handle_command(
            _rescue_delivery(FakeSettlement(duplicate_order)),
            _context(duplicate),
        )
        superseded = await handle_command(
            _rescue_delivery(FakeSettlement(stale_order), sequence=2),
            _context(stale),
        )
        with pytest.raises(ReceiptError) as raised:
            await handle_command(
                _rescue_delivery(FakeSettlement(conflict_order)),
                _context(conflicting),
            )

        # Assert
        self.assertEqual(
            (
                CommandProcessingOutcome.DUPLICATE,
                prior,
                CommandProcessingOutcome.SUPERSEDED,
                digest_conflict,
            ),
            (replayed.outcome, replayed.result, superseded.outcome, raised.value),
        )
        self.assertEqual(
            (
                duplicate.transaction.applied,
                stale.transaction.applied,
                conflicting.transaction.applied,
            ),
            (0, 0, 0),
        )
        self.assertEqual(duplicate_order[-2:], ["commit", "settle-accepted"])
        self.assertEqual(stale_order[-2:], ["rollback", "settle-accepted"])
        self.assertEqual(conflict_order[-2:], ["claim", "rollback"])

    async def test_unknown_drone_is_rejected_before_opening_a_transaction(self) -> None:
        # Arrange
        order: list[str] = []
        work = FakeUnitOfWork(Script(), order)
        settlement = FakeSettlement(order)

        # Act
        with pytest.raises(ProcessingError) as raised:
            await handle_command(_delivery(settlement, drone="drone-unknown-99"), _context(work))

        # Assert
        fact = work.refusals[0]
        self.assertEqual(
            (
                ProcessingRefusal.UNKNOWN_DRONE,
                ["refusal-commit", "settle-rejected"],
                "fleet-simulator",
                "drone.command",
                "fleet-simulator-drone-command-drone-unknown-99",
                "unknown-drone",
                hashlib.sha256(_command(drone="drone-unknown-99")).hexdigest(),
                False,
            ),
            (
                raised.value.refusal,
                order,
                fact.consumer,
                fact.family,
                fact.channel,
                fact.refusal_code,
                fact.raw_digest,
                hasattr(fact, "payload"),
            ),
        )

    async def test_refusal_commit_failure_leaves_the_malformed_command_unsettled(self) -> None:
        # Arrange
        order: list[str] = []
        failure = RuntimeError("injected refusal commit failure")
        work = FakeUnitOfWork(Script(), order, failure)
        settlement = FakeSettlement(order)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_command(
                CommandDelivery(_topic(), b"not-json", settlement),
                _context(work),
            )

        # Assert
        self.assertEqual((failure, ["refusal-commit"]), (captured.value, order))

    async def test_critical_capacity_rolls_back_and_returns_delivery_for_redelivery(self) -> None:
        # Arrange
        order: list[str] = []
        work = FakeUnitOfWork(Script(overflow=True), order)

        # Act
        result = await handle_command(_delivery(FakeSettlement(order)), _context(work))

        # Assert
        self.assertEqual(result.outcome, CommandProcessingOutcome.OUTBOX_FULL)
        self.assertIn("rollback", order)
        self.assertEqual(order[-1], "settle-failed")
        self.assertNotIn("complete", order)

    async def test_commit_survives_settlement_failure_and_redelivery_replays_it(self) -> None:
        # Arrange
        order: list[str] = []
        first_work = FakeUnitOfWork(Script(), order)
        first = FakeSettlement(order, refuse_accept=True)

        # Act
        with pytest.raises(RuntimeError, match="settlement refused"):
            await handle_command(_delivery(first), _context(first_work))
        stored_result = first_work.transaction.completed[0][1]
        replay_work = FakeUnitOfWork(
            Script(
                receipt=ReceiptOutcome(
                    ReceiptDecision.DUPLICATE,
                    stored_result,
                    applied_sequence=7,
                    processed_at="2026-08-25T12:00:01.000Z",
                )
            ),
            order,
        )
        replay = await handle_command(_delivery(FakeSettlement(order)), _context(replay_work))

        # Assert
        self.assertEqual(replay.outcome, CommandProcessingOutcome.DUPLICATE)
        self.assertEqual((first_work.transaction.applied, replay_work.transaction.applied), (1, 0))
        self.assertEqual(replay.result, stored_result)

    async def test_invalid_topic_payload_effect_and_incomplete_duplicate_fail_closed(self) -> None:
        # Arrange
        cases = (
            (
                CommandDelivery("not-an-application-topic", _command(), FakeSettlement([])),
                Script(),
                ProcessingRefusal.INVALID_COMMAND,
            ),
            (
                CommandDelivery(
                    f"aerial-rescue/v1/{MISSION}/drone/{DRONE}/telemetry",
                    _command(),
                    FakeSettlement([]),
                ),
                Script(),
                ProcessingRefusal.INVALID_COMMAND,
            ),
            (
                CommandDelivery(_topic(), b"not-json", FakeSettlement([])),
                Script(),
                ProcessingRefusal.INVALID_COMMAND,
            ),
            (
                _delivery(FakeSettlement([])),
                Script(effect=CommandEvent.ACKNOWLEDGE),
                ProcessingRefusal.INVALID_EFFECT,
            ),
            (
                _delivery(FakeSettlement([])),
                Script(
                    receipt=ReceiptOutcome(
                        ReceiptDecision.DUPLICATE,
                        None,
                        applied_sequence=7,
                        processed_at="2026-08-25T12:00:01.000Z",
                    )
                ),
                ProcessingRefusal.DUPLICATE_RESULT,
            ),
        )
        observed: list[ProcessingRefusal] = []

        # Act
        for delivery, script, _expected in cases:
            work = FakeUnitOfWork(script, [])
            with pytest.raises(ProcessingError) as raised:
                await handle_command(delivery, _context(work))
            observed.append(cast("ProcessingRefusal", raised.value.refusal))

        # Assert
        self.assertEqual(observed, [expected for _delivery, _script, expected in cases])


if __name__ == "__main__":
    unittest.main()
