"""Five-send progression and transactional command-result settlement."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import override

import pytest
from aerial_rescue_command_gateway.ports import GuaranteedDelivery
from aerial_rescue_command_gateway.progression import (
    CommandResultOutcome,
    ProgressionError,
    ProgressionRefusal,
    SendClock,
    TimeoutOutcome,
    handle_command_result,
    record_send,
    record_timeout,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_domain.commands import (
    CommandEvent,
    CommandProgress,
    CommandState,
    SendBudget,
    advance,
)
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.command_progress import (
    CommandIdentity,
    StoredCommandProgress,
    TransitionFacts,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome

ROOT = Path(__file__).parents[3]
RESULT_TOPIC = "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/command-result/cmd-2026-0001"


def _result_payload(outcome: str = "acknowledged") -> bytes:
    """Return a valid command-result event with one selected lifecycle outcome."""
    fixture = ROOT / "fixtures/golden/v1/event/drone-command-result/baseline.json"
    document = canonical.decode(fixture.read_bytes())
    assert isinstance(document, dict)
    data = document["data"]
    assert isinstance(data, dict)
    data["outcome"] = outcome
    return canonical.canonical_bytes(document)


def _stored(state: CommandState, sends: int) -> StoredCommandProgress:
    """Return durable progress for the golden command identity."""
    return StoredCommandProgress(
        identity=CommandIdentity("cmd-2026-0001", "m-2026-0001", "drone-vision-01"),
        progress=CommandProgress(state, sends),
        last_sent_at="2026-08-23T07:31:04.000Z" if sends else None,
        deadline_at="2026-08-23T07:31:10.000Z" if state is CommandState.IN_FLIGHT else None,
        result_id=None,
        updated_at="2026-08-23T07:31:04.000Z",
    )


class FakeProgressRecorder:
    """Apply the domain table and record every durable transition request."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Configure an optional persistence failure after the pure decision."""
        self.failure = failure
        self.recorded: list[tuple[CommandEvent, TransitionFacts]] = []

    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Apply the pure state table, then persist or inject a crash."""
        became = advance(current.progress, event, budget)
        if self.failure is not None:
            raise self.failure
        self.recorded.append((event, facts))
        return StoredCommandProgress(
            current.identity,
            became,
            facts.last_sent_at,
            facts.deadline_at,
            facts.result_id,
            facts.updated_at,
        )


class FakeResultTransaction(FakeProgressRecorder):
    """One broker inbox and command-progress transaction."""

    def __init__(
        self,
        current: StoredCommandProgress,
        claim: InboxOutcome | None = None,
        failure: Exception | None = None,
    ) -> None:
        """Configure current state, duplicate outcome, and a transition failure."""
        super().__init__(failure)
        self.current = current
        self.claim_outcome = claim or InboxOutcome(InboxDecision.CLAIMED, None)
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.order: list[str] = []

    async def claim(self, _identity: InboxIdentity) -> InboxOutcome:
        """Return the configured inbox result."""
        self.order.append("claim")
        return self.claim_outcome

    async def load_progress(self, _command_id: str) -> StoredCommandProgress:
        """Return the configured authoritative command progress."""
        self.order.append("load")
        return self.current

    @override
    async def transition(
        self,
        current: StoredCommandProgress,
        event: CommandEvent,
        budget: SendBudget,
        facts: TransitionFacts,
    ) -> StoredCommandProgress:
        """Record ordering around the shared transition implementation."""
        self.order.append("transition")
        return await super().transition(current, event, budget, facts)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the inbox inside the same transaction."""
        self.order.append("complete")
        self.completed.append((identity, result, processed_at))


class FakeResultUnitOfWork:
    """Commit on successful exit and roll back on a transition crash."""

    def __init__(self, transaction: FakeResultTransaction) -> None:
        """Wrap one configured transaction."""
        self.transaction = transaction
        self.committed = False
        self.rolled_back = False
        self.refusals: list[BrokerRefusalCandidate] = []

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Record one independently committed malformed-ingress fact."""
        self.transaction.order.append("refusal-commit")
        self.refusals.append(fact)
        stored = StoredBrokerRefusal(
            fact.consumer,
            fact.source,
            fact.family,
            fact.channel,
            fact.refusal_code,
            fact.raw_digest,
            "2026-08-25T12:00:00.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)

    def begin(self) -> FakeResultUnitOfWork:
        """Return this context."""
        return self

    async def __aenter__(self) -> FakeResultTransaction:
        """Return the typed transaction."""
        return self.transaction

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        """Record commit or rollback."""
        self.committed = exception_type is None
        self.rolled_back = exception_type is not None
        self.transaction.order.append("commit" if self.committed else "rollback")
        return False


class FakeSettlement:
    """Record acceptance after the result transaction commits."""

    def __init__(self, order: list[str]) -> None:
        """Share the transaction's ordering evidence."""
        self.order = order
        self.accepted: list[str] = []
        self.rejected = 0

    async def accept(self, event_id: str) -> None:
        """Accept exactly one event."""
        self.order.append("settle")
        self.accepted.append(event_id)

    async def reject(self) -> None:
        """Record permanent settlement after refusal commit."""
        self.order.append("settle-rejected")
        self.rejected += 1


class FiveSendProgressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_sends_use_four_backoffs_then_abandon_on_the_last_timeout(self) -> None:
        # Arrange
        recorder = FakeProgressRecorder()
        current = _stored(CommandState.ACCEPTED, 0)
        waits: list[int | None] = []
        last_outcome = TimeoutOutcome.RETRY

        # Act
        for send_number in range(1, 6):
            current = await record_send(
                current,
                SendClock(
                    sent_at=f"2026-08-23T07:3{send_number}:00.000Z",
                    deadline_at=f"2026-08-23T07:3{send_number}:06.000Z",
                    updated_at=f"2026-08-23T07:3{send_number}:00.000Z",
                ),
                recorder,
            )
            timeout = await record_timeout(
                current,
                f"2026-08-23T07:3{send_number}:06.000Z",
                0,
                recorder,
            )
            current = timeout.progress
            waits.append(timeout.wait_milliseconds)
            last_outcome = timeout.outcome

        # Assert
        self.assertEqual(
            (
                CommandState.ABANDONED,
                5,
                [6_000, 12_000, 24_000, 48_000, None],
                TimeoutOutcome.ABANDONED,
                10,
            ),
            (
                current.progress.state,
                current.progress.sends,
                waits,
                last_outcome,
                len(recorder.recorded),
            ),
        )


class ResultHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledgement_commits_progress_and_inbox_before_settlement(self) -> None:
        # Arrange
        transaction = FakeResultTransaction(_stored(CommandState.IN_FLIGHT, 1))
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        delivery = GuaranteedDelivery(RESULT_TOPIC, _result_payload())

        # Act
        result = await handle_command_result(delivery, unit, settlement)

        # Assert
        self.assertEqual(
            (
                CommandResultOutcome.UPDATED,
                CommandState.ACKNOWLEDGED,
                True,
                ["commit", "settle"],
                ["0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6f"],
            ),
            (
                result.outcome,
                result.state,
                unit.committed,
                transaction.order[-2:],
                settlement.accepted,
            ),
        )

    async def test_stale_result_is_recorded_without_reopening_or_advancing_progress(self) -> None:
        # Arrange
        transaction = FakeResultTransaction(_stored(CommandState.IN_FLIGHT, 1))
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        delivery = GuaranteedDelivery(RESULT_TOPIC, _result_payload("succeeded"))

        # Act
        result = await handle_command_result(delivery, unit, settlement)

        # Assert
        self.assertEqual(
            (CommandResultOutcome.STALE, CommandState.IN_FLIGHT, [], 1, True),
            (
                result.outcome,
                result.state,
                transaction.recorded,
                len(transaction.completed),
                unit.committed,
            ),
        )

    async def test_result_identity_mismatch_is_completed_without_advancing_progress(self) -> None:
        # Arrange
        current = _stored(CommandState.IN_FLIGHT, 1)
        current = replace(
            current,
            identity=replace(current.identity, drone_id="drone-vision-02"),
        )
        transaction = FakeResultTransaction(current)
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_command_result(
            GuaranteedDelivery(RESULT_TOPIC, _result_payload()), unit, settlement
        )

        # Assert
        self.assertEqual(
            (CommandResultOutcome.MISMATCH, CommandState.IN_FLIGHT, [], 1, True),
            (
                result.outcome,
                result.state,
                transaction.recorded,
                len(transaction.completed),
                unit.committed,
            ),
        )

    async def test_exact_redelivery_returns_prior_result_without_a_second_transition(self) -> None:
        # Arrange
        prior = b'{"commandResult":"updated"}'
        transaction = FakeResultTransaction(
            _stored(CommandState.ACKNOWLEDGED, 1),
            InboxOutcome(InboxDecision.DUPLICATE, prior),
        )
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_command_result(
            GuaranteedDelivery(RESULT_TOPIC, _result_payload()), unit, settlement
        )

        # Assert
        self.assertEqual(
            (CommandResultOutcome.DUPLICATE, prior, [], [], True),
            (
                result.outcome,
                result.result,
                transaction.recorded,
                transaction.completed,
                unit.committed,
            ),
        )

    async def test_incomplete_duplicate_stays_unsettled_and_wrong_ingress_is_durably_rejected(
        self,
    ) -> None:
        # Arrange
        duplicate_transaction = FakeResultTransaction(
            _stored(CommandState.ACKNOWLEDGED, 1),
            InboxOutcome(InboxDecision.DUPLICATE, None),
        )
        duplicate_unit = FakeResultUnitOfWork(duplicate_transaction)
        duplicate_settlement = FakeSettlement(duplicate_transaction.order)
        wrong_transaction = FakeResultTransaction(_stored(CommandState.IN_FLIGHT, 1))
        wrong_unit = FakeResultUnitOfWork(wrong_transaction)
        wrong_settlement = FakeSettlement(wrong_transaction.order)
        wrong_delivery = GuaranteedDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/operator/command/escalate-rescue",
            (ROOT / "fixtures/golden/v1/event/operator-command/escalate-rescue.json").read_bytes(),
        )

        # Act
        refusals = []
        for delivery, unit, settlement in (
            (
                GuaranteedDelivery(RESULT_TOPIC, _result_payload()),
                duplicate_unit,
                duplicate_settlement,
            ),
            (wrong_delivery, wrong_unit, wrong_settlement),
        ):
            with pytest.raises(ProgressionError) as captured:
                await handle_command_result(delivery, unit, settlement)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            (
                [ProgressionRefusal.DUPLICATE_RESULT, ProgressionRefusal.INGRESS_KIND],
                ["refusal-commit", "settle-rejected"],
            ),
            (refusals, wrong_transaction.order),
        )

    async def test_transition_crash_rolls_back_and_leaves_the_delivery_unsettled(self) -> None:
        # Arrange
        transaction = FakeResultTransaction(
            _stored(CommandState.IN_FLIGHT, 1),
            failure=RuntimeError("injected progress crash"),
        )
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_command_result(
                GuaranteedDelivery(RESULT_TOPIC, _result_payload()), unit, settlement
            )

        # Assert
        self.assertEqual(
            ("injected progress crash", True, False, []),
            (str(captured.value), unit.rolled_back, unit.committed, settlement.accepted),
        )


if __name__ == "__main__":
    unittest.main()
