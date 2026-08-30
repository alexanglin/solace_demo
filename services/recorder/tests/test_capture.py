"""Recorder capture orchestration, durable idempotency, and settlement order."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, override

import pytest
from aerial_rescue_broker.messaging import InboundMessage, Outcome
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import Envelope, envelope_document
from aerial_rescue_contracts.topics import Family, Topic
from aerial_rescue_domain.mission import MissionError, MissionRefusal, MissionState
from aerial_rescue_recorder.capture import (
    AuditFact,
    CaptureDecision,
    CaptureError,
    CaptureProcessor,
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
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard.events import (
    BrokerEvent,
    DashboardEventError,
    DashboardEventRefusal,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MISSION_EVENT = (
    REPOSITORY_ROOT / "fixtures/golden/v1/event/mission-event-lifecycle/baseline.json"
).read_bytes()
MISSION_TOPIC = "aerial-rescue/v1/mission-01/mission/event/lifecycle"

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
    transitions: list[tuple[str, MissionState]] = field(default_factory=list)
    completions: list[tuple[InboxFact, int, str]] = field(default_factory=list)
    links: list[tuple[BrokerEvent, str, int]] = field(default_factory=list)

    async def claim_inbox(self, fact: InboxFact, /) -> InboxOutcome:
        """Claim or replay one durable inbox result."""
        self.inbox_facts.append(fact)
        self.calls.append("claim-inbox")
        return self.inbox

    async def record_source_event(self, fact: SourceEventFact, /) -> None:
        """Record the source-event operation."""
        self.source_facts.append(fact)
        self.calls.append("record-source-event")

    async def apply_mission_lifecycle(self, mission_id: str, target: MissionState, /) -> None:
        """Record the domain-approved mission transition this event carries."""
        self.transitions.append((mission_id, target))
        self.calls.append("apply-mission-lifecycle")

    async def append_audit(self, fact: AuditFact, /) -> int:
        """Append one audit fact or inject the requested failure."""
        self.audit_facts.append(fact)
        self.calls.append("append-audit")
        if self.fail_at == "append-audit":
            message = "injected append failure"
            raise RuntimeError(message)
        return self.audit_ordinal

    async def link_broker_event(self, event: BrokerEvent, mission_id: str, ordinal: int, /) -> None:
        """Record the provenance link the dashboard watermark reads."""
        self.links.append((event, mission_id, ordinal))
        self.calls.append("link-broker-event")

    async def complete_inbox(self, fact: InboxFact, ordinal: int, processed_at: str, /) -> None:
        """Record completion inside the same transaction."""
        self.completions.append((fact, ordinal, processed_at))
        self.calls.append("complete-inbox")


@dataclass
class _RefusingTransaction(_Transaction):
    """Refuse the provenance link the way a badly-behaved producer's sequence does."""

    @override
    async def link_broker_event(self, event: BrokerEvent, mission_id: str, ordinal: int, /) -> None:
        """Raise the exact permanent refusal the deployed store raises."""
        del event, mission_id, ordinal
        self.calls.append("link-broker-event")
        raise DashboardEventError(DashboardEventRefusal.STALE_SEQUENCE, 0)


@dataclass
class _RejectableSettlement:
    """Record which one-shot settlement a permanent refusal selected."""

    calls: list[str]

    def accept(self) -> None:
        """Settle the delivery as accepted."""
        self.calls.append("settle-accepted")

    def reject(self) -> None:
        """Settle the delivery as permanently refused."""
        self.calls.append("settle-rejected")


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

    def reject(self) -> None:
        """Settle a delivery the durable store refused permanently."""
        self.calls.append("settle-rejected")


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
                    "apply-mission-lifecycle",
                    "append-audit",
                    "link-broker-event",
                    "complete-inbox",
                    "commit",
                    "settle-accepted",
                ],
            ),
            (outcome.decision, outcome.audit_ordinal, calls),
        )

    async def test_a_recorded_fact_links_the_provenance_the_dashboard_watermark_reads(
        self,
    ) -> None:
        # Arrange
        calls: list[str] = []
        transaction = _Transaction(calls)
        recorder = Recorder("recorder", _Transactions(transaction))
        notification = _notification()

        # Act
        outcome = await recorder.capture(notification, _Settlement(calls))

        # Assert
        envelope = notification.envelope
        self.assertEqual(
            [(envelope.source, envelope.id, 1, MISSION, outcome.audit_ordinal)],
            [
                (event.source, event.event_id, event.source_sequence, mission, ordinal)
                for event, mission, ordinal in transaction.links
            ],
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
                notification.envelope.time,
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
                [
                    "begin",
                    "claim-inbox",
                    "record-source-event",
                    "apply-mission-lifecycle",
                    "append-audit",
                    "rollback",
                ],
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
                    "link-broker-event",
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


@dataclass(frozen=True)
class _Message(InboundMessage):
    payload: bytes | None = MISSION_EVENT
    destination: str | None = MISSION_TOPIC

    @override
    def get_payload_as_bytes(self) -> bytes | None:
        return self.payload

    @override
    def get_destination_name(self) -> str | None:
        return self.destination

    @override
    def get_properties(self) -> Mapping[str, object]:
        return {}


@dataclass
class _Receiver:
    calls: list[str]

    def receive(self, _timeout_milliseconds: int, /) -> InboundMessage | None:
        return None

    def settle(self, _message: InboundMessage, outcome: Outcome, /) -> None:
        self.calls.append(f"settle:{outcome.name}")


@dataclass
class _Appender:
    calls: list[str]
    refusal: DashboardEventRefusal | None = None
    unexpected: Exception | None = None
    payloads: list[bytes] = field(default_factory=list)

    async def append(self, _event: BrokerEvent, record: AuditRecord) -> None:
        self.calls.append("append")
        if self.unexpected is not None:
            raise self.unexpected
        if self.refusal is not None:
            raise DashboardEventError(self.refusal, "redacted")
        self.payloads.append(record.payload)
        self.calls.append("commit")


class NormalizedGuaranteedCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_guaranteed_message_is_acknowledged_after_append_returns(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls)
        processor = CaptureProcessor(appender)

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "commit", "settle:ACCEPTED"], calls)
        self.assertEqual(1, len(appender.payloads))
        self.assertNotIn(b"traceparent", appender.payloads[0])

    async def test_invalid_topic_envelope_binding_or_payload_is_permanently_rejected(self) -> None:
        # Arrange
        calls: list[str] = []
        appender = _Appender(calls)
        processor = CaptureProcessor(appender)
        malformed_payload = MISSION_EVENT.replace(b'"SEARCHING"', b'"UNKNOWN"')
        cases = (
            _Message(destination="foreign/topic"),
            _Message(payload=None),
            _Message(payload=b'{"id":"duplicate","id":"duplicate"}'),
            _Message(destination=MISSION_TOPIC.replace("mission-01", "other-mission")),
            _Message(payload=malformed_payload),
        )

        # Act
        for message in cases:
            receiver = _Receiver(calls)
            await processor.process_guaranteed(receiver, message)

        # Assert
        self.assertEqual([], appender.payloads)
        self.assertEqual(["settle:REJECTED"] * len(cases), calls)

    async def test_transient_store_refusal_leaves_message_recoverable(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls, refusal=DashboardEventRefusal.SOURCE_MOVED)
        processor = CaptureProcessor(appender)

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:FAILED"], calls)

    async def test_divergent_duplicate_is_permanently_rejected(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls, refusal=DashboardEventRefusal.DIVERGENT_DUPLICATE)
        processor = CaptureProcessor(appender)

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:REJECTED"], calls)

    async def test_domain_refused_mission_transition_is_permanently_rejected(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        refused = MissionError(MissionRefusal.TRANSITION, "synthetic-regression")
        processor = CaptureProcessor(_Appender(calls, unexpected=refused))

        # Act
        await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:REJECTED"], calls)

    async def test_unexpected_append_failure_is_left_recoverable_and_propagated(self) -> None:
        # Arrange
        calls: list[str] = []
        receiver = _Receiver(calls)
        appender = _Appender(calls, unexpected=RuntimeError("synthetic failure"))
        processor = CaptureProcessor(appender)

        # Act
        with pytest.raises(RuntimeError, match="synthetic failure"):
            await processor.process_guaranteed(receiver, _Message())

        # Assert
        self.assertEqual(["append", "settle:FAILED"], calls)


class BestEffortCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_telemetry_has_no_acknowledgement_claim(self) -> None:
        # Arrange
        calls: list[str] = []
        appender = _Appender(calls)
        processor = CaptureProcessor(appender)
        telemetry = (
            REPOSITORY_ROOT / "fixtures/golden/v1/event/drone-telemetry/baseline.json"
        ).read_bytes()
        message = _Message(
            payload=telemetry,
            destination="aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry",
        )

        # Act
        await processor.process_best_effort(message)

        # Assert
        self.assertEqual(["append", "commit"], calls)

    async def test_direct_store_outcomes_have_no_discarded_result_or_acknowledgement(self) -> None:
        # Arrange
        cases = (
            _Appender([]),
            _Appender([], refusal=DashboardEventRefusal.DIVERGENT_DUPLICATE),
            _Appender([], refusal=DashboardEventRefusal.SOURCE_MOVED),
        )

        # Act
        for appender in cases:
            await CaptureProcessor(appender).process_best_effort(_Message())

        # Assert
        self.assertEqual(
            [["append", "commit"], ["append"], ["append"]],
            [case.calls for case in cases],
        )

    async def test_direct_invalid_input_is_rejected_without_calling_the_store(self) -> None:
        # Arrange
        calls: list[str] = []
        processor = CaptureProcessor(_Appender(calls))

        # Act
        await processor.process_best_effort(_Message(payload=None))

        # Assert
        self.assertEqual([], calls)


class PermanentRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_permanently_refused_direct_fact_is_reported_not_raised(self) -> None:
        # Arrange
        calls: list[str] = []
        recorder = Recorder("recorder", _Transactions(_RefusingTransaction(calls)))

        # Act
        outcome = await recorder.capture(_notification(Family.DRONE_TELEMETRY), None)

        # Assert
        self.assertEqual(CaptureDecision.REFUSED, outcome.decision)

    async def test_a_permanently_refused_guaranteed_fact_is_rejected_not_accepted(self) -> None:
        # Arrange
        calls: list[str] = []
        settlement = _RejectableSettlement(calls)
        recorder = Recorder("recorder", _Transactions(_RefusingTransaction(calls)))

        # Act
        outcome = await recorder.capture(_notification(), settlement)

        # Assert
        self.assertEqual(
            (CaptureDecision.REFUSED, ["settle-rejected"]),
            (outcome.decision, [call for call in calls if call.startswith("settle-")]),
        )


class MissionLifecycleTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_mission_event_transitions_the_mission_before_its_audit_row(self) -> None:
        """The recorder owns the lifecycle column, and the deployed composition never moved it."""
        # Arrange
        calls: list[str] = []
        transaction = _Transaction(calls)
        recorder = Recorder("recorder", _Transactions(transaction))

        # Act
        await recorder.capture(_notification(), _Settlement(calls))

        # Assert
        self.assertEqual([(MISSION, MissionState.SEARCHING)], transaction.transitions)
        self.assertLess(
            calls.index("apply-mission-lifecycle"),
            calls.index("append-audit"),
        )

    async def test_an_event_of_another_family_transitions_no_mission(self) -> None:
        # Arrange
        calls: list[str] = []
        transaction = _Transaction(calls)
        recorder = Recorder("recorder", _Transactions(transaction))

        # Act
        await recorder.capture(_notification(Family.DRONE_TELEMETRY), None)

        # Assert
        self.assertEqual([], transaction.transitions)
        self.assertNotIn("apply-mission-lifecycle", calls)


class MissionLifecyclePayloadRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_lifecycle_member_that_is_not_text_is_refused_before_any_effect(self) -> None:
        """The committed schema forbids this, so reaching it means a validator was bypassed."""
        # Arrange
        calls: list[str] = []
        transaction = _Transaction(calls)
        recorder = Recorder("recorder", _Transactions(transaction))
        notification = _lifecycle_notification({"missionId": MISSION, "lifecycle": 7})

        # Act
        with pytest.raises(CaptureError) as refused:
            await recorder.capture(notification, _Settlement(calls))

        # Assert
        self.assertIs(CaptureRefusal.MISSION_LIFECYCLE, refused.value.refusal)
        self.assertEqual([], transaction.transitions)

    async def test_a_lifecycle_name_the_domain_does_not_hold_is_refused(self) -> None:
        # Arrange
        calls: list[str] = []
        transaction = _Transaction(calls)
        recorder = Recorder("recorder", _Transactions(transaction))
        notification = _lifecycle_notification({"missionId": MISSION, "lifecycle": "SWEEPING"})

        # Act
        with pytest.raises(CaptureError) as refused:
            await recorder.capture(notification, _Settlement(calls))

        # Assert
        self.assertIs(CaptureRefusal.MISSION_LIFECYCLE, refused.value.refusal)
        self.assertEqual([], transaction.transitions)


def _lifecycle_notification(data: dict[str, object]) -> ReceivedNotification:
    """Return one mission-lifecycle notification carrying an out-of-contract payload."""
    accepted = _notification()
    return replace(accepted, envelope=replace(accepted.envelope, data=data))
