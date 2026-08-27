"""Five-send progression and transactional command-result settlement."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import override

import pytest
from aerial_rescue_command_gateway.ingress import IngressError, IngressRefusal
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

from .fixture_paths import repository_root

ROOT = repository_root(Path(__file__))
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
        self.recorded: list[tuple[CommandEvent, SendBudget, TransitionFacts]] = []

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
        self.recorded.append((event, budget, facts))
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
        self.claimed: list[InboxIdentity] = []
        self.loaded: list[str] = []
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.order: list[str] = []

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Return the configured inbox result."""
        self.order.append("claim")
        self.claimed.append(identity)
        return self.claim_outcome

    async def load_progress(self, command_id: str) -> StoredCommandProgress:
        """Return the configured authoritative command progress."""
        self.order.append("load")
        self.loaded.append(command_id)
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

    async def test_send_and_retry_preserve_every_transition_fact(self) -> None:
        # Arrange
        recorder = FakeProgressRecorder()
        current = replace(
            _stored(CommandState.ACCEPTED, 0),
            result_id="result-before-retry",
        )
        clock = SendClock(
            sent_at="2026-08-23T07:31:00.000Z",
            deadline_at="2026-08-23T07:31:06.000Z",
            updated_at="2026-08-23T07:31:01.000Z",
        )

        # Act
        sent = await record_send(current, clock, recorder)
        timed_out = await record_timeout(
            sent,
            "2026-08-23T07:31:07.000Z",
            17,
            recorder,
        )

        # Assert
        self.assertEqual(
            [
                (
                    CommandEvent.SEND,
                    SendBudget(5),
                    TransitionFacts(
                        last_sent_at=clock.sent_at,
                        deadline_at=clock.deadline_at,
                        result_id="result-before-retry",
                        updated_at=clock.updated_at,
                    ),
                ),
                (
                    CommandEvent.TIME_OUT,
                    SendBudget(5),
                    TransitionFacts(
                        last_sent_at=clock.sent_at,
                        deadline_at=None,
                        result_id="result-before-retry",
                        updated_at="2026-08-23T07:31:07.000Z",
                    ),
                ),
            ],
            recorder.recorded,
        )
        self.assertEqual(
            (TimeoutOutcome.RETRY, 6_017),
            (timed_out.outcome, timed_out.wait_milliseconds),
        )


class ResultHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledgement_commits_progress_and_inbox_before_settlement(self) -> None:
        # Arrange
        transaction = FakeResultTransaction(_stored(CommandState.IN_FLIGHT, 1))
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        delivery = GuaranteedDelivery(RESULT_TOPIC, _result_payload())
        expected_identity = InboxIdentity(
            consumer="command-gateway",
            source="urn:aerial-rescue:drone:drone-vision-01",
            event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6f",
            mission_id="m-2026-0001",
            canonical_digest=hashlib.sha256(delivery.payload).hexdigest(),
        )
        expected_result = canonical.canonical_bytes(
            {
                "commandResult": "updated",
                "commandId": "cmd-2026-0001",
                "state": "acknowledged",
            }
        )

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
                [expected_identity],
                ["cmd-2026-0001"],
                [
                    (
                        expected_identity,
                        expected_result,
                        "2026-08-23T07:31:05.117Z",
                    )
                ],
                [
                    (
                        CommandEvent.ACKNOWLEDGE,
                        SendBudget(5),
                        TransitionFacts(
                            last_sent_at="2026-08-23T07:31:04.000Z",
                            deadline_at=None,
                            result_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6f",
                            updated_at="2026-08-23T07:31:05.117Z",
                        ),
                    )
                ],
                expected_result,
            ),
            (
                result.outcome,
                result.state,
                unit.committed,
                transaction.order[-2:],
                settlement.accepted,
                transaction.claimed,
                transaction.loaded,
                transaction.completed,
                transaction.recorded,
                result.result,
            ),
        )

    async def test_stale_result_is_recorded_without_reopening_or_advancing_progress(self) -> None:
        # Arrange
        transaction = FakeResultTransaction(_stored(CommandState.IN_FLIGHT, 1))
        unit = FakeResultUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        delivery = GuaranteedDelivery(RESULT_TOPIC, _result_payload("succeeded"))
        expected_result = canonical.canonical_bytes(
            {
                "commandResult": "stale",
                "commandId": "cmd-2026-0001",
                "state": "in-flight",
            }
        )

        # Act
        result = await handle_command_result(delivery, unit, settlement)

        # Assert
        self.assertEqual(
            (
                CommandResultOutcome.STALE,
                CommandState.IN_FLIGHT,
                expected_result,
                [],
                1,
                expected_result,
                True,
            ),
            (
                result.outcome,
                result.state,
                result.result,
                transaction.recorded,
                len(transaction.completed),
                transaction.completed[0][1],
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
        expected_result = canonical.canonical_bytes(
            {
                "commandResult": "identity-mismatch",
                "commandId": "cmd-2026-0001",
                "state": "in-flight",
            }
        )

        # Act
        result = await handle_command_result(
            GuaranteedDelivery(RESULT_TOPIC, _result_payload()), unit, settlement
        )

        # Assert
        self.assertEqual(
            (
                CommandResultOutcome.MISMATCH,
                CommandState.IN_FLIGHT,
                expected_result,
                [],
                1,
                expected_result,
                True,
            ),
            (
                result.outcome,
                result.state,
                result.result,
                transaction.recorded,
                len(transaction.completed),
                transaction.completed[0][1],
                unit.committed,
            ),
        )

    async def test_exact_redelivery_returns_prior_result_without_a_second_transition(self) -> None:
        # Arrange
        prior = canonical.canonical_bytes(
            {
                "commandResult": "updated",
                "commandId": "cmd-2026-0001",
                "state": "acknowledged",
            }
        )
        transaction = FakeResultTransaction(
            _stored(CommandState.SUCCEEDED, 1),
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
            (
                CommandResultOutcome.DUPLICATE,
                CommandState.ACKNOWLEDGED,
                prior,
                [],
                [],
                [],
                True,
            ),
            (
                result.outcome,
                result.state,
                result.result,
                transaction.loaded,
                transaction.recorded,
                transaction.completed,
                unit.committed,
            ),
        )

    async def test_duplicate_prior_result_is_closed_and_bound_to_the_command(self) -> None:
        # Arrange
        valid = {
            "commandResult": "updated",
            "commandId": "cmd-2026-0001",
            "state": "acknowledged",
        }
        documents = (
            {"state": "acknowledged"},
            {**valid, "commandId": "cmd-another"},
            {**valid, "commandResult": "duplicate"},
            {**valid, "untrusted": True},
            {**valid, "state": "unknown"},
            {**valid, "state": 1},
            {**valid, "state": "accepted"},
            {**valid, "state": "in-flight"},
            {**valid, "state": "abandoned"},
            None,
        )
        prior_results = (
            *(canonical.canonical_bytes(document) for document in documents),
            b"not-json",
            b'{ "commandResult":"updated","commandId":"cmd-2026-0001","state":"acknowledged"}',
        )

        # Act
        observations = []
        for prior in prior_results:
            transaction = FakeResultTransaction(
                _stored(CommandState.SUCCEEDED, 1),
                InboxOutcome(InboxDecision.DUPLICATE, prior),
            )
            unit = FakeResultUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            with pytest.raises(ProgressionError) as captured:
                await handle_command_result(
                    GuaranteedDelivery(RESULT_TOPIC, _result_payload()),
                    unit,
                    settlement,
                )
            observations.append(
                (
                    captured.value.refusal,
                    str(captured.value),
                    transaction.order,
                    transaction.loaded,
                    transaction.completed,
                    settlement.accepted,
                )
            )

        # Assert
        self.assertEqual(
            [
                (
                    ProgressionRefusal.DUPLICATE_RESULT,
                    ProgressionRefusal.DUPLICATE_RESULT.value,
                    ["claim", "rollback"],
                    [],
                    [],
                    [],
                )
            ]
            * len(prior_results),
            observations,
        )

    async def test_every_handler_generated_duplicate_result_pair_remains_accepted(self) -> None:
        # Arrange
        pairs = (
            ("updated", "acknowledged"),
            ("updated", "succeeded"),
            ("updated", "failed"),
            ("stale", "accepted"),
            ("identity-mismatch", "abandoned"),
        )

        # Act
        observations = []
        for outcome, state in pairs:
            prior = canonical.canonical_bytes(
                {
                    "commandResult": outcome,
                    "commandId": "cmd-2026-0001",
                    "state": state,
                }
            )
            transaction = FakeResultTransaction(
                _stored(CommandState.SUCCEEDED, 1),
                InboxOutcome(InboxDecision.DUPLICATE, prior),
            )
            unit = FakeResultUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            result = await handle_command_result(
                GuaranteedDelivery(RESULT_TOPIC, _result_payload()),
                unit,
                settlement,
            )
            observations.append((result.state.value, result.result, settlement.accepted))

        # Assert
        self.assertEqual(
            [
                (
                    state,
                    canonical.canonical_bytes(
                        {
                            "commandResult": outcome,
                            "commandId": "cmd-2026-0001",
                            "state": state,
                        }
                    ),
                    ["0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6f"],
                )
                for outcome, state in pairs
            ],
            observations,
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
        messages = []
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
            messages.append(str(captured.value))

        # Assert
        self.assertEqual(
            (
                [ProgressionRefusal.DUPLICATE_RESULT, ProgressionRefusal.INGRESS_KIND],
                [
                    ProgressionRefusal.DUPLICATE_RESULT.value,
                    ProgressionRefusal.INGRESS_KIND.value,
                ],
                ["refusal-commit", "settle-rejected"],
            ),
            (refusals, messages, wrong_transaction.order),
        )
        self.assertEqual(
            (
                "command-gateway-command-result",
                "unexpected-family",
            ),
            (
                wrong_unit.refusals[0].channel,
                wrong_unit.refusals[0].refusal_code,
            ),
        )

    async def test_failed_result_and_unauthorized_family_keep_closed_wire_vocabulary(self) -> None:
        # Arrange
        failed_transaction = FakeResultTransaction(_stored(CommandState.ACKNOWLEDGED, 1))
        failed_unit = FakeResultUnitOfWork(failed_transaction)
        failed_settlement = FakeSettlement(failed_transaction.order)
        refused_transaction = FakeResultTransaction(_stored(CommandState.IN_FLIGHT, 1))
        refused_unit = FakeResultUnitOfWork(refused_transaction)
        refused_settlement = FakeSettlement(refused_transaction.order)
        refused_delivery = GuaranteedDelivery(
            "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry",
            b"{}",
        )

        # Act
        failed = await handle_command_result(
            GuaranteedDelivery(RESULT_TOPIC, _result_payload("failed")),
            failed_unit,
            failed_settlement,
        )
        with pytest.raises(IngressError) as captured:
            await handle_command_result(
                refused_delivery,
                refused_unit,
                refused_settlement,
            )

        # Assert
        self.assertEqual(CommandResultOutcome.UPDATED, failed.outcome)
        self.assertEqual(CommandState.FAILED, failed.state)
        self.assertEqual(IngressRefusal.UNAUTHORIZED_FAMILY, captured.value.refusal)
        self.assertEqual(
            (
                "command-gateway-command-result",
                "unauthorized-family",
                1,
            ),
            (
                refused_unit.refusals[0].channel,
                refused_unit.refusals[0].refusal_code,
                refused_settlement.rejected,
            ),
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
            ("injected progress crash", True, False, [], []),
            (
                str(captured.value),
                unit.rolled_back,
                unit.committed,
                settlement.accepted,
                transaction.completed,
            ),
        )


if __name__ == "__main__":
    unittest.main()
