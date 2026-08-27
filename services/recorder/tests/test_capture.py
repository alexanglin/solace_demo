"""Recorder capture orchestration, durable idempotency, and settlement order."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field, replace
from typing import Final

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import Envelope, envelope_document
from aerial_rescue_contracts.topics import Family, Topic
from aerial_rescue_recorder.capture import (
    AuditFact,
    CaptureDecision,
    CaptureError,
    CaptureRefusal,
    InboxDecision,
    InboxFact,
    InboxOutcome,
    ReceivedNotification,
    Recorder,
    RecordingPolicy,
    SourceEventFact,
    recording_policy,
)

MISSION: Final = "mission-1"
EVENT_ID: Final = "event-1"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"
INSTANT: Final = "2026-08-25T12:00:00.000Z"
OBSERVED_AT: Final = "2026-08-25T12:00:00.250Z"


def _notification(family: Family = Family.MISSION_EVENT) -> ReceivedNotification:
    """Return one already validated notification on the selected recordable family."""
    if family is Family.DRONE_TELEMETRY:
        topic = Topic(family, MISSION, {"droneId": "drone-1"})
        envelope = Envelope(
            id=EVENT_ID,
            source="urn:aerial-rescue:fleet:drone-1",
            type="aerial-rescue.v1.drone.telemetry",
            subject=MISSION,
            time=INSTANT,
            dataschema=(
                "https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
            ),
            sequence="000000000000001",
            correlation_id="correlation-1",
            traceparent=TRACEPARENT,
            data={"missionId": MISSION, "droneId": "drone-1"},
        )
    else:
        topic = Topic(family, MISSION, {"eventType": "lifecycle"})
        envelope = Envelope(
            id=EVENT_ID,
            source="urn:aerial-rescue:mission-lifecycle:run-1",
            type="aerial-rescue.v1.mission.event.lifecycle",
            subject=MISSION,
            time=INSTANT,
            dataschema=(
                "https://aerial-rescue.invalid/schemas/v1/payload/"
                "mission-event-lifecycle.schema.json"
            ),
            sequence="000000000000001",
            correlation_id="correlation-1",
            traceparent=TRACEPARENT,
            data={"missionId": MISSION, "lifecycle": "SEARCHING"},
        )
    return ReceivedNotification(topic=topic, envelope=envelope, observed_at=OBSERVED_AT)


@dataclass
class _Transaction:
    """Record the exact durable operations one transaction receives."""

    calls: list[str]
    inbox: InboxOutcome = field(default_factory=lambda: InboxOutcome(InboxDecision.CLAIMED, None))
    fail_at: str | None = None
    audit_ordinal: int = 7
    inbox_facts: list[InboxFact] = field(default_factory=list)
    source_facts: list[SourceEventFact] = field(default_factory=list)
    audit_facts: list[AuditFact] = field(default_factory=list)
    completions: list[tuple[InboxFact, int, str]] = field(default_factory=list)

    async def claim_inbox(self, fact: InboxFact, /) -> InboxOutcome:
        """Claim or replay one durable inbox result."""
        self.inbox_facts.append(fact)
        self.calls.append("claim-inbox")
        return self.inbox

    async def record_source_event(self, fact: SourceEventFact, /) -> None:
        """Record the source-event operation."""
        self.source_facts.append(fact)
        self.calls.append("record-source-event")

    async def append_audit(self, fact: AuditFact, /) -> int:
        """Append one audit fact or inject the requested failure."""
        self.audit_facts.append(fact)
        self.calls.append("append-audit")
        if self.fail_at == "append-audit":
            message = "injected append failure"
            raise RuntimeError(message)
        return self.audit_ordinal

    async def complete_inbox(self, fact: InboxFact, ordinal: int, processed_at: str, /) -> None:
        """Record completion inside the same transaction."""
        self.completions.append((fact, ordinal, processed_at))
        self.calls.append("complete-inbox")


@dataclass
class _UnitOfWork:
    """Expose one recording transaction and its commit/rollback boundary."""

    transaction: _Transaction

    async def __aenter__(self) -> _Transaction:
        """Open the scripted transaction."""
        self.transaction.calls.append("begin")
        return self.transaction

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        """Record whether leaving the boundary commits or rolls back."""
        del exception, traceback
        action = "commit" if exception_type is None else "rollback"
        self.transaction.calls.append(action)


@dataclass
class _Transactions:
    """Create one injected unit of work without opening a real database."""

    transaction: _Transaction
    opens: int = 0

    def open(self) -> _UnitOfWork:
        """Return the scripted transaction context."""
        self.opens += 1
        return _UnitOfWork(self.transaction)


@dataclass
class _Settlement:
    """Record an accepted guaranteed-delivery settlement."""

    calls: list[str]

    def accept(self) -> None:
        """Accept the delivery after its transaction commits."""
        self.calls.append("settle-accepted")


class RecordingPolicyTests(unittest.TestCase):
    def test_every_family_is_either_a_recorded_notification_or_excluded_transport(self) -> None:
        # Arrange
        excluded = {
            Family.AGENT_RESPONSE,
            Family.GATEWAY_REQUEST,
            Family.GATEWAY_RESPONSE,
        }

        # Act
        policies = {family: recording_policy(family) for family in Family}

        # Assert
        self.assertEqual(
            (
                {
                    family
                    for family, policy in policies.items()
                    if policy is RecordingPolicy.EXCLUDED
                },
                15,
            ),
            (excluded, len(policies)),
        )


class GuaranteedCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_excluded_integration_family_is_refused_before_storage_or_settlement(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []
        transactions = _Transactions(_Transaction(calls))
        recorder = Recorder("recorder", transactions)
        excluded = replace(
            _notification(),
            topic=Topic(Family.AGENT_RESPONSE, MISSION, {"agentName": "MissionCoordinator"}),
        )

        # Act
        with pytest.raises(CaptureError) as captured:
            await recorder.capture(excluded, _Settlement(calls))

        # Assert
        self.assertEqual(
            (CaptureRefusal.EXCLUDED_FAMILY, 0, []),
            (captured.value.refusal, transactions.opens, calls),
        )

    async def test_a_new_guaranteed_fact_commits_every_durable_effect_before_settlement(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []
        transactions = _Transactions(_Transaction(calls))
        settlement = _Settlement(calls)
        recorder = Recorder("recorder", transactions)

        # Act
        outcome = await recorder.capture(_notification(), settlement)

        # Assert
        self.assertEqual(
            (
                CaptureDecision.RECORDED,
                7,
                [
                    "begin",
                    "claim-inbox",
                    "record-source-event",
                    "append-audit",
                    "complete-inbox",
                    "commit",
                    "settle-accepted",
                ],
            ),
            (outcome.decision, outcome.audit_ordinal, calls),
        )

    async def test_an_exact_duplicate_reuses_its_ordinal_without_a_second_effect(self) -> None:
        # Arrange
        calls: list[str] = []
        duplicate = InboxOutcome(InboxDecision.DUPLICATE, 7)
        transactions = _Transactions(_Transaction(calls, inbox=duplicate))
        settlement = _Settlement(calls)
        recorder = Recorder("recorder", transactions)

        # Act
        outcome = await recorder.capture(_notification(), settlement)

        # Assert
        self.assertEqual(
            (CaptureDecision.DUPLICATE, 7, ["begin", "claim-inbox", "commit", "settle-accepted"]),
            (outcome.decision, outcome.audit_ordinal, calls),
        )

    async def test_provenance_uses_complete_canonical_bytes_and_the_receiver_observed_instant(
        self,
    ) -> None:
        # Arrange
        transaction = _Transaction([])
        notification = _notification()
        recorder = Recorder("recorder", _Transactions(transaction))

        # Act
        await recorder.capture(notification, _Settlement(transaction.calls))
        inbox = transaction.inbox_facts[0]
        source = transaction.source_facts[0]
        audit = transaction.audit_facts[0]

        # Assert
        self.assertEqual(
            (
                source_event_digest(notification.envelope),
                canonical.canonical_bytes(envelope_document(notification.envelope)),
                OBSERVED_AT,
                OBSERVED_AT,
                notification.envelope.time,
            ),
            (
                inbox.canonical_digest,
                source.canonical_event,
                source.observed_at,
                transaction.completions[0][2],
                audit.occurred_at,
            ),
        )

    async def test_a_failed_durable_effect_rolls_back_and_leaves_the_delivery_unsettled(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []
        transactions = _Transactions(_Transaction(calls, fail_at="append-audit"))
        settlement = _Settlement(calls)
        recorder = Recorder("recorder", transactions)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await recorder.capture(_notification(), settlement)

        # Assert
        self.assertEqual(
            (
                "injected append failure",
                ["begin", "claim-inbox", "record-source-event", "append-audit", "rollback"],
            ),
            (str(captured.value), calls),
        )

    async def test_topic_mismatch_and_malformed_store_ordinals_roll_back_unsettled(self) -> None:
        # Arrange
        mismatched = replace(
            _notification(),
            topic=Topic(Family.MISSION_EVENT, "mission-2", {"eventType": "lifecycle"}),
        )
        cases = (
            (mismatched, _Transaction([]), CaptureRefusal.TOPIC_BINDING),
            (
                _notification(),
                _Transaction([], inbox=InboxOutcome(InboxDecision.CLAIMED, 7)),
                CaptureRefusal.INBOX_OUTCOME,
            ),
            (_notification(), _Transaction([], audit_ordinal=0), CaptureRefusal.AUDIT_ORDINAL),
        )

        # Act
        refusals = []
        settlements = []
        for notification, transaction, expected in cases:
            with self.subTest(expected=expected):
                settlement = _Settlement(transaction.calls)
                with pytest.raises(CaptureError) as captured:
                    await Recorder("recorder", _Transactions(transaction)).capture(
                        notification, settlement
                    )
                refusals.append(captured.value.refusal)
                settlements.append("settle-accepted" in transaction.calls)

        # Assert
        self.assertEqual(
            (
                [
                    CaptureRefusal.TOPIC_BINDING,
                    CaptureRefusal.INBOX_OUTCOME,
                    CaptureRefusal.AUDIT_ORDINAL,
                ],
                [False, False, False],
            ),
            (refusals, settlements),
        )

    async def test_a_guaranteed_notification_without_a_settlement_capability_is_refused_pre_io(
        self,
    ) -> None:
        # Arrange
        transactions = _Transactions(_Transaction([]))
        recorder = Recorder("recorder", transactions)

        # Act
        with pytest.raises(CaptureError) as captured:
            await recorder.capture(_notification(), None)

        # Assert
        self.assertEqual(
            (CaptureRefusal.SETTLEMENT_MISMATCH, 0),
            (captured.value.refusal, transactions.opens),
        )


class DirectCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_telemetry_is_best_effort_but_still_deduplicated_durably(self) -> None:
        # Arrange
        calls: list[str] = []
        transactions = _Transactions(_Transaction(calls))
        recorder = Recorder("recorder", transactions)

        # Act
        outcome = await recorder.capture(_notification(Family.DRONE_TELEMETRY), None)

        # Assert
        self.assertEqual(
            (
                CaptureDecision.RECORDED,
                [
                    "begin",
                    "claim-inbox",
                    "record-source-event",
                    "append-audit",
                    "complete-inbox",
                    "commit",
                ],
            ),
            (outcome.decision, calls),
        )

    async def test_direct_delivery_refuses_a_settlement_capability_before_opening_storage(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []
        transactions = _Transactions(_Transaction(calls))
        recorder = Recorder("recorder", transactions)

        # Act
        with pytest.raises(CaptureError) as captured:
            await recorder.capture(_notification(Family.DRONE_TELEMETRY), _Settlement(calls))

        # Assert
        self.assertEqual(
            (CaptureRefusal.SETTLEMENT_MISMATCH, 0, []),
            (captured.value.refusal, transactions.opens, calls),
        )


if __name__ == "__main__":
    unittest.main()
