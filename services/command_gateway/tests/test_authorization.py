"""Atomic operator-command authorization, dispatch staging, and settlement order."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import cast
from unittest.mock import patch

import aerial_rescue_command_gateway.authorization as authorization_module
import aerial_rescue_command_gateway.command_artifacts as command_artifacts_module
import pytest
from aerial_rescue_command_gateway.authorization import (
    AuthorizationClock,
    AuthorizationError,
    AuthorizationOutcome,
    AuthorizationRefusal,
    AuthorizationStamp,
    handle_operator_command,
)
from aerial_rescue_command_gateway.command_artifacts import (
    ArtifactError,
    ArtifactRefusal,
    build_authorization_artifacts,
)
from aerial_rescue_command_gateway.ingress import (
    ApprovalAction,
    EscalateRescueAction,
    IngressError,
    IngressRefusal,
    OperatorCommandIngress,
    accept_ingress,
)
from aerial_rescue_command_gateway.ports import BoundApproval, GuaranteedDelivery
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import (
    evidence_decision_digest,
    proposal_digest,
)
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_contracts.topics import Family, Topic, TopicError, TopicRefusal
from aerial_rescue_domain.approvals import (
    ApprovalError,
    ApprovalRefusal,
    ApprovalState,
    ClockReading,
    Proposal,
    approve,
)
from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_domain.scoring import EvidenceBand
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.command_progress import CommandIdentity
from aerial_rescue_store.evidence import EvidenceDecisionOutcome, StoredEvidenceDecision
from aerial_rescue_store.idempotency import ClaimOutcome, StoredClaim
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.outbox import StagedCommand
from aerial_rescue_store.proposals import StoredProposal

from .fixture_paths import repository_root

ROOT = repository_root(Path(__file__))
COMMAND_TOPIC = "aerial-rescue/v1/mission-synthetic-0001/operator/command/escalate-rescue"
ASSIGN_TOPIC = "aerial-rescue/v1/mission-synthetic-0001/operator/command/assign-sector"

STAMP = AuthorizationStamp(
    producer_id="gateway-synthetic-01",
    command_event_id="event-drone-command-escalate-0001",
    audit_record_id="audit-command-escalate-authorized-0001",
    audit_event_id="event-audit-command-escalate-authorized-0001",
    occurred_at="2026-08-25T12:05:00.000Z",
    command_sequence=10,
    audit_sequence=19,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
)

NOW = AuthorizationClock(
    reading=ClockReading(
        wall=parse_instant("2026-08-25T12:02:00.000Z"),
        monotonic=timedelta(minutes=1),
    ),
    runtime_epoch="gateway-start-0001",
)


def _fixture(relative: str) -> bytes:
    """Return exact committed fixture bytes."""
    return (ROOT / "fixtures" / "golden" / "v1" / relative).read_bytes()


def _proposal() -> StoredProposal:
    """Return the immutable proposal named by the escalation fixture."""
    envelope = decode_envelope(_fixture("event/agent-proposal/baseline.json"))
    data = envelope.data
    return StoredProposal(
        proposal_id=str(data["proposalId"]),
        mission_id=str(data["missionId"]),
        source_event_id=str(data["sourceEventId"]),
        source_event_digest=str(data["sourceEventDigest"]),
        agent_name=str(data["agentName"]),
        invocation_id=str(data["sourceInvocationId"]),
        proposal_type=str(data["proposalType"]),
        proposal_digest=str(data["proposalDigest"]),
        payload=canonical.canonical_bytes(data),
        drone_id=str(data["droneId"]),
        latitude_microdegrees=int(str(data["latitudeMicrodegrees"])),
        longitude_microdegrees=int(str(data["longitudeMicrodegrees"])),
        command_type=str(data["commandType"]),
        issued_at=envelope.time,
        sequence=int(envelope.sequence),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        traceparent=envelope.traceparent,
    )


def _decision() -> StoredEvidenceDecision:
    """Return the exact corroborated evidence decision selected by the command."""
    payload = cast(
        "dict[str, object]", canonical.decode(_fixture("payload/evidence-decision/baseline.json"))
    )
    return StoredEvidenceDecision(
        decision_id=str(payload["evidenceDecisionId"]),
        mission_id=str(payload["missionId"]),
        proposal_id=str(payload["proposalId"]),
        proposal_digest=str(payload["proposalDigest"]),
        decision_digest=str(payload["evidenceDecisionDigest"]),
        decision_version=int(str(payload["evidenceDecisionVersion"])),
        score_version=int(str(payload["scoreVersion"])),
        score=int(str(payload["score"])),
        band=EvidenceBand(str(payload["band"])),
        outcome=EvidenceDecisionOutcome(str(payload["outcome"])),
        contributors=canonical.canonical_bytes(payload["contributors"]),
        payload=canonical.canonical_bytes(payload),
        decided_at="2026-08-25T12:04:00.000Z",
        sequence=7,
    )


def _approval(state: ApprovalState = ApprovalState.APPROVED) -> BoundApproval:
    """Return the exact single-use approval selected by the command."""
    proposal = _proposal()
    parameters = cast("dict[str, object]", canonical.decode(proposal.payload))
    issued = ClockReading(
        wall=parse_instant("2026-08-25T12:01:00.000Z"),
        monotonic=timedelta(seconds=-50),
    )
    approved = approve(
        ApprovalState.REQUESTED,
        Proposal(proposal.mission_id, proposal.proposal_id, parameters),
        "operator-synthetic-0001",
        issued,
        timedelta(minutes=5),
    )
    return BoundApproval(
        approval_id="approval-synthetic-0001",
        approval=replace(approved, state=state),
        durable_approval=StoredApproval(
            mission_id=proposal.mission_id,
            proposal_id=proposal.proposal_id,
            state=state,
            operator_identity="operator-synthetic-0001",
            issued_wall="2026-08-25T12:01:00.000Z",
            issued_monotonic_milliseconds=0,
            time_to_live_milliseconds=300_000,
            proposal_digest=approved.proposal_digest,
        ),
        evidence_decision_id="decision-synthetic-0001",
        evidence_decision_digest=(
            "3c3775801fc324695e0f1eca64cf8fa91d6f213eec7968c71ffe8db61ce6abe3"
        ),
        evidence_decision_version=1,
        action=ApprovalAction(
            commandType="escalate-rescue",
            droneId="drone-synthetic-01",
            latitudeMicrodegrees=45123456,
            longitudeMicrodegrees=-75123456,
        ),
        runtime_epoch=NOW.runtime_epoch,
    )


class FakeAuthorizationTransaction:
    """One command-authorization transaction with injected authoritative rows."""

    def __init__(
        self,
        approval: BoundApproval | None = None,
        claim: InboxOutcome | None = None,
        command_claim: ClaimOutcome | None = None,
        failure: Exception | None = None,
    ) -> None:
        """Configure approval state, duplicate decisions, and one stage failure."""
        self.proposal = _proposal()
        self.decision = _decision()
        self.approval = approval or _approval()
        self.claim_outcome = claim or InboxOutcome(InboxDecision.CLAIMED, None)
        self.command_claim = command_claim or ClaimOutcome(IdempotencyDecision.EXECUTE, None)
        self.failure = failure
        self.consumed: list[BoundApproval] = []
        self.audits: list[AuditRecord] = []
        self.application_events: list[StagedApplicationEvent] = []
        self.commands: list[StagedCommand] = []
        self.progress: list[CommandIdentity] = []
        self.progress_initializations: list[tuple[CommandIdentity, str]] = []
        self.results: list[tuple[str, bytes]] = []
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.inbox_claims: list[InboxIdentity] = []
        self.command_claims: list[StoredClaim] = []
        self.loaded_proposal_ids: list[str] = []
        self.loaded_decision_ids: list[str] = []
        self.loaded_approval_ids: list[str] = []
        self.order: list[str] = []

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Return the configured broker inbox decision."""
        self.order.append("claim-inbox")
        self.inbox_claims.append(identity)
        return self.claim_outcome

    async def claim_command(self, claim: StoredClaim) -> ClaimOutcome:
        """Return the configured command idempotency decision."""
        self.order.append("claim-command")
        self.command_claims.append(claim)
        return self.command_claim

    async def load_proposal(self, proposal_id: str) -> StoredProposal:
        """Return the immutable normalized proposal."""
        self.order.append("load-proposal")
        self.loaded_proposal_ids.append(proposal_id)
        return self.proposal

    async def load_decision(self, decision_id: str) -> StoredEvidenceDecision:
        """Return the immutable corroborated evidence decision."""
        self.order.append("load-decision")
        self.loaded_decision_ids.append(decision_id)
        return self.decision

    async def load_approval(self, proposal_id: str) -> BoundApproval:
        """Return the authoritative approval row under its lock."""
        self.order.append("load-approval")
        self.loaded_approval_ids.append(proposal_id)
        return self.approval

    async def persist_consumed(self, approval: BoundApproval) -> None:
        """Buffer only the executed approval returned by domain consumption."""
        self.order.append("consume-approval")
        self.consumed.append(approval)

    async def append_audit(self, record: AuditRecord) -> None:
        """Buffer one append-only authorization audit."""
        self.order.append("append-audit")
        self.audits.append(record)

    async def stage_application(self, event: StagedApplicationEvent) -> None:
        """Buffer the audit publication."""
        self.order.append("stage-audit-event")
        self.application_events.append(event)

    async def stage_command(self, command: StagedCommand) -> None:
        """Buffer the exact command or inject a pre-commit crash."""
        self.order.append("stage-command")
        if self.failure is not None:
            raise self.failure
        self.commands.append(command)

    async def initialize_progress(self, identity: CommandIdentity, updated_at: str) -> None:
        """Buffer accepted, unsent command progress."""
        self.order.append("initialize-progress")
        self.progress.append(identity)
        self.progress_initializations.append((identity, updated_at))

    async def record_result(self, key: str, result: bytes) -> None:
        """Buffer the result future idempotent repeats receive."""
        self.order.append("record-result")
        self.results.append((key, result))

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the broker inbox claim."""
        self.order.append("complete-inbox")
        self.completed.append((identity, result, processed_at))


class FakeAuthorizationUnitOfWork:
    """Commit only after all four authorization effects and durable bookkeeping."""

    def __init__(self, transaction: FakeAuthorizationTransaction) -> None:
        """Wrap one transaction."""
        self.transaction = transaction
        self.committed = False
        self.rolled_back = False
        self.refusals: list[BrokerRefusalCandidate] = []

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Record one independently committed malformed-ingress fact."""
        self.transaction.order.append("refusal-commit")
        self.refusals.append(fact)
        return _refusal_outcome(fact)

    def begin(self) -> FakeAuthorizationUnitOfWork:
        """Return this transaction context."""
        return self

    async def __aenter__(self) -> FakeAuthorizationTransaction:
        """Expose the typed transaction."""
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
    """Record broker acceptance, optionally failing after commit."""

    def __init__(self, order: list[str], failure: Exception | None = None) -> None:
        """Attach to transaction order and configure a settlement failure."""
        self.order = order
        self.failure = failure
        self.accepted: list[str] = []
        self.rejected = 0

    async def accept(self, event_id: str) -> None:
        """Accept after commit or raise the injected transport failure."""
        self.order.append("settle")
        if self.failure is not None:
            raise self.failure
        self.accepted.append(event_id)

    async def reject(self) -> None:
        """Record permanent settlement after durable refusal evidence."""
        self.order.append("settle-rejected")
        self.rejected += 1


def _refusal_outcome(fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
    """Return one deterministic committed fact for the handler fake."""
    stored = StoredBrokerRefusal(
        fact.consumer,
        fact.source,
        fact.family,
        fact.channel,
        fact.refusal_code,
        fact.raw_digest,
        STAMP.occurred_at,
    )
    return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)


def _delivery() -> GuaranteedDelivery:
    """Return the exact escalation operator command."""
    return GuaranteedDelivery(
        topic=COMMAND_TOPIC,
        payload=_fixture("event/operator-command/escalate-rescue.json"),
    )


def _assign_delivery() -> GuaranteedDelivery:
    """Return the exact sector-assignment operator command."""
    return GuaranteedDelivery(
        topic=ASSIGN_TOPIC,
        payload=_fixture("event/operator-command/baseline.json"),
    )


def _accepted(delivery: GuaranteedDelivery) -> OperatorCommandIngress:
    """Return one validated operator command ingress value."""
    accepted = accept_ingress(delivery.payload, delivery.topic)
    assert isinstance(accepted, OperatorCommandIngress)
    return accepted


def _escalation_action() -> EscalateRescueAction:
    """Return the exact validated escalation action under test."""
    action = _accepted(_delivery()).payload.action
    assert isinstance(action, EscalateRescueAction)
    return action


def _proposal_payload_case(
    updates: dict[str, object],
    *,
    recompute: bool,
) -> tuple[StoredProposal, EscalateRescueAction, dict[str, object]]:
    """Return a proposal row, action, and decoded payload with controlled changes."""
    stored = _proposal()
    payload = cast("dict[str, object]", canonical.decode(stored.payload))
    payload.update(updates)
    if recompute:
        payload["proposalDigest"] = proposal_digest(payload)
    changed_digest = str(payload["proposalDigest"])
    action_updates: dict[str, object] = {"proposal_digest": changed_digest}
    if "proposalVersion" in updates:
        action_updates["proposal_version"] = updates["proposalVersion"]
    action = _escalation_action().model_copy(update=action_updates)
    changed = replace(
        stored,
        proposal_digest=changed_digest,
        payload=canonical.canonical_bytes(payload),
    )
    return changed, action, payload


def _decision_payload_case(
    updates: dict[str, object],
) -> tuple[StoredEvidenceDecision, EscalateRescueAction, dict[str, object]]:
    """Return a decision row, action, and decoded payload with a valid new self digest."""
    stored = _decision()
    payload = cast("dict[str, object]", canonical.decode(stored.payload))
    payload.update(updates)
    payload["evidenceDecisionDigest"] = evidence_decision_digest(payload)
    changed_digest = str(payload["evidenceDecisionDigest"])
    action = _escalation_action().model_copy(update={"evidence_decision_digest": changed_digest})
    changed = replace(
        stored,
        decision_digest=changed_digest,
        payload=canonical.canonical_bytes(payload),
    )
    return changed, action, payload


def _decision_guard_cases() -> tuple[
    tuple[StoredEvidenceDecision, EscalateRescueAction, dict[str, object]], ...
]:
    """Return one otherwise-valid case for every independent decision guard."""
    stored = _decision()
    action = _escalation_action()
    payload = cast("dict[str, object]", canonical.decode(stored.payload))
    supported = _decision_payload_case({"band": EvidenceBand.SUPPORTED.value})
    manual = _decision_payload_case({"outcome": EvidenceDecisionOutcome.MANUAL_REVIEW.value})
    payload_band = _decision_payload_case({"band": EvidenceBand.SUPPORTED.value})
    payload_outcome = _decision_payload_case(
        {"outcome": EvidenceDecisionOutcome.MANUAL_REVIEW.value}
    )
    return (
        (stored, action.model_copy(update={"evidence_decision_id": "decision-other"}), payload),
        (stored, action.model_copy(update={"proposal_digest": "0" * 64}), payload),
        (stored, action.model_copy(update={"proposal_id": "proposal-other"}), payload),
        (replace(supported[0], band=EvidenceBand.SUPPORTED), supported[1], supported[2]),
        (
            replace(manual[0], outcome=EvidenceDecisionOutcome.MANUAL_REVIEW),
            manual[1],
            manual[2],
        ),
        (replace(stored, contributors=canonical.canonical_bytes([])), action, payload),
        (replace(stored, contributors=None), action, payload),
        (payload_outcome[0], payload_outcome[1], payload_outcome[2]),
        (payload_band[0], payload_band[1], payload_band[2]),
        (replace(stored, band=None), action, payload),
        (replace(stored, score=cast("int", stored.score) - 1), action, payload),
        (
            replace(stored, score_version=cast("int", stored.score_version) + 1),
            action,
            payload,
        ),
        (replace(stored, decision_version=stored.decision_version + 1), action, payload),
        (replace(stored, decision_digest="0" * 64), action, payload),
    )


class AuthorizationGuardTests(unittest.TestCase):
    def test_bound_approval_refuses_a_blank_durable_identity(self) -> None:
        # Arrange
        approval = _approval()

        # Act
        with pytest.raises(ValueError, match=r"^approval_id must not be blank$") as captured:
            replace(approval, approval_id="")

        # Assert
        self.assertEqual("approval_id must not be blank", str(captured.value))

    def test_every_proposal_row_and_payload_guard_is_independently_required(self) -> None:
        # Arrange
        stored = _proposal()
        action = _escalation_action()
        payload = cast("dict[str, object]", canonical.decode(stored.payload))
        invalid_digest = _proposal_payload_case({"proposalDigest": "0" * 64}, recompute=False)
        version = _proposal_payload_case({"proposalVersion": 2}, recompute=True)
        canonical_version = _proposal_payload_case({"canonicalizationVersion": 2}, recompute=False)
        extra_member = _proposal_payload_case({"unexpected": "value"}, recompute=True)
        cases = (
            invalid_digest,
            (replace(stored, payload=b" " + stored.payload), action, payload),
            (replace(stored, proposal_type="other-type"), action, payload),
            (replace(stored, invocation_id="invocation-other"), action, payload),
            (replace(stored, agent_name="OtherAgent"), action, payload),
            (replace(stored, source_event_digest="0" * 64), action, payload),
            (replace(stored, source_event_id="source-event-other"), action, payload),
            version,
            canonical_version,
            extra_member,
        )

        # Act
        results = [
            authorization_module._proposal_matches(case[0], case[1], case[2]) for case in cases
        ]

        # Assert
        self.assertEqual([False] * len(cases), results)

    def test_every_evidence_row_and_escalation_binding_guard_is_independently_required(
        self,
    ) -> None:
        # Arrange
        cases = _decision_guard_cases()

        # Act
        results = [
            authorization_module._decision_matches(case[0], case[1], case[2]) for case in cases
        ]

        # Assert
        self.assertEqual([False] * len(cases), results)

    def test_every_domain_approval_refusal_maps_to_one_exact_audit_reason(self) -> None:
        # Arrange
        cases = (
            (ApprovalRefusal.NOT_APPROVED, ApprovalState.REJECTED, "approval-rejected"),
            (ApprovalRefusal.NOT_APPROVED, ApprovalState.REQUESTED, "approval-missing"),
            (ApprovalRefusal.EXPIRED, ApprovalState.APPROVED, "approval-expired"),
            (ApprovalRefusal.CLOCK_REGRESSION, ApprovalState.APPROVED, "approval-expired"),
            (ApprovalRefusal.SUPERSEDED, ApprovalState.SUPERSEDED, "approval-superseded"),
            (ApprovalRefusal.ALREADY_CONSUMED, ApprovalState.EXECUTED, "approval-consumed"),
            (ApprovalRefusal.MISSION, ApprovalState.APPROVED, "proposal-mismatch"),
            (ApprovalRefusal.PROPOSAL, ApprovalState.APPROVED, "proposal-mismatch"),
            (ApprovalRefusal.DIGEST, ApprovalState.APPROVED, "proposal-mismatch"),
            (ApprovalRefusal.PARAMETERS, ApprovalState.APPROVED, "proposal-mismatch"),
            (ApprovalRefusal.TRANSITION, ApprovalState.APPROVED, "approval-missing"),
            (ApprovalRefusal.TIME_TO_LIVE, ApprovalState.APPROVED, "approval-missing"),
        )

        # Act
        reasons = [
            authorization_module._approval_reason(ApprovalError(refusal, None), state)
            for refusal, state, _expected in cases
        ]
        fallback = authorization_module._approval_reason(
            ApprovalError(EvidenceBand.WEAK, None),
            ApprovalState.APPROVED,
        )

        # Assert
        self.assertEqual(
            ([expected for _refusal, _state, expected in cases], "approval-missing"),
            (reasons, fallback),
        )

    def test_structured_authorization_errors_expose_the_exact_closed_message(self) -> None:
        # Arrange
        authorization_refusal = AuthorizationRefusal.DUPLICATE_RESULT
        artifact_refusal = ArtifactRefusal.APPROVAL_IDENTITY

        # Act
        authorization_error = AuthorizationError(authorization_refusal)
        artifact_error = ArtifactError(artifact_refusal)

        # Assert
        self.assertEqual(
            (
                authorization_refusal.value,
                authorization_refusal,
                artifact_refusal.value,
                artifact_refusal,
            ),
            (
                str(authorization_error),
                authorization_error.refusal,
                str(artifact_error),
                artifact_error.refusal,
            ),
        )


class AuthorizedCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_approval_commits_every_effect_before_broker_settlement(self) -> None:
        # Arrange
        transaction = FakeAuthorizationTransaction()
        unit_of_work = FakeAuthorizationUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_operator_command(_delivery(), STAMP, NOW, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (
                AuthorizationOutcome.AUTHORIZED,
                ApprovalState.EXECUTED,
                -50_000,
                1,
                1,
                1,
                True,
                ["event-operator-command-escalate-0001"],
                ["commit", "settle"],
            ),
            (
                result.outcome,
                transaction.consumed[0].approval.state,
                round(transaction.consumed[0].approval.issued.monotonic.total_seconds() * 1_000),
                len(transaction.audits),
                len(transaction.commands),
                len(transaction.progress),
                unit_of_work.committed,
                settlement.accepted,
                transaction.order[-2:],
            ),
        )

    async def test_authorization_passes_every_exact_identity_and_artifact_to_the_transaction(
        self,
    ) -> None:
        # Arrange
        delivery = _delivery()
        ingress = _accepted(delivery)
        transaction = FakeAuthorizationTransaction()
        unit_of_work = FakeAuthorizationUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        expected = build_authorization_artifacts(
            ingress,
            STAMP,
            authorized=True,
            approval_id="approval-synthetic-0001",
            reason=None,
        )
        expected_identity = InboxIdentity(
            consumer="command-gateway",
            source="urn:aerial-rescue:dashboard-api:dashboard-synthetic-01",
            event_id="event-operator-command-escalate-0001",
            mission_id="mission-synthetic-0001",
            canonical_digest="35566d5c0a50faa63e58fb01fb9cb30ddffe54aa5abbc19449c310df591157f5",
        )
        expected_claim = StoredClaim(
            idempotency_key="command-synthetic-0002",
            kind=IdempotencyKind.COMMAND,
            body_digest="8cf5c7ab9213a617e71c2a89eb51a60079fd98020eaaa47c86ddcf0bfbc98adf",
            mission_id="mission-synthetic-0001",
            claimed_at=STAMP.occurred_at,
        )

        # Act
        result = await handle_operator_command(delivery, STAMP, NOW, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (
                [expected_identity],
                [expected_claim],
                ["proposal-synthetic-0001"],
                ["proposal-synthetic-0001"],
                ["decision-synthetic-0001"],
                [expected.audit_record],
                [expected.audit_event],
                [expected.command],
                [expected.progress],
                [(expected.progress, STAMP.occurred_at)],
                [("command-synthetic-0002", expected.result)],
                [(expected_identity, expected.result, STAMP.occurred_at)],
                expected.result,
            ),
            (
                transaction.inbox_claims,
                transaction.command_claims,
                transaction.loaded_proposal_ids,
                transaction.loaded_approval_ids,
                transaction.loaded_decision_ids,
                transaction.audits,
                transaction.application_events,
                transaction.commands,
                transaction.progress,
                transaction.progress_initializations,
                transaction.results,
                transaction.completed,
                result.result,
            ),
        )


class ApprovalDenialTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_expired_and_replayed_approvals_publish_no_command(self) -> None:
        # Arrange
        cases = (
            (ApprovalState.REJECTED, "approval-rejected"),
            (ApprovalState.EXPIRED, "approval-expired"),
            (ApprovalState.EXECUTED, "approval-consumed"),
        )
        runs = [
            (
                FakeAuthorizationTransaction(_approval(state)),
                expected,
            )
            for state, expected in cases
        ]

        # Act
        outcomes = []
        for transaction, expected in runs:
            unit_of_work = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            result = await handle_operator_command(
                _delivery(), STAMP, NOW, unit_of_work, settlement
            )
            outcomes.append(
                (result.outcome, result.reason, transaction.commands, settlement.accepted, expected)
            )

        # Assert
        self.assertEqual(
            [
                (
                    AuthorizationOutcome.REFUSED,
                    expected,
                    [],
                    ["event-operator-command-escalate-0001"],
                    expected,
                )
                for _, expected in cases
            ],
            outcomes,
        )

    async def test_changed_proposal_digest_is_a_refusal_and_never_a_command(self) -> None:
        # Arrange
        transaction = FakeAuthorizationTransaction()
        transaction.proposal = replace(transaction.proposal, proposal_digest="0" * 64)
        unit_of_work = FakeAuthorizationUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_operator_command(_delivery(), STAMP, NOW, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            (AuthorizationOutcome.REFUSED, "proposal-mismatch", [], 1, True),
            (
                result.outcome,
                result.reason,
                transaction.commands,
                len(transaction.audits),
                unit_of_work.committed,
            ),
        )

    async def test_every_remaining_approval_state_and_epoch_refusal_stays_non_actuating(
        self,
    ) -> None:
        # Arrange
        cases = (
            (_approval(ApprovalState.REQUESTED), NOW, "approval-missing"),
            (_approval(ApprovalState.SUPERSEDED), NOW, "approval-superseded"),
            (
                replace(_approval(), runtime_epoch="gateway-start-stale"),
                NOW,
                "approval-expired",
            ),
            (
                replace(_approval(), runtime_epoch=None),
                NOW,
                "approval-expired",
            ),
            (
                _approval(),
                AuthorizationClock(
                    ClockReading(
                        wall=parse_instant("2026-08-25T12:00:59.000Z"),
                        monotonic=timedelta(seconds=-1),
                    ),
                    NOW.runtime_epoch,
                ),
                "approval-expired",
            ),
        )

        # Act
        results = []
        for approval, clock, expected in cases:
            transaction = FakeAuthorizationTransaction(approval)
            unit = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            result = await handle_operator_command(_delivery(), STAMP, clock, unit, settlement)
            results.append((result.reason, transaction.commands, expected))

        # Assert
        self.assertEqual([(expected, [], expected) for *_unused, expected in cases], results)

    async def test_malformed_proposal_keeps_state_first_and_non_object_is_mismatch(self) -> None:
        # Arrange
        rejected = FakeAuthorizationTransaction(_approval(ApprovalState.REJECTED))
        rejected.proposal = replace(rejected.proposal, payload=b"not-json")
        non_object = FakeAuthorizationTransaction()
        non_object.proposal = replace(non_object.proposal, payload=b"[]")
        runs = (rejected, non_object)

        # Act
        outcomes = []
        for transaction in runs:
            unit = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            result = await handle_operator_command(_delivery(), STAMP, NOW, unit, settlement)
            outcomes.append((result.reason, transaction.commands))

        # Assert
        self.assertEqual(
            [("approval-rejected", []), ("proposal-mismatch", [])],
            outcomes,
        )

    async def test_evidence_and_approval_action_mismatches_are_distinct_refusals(self) -> None:
        # Arrange
        decision = FakeAuthorizationTransaction()
        decision.decision = replace(decision.decision, decision_digest="0" * 64)
        bound_evidence = FakeAuthorizationTransaction(
            replace(_approval(), evidence_decision_version=2)
        )
        bound_action = FakeAuthorizationTransaction(
            replace(
                _approval(),
                action=ApprovalAction(
                    commandType="escalate-rescue",
                    droneId="drone-synthetic-02",
                    latitudeMicrodegrees=45123456,
                    longitudeMicrodegrees=-75123456,
                ),
            )
        )
        runs = (
            (decision, "evidence-decision-mismatch"),
            (bound_evidence, "evidence-decision-mismatch"),
            (bound_action, "action-mismatch"),
        )

        # Act
        outcomes = []
        for transaction, expected in runs:
            unit = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            result = await handle_operator_command(_delivery(), STAMP, NOW, unit, settlement)
            outcomes.append((result.reason, transaction.commands, expected))

        # Assert
        self.assertEqual([(expected, [], expected) for _, expected in runs], outcomes)

    async def test_each_escalation_refusal_is_an_explicit_false_decision(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())
        action = cast("EscalateRescueAction", ingress.payload.action)
        rejected = FakeAuthorizationTransaction(_approval(ApprovalState.REJECTED))
        mission = replace(
            ingress,
            payload=ingress.payload.model_copy(update={"mission_id": "mission-other"}),
        )
        evidence = FakeAuthorizationTransaction()
        evidence.decision = replace(evidence.decision, decision_digest="0" * 64)
        binding = FakeAuthorizationTransaction(
            replace(_approval(), evidence_decision_id="decision-other")
        )
        cases = (
            (rejected, ingress, "approval-rejected"),
            (FakeAuthorizationTransaction(), mission, "proposal-mismatch"),
            (evidence, ingress, "evidence-decision-mismatch"),
            (binding, ingress, "evidence-decision-mismatch"),
        )

        # Act
        decisions = [
            await authorization_module._authorize_escalation(
                transaction,
                candidate,
                action,
                NOW,
            )
            for transaction, candidate, _reason in cases
        ]

        # Assert
        self.assertEqual(
            [(False, reason, None) for _transaction, _candidate, reason in cases],
            [(decision.authorized, decision.reason, decision.approval) for decision in decisions],
        )


class AlternateAuthorizationPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_assign_prior_command_and_denied_claim_follow_three_closed_paths(self) -> None:
        # Arrange
        assign = FakeAuthorizationTransaction()
        prior = b'{"authorization":"authorized"}'
        repeated = FakeAuthorizationTransaction(
            command_claim=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, prior)
        )
        denied = FakeAuthorizationTransaction(
            command_claim=ClaimOutcome(IdempotencyDecision.DENY, None)
        )
        cases = (
            (_assign_delivery(), assign),
            (_delivery(), repeated),
            (_delivery(), denied),
        )

        # Act
        outcomes = []
        for delivery, transaction in cases:
            unit = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            result = await handle_operator_command(delivery, STAMP, NOW, unit, settlement)
            outcomes.append((result.outcome, result.reason, len(transaction.commands)))

        # Assert
        self.assertEqual(
            [
                (AuthorizationOutcome.AUTHORIZED, None, 1),
                (AuthorizationOutcome.DUPLICATE, None, 0),
                (AuthorizationOutcome.REFUSED, "idempotency-conflict", 0),
            ],
            outcomes,
        )

    async def test_incomplete_claims_stay_unsettled_and_wrong_ingress_is_durably_rejected(
        self,
    ) -> None:
        # Arrange
        inbox = FakeAuthorizationTransaction(claim=InboxOutcome(InboxDecision.DUPLICATE, None))
        idempotency = FakeAuthorizationTransaction(
            command_claim=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, None)
        )
        wrong = FakeAuthorizationTransaction()
        cases = (
            (_delivery(), inbox, AuthorizationRefusal.DUPLICATE_RESULT),
            (_delivery(), idempotency, AuthorizationRefusal.IDEMPOTENCY_RESULT),
            (
                GuaranteedDelivery(
                    "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve",
                    _fixture("event/operator-approval/baseline.json"),
                ),
                wrong,
                AuthorizationRefusal.INGRESS_KIND,
            ),
        )

        # Act
        refusals = []
        for delivery, transaction, _expected in cases:
            unit = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            with pytest.raises(AuthorizationError) as captured:
                await handle_operator_command(delivery, STAMP, NOW, unit, settlement)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            (
                [expected for _delivery_value, _transaction, expected in cases],
                ["refusal-commit", "settle-rejected"],
            ),
            (refusals, wrong.order),
        )

    async def test_idempotent_result_replay_completes_the_exact_inbox_without_effects(self) -> None:
        # Arrange
        prior = b'{"authorization":"authorized","commandId":"command-synthetic-0002"}'
        transaction = FakeAuthorizationTransaction(
            command_claim=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, prior)
        )
        unit = FakeAuthorizationUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        expected_identity = InboxIdentity(
            consumer="command-gateway",
            source="urn:aerial-rescue:dashboard-api:dashboard-synthetic-01",
            event_id="event-operator-command-escalate-0001",
            mission_id="mission-synthetic-0001",
            canonical_digest="35566d5c0a50faa63e58fb01fb9cb30ddffe54aa5abbc19449c310df591157f5",
        )

        # Act
        result = await handle_operator_command(_delivery(), STAMP, NOW, unit, settlement)

        # Assert
        self.assertEqual(
            (
                AuthorizationOutcome.DUPLICATE,
                prior,
                [(expected_identity, prior, STAMP.occurred_at)],
                [],
                [],
                ["event-operator-command-escalate-0001"],
            ),
            (
                result.outcome,
                result.result,
                transaction.completed,
                transaction.audits,
                transaction.commands,
                settlement.accepted,
            ),
        )

    async def test_idempotency_conflict_passes_an_explicit_false_decision_to_artifacts(
        self,
    ) -> None:
        # Arrange
        transaction = FakeAuthorizationTransaction(
            command_claim=ClaimOutcome(IdempotencyDecision.DENY, None)
        )
        unit = FakeAuthorizationUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        with patch.object(
            authorization_module,
            "build_authorization_artifacts",
            wraps=build_authorization_artifacts,
        ) as artifact_builder:
            result = await handle_operator_command(_delivery(), STAMP, NOW, unit, settlement)

        # Assert
        call = artifact_builder.call_args
        self.assertEqual(
            (AuthorizationOutcome.REFUSED, False, "idempotency-conflict"),
            (result.outcome, call.kwargs["authorized"], call.kwargs["reason"]),
        )

    async def test_malformed_and_wrong_family_deliveries_persist_exact_refusal_facts(self) -> None:
        # Arrange
        malformed = GuaranteedDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/agent/proposal/VisionAgent/candidate-location",
            b"not-json",
        )
        wrong_family = GuaranteedDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve",
            _fixture("event/operator-approval/baseline.json"),
        )
        malformed_unit = FakeAuthorizationUnitOfWork(FakeAuthorizationTransaction())
        wrong_unit = FakeAuthorizationUnitOfWork(FakeAuthorizationTransaction())
        malformed_settlement = FakeSettlement(malformed_unit.transaction.order)
        wrong_settlement = FakeSettlement(wrong_unit.transaction.order)

        # Act
        with pytest.raises(IngressError) as malformed_error:
            await handle_operator_command(
                malformed,
                STAMP,
                NOW,
                malformed_unit,
                malformed_settlement,
            )
        with pytest.raises(AuthorizationError) as wrong_error:
            await handle_operator_command(
                wrong_family,
                STAMP,
                NOW,
                wrong_unit,
                wrong_settlement,
            )

        # Assert
        malformed_fact = malformed_unit.refusals[0]
        wrong_fact = wrong_unit.refusals[0]
        self.assertEqual(
            (
                IngressRefusal.UNAUTHORIZED_FAMILY,
                "command-gateway-operator-command",
                "unauthorized-family",
                AuthorizationRefusal.INGRESS_KIND,
                "command-gateway-operator-command",
                "unexpected-family",
                1,
                1,
            ),
            (
                malformed_error.value.refusal,
                malformed_fact.channel,
                malformed_fact.refusal_code,
                wrong_error.value.refusal,
                wrong_fact.channel,
                wrong_fact.refusal_code,
                malformed_settlement.rejected,
                wrong_settlement.rejected,
            ),
        )


class AuthorizationArtifactTests(unittest.TestCase):
    def test_command_event_construction_refuses_every_misspelled_topic_parameter(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())
        action = ingress.payload.action
        command_payload = cast(
            "dict[str, object]",
            canonical.decode(_fixture("payload/drone-command-escalate-rescue/baseline.json")),
        )
        invalid_parameter_names = ("XXdroneIdXX", "droneid", "DRONEID")
        expected_parameter_sets = [
            tuple(sorted({name, "commandType"})) for name in invalid_parameter_names
        ]
        specs = [
            command_artifacts_module._EventSpec(
                Topic(
                    Family.DRONE_COMMAND,
                    ingress.payload.mission_id,
                    {name: action.drone_id, "commandType": action.command_type},
                ),
                STAMP.command_event_id,
                STAMP.command_sequence,
                command_payload,
            )
            for name in invalid_parameter_names
        ]

        # Act
        observations = []
        for spec in specs:
            with pytest.raises(TopicError) as captured:
                command_artifacts_module._event(ingress, STAMP, spec)
            observations.append(
                (
                    captured.value.refusal,
                    captured.value.value,
                    captured.value.parameter,
                )
            )

        # Assert
        self.assertEqual(
            [(TopicRefusal.PARAMETER_SET, expected, None) for expected in expected_parameter_sets],
            observations,
        )

    def test_the_audit_record_kind_is_the_audit_envelope_type(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())

        # Act
        artifacts = build_authorization_artifacts(
            ingress,
            STAMP,
            authorized=True,
            approval_id="approval-synthetic-0001",
            reason=None,
        )

        # Assert
        audit = cast("dict[str, object]", canonical.decode(artifacts.audit_record.payload))
        self.assertEqual(audit["type"], artifacts.audit_record.kind)

    def test_escalation_artifacts_preserve_every_exact_wire_and_store_member(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())
        traced = replace(STAMP, tracestate="vendor=value")
        expected_audit_data = canonical.decode(
            _fixture("payload/audit/command-authorization/escalate-authorized.json")
        )
        expected_command_data = canonical.decode(
            _fixture("payload/drone-command-escalate-rescue/baseline.json")
        )

        # Act
        artifacts = build_authorization_artifacts(
            ingress,
            traced,
            authorized=True,
            approval_id="approval-synthetic-0001",
            reason=None,
        )

        # Assert
        audit_document = cast("dict[str, object]", canonical.decode(artifacts.audit_event.payload))
        command = cast("StagedCommand", artifacts.command)
        command_document = cast("dict[str, object]", canonical.decode(command.payload))
        progress = cast("CommandIdentity", artifacts.progress)
        self.assertEqual(
            (
                expected_audit_data,
                expected_command_data,
                "command-gateway",
                "event-audit-command-escalate-authorized-0001",
                "audit",
                "aerial-rescue/v1/mission-synthetic-0001/audit/command-authorization",
                b"{}",
                traced.traceparent,
                "vendor=value",
                "correlation-synthetic-0001",
                "event-operator-command-escalate-0001",
                traced.occurred_at,
                "mission-synthetic-0001",
                "aerial-rescue.v1.audit.command-authorization",
                traced.occurred_at,
                artifacts.audit_event.payload,
                "correlation-synthetic-0001",
                "event-operator-command-escalate-0001",
                traced.traceparent,
                "command-synthetic-0002",
                "mission-synthetic-0001",
                "drone-synthetic-01",
                "correlation-synthetic-0001",
                "event-operator-command-escalate-0001",
                traced.traceparent,
                traced.occurred_at,
                CommandIdentity(
                    "command-synthetic-0002",
                    "mission-synthetic-0001",
                    "drone-synthetic-01",
                ),
                {"authorization": "authorized", "commandId": "command-synthetic-0002"},
            ),
            (
                audit_document["data"],
                command_document["data"],
                artifacts.audit_event.producer,
                artifacts.audit_event.event_id,
                artifacts.audit_event.family,
                artifacts.audit_event.topic,
                artifacts.audit_event.headers,
                artifacts.audit_event.traceparent,
                artifacts.audit_event.tracestate,
                artifacts.audit_event.correlation_id,
                artifacts.audit_event.causation_id,
                artifacts.audit_event.staged_at,
                artifacts.audit_record.mission_id,
                artifacts.audit_record.kind,
                artifacts.audit_record.occurred_at,
                artifacts.audit_record.payload,
                artifacts.audit_record.correlation_id,
                artifacts.audit_record.causation_id,
                artifacts.audit_record.traceparent,
                command.command_id,
                command.mission_id,
                command.drone_id,
                command.correlation_id,
                command.causation_id,
                command.traceparent,
                command.staged_at,
                progress,
                canonical.decode(artifacts.result),
            ),
        )

    def test_refused_artifacts_preserve_the_exact_reason_and_never_leak_approval(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())
        expected = cast(
            "dict[str, object]",
            canonical.decode(
                _fixture("payload/audit/command-authorization/escalate-authorized.json")
            ),
        )
        expected = dict(expected)
        expected.pop("approvalId")
        expected.update({"outcome": "refused", "reason": "approval-expired"})

        # Act
        artifacts = build_authorization_artifacts(
            ingress,
            STAMP,
            authorized=False,
            approval_id="approval-must-not-leak",
            reason="approval-expired",
        )

        # Assert
        audit = cast("dict[str, object]", canonical.decode(artifacts.audit_event.payload))
        self.assertEqual(
            (
                expected,
                {
                    "authorization": "refused",
                    "commandId": "command-synthetic-0002",
                    "reason": "approval-expired",
                },
                artifacts.audit_event.payload,
                None,
                None,
            ),
            (
                audit["data"],
                canonical.decode(artifacts.result),
                artifacts.audit_record.payload,
                artifacts.command,
                artifacts.progress,
            ),
        )

    def test_assign_branch_and_tracestate_produce_exact_self_validated_artifacts(self) -> None:
        # Arrange
        ingress = _accepted(_assign_delivery())
        traced = replace(STAMP, tracestate="vendor=value")

        # Act
        artifacts = build_authorization_artifacts(
            ingress,
            traced,
            authorized=True,
            approval_id=None,
            reason=None,
        )

        # Assert
        command = cast(
            "dict[str, object]",
            canonical.decode(artifacts.command.payload if artifacts.command else b""),
        )
        command_data = cast("dict[str, object]", command["data"])
        audit = cast("dict[str, object]", canonical.decode(artifacts.audit_event.payload))
        audit_data = cast("dict[str, object]", audit["data"])
        self.assertEqual(
            ("sector-synthetic-01", "vendor=value", "vendor=value", False),
            (
                command_data["sectorId"],
                command["tracestate"],
                audit["tracestate"],
                "approvalId" in audit_data,
            ),
        )

    def test_invalid_sequences_and_impossible_decision_arguments_are_refused(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())
        cases = (
            (replace(STAMP, audit_sequence=-1), False, None, "approval-missing"),
            (STAMP, False, None, None),
            (STAMP, True, None, None),
        )

        # Act
        refusals = []
        for stamp, authorized, approval_id, reason in cases:
            with pytest.raises(ArtifactError) as captured:
                build_authorization_artifacts(
                    ingress,
                    stamp,
                    authorized=authorized,
                    approval_id=approval_id,
                    reason=reason,
                )
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                ArtifactRefusal.SEQUENCE,
                ArtifactRefusal.REFUSAL_REASON,
                ArtifactRefusal.APPROVAL_IDENTITY,
            ],
            refusals,
        )


class AuthorizationFailureInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_command_artifact_pairs_fail_before_commit_or_settlement(self) -> None:
        # Arrange
        ingress = _accepted(_delivery())
        complete = build_authorization_artifacts(
            ingress,
            STAMP,
            authorized=True,
            approval_id="approval-synthetic-0001",
            reason=None,
        )
        cases = (
            replace(complete, progress=None),
            replace(complete, command=None),
        )

        # Act
        outcomes = []
        for artifacts in cases:
            transaction = FakeAuthorizationTransaction()
            unit = FakeAuthorizationUnitOfWork(transaction)
            settlement = FakeSettlement(transaction.order)
            with (
                patch.object(
                    authorization_module,
                    "build_authorization_artifacts",
                    return_value=artifacts,
                ),
                pytest.raises(RuntimeError) as captured,
            ):
                await handle_operator_command(_delivery(), STAMP, NOW, unit, settlement)
            outcomes.append(
                (
                    str(captured.value),
                    unit.rolled_back,
                    transaction.commands,
                    transaction.progress,
                    settlement.accepted,
                )
            )

        # Assert
        self.assertEqual(
            [
                ("authorization command artifacts are incomplete", True, [], [], []),
                ("authorization command artifacts are incomplete", True, [], [], []),
            ],
            outcomes,
        )

    async def test_a_crash_before_commit_rolls_back_and_leaves_delivery_unsettled(self) -> None:
        # Arrange
        transaction = FakeAuthorizationTransaction(failure=RuntimeError("injected stage crash"))
        unit_of_work = FakeAuthorizationUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_operator_command(_delivery(), STAMP, NOW, unit_of_work, settlement)

        # Assert
        self.assertEqual(
            ("injected stage crash", False, True, [], []),
            (
                str(captured.value),
                unit_of_work.committed,
                unit_of_work.rolled_back,
                transaction.commands,
                settlement.accepted,
            ),
        )

    async def test_a_crash_after_commit_redelivers_as_an_exact_duplicate(self) -> None:
        # Arrange
        first_transaction = FakeAuthorizationTransaction()
        first_unit = FakeAuthorizationUnitOfWork(first_transaction)
        failed_settlement = FakeSettlement(
            first_transaction.order, RuntimeError("injected settlement crash")
        )
        prior = b'{"authorization":"authorized"}'
        duplicate_transaction = FakeAuthorizationTransaction(
            claim=InboxOutcome(InboxDecision.DUPLICATE, prior)
        )
        duplicate_unit = FakeAuthorizationUnitOfWork(duplicate_transaction)
        recovered_settlement = FakeSettlement(duplicate_transaction.order)

        # Act
        with pytest.raises(RuntimeError):
            await handle_operator_command(_delivery(), STAMP, NOW, first_unit, failed_settlement)
        duplicate = await handle_operator_command(
            _delivery(), STAMP, NOW, duplicate_unit, recovered_settlement
        )

        # Assert
        self.assertEqual(
            (
                True,
                AuthorizationOutcome.DUPLICATE,
                prior,
                [],
                [],
                ["event-operator-command-escalate-0001"],
            ),
            (
                first_unit.committed,
                duplicate.outcome,
                duplicate.result,
                duplicate_transaction.commands,
                duplicate_transaction.audits,
                recovered_settlement.accepted,
            ),
        )


if __name__ == "__main__":
    unittest.main()
