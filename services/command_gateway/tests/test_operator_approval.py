"""Transactional verification of dashboard-persisted operator approval events."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from typing import cast
from unittest.mock import patch

import aerial_rescue_command_gateway.operator_approval as operator_approval_module
import pytest
from aerial_rescue_command_gateway.authorization import AuthorizationClock
from aerial_rescue_command_gateway.ingress import (
    IngressError,
    OperatorApprovalIngress,
    accept_ingress,
)
from aerial_rescue_command_gateway.operator_approval import (
    ApprovalIngressError,
    ApprovalIngressOutcome,
    ApprovalIngressRefusal,
    handle_operator_approval,
)
from aerial_rescue_command_gateway.ports import (
    GuaranteedDelivery,
    StoredApprovalBinding,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.instant import InstantError, InstantRefusal, parse_instant
from aerial_rescue_domain.approvals import ClockReading
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    StoredApprovalAuthority,
)
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome

from .fixture_paths import repository_root

ROOT = repository_root(Path(__file__))
APPROVAL_TOPIC = "aerial-rescue/v1/mission-synthetic-0001/operator/approval/approve"
GATEWAY_NOW = AuthorizationClock(
    ClockReading(
        parse_instant("2026-08-25T12:02:00.000Z"),
        timedelta(seconds=10),
    ),
    "gateway-start-0001",
)


def _payload() -> bytes:
    """Return the exact committed approval event."""
    return (ROOT / "fixtures/golden/v1/event/operator-approval/baseline.json").read_bytes()


def _binding() -> StoredApprovalBinding:
    """Return the authoritative dashboard/store binding represented by the event."""
    ingress = accept_ingress(_payload(), APPROVAL_TOPIC)
    assert isinstance(ingress, OperatorApprovalIngress)
    payload = ingress.payload
    return StoredApprovalBinding(
        approval_id=payload.approval_id,
        mission_id=payload.mission_id,
        operator_id=payload.operator_id,
        decision=payload.decision,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
        proposal_id=payload.proposal_id,
        proposal_digest=payload.proposal_digest,
        proposal_version=payload.proposal_version,
        evidence_decision_id=payload.evidence_decision_id,
        evidence_decision_digest=payload.evidence_decision_digest,
        evidence_decision_version=payload.evidence_decision_version,
        action=payload.action,
        decision_runtime_id="dashboard-synthetic-01",
        time_to_live_milliseconds=300_000,
    )


class FakeApprovalTransaction:
    """Claim one broker identity and expose a persisted authoritative binding."""

    def __init__(
        self,
        binding: StoredApprovalBinding | None = None,
        claim: InboxOutcome | None = None,
        failure: Exception | None = None,
        authority_decision: ApprovalAuthorityDecision = ApprovalAuthorityDecision.BOUND,
    ) -> None:
        """Configure authoritative data, duplicate result, and completion failure."""
        self.binding = binding or _binding()
        self.claim_outcome = claim or InboxOutcome(InboxDecision.CLAIMED, None)
        self.failure = failure
        self.authority_decision = authority_decision
        self.authorities: list[StoredApprovalAuthority] = []
        self.claimed_identities: list[InboxIdentity] = []
        self.loaded_approval_ids: list[str] = []
        self.bound_requests: list[tuple[str, StoredApprovalAuthority]] = []
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.order: list[str] = []

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Return the configured inbox decision."""
        self.order.append("claim")
        self.claimed_identities.append(identity)
        return self.claim_outcome

    async def load_binding(self, approval_id: str) -> StoredApprovalBinding:
        """Return the authoritative dashboard/store binding."""
        self.order.append("load-binding")
        self.loaded_approval_ids.append(approval_id)
        return self.binding

    async def bind_authority(
        self,
        approval_id: str,
        authority: StoredApprovalAuthority,
    ) -> ApprovalAuthorityDecision:
        """Record the gateway-owned epoch and rebased monotonic issue reading."""
        self.order.append("bind-authority")
        self.authorities.append(authority)
        self.bound_requests.append((approval_id, authority))
        return self.authority_decision

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete the inbox or inject a pre-commit failure."""
        self.order.append("complete")
        if self.failure is not None:
            raise self.failure
        self.completed.append((identity, result, processed_at))


class FakeApprovalUnitOfWork:
    """Commit only when binding verification and inbox completion both succeed."""

    def __init__(self, transaction: FakeApprovalTransaction) -> None:
        """Wrap one transaction."""
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

    def begin(self) -> FakeApprovalUnitOfWork:
        """Return this transaction context."""
        return self

    async def __aenter__(self) -> FakeApprovalTransaction:
        """Expose the transaction."""
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
    """Record broker acceptance after commit."""

    def __init__(self, order: list[str]) -> None:
        """Share transaction ordering evidence."""
        self.order = order
        self.accepted: list[str] = []
        self.rejected = 0

    async def accept(self, event_id: str) -> None:
        """Accept one event."""
        self.order.append("settle")
        self.accepted.append(event_id)

    async def reject(self) -> None:
        """Record permanent settlement after refusal commit."""
        self.order.append("settle-rejected")
        self.rejected += 1


class ApprovalVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_persisted_binding_commits_inbox_before_settlement(self) -> None:
        # Arrange
        transaction = FakeApprovalTransaction()
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        ingress = cast("OperatorApprovalIngress", accept_ingress(_payload(), APPROVAL_TOPIC))
        expected_result = canonical.canonical_bytes(
            {
                "approvalIngress": "verified",
                "approvalId": "approval-synthetic-0001",
            }
        )
        expected_identity = InboxIdentity(
            consumer="command-gateway",
            source="urn:aerial-rescue:dashboard-api:dashboard-synthetic-01",
            event_id="event-operator-approval-approve-0001",
            mission_id="mission-synthetic-0001",
            canonical_digest=hashlib.sha256(
                canonical.canonical_bytes(canonical.decode(_payload()))
            ).hexdigest(),
        )
        expected_authority = StoredApprovalAuthority("gateway-start-0001", -50_000)

        # Act
        result = await handle_operator_approval(
            GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
        )

        # Assert
        self.assertEqual(
            (
                ApprovalIngressOutcome.VERIFIED,
                True,
                [expected_identity],
                ["approval-synthetic-0001"],
                [("approval-synthetic-0001", expected_authority)],
                [(expected_identity, expected_result, ingress.envelope.time)],
                expected_result,
                ["claim", "load-binding", "bind-authority", "complete", "commit", "settle"],
                ["commit", "settle"],
                ["event-operator-approval-approve-0001"],
            ),
            (
                result.outcome,
                unit.committed,
                transaction.claimed_identities,
                transaction.loaded_approval_ids,
                transaction.bound_requests,
                transaction.completed,
                result.result,
                transaction.order,
                transaction.order[-2:],
                settlement.accepted,
            ),
        )

    async def test_zero_elapsed_zero_monotonic_and_one_millisecond_ttl_remain_valid(self) -> None:
        # Arrange
        expires_at = "2026-08-25T12:01:00.001Z"
        document = cast("dict[str, object]", canonical.decode(_payload()))
        payload_data = cast("dict[str, object]", document["data"])
        payload_data["expiresAt"] = expires_at
        payload = canonical.canonical_bytes(document)
        binding = replace(
            _binding(),
            expires_at=expires_at,
            time_to_live_milliseconds=1,
        )
        clock = AuthorizationClock(
            ClockReading(parse_instant(binding.issued_at), timedelta(0)),
            "gateway-zero-origin",
        )
        transaction = FakeApprovalTransaction(binding)

        # Act
        result = await handle_operator_approval(
            GuaranteedDelivery(APPROVAL_TOPIC, payload),
            clock,
            FakeApprovalUnitOfWork(transaction),
            FakeSettlement(transaction.order),
        )

        # Assert
        authority = transaction.authorities[0]
        self.assertEqual(
            (ApprovalIngressOutcome.VERIFIED, "gateway-zero-origin", 0, int),
            (
                result.outcome,
                authority.runtime_epoch,
                authority.issued_monotonic_milliseconds,
                type(authority.issued_monotonic_milliseconds),
            ),
        )

    async def test_multiword_ingress_refusal_commits_its_exact_bounded_code(self) -> None:
        # Arrange
        transaction = FakeApprovalTransaction()
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        delivery = GuaranteedDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/agent/proposal/VisionAgent/candidate-location",
            b"payload-is-not-inspected-for-an-unauthorized-family",
        )

        # Act
        with pytest.raises(IngressError) as captured:
            await handle_operator_approval(delivery, GATEWAY_NOW, unit, settlement)

        # Assert
        fact = unit.refusals[0]
        self.assertEqual(
            (
                "topic family is not command-gateway ingress authority",
                "command-gateway-operator-approval",
                "unauthorized-family",
                ["refusal-commit", "settle-rejected"],
            ),
            (
                str(captured.value),
                fact.channel,
                fact.refusal_code,
                transaction.order,
            ),
        )

    async def test_digest_mismatch_is_durably_refused_without_changing_approval_state(self) -> None:
        # Arrange
        changed = replace(_binding(), evidence_decision_digest="0" * 64)
        transaction = FakeApprovalTransaction(changed)
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_operator_approval(
            GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
        )

        # Assert
        self.assertEqual(
            (
                ApprovalIngressOutcome.MISMATCH,
                1,
                True,
                [],
                ["event-operator-approval-approve-0001"],
            ),
            (
                result.outcome,
                len(transaction.completed),
                unit.committed,
                transaction.authorities,
                settlement.accepted,
            ),
        )

    async def test_dashboard_runtime_source_mismatch_cannot_bind_gateway_authority(self) -> None:
        # Arrange
        changed = replace(_binding(), decision_runtime_id="dashboard-start-other")
        transaction = FakeApprovalTransaction(changed)
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_operator_approval(
            GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
        )

        # Assert
        self.assertEqual(
            (ApprovalIngressOutcome.MISMATCH, [], True, ["commit", "settle"]),
            (
                result.outcome,
                transaction.authorities,
                unit.committed,
                transaction.order[-2:],
            ),
        )

    async def test_expired_and_clock_regressed_events_are_recorded_without_authority(self) -> None:
        # Arrange
        clocks = (
            AuthorizationClock(
                ClockReading(
                    parse_instant("2026-08-25T12:06:00.000Z"),
                    timedelta(minutes=5),
                ),
                GATEWAY_NOW.runtime_epoch,
            ),
            AuthorizationClock(
                ClockReading(
                    parse_instant("2026-08-25T12:00:59.999Z"),
                    timedelta(seconds=10),
                ),
                GATEWAY_NOW.runtime_epoch,
            ),
        )
        runs = [
            (FakeApprovalTransaction(), FakeSettlement([])),
            (FakeApprovalTransaction(), FakeSettlement([])),
        ]
        for transaction, settlement in runs:
            settlement.order = transaction.order

        # Act
        outcomes = []
        for clock, (transaction, settlement) in zip(clocks, runs, strict=True):
            result = await handle_operator_approval(
                GuaranteedDelivery(APPROVAL_TOPIC, _payload()),
                clock,
                FakeApprovalUnitOfWork(transaction),
                settlement,
            )
            outcomes.append(result.outcome)

        # Assert
        self.assertEqual(
            (
                [ApprovalIngressOutcome.EXPIRED, ApprovalIngressOutcome.CLOCK_REGRESSION],
                [[], []],
                [["commit", "settle"], ["commit", "settle"]],
            ),
            (
                outcomes,
                [transaction.authorities for transaction, _settlement in runs],
                [transaction.order[-2:] for transaction, _settlement in runs],
            ),
        )

    async def test_invalid_persisted_clock_inputs_are_refused_without_binding_authority(
        self,
    ) -> None:
        # Arrange
        cases = (
            (
                replace(_binding(), issued_at="not-an-instant"),
                GATEWAY_NOW,
                ApprovalIngressOutcome.MISMATCH,
            ),
            (
                replace(_binding(), time_to_live_milliseconds=0),
                GATEWAY_NOW,
                ApprovalIngressOutcome.MISMATCH,
            ),
            (
                _binding(),
                AuthorizationClock(
                    ClockReading(
                        parse_instant("2026-08-25T12:02:00.000Z"),
                        timedelta(milliseconds=-1),
                    ),
                    GATEWAY_NOW.runtime_epoch,
                ),
                ApprovalIngressOutcome.CLOCK_REGRESSION,
            ),
        )

        # Act
        observations = []
        for binding, clock, expected in cases:
            transaction = FakeApprovalTransaction(binding)
            result = await handle_operator_approval(
                GuaranteedDelivery(APPROVAL_TOPIC, _payload()),
                clock,
                FakeApprovalUnitOfWork(transaction),
                FakeSettlement(transaction.order),
            )
            observations.append((result.outcome, transaction.authorities, expected))

        # Assert
        self.assertEqual(
            [(expected, [], expected) for _binding_value, _clock, expected in cases],
            observations,
        )

    async def test_clock_parse_failure_is_fail_closed_before_authority_binding(self) -> None:
        # Arrange
        transaction = FakeApprovalTransaction()
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)
        parse_failure = InstantError(InstantRefusal.FORM, "unreadable-clock")

        # Act
        with patch.object(operator_approval_module, "parse_instant", side_effect=parse_failure):
            result = await handle_operator_approval(
                GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
            )

        # Assert
        self.assertEqual(
            (ApprovalIngressOutcome.CLOCK_REGRESSION, [], ["commit", "settle"]),
            (result.outcome, transaction.authorities, transaction.order[-2:]),
        )

    async def test_missing_computed_authority_is_fail_closed(self) -> None:
        # Arrange
        transaction = FakeApprovalTransaction()
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        with patch.object(operator_approval_module, "_authority", return_value=(None, None)):
            result = await handle_operator_approval(
                GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
            )

        # Assert
        self.assertEqual(
            (ApprovalIngressOutcome.MISMATCH, [], ["commit", "settle"]),
            (result.outcome, transaction.authorities, transaction.order[-2:]),
        )

    async def test_an_existing_authority_from_another_gateway_epoch_is_recorded_as_mismatch(
        self,
    ) -> None:
        # Arrange
        transaction = FakeApprovalTransaction(
            authority_decision=ApprovalAuthorityDecision.EPOCH_CONFLICT
        )
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_operator_approval(
            GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
        )

        # Assert
        self.assertEqual(
            (ApprovalIngressOutcome.EPOCH_MISMATCH, True, ["commit", "settle"]),
            (result.outcome, unit.committed, transaction.order[-2:]),
        )

    async def test_exact_duplicate_returns_prior_result_without_reloading_the_binding(self) -> None:
        # Arrange
        prior = b'{"approvalIngress":"verified"}'
        transaction = FakeApprovalTransaction(claim=InboxOutcome(InboxDecision.DUPLICATE, prior))
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        result = await handle_operator_approval(
            GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
        )

        # Assert
        self.assertEqual(
            (ApprovalIngressOutcome.DUPLICATE, prior, ["claim", "commit", "settle"]),
            (result.outcome, result.result, transaction.order),
        )

    async def test_incomplete_duplicate_stays_unsettled_and_wrong_ingress_is_durably_rejected(
        self,
    ) -> None:
        # Arrange
        duplicate_transaction = FakeApprovalTransaction(
            claim=InboxOutcome(InboxDecision.DUPLICATE, None)
        )
        duplicate_unit = FakeApprovalUnitOfWork(duplicate_transaction)
        duplicate_settlement = FakeSettlement(duplicate_transaction.order)
        wrong_transaction = FakeApprovalTransaction()
        wrong_unit = FakeApprovalUnitOfWork(wrong_transaction)
        wrong_settlement = FakeSettlement(wrong_transaction.order)
        wrong_delivery = GuaranteedDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/operator/command/escalate-rescue",
            (ROOT / "fixtures/golden/v1/event/operator-command/escalate-rescue.json").read_bytes(),
        )

        # Act
        refusals = []
        for delivery, clock, unit, settlement in (
            (
                GuaranteedDelivery(APPROVAL_TOPIC, _payload()),
                GATEWAY_NOW,
                duplicate_unit,
                duplicate_settlement,
            ),
            (wrong_delivery, GATEWAY_NOW, wrong_unit, wrong_settlement),
        ):
            with pytest.raises(ApprovalIngressError) as captured:
                await handle_operator_approval(delivery, clock, unit, settlement)
            refusals.append((captured.value.refusal, str(captured.value)))

        # Assert
        self.assertEqual(
            (
                [
                    (
                        ApprovalIngressRefusal.DUPLICATE_RESULT,
                        ApprovalIngressRefusal.DUPLICATE_RESULT.value,
                    ),
                    (
                        ApprovalIngressRefusal.INGRESS_KIND,
                        ApprovalIngressRefusal.INGRESS_KIND.value,
                    ),
                ],
                (
                    "command-gateway-operator-approval",
                    "unexpected-family",
                    ["refusal-commit", "settle-rejected"],
                ),
            ),
            (
                refusals,
                (
                    wrong_unit.refusals[0].channel,
                    wrong_unit.refusals[0].refusal_code,
                    wrong_transaction.order,
                ),
            ),
        )

    async def test_completion_crash_rolls_back_and_leaves_delivery_unsettled(self) -> None:
        # Arrange
        transaction = FakeApprovalTransaction(failure=RuntimeError("injected inbox crash"))
        unit = FakeApprovalUnitOfWork(transaction)
        settlement = FakeSettlement(transaction.order)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_operator_approval(
                GuaranteedDelivery(APPROVAL_TOPIC, _payload()), GATEWAY_NOW, unit, settlement
            )

        # Assert
        self.assertEqual(
            (
                "injected inbox crash",
                True,
                ["bind-authority", "complete", "rollback"],
                [],
            ),
            (
                str(captured.value),
                unit.rolled_back,
                transaction.order[-3:],
                settlement.accepted,
            ),
        )


if __name__ == "__main__":
    unittest.main()
