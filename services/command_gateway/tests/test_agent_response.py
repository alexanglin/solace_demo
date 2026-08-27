"""Durable normalization of direct Agent Responses into canonical proposals.

The response is direct and therefore has no settlement.  Its durable boundary begins at one
transaction which validates the pending invocation, claims the inbox identity, stores the
immutable proposal, stages proposal and audit events, and completes the claim.  Exact duplicate
delivery reuses the prior result; changed or untrusted context writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import cast

import pytest
from aerial_rescue_command_gateway.agent_response import (
    NormalizationOutcome,
    handle_agent_response,
)
from aerial_rescue_command_gateway.ingress import AgentResponseIngress, accept_ingress
from aerial_rescue_command_gateway.normalization import (
    NormalizationError,
    NormalizationRefusal,
    NormalizationStamp,
    PendingInvocation,
    build_normalization,
)
from aerial_rescue_command_gateway.ports import DirectDelivery
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.integration import (
    AgentCandidate,
    AgentOutcome,
    agent_response_document,
)
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome
from aerial_rescue_store.pending_invocations import (
    PendingInvocationError,
    PendingInvocationRefusal,
)
from aerial_rescue_store.proposals import StoredProposal

from .fixture_paths import repository_root

ROOT = repository_root(Path(__file__))
AGENT_TOPIC = "aerial-rescue/v1/mission-synthetic-0001/agent/response/VisionAgent"

PENDING = PendingInvocation(
    mission_id="mission-synthetic-0001",
    agent_name="VisionAgent",
    invocation_id="invocation-synthetic-0001",
    correlation_id="correlation-synthetic-0001",
    source_event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6c",
    source_event_digest="9716b17a9f5a0cfcb645d9e7abdf1e5905fdf17c327d7e0f955eedd444057b52",
)


def _transport_properties(pending: PendingInvocation) -> Mapping[str, object]:
    """Return the exact trusted broker property set for one invocation."""
    return {
        "aerial-rescue-agent-response-invocation-id": pending.invocation_id,
        "aerial-rescue-agent-response-correlation-id": pending.correlation_id,
        "aerial-rescue-agent-response-mission-id": pending.mission_id,
        "aerial-rescue-agent-response-source-event-id": pending.source_event_id,
        "aerial-rescue-agent-response-source-event-digest": pending.source_event_digest,
        "aerial-rescue-agent-response-agent-name": pending.agent_name,
    }


TRANSPORT_PROPERTIES: Mapping[str, object] = _transport_properties(PENDING)

STAMP = NormalizationStamp(
    producer_id="gateway-synthetic-01",
    proposal_id="proposal-synthetic-0001",
    proposal_event_id="event-agent-proposal-0001",
    audit_record_id="audit-proposal-normalized-0001",
    audit_event_id="event-audit-proposal-normalized-0001",
    occurred_at="2026-08-25T12:03:00.000Z",
    proposal_sequence=5,
    audit_sequence=11,
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
)


def _fixture(relative: str) -> bytes:
    """Return exact committed fixture bytes."""
    return (ROOT / "fixtures" / "golden" / "v1" / relative).read_bytes()


class FakeTransaction:
    """One atomic normalization transaction."""

    def __init__(
        self,
        pending: PendingInvocation = PENDING,
        claim: InboxOutcome | None = None,
        failure: Exception | None = None,
        pending_failure: Exception | None = None,
    ) -> None:
        """Configure its authoritative invocation, inbox answer, and injected failure."""
        self.pending = pending
        self.claim_outcome = claim or InboxOutcome(InboxDecision.CLAIMED, None)
        self.failure = failure
        self.pending_failure = pending_failure
        self.recorded_pending: list[PendingInvocation] = []
        self.proposals: list[StoredProposal] = []
        self.events: list[StagedApplicationEvent] = []
        self.completed: list[tuple[InboxIdentity, bytes, str]] = []
        self.loaded_invocation_ids: list[str] = []
        self.claimed_identities: list[InboxIdentity] = []
        self.order: list[str] = []

    async def record_pending(self, pending: PendingInvocation) -> None:
        """Buffer trusted transport context or inject a repository refusal."""
        self.order.append("record-pending")
        if self.pending_failure is not None:
            raise self.pending_failure
        self.recorded_pending.append(pending)

    async def load_pending(self, invocation_id: str) -> PendingInvocation:
        """Return the trusted forward context."""
        self.order.append("load-pending")
        self.loaded_invocation_ids.append(invocation_id)
        return self.pending

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Return the configured inbox decision."""
        self.order.append("claim")
        self.claimed_identities.append(identity)
        return self.claim_outcome

    async def record_proposal(self, proposal: StoredProposal) -> None:
        """Buffer one immutable proposal or inject a crash."""
        self.order.append("record-proposal")
        if self.failure is not None:
            raise self.failure
        self.proposals.append(proposal)

    async def stage(self, event: StagedApplicationEvent) -> None:
        """Buffer one exact application publication."""
        self.order.append("stage")
        self.events.append(event)

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Buffer the exact durable inbox result."""
        self.order.append("complete")
        self.completed.append((identity, result, processed_at))


class FakeUnitOfWork:
    """Commit only after every normalization effect succeeds."""

    def __init__(self, transaction: FakeTransaction) -> None:
        """Wrap one transaction."""
        self.transaction = transaction
        self.committed = False
        self.rolled_back = False

    def begin(self) -> FakeUnitOfWork:
        """Return this transaction context."""
        return self

    async def __aenter__(self) -> FakeTransaction:
        """Expose the transaction operations."""
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


def _delivery(
    relative: str = "integration/agent-response/baseline.json",
    properties: Mapping[str, object] = TRANSPORT_PROPERTIES,
) -> DirectDelivery:
    """Return one direct Agent Response delivery."""
    return DirectDelivery(AGENT_TOPIC, _fixture(relative), properties)


def _ingress(relative: str = "integration/agent-response/baseline.json") -> AgentResponseIngress:
    """Return one validated Agent Response ingress value."""
    accepted = accept_ingress(_fixture(relative), AGENT_TOPIC)
    assert isinstance(accepted, AgentResponseIngress)
    return accepted


class CandidateNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_candidate_commits_the_exact_proposal_and_audit_before_returning(self) -> None:
        # Arrange
        transaction = FakeTransaction()
        unit_of_work = FakeUnitOfWork(transaction)
        expected_payload = json.loads(_fixture("payload/agent-proposal/baseline.json"))
        ingress = _ingress()
        response_bytes = canonical.canonical_bytes(agent_response_document(ingress.response))
        expected_identity = InboxIdentity(
            consumer="command-gateway",
            source="event-mesh-gateway",
            event_id=ingress.response.invocation_id,
            mission_id=ingress.response.mission_id,
            canonical_digest=hashlib.sha256(response_bytes).hexdigest(),
        )
        expected_artifacts = build_normalization(ingress, PENDING, STAMP)

        # Act
        result = await handle_agent_response(_delivery(), STAMP, unit_of_work)

        # Assert
        self.assertEqual(
            (
                NormalizationOutcome.COMMITTED,
                True,
                [
                    "record-pending",
                    "load-pending",
                    "claim",
                    "record-proposal",
                    "stage",
                    "stage",
                    "complete",
                    "commit",
                ],
                expected_payload,
                2,
                [PENDING.invocation_id],
                [expected_identity],
                [(expected_identity, expected_artifacts.result, STAMP.occurred_at)],
                expected_artifacts.result,
                [PENDING],
            ),
            (
                result.outcome,
                unit_of_work.committed,
                transaction.order,
                canonical.decode(transaction.proposals[0].payload),
                len(transaction.events),
                transaction.loaded_invocation_ids,
                transaction.claimed_identities,
                transaction.completed,
                result.result,
                transaction.recorded_pending,
            ),
        )

    def test_candidate_artifacts_preserve_every_exact_proposal_and_outbox_member(self) -> None:
        # Arrange
        ingress = _ingress()
        traced = replace(STAMP, tracestate="vendor=value")
        expected_proposal_data = canonical.decode(_fixture("payload/agent-proposal/baseline.json"))
        expected_audit_data = canonical.decode(
            _fixture("payload/audit/proposal-normalization/normalized.json")
        )

        # Act
        artifacts = build_normalization(ingress, PENDING, traced)

        # Assert
        proposal = cast("StoredProposal", artifacts.proposal)
        proposal_event, audit_event = artifacts.events
        proposal_document = cast("dict[str, object]", canonical.decode(proposal_event.payload))
        audit_document = cast("dict[str, object]", canonical.decode(audit_event.payload))
        self.assertEqual(
            (
                expected_proposal_data,
                expected_audit_data,
                PENDING.source_event_id,
                PENDING.source_event_id,
                "proposal-synthetic-0001",
                "mission-synthetic-0001",
                PENDING.source_event_id,
                PENDING.source_event_digest,
                "VisionAgent",
                "invocation-synthetic-0001",
                "candidate-location",
                "e3b6c8a4c2a075031275dc288bad3f780c992338617978dcb5863bc51aa6f761",
                canonical.canonical_bytes(expected_proposal_data),
                "drone-synthetic-01",
                45_123_456,
                -75_123_456,
                "escalate-rescue",
                traced.occurred_at,
                5,
                "correlation-synthetic-0001",
                PENDING.source_event_id,
                traced.traceparent,
                (
                    "command-gateway",
                    "event-agent-proposal-0001",
                    "agent-proposal",
                    "aerial-rescue/v1/mission-synthetic-0001/agent/proposal/VisionAgent/candidate-location",
                    b"{}",
                    traced.traceparent,
                    "vendor=value",
                    "correlation-synthetic-0001",
                    PENDING.source_event_id,
                    traced.occurred_at,
                ),
                (
                    "command-gateway",
                    "event-audit-proposal-normalized-0001",
                    "audit",
                    "aerial-rescue/v1/mission-synthetic-0001/audit/proposal-normalization",
                    b"{}",
                    traced.traceparent,
                    "vendor=value",
                    "correlation-synthetic-0001",
                    PENDING.source_event_id,
                    traced.occurred_at,
                ),
                proposal_event.payload,
            ),
            (
                proposal_document["data"],
                audit_document["data"],
                proposal_document["causationid"],
                audit_document["causationid"],
                proposal.proposal_id,
                proposal.mission_id,
                proposal.source_event_id,
                proposal.source_event_digest,
                proposal.agent_name,
                proposal.invocation_id,
                proposal.proposal_type,
                proposal.proposal_digest,
                proposal.payload,
                proposal.drone_id,
                proposal.latitude_microdegrees,
                proposal.longitude_microdegrees,
                proposal.command_type,
                proposal.issued_at,
                proposal.sequence,
                proposal.correlation_id,
                proposal.causation_id,
                proposal.traceparent,
                (
                    proposal_event.producer,
                    proposal_event.event_id,
                    proposal_event.family,
                    proposal_event.topic,
                    proposal_event.headers,
                    proposal_event.traceparent,
                    proposal_event.tracestate,
                    proposal_event.correlation_id,
                    proposal_event.causation_id,
                    proposal_event.staged_at,
                ),
                (
                    audit_event.producer,
                    audit_event.event_id,
                    audit_event.family,
                    audit_event.topic,
                    audit_event.headers,
                    audit_event.traceparent,
                    audit_event.tracestate,
                    audit_event.correlation_id,
                    audit_event.causation_id,
                    audit_event.staged_at,
                ),
                artifacts.result,
            ),
        )

    async def test_an_abstention_commits_only_a_redacted_audit_event(self) -> None:
        # Arrange
        pending = PendingInvocation(
            mission_id=PENDING.mission_id,
            agent_name=PENDING.agent_name,
            invocation_id="invocation-synthetic-0002",
            correlation_id="correlation-synthetic-0002",
            source_event_id=PENDING.source_event_id,
            source_event_digest=PENDING.source_event_digest,
        )
        transaction = FakeTransaction(pending)
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        result = await handle_agent_response(
            _delivery(
                "integration/agent-response/abstained.json",
                _transport_properties(pending),
            ),
            STAMP,
            unit_of_work,
        )

        # Assert
        audit = cast("Mapping[str, object]", canonical.decode(transaction.events[0].payload))
        data = cast("Mapping[str, object]", audit["data"])
        self.assertEqual(
            (
                NormalizationOutcome.COMMITTED,
                [],
                "abstained",
                "invalid-output",
                1,
                transaction.events[0].payload,
            ),
            (
                result.outcome,
                transaction.proposals,
                data["outcome"],
                data["reason"],
                len(transaction.events),
                result.result,
            ),
        )


class NormalizationRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_non_response_ingress_is_refused_before_opening_a_transaction(self) -> None:
        # Arrange
        transaction = FakeTransaction()
        unit_of_work = FakeUnitOfWork(transaction)
        delivery = DirectDelivery(
            "aerial-rescue/v1/mission-synthetic-0001/operator/command/escalate-rescue",
            _fixture("event/operator-command/escalate-rescue.json"),
            TRANSPORT_PROPERTIES,
        )

        # Act
        with pytest.raises(NormalizationError) as captured:
            await handle_agent_response(delivery, STAMP, unit_of_work)

        # Assert
        self.assertEqual(
            (NormalizationRefusal.RESPONSE_KIND, [], False),
            (captured.value.refusal, transaction.order, unit_of_work.committed),
        )

    async def test_missing_malformed_or_open_transport_context_is_refused_before_the_store(
        self,
    ) -> None:
        # Arrange
        missing = dict(TRANSPORT_PROPERTIES)
        del missing["aerial-rescue-agent-response-source-event-id"]
        malformed = dict(TRANSPORT_PROPERTIES)
        malformed["aerial-rescue-agent-response-agent-name"] = "not an agent"
        opened = dict(TRANSPORT_PROPERTIES)
        opened["aerial-rescue-agent-response-model-claim"] = "untrusted"
        transaction = FakeTransaction()
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        refusals = []
        for properties in (missing, malformed, opened):
            with pytest.raises(NormalizationError) as captured:
                await handle_agent_response(_delivery(properties=properties), STAMP, unit_of_work)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            ([NormalizationRefusal.TRANSPORT_CONTEXT] * 3, [], False, False),
            (refusals, transaction.order, unit_of_work.committed, unit_of_work.rolled_back),
        )

    async def test_conflicting_durable_context_rolls_back_before_body_normalization(self) -> None:
        # Arrange
        conflict = PendingInvocationError(
            PendingInvocationRefusal.IDENTITY_CONFLICT,
            PENDING.invocation_id,
        )
        transaction = FakeTransaction(pending_failure=conflict)
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        with pytest.raises(NormalizationError) as captured:
            await handle_agent_response(_delivery(), STAMP, unit_of_work)

        # Assert
        self.assertEqual(
            (
                NormalizationRefusal.TRANSPORT_CONTEXT_CONFLICT,
                ["record-pending", "rollback"],
                True,
                [],
                [],
            ),
            (
                captured.value.refusal,
                transaction.order,
                unit_of_work.rolled_back,
                transaction.proposals,
                transaction.events,
            ),
        )

    async def test_source_digest_mismatch_rolls_back_before_any_claim_or_write(self) -> None:
        # Arrange
        conflicting_context = PendingInvocation(
            mission_id=PENDING.mission_id,
            agent_name=PENDING.agent_name,
            invocation_id=PENDING.invocation_id,
            correlation_id=PENDING.correlation_id,
            source_event_id=PENDING.source_event_id,
            source_event_digest="0" * 64,
        )
        transaction = FakeTransaction(conflicting_context)
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        with pytest.raises(NormalizationError) as captured:
            await handle_agent_response(
                _delivery(properties=_transport_properties(conflicting_context)),
                STAMP,
                unit_of_work,
            )

        # Assert
        self.assertEqual(
            (
                NormalizationRefusal.DIGEST_MISMATCH,
                ["record-pending", "load-pending", "rollback"],
                True,
                [],
                [],
                [conflicting_context],
            ),
            (
                captured.value.refusal,
                transaction.order,
                unit_of_work.rolled_back,
                transaction.proposals,
                transaction.events,
                transaction.recorded_pending,
            ),
        )


class DefensiveNormalizationTests(unittest.IsolatedAsyncioTestCase):
    def test_structured_normalization_errors_expose_the_exact_closed_message(self) -> None:
        # Arrange
        refusal = NormalizationRefusal.DIGEST_MISMATCH

        # Act
        error = NormalizationError(refusal)

        # Assert
        self.assertEqual((refusal.value, refusal), (str(error), error.refusal))

    def test_trusted_common_identity_and_source_event_identity_are_both_rechecked(self) -> None:
        # Arrange
        ingress = _ingress()
        candidate = cast("AgentCandidate", ingress.response.candidate)
        wrong_common = replace(
            ingress,
            response=replace(ingress.response, mission_id="mission-synthetic-0002"),
        )
        wrong_source = replace(
            ingress,
            response=replace(
                ingress.response,
                candidate=replace(
                    candidate,
                    source_event_id="event-synthetic-wrong",
                ),
            ),
        )

        # Act
        refusals = []
        for response_ingress in (wrong_common, wrong_source):
            with pytest.raises(NormalizationError) as captured:
                build_normalization(response_ingress, PENDING, STAMP)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [NormalizationRefusal.IDENTITY_MISMATCH, NormalizationRefusal.IDENTITY_MISMATCH],
            refusals,
        )

    def test_impossible_outcome_members_are_refused_inside_normalization(self) -> None:
        # Arrange
        candidate = _ingress()
        impossible_candidate = replace(
            candidate,
            response=replace(candidate.response, candidate=None),
        )
        impossible_abstention = replace(
            candidate,
            response=replace(
                candidate.response,
                outcome=AgentOutcome.ABSTAINED,
                candidate=None,
                reason=None,
            ),
        )

        # Act
        refusals = []
        for ingress in (impossible_candidate, impossible_abstention):
            with pytest.raises(NormalizationError) as captured:
                build_normalization(ingress, PENDING, STAMP)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [NormalizationRefusal.RESPONSE_KIND, NormalizationRefusal.RESPONSE_KIND],
            refusals,
        )

    def test_sequence_range_is_refused_and_valid_tracestate_is_propagated(self) -> None:
        # Arrange
        ingress = _ingress()
        invalid = replace(STAMP, proposal_sequence=-1)
        traced = replace(STAMP, tracestate="vendor=value")

        # Act
        with pytest.raises(NormalizationError) as captured:
            build_normalization(ingress, PENDING, invalid)
        artifacts = build_normalization(ingress, PENDING, traced)

        # Assert
        events = [
            cast("Mapping[str, object]", canonical.decode(event.payload))
            for event in artifacts.events
        ]
        self.assertEqual(
            (NormalizationRefusal.SEQUENCE, ["vendor=value", "vendor=value"]),
            (captured.value.refusal, [event["tracestate"] for event in events]),
        )

    async def test_an_injected_crash_before_commit_rolls_back_every_buffered_effect(self) -> None:
        # Arrange
        transaction = FakeTransaction(failure=RuntimeError("injected store failure"))
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await handle_agent_response(_delivery(), STAMP, unit_of_work)

        # Assert
        self.assertEqual(
            ("injected store failure", False, True, [], [], []),
            (
                str(captured.value),
                unit_of_work.committed,
                unit_of_work.rolled_back,
                transaction.proposals,
                transaction.events,
                transaction.completed,
            ),
        )


class DuplicateNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_exact_duplicate_returns_prior_result_without_a_second_effect(self) -> None:
        # Arrange
        prior = b'{"prior":"proposal"}'
        transaction = FakeTransaction(
            claim=InboxOutcome(InboxDecision.DUPLICATE, prior),
        )
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        result = await handle_agent_response(_delivery(), STAMP, unit_of_work)

        # Assert
        self.assertEqual(
            (
                NormalizationOutcome.DUPLICATE,
                prior,
                ["record-pending", "load-pending", "claim", "commit"],
                [],
                [],
            ),
            (
                result.outcome,
                result.result,
                transaction.order,
                transaction.proposals,
                transaction.events,
            ),
        )

    async def test_an_incomplete_duplicate_is_refused_and_transaction_rolls_back(self) -> None:
        # Arrange
        transaction = FakeTransaction(
            claim=InboxOutcome(InboxDecision.DUPLICATE, None),
        )
        unit_of_work = FakeUnitOfWork(transaction)

        # Act
        with pytest.raises(NormalizationError) as captured:
            await handle_agent_response(_delivery(), STAMP, unit_of_work)

        # Assert
        self.assertEqual(
            (
                NormalizationRefusal.DUPLICATE_RESULT,
                True,
                ["record-pending", "load-pending", "claim", "rollback"],
            ),
            (captured.value.refusal, unit_of_work.rolled_back, transaction.order),
        )


if __name__ == "__main__":
    unittest.main()
