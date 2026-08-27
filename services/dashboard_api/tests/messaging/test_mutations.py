"""Atomic broker-backed command and exact proposal-decision mutation tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, cast, override

import pytest
from aerial_rescue_broker.ingress import load_runtime_schema_registry, validate_notification
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_dashboard_api.boundary.ingress import parse_mutation
from aerial_rescue_dashboard_api.boundary.mutation_boundary import AuthorizedMutation
from aerial_rescue_dashboard_api.messaging import mutations as mutations_module
from aerial_rescue_dashboard_api.messaging.mutations import (
    DashboardMutationError,
    DashboardMutationService,
    MutationRefusal,
    MutationStamp,
)
from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_domain.idempotency import IdempotencyDecision
from aerial_rescue_domain.scoring import EvidenceBand
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.approval_bindings import StoredApprovalBinding
from aerial_rescue_store.approvals import StoredApproval
from aerial_rescue_store.evidence import EvidenceDecisionOutcome, StoredEvidenceDecision
from aerial_rescue_store.idempotency import ClaimOutcome, StoredClaim
from aerial_rescue_store.proposals import StoredProposal

_ROOT = Path(__file__).parents[4]
_FIXTURES = _ROOT / "fixtures" / "golden" / "v1"
_KEY: Final = "123e4567-e89b-42d3-a456-426614174000"
_TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203332-01"
_NOW: Final = "2026-08-26T12:00:00.000Z"
_VALUE_REFUSAL_COUNT: Final = 7


def _bytes(path: str) -> bytes:
    """Read one committed canonical fixture."""
    return (_FIXTURES / path).read_bytes()


def _mutation(name: str, *, operator: str = "local-operator") -> AuthorizedMutation:
    """Return one already admitted public mutation."""
    body = _bytes(f"dashboard/{name}/baseline.json")
    ingress = parse_mutation(
        schema_id=(f"https://aerial-rescue.invalid/schemas/v1/dashboard/{name}.schema.json"),
        body=body,
        content_type="application/json",
        idempotency_key=_KEY,
        path_bindings={},
    )
    return AuthorizedMutation(ingress, operator)


def _decision_mutation(decision: str) -> AuthorizedMutation:
    document = cast(
        "dict[str, object]",
        canonical.decode(_bytes("dashboard/proposal-decision-request/baseline.json")),
    )
    document["decision"] = decision
    body = canonical.canonical_bytes(document)
    ingress = parse_mutation(
        schema_id=(
            "https://aerial-rescue.invalid/schemas/v1/dashboard/"
            "proposal-decision-request.schema.json"
        ),
        body=body,
        content_type="application/json",
        idempotency_key=_KEY,
        path_bindings={},
    )
    return AuthorizedMutation(ingress, "local-operator")


def _proposal() -> StoredProposal:
    """Return the authoritative stored proposal behind the decision fixture."""
    payload = _bytes("payload/agent-proposal/baseline.json")
    document = cast("dict[str, object]", canonical.decode(payload))
    return StoredProposal(
        proposal_id=cast("str", document["proposalId"]),
        mission_id=cast("str", document["missionId"]),
        source_event_id=cast("str", document["sourceEventId"]),
        source_event_digest=cast("str", document["sourceEventDigest"]),
        agent_name=cast("str", document["agentName"]),
        invocation_id=cast("str", document["sourceInvocationId"]),
        proposal_type=cast("str", document["proposalType"]),
        proposal_digest=cast("str", document["proposalDigest"]),
        payload=canonical.canonical_bytes(document),
        drone_id=cast("str", document["droneId"]),
        latitude_microdegrees=cast("int", document["latitudeMicrodegrees"]),
        longitude_microdegrees=cast("int", document["longitudeMicrodegrees"]),
        command_type=cast("str", document["commandType"]),
        issued_at="2026-08-26T11:59:00.000Z",
        sequence=1,
        correlation_id="correlation-01",
        causation_id="source-event-01",
        traceparent=_TRACEPARENT,
    )


def _evidence() -> StoredEvidenceDecision:
    """Return the authoritative corroborated evidence decision behind the fixture."""
    payload = _bytes("payload/evidence-decision/baseline.json")
    document = cast("dict[str, object]", canonical.decode(payload))
    return StoredEvidenceDecision(
        decision_id=cast("str", document["evidenceDecisionId"]),
        mission_id=cast("str", document["missionId"]),
        proposal_id=cast("str", document["proposalId"]),
        proposal_digest=cast("str", document["proposalDigest"]),
        decision_digest=cast("str", document["evidenceDecisionDigest"]),
        decision_version=1,
        score_version=1,
        score=75,
        band=EvidenceBand.CORROBORATED,
        outcome=EvidenceDecisionOutcome.CONTRIBUTING,
        contributors=canonical.canonical_bytes(document["contributors"]),
        payload=canonical.canonical_bytes(document),
        decided_at="2026-08-26T11:59:30.000Z",
        sequence=1,
    )


@dataclass
class _Transaction:
    """Record the exact operations requested inside one fake atomic transaction."""

    claim_outcome: ClaimOutcome = field(
        default_factory=lambda: ClaimOutcome(IdempotencyDecision.EXECUTE, None)
    )
    proposal: StoredProposal = field(default_factory=_proposal)
    evidence: StoredEvidenceDecision = field(default_factory=_evidence)
    calls: list[str] = field(default_factory=list)
    claim_request: StoredClaim | None = None
    staged: list[StagedApplicationEvent] = field(default_factory=list)
    approval: StoredApproval | None = None
    binding: StoredApprovalBinding | None = None
    result: bytes | None = None

    async def claim(self, request: StoredClaim) -> ClaimOutcome:
        self.calls.append("claim")
        self.claim_request = request
        return self.claim_outcome

    async def load_proposal(self, _proposal_id: str) -> StoredProposal:
        self.calls.append("load-proposal")
        return self.proposal

    async def load_evidence_decision(self, _decision_id: str) -> StoredEvidenceDecision:
        self.calls.append("load-evidence")
        return self.evidence

    async def load_evidence_decisions(
        self, _proposal_id: str
    ) -> tuple[StoredEvidenceDecision, ...]:
        self.calls.append("load-evidence-history")
        return (self.evidence,)

    async def record_decision(
        self, approval: StoredApproval, binding: StoredApprovalBinding
    ) -> None:
        self.calls.append("record-decision")
        self.approval = approval
        self.binding = binding

    async def stage(self, event: StagedApplicationEvent) -> None:
        self.calls.append("stage")
        self.staged.append(event)

    async def record_result(self, _idempotency_key: str, result: bytes) -> None:
        self.calls.append("record-result")
        self.result = result


@dataclass
class _Transactions:
    transaction: _Transaction
    exits: list[str] = field(default_factory=list)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_Transaction]:
        try:
            yield self.transaction
        finally:
            self.exits.append("commit")


@dataclass
class _Stamps:
    sequence: int = 0

    def next(self) -> MutationStamp:
        self.sequence += 1
        return MutationStamp(
            event_id=f"event-{self.sequence:04d}",
            entity_id=f"entity-{self.sequence:04d}",
            occurred_at=_NOW,
            monotonic_milliseconds=1_000,
            sequence=self.sequence,
            traceparent=_TRACEPARENT,
        )


def _service(transaction: _Transaction) -> DashboardMutationService:
    """Build one deterministic mutation service with the real schema registry."""
    return DashboardMutationService(
        transactions=_Transactions(transaction),
        runtime_id="runtime-synthetic-0001",
        stamps=_Stamps().next,
        schemas=load_runtime_schema_registry(_ROOT / "schemas"),
        approval_time_to_live_milliseconds=60_000,
    )


@pytest.mark.asyncio
async def test_assign_sector_command_is_claimed_staged_and_answered_in_one_transaction() -> None:
    # Arrange
    transaction = _Transaction()
    service = _service(transaction)

    # Act
    result = await service.command(_mutation("operator-command-request"))
    staged = transaction.staged[0]
    validated = validate_notification(staged.topic, staged.payload, service.schemas)
    envelope = decode_envelope(staged.payload)

    # Assert
    assert transaction.calls == ["claim", "stage", "record-result"]
    assert result == transaction.result
    assert validated.topic.family.name == "OPERATOR_COMMAND"
    assert envelope.data["commandId"] == "entity-0001"
    assert envelope.data["operatorId"] == "local-operator"
    assert envelope.source == "urn:aerial-rescue:dashboard-api:runtime-synthetic-0001"


@pytest.mark.asyncio
async def test_exact_command_repeat_returns_prior_result_without_another_event() -> None:
    # Arrange
    prior = canonical.canonical_bytes(
        {
            "commandId": "prior",
            "eventId": "prior-event",
            "missionId": "mission-synthetic-0001",
            "operationVersion": "dashboard-command-response/v1",
        }
    )
    transaction = _Transaction(
        claim_outcome=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, prior)
    )
    service = _service(transaction)

    # Act
    result = await service.command(_mutation("operator-command-request"))

    # Assert
    assert result == prior
    assert transaction.calls == ["claim"]
    assert transaction.staged == []


@pytest.mark.asyncio
async def test_exact_corroborated_approval_persists_binding_event_and_response_atomically() -> None:
    # Arrange
    transaction = _Transaction()
    service = _service(transaction)

    # Act
    result = await service.decide(_mutation("proposal-decision-request"))
    envelope = decode_envelope(transaction.staged[0].payload)
    response = cast("dict[str, object]", canonical.decode(result))

    # Assert
    assert transaction.calls == [
        "claim",
        "load-proposal",
        "load-evidence",
        "load-evidence-history",
        "record-decision",
        "stage",
        "record-result",
    ]
    assert transaction.approval is not None
    assert transaction.approval.state.value == "approved"
    assert transaction.binding is not None
    request = cast(
        "dict[str, object]",
        canonical.decode(_bytes("dashboard/proposal-decision-request/baseline.json")),
    )
    assert transaction.binding.action_payload == canonical.canonical_bytes(request["action"])
    assert transaction.binding.decision_runtime_id == "runtime-synthetic-0001"
    assert transaction.binding.authority_runtime_epoch is None
    assert transaction.binding.authority_issued_monotonic_milliseconds is None
    assert envelope.data["evidenceDecisionDigest"] == transaction.evidence.decision_digest
    assert response["expiresAt"] == "2026-08-26T12:01:00.000Z"


@pytest.mark.asyncio
async def test_mismatched_authority_refuses_before_decision_or_publication() -> None:
    # Arrange
    transaction = _Transaction(evidence=replace(_evidence(), decision_digest="f" * 64))
    service = _service(transaction)

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        await service.decide(_mutation("proposal-decision-request"))

    # Assert
    assert captured.value.refusal is MutationRefusal.AUTHORITY_MISMATCH
    assert transaction.calls == [
        "claim",
        "load-proposal",
        "load-evidence",
        "load-evidence-history",
    ]
    assert transaction.staged == []
    assert transaction.approval is None


@pytest.mark.asyncio
async def test_superseded_evidence_decision_refuses_before_operator_event() -> None:
    # Arrange
    class _StaleTransaction(_Transaction):
        @override
        async def load_evidence_decisions(
            self, _proposal_id: str
        ) -> tuple[StoredEvidenceDecision, ...]:
            self.calls.append("load-evidence-history")
            return (self.evidence, replace(self.evidence, decision_id="decision-new", sequence=2))

    transaction = _StaleTransaction()
    service = _service(transaction)

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        await service.decide(_mutation("proposal-decision-request"))

    # Assert
    assert captured.value.refusal is MutationRefusal.AUTHORITY_MISMATCH
    assert transaction.staged == []
    assert transaction.approval is None


@pytest.mark.parametrize("time_to_live", [0, -1])
def test_service_refuses_nonpositive_approval_lifetime(time_to_live: int) -> None:
    # Arrange
    transaction = _Transaction()

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        DashboardMutationService(
            transactions=_Transactions(transaction),
            runtime_id="runtime-synthetic-0001",
            stamps=_Stamps().next,
            schemas=load_runtime_schema_registry(_ROOT / "schemas"),
            approval_time_to_live_milliseconds=time_to_live,
        )

    # Assert
    assert captured.value.refusal is MutationRefusal.REQUEST


@pytest.mark.asyncio
async def test_exact_decision_repeat_returns_prior_result_without_loading_authority() -> None:
    # Arrange
    prior = canonical.canonical_bytes(
        {
            "operationVersion": "dashboard-proposal-decision-response/v1",
            "missionId": "mission-synthetic-0001",
            "proposalId": "proposal-synthetic-0001",
            "approvalId": "approval-synthetic-0001",
            "eventId": "event-synthetic-0001",
            "decision": "reject",
            "issuedAt": _NOW,
        }
    )
    transaction = _Transaction(
        claim_outcome=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, prior)
    )
    service = _service(transaction)

    # Act
    result = await service.decide(_decision_mutation("reject"))

    # Assert
    assert result == prior
    assert transaction.calls == ["claim"]


@pytest.mark.asyncio
async def test_rejection_persists_no_expiry_and_does_not_require_eligible_evidence() -> None:
    # Arrange
    transaction = _Transaction(
        evidence=replace(
            _evidence(),
            outcome=EvidenceDecisionOutcome.REJECTED,
            band=None,
        )
    )
    service = _service(transaction)

    # Act
    result = await service.decide(_decision_mutation("reject"))
    response = cast("dict[str, object]", canonical.decode(result))
    envelope = decode_envelope(transaction.staged[0].payload)

    # Assert
    assert response["decision"] == "reject"
    assert "expiresAt" not in response
    assert "expiresAt" not in envelope.data
    assert transaction.approval is not None
    assert transaction.approval.state is ApprovalState.REJECTED
    assert transaction.binding is not None
    assert transaction.binding.expires_at is None


@pytest.mark.parametrize(
    "evidence",
    [
        replace(_evidence(), outcome=EvidenceDecisionOutcome.MANUAL_REVIEW),
        replace(_evidence(), band=None),
    ],
)
@pytest.mark.asyncio
async def test_approval_refuses_noncontributing_or_noncorroborated_evidence(
    evidence: StoredEvidenceDecision,
) -> None:
    # Arrange
    transaction = _Transaction(evidence=evidence)
    service = _service(transaction)

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        await service.decide(_mutation("proposal-decision-request"))

    # Assert
    assert captured.value.refusal is MutationRefusal.APPROVAL_INELIGIBLE
    assert transaction.staged == []


@dataclass
class _InvalidSequenceStamps:
    def next(self) -> MutationStamp:
        return MutationStamp(
            event_id="event-synthetic-0001",
            entity_id="command-synthetic-0001",
            occurred_at=_NOW,
            monotonic_milliseconds=1_000,
            sequence=-1,
            traceparent=_TRACEPARENT,
        )


@pytest.mark.asyncio
async def test_command_refuses_a_stamp_outside_the_envelope_sequence_profile() -> None:
    # Arrange
    transaction = _Transaction()
    service = DashboardMutationService(
        transactions=_Transactions(transaction),
        runtime_id="runtime-synthetic-0001",
        stamps=_InvalidSequenceStamps().next,
        schemas=load_runtime_schema_registry(_ROOT / "schemas"),
        approval_time_to_live_milliseconds=60_000,
    )

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        await service.command(_mutation("operator-command-request"))

    # Assert
    assert captured.value.refusal is MutationRefusal.SEQUENCE
    assert transaction.staged == []


@pytest.mark.asyncio
async def test_duplicate_claim_without_a_recorded_result_fails_closed() -> None:
    # Arrange
    transaction = _Transaction(
        claim_outcome=ClaimOutcome(IdempotencyDecision.RETURN_PRIOR_RESULT, None)
    )
    service = _service(transaction)

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        await service.command(_mutation("operator-command-request"))

    # Assert
    assert captured.value.refusal is MutationRefusal.DUPLICATE_RESULT
    assert transaction.calls == ["claim"]


@dataclass
class _EmptyHistoryTransaction(_Transaction):
    @override
    async def load_evidence_decisions(
        self,
        _proposal_id: str,
    ) -> tuple[StoredEvidenceDecision, ...]:
        self.calls.append("load-evidence-history")
        return ()


@pytest.mark.asyncio
async def test_empty_evidence_history_refuses_before_binding_or_publication() -> None:
    # Arrange
    transaction = _EmptyHistoryTransaction()
    service = _service(transaction)

    # Act
    with pytest.raises(DashboardMutationError) as captured:
        await service.decide(_mutation("proposal-decision-request"))

    # Assert
    assert captured.value.refusal is MutationRefusal.AUTHORITY_MISMATCH
    assert transaction.calls[-1] == "load-evidence-history"
    assert transaction.staged == []


def _evidence_with_contributors(contributors: object) -> StoredEvidenceDecision:
    document = cast("dict[str, object]", canonical.decode(_evidence().payload))
    document["contributors"] = contributors
    return replace(
        _evidence(),
        payload=canonical.canonical_bytes(document),
    )


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence_with_contributors({}),
        _evidence_with_contributors(["not-an-object"]),
        _evidence_with_contributors(
            [
                {"sourceId": 7, "origin": "live-sensor"},
                {"sourceId": "source-2", "origin": "recorded"},
            ]
        ),
        _evidence_with_contributors(
            [
                {"sourceId": "source-1", "origin": "live-sensor"},
                {"sourceId": "source-1", "origin": "live-model"},
            ]
        ),
    ],
)
def test_eligibility_refuses_malformed_untrusted_or_nondistinct_provenance(
    evidence: StoredEvidenceDecision,
) -> None:
    # Arrange
    candidate = evidence

    # Act
    eligible = mutations_module._eligible(candidate)

    # Assert
    assert eligible is False


@dataclass
class _NonMappingDocument:
    def model_dump(self, *, mode: str, by_alias: bool) -> object:
        return [mode, by_alias]


@dataclass
class _NonMappingIngress:
    document: _NonMappingDocument = field(default_factory=_NonMappingDocument)


def test_mutation_value_helpers_refuse_noncanonical_or_wrongly_typed_authority() -> None:
    # Arrange
    malformed_mutation = cast(
        "AuthorizedMutation",
        type("MalformedMutation", (), {"ingress": _NonMappingIngress()})(),
    )

    # Act
    with pytest.raises(DashboardMutationError) as document:
        mutations_module._mutation_document(malformed_mutation)
    canonical_refusals: list[pytest.ExceptionInfo[DashboardMutationError]] = []
    for payload in (b'{ "member": 1 }', b"[]", b"not-json"):
        with pytest.raises(DashboardMutationError) as captured:
            mutations_module._canonical_mapping(payload)
        canonical_refusals.append(captured)
    with pytest.raises(DashboardMutationError) as mapping:
        mutations_module._mapping({}, "member")
    with pytest.raises(DashboardMutationError) as text:
        mutations_module._text({"member": 1}, "member")
    with pytest.raises(DashboardMutationError) as integer:
        mutations_module._integer({"member": True}, "member")

    # Assert
    refusals = (document, *canonical_refusals, mapping, text, integer)
    assert all(
        captured.value.refusal is MutationRefusal.REQUEST
        for captured in (document, mapping, text, integer)
    )
    assert all(
        captured.value.refusal is MutationRefusal.AUTHORITY_MISMATCH
        for captured in canonical_refusals
    )
    assert len(refusals) == _VALUE_REFUSAL_COUNT
