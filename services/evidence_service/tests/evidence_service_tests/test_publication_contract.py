from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import cast

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import evidence_decision_digest_matches
from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_evidence_service.evaluation import evaluate
from aerial_rescue_evidence_service.ports import DecisionStamp
from aerial_rescue_evidence_service.publication import build_artifacts
from aerial_rescue_evidence_service.wire import accept_proposal
from jsonschema import validators
from jsonschema.protocols import Validator
from referencing import Registry, Resource

from tools.contract_gate import JsonObject

from .support import (
    BOUND_MISSION,
    BOUND_PROPOSAL,
    BOUND_PROPOSAL_TOPIC,
    bound_proposal_bytes,
    provenance_fact,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _load(path: Path) -> JsonObject:
    """Load one committed JSON Schema as an object."""
    return cast("JsonObject", json.loads(path.read_text(encoding="utf-8")))


def _validator(relative: str) -> Validator:
    """Return an offline validator with the complete committed registry."""
    schemas = tuple(
        _load(path) for path in sorted(REPOSITORY_ROOT.glob("schemas/**/*.schema.json"))
    )
    registry = Registry().with_resources(
        (cast("str", schema["$id"]), Resource.from_contents(schema)) for schema in schemas
    )
    schema = _load(REPOSITORY_ROOT / relative)
    return validators.validator_for(schema)(schema, registry=registry)


class PublicationContractTests(unittest.TestCase):
    def test_the_audit_record_kind_is_the_audit_envelope_type(self) -> None:
        # Arrange
        proposal = accept_proposal(bound_proposal_bytes(), BOUND_PROPOSAL_TOPIC)
        facts = (
            provenance_fact(
                "evidence-item-sensor-0001", "source-sensor-0001", ObservationOrigin.LIVE_SENSOR
            ),
        )
        evaluation = evaluate(BOUND_MISSION, BOUND_PROPOSAL, facts)
        stamp = DecisionStamp(
            producer_id="evidence-runtime-01",
            decision_id="decision-bound-0001",
            decision_event_id="event-evidence-bound-0001",
            audit_record_id="audit-evidence-bound-0001",
            audit_event_id="event-audit-bound-0001",
            decided_at="2026-08-25T12:04:00.000Z",
            decision_sequence=6,
            audit_sequence=7,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4739-b7ad6b7169203335-01",
        )

        # Act
        artifacts = build_artifacts(proposal, evaluation, stamp)

        # Assert
        audit = cast("JsonObject", canonical.decode(artifacts.audit_record.payload))
        self.assertEqual(audit["type"], artifacts.audit_record.kind)

    def test_contributing_decision_and_audit_are_valid_closed_contract_events(self) -> None:
        # Arrange
        proposal = accept_proposal(bound_proposal_bytes(), BOUND_PROPOSAL_TOPIC)
        facts = (
            provenance_fact(
                "evidence-item-sensor-0001", "source-sensor-0001", ObservationOrigin.LIVE_SENSOR
            ),
            provenance_fact(
                "evidence-item-model-0001", "source-model-0001", ObservationOrigin.LIVE_MODEL
            ),
        )
        evaluation = evaluate(BOUND_MISSION, BOUND_PROPOSAL, facts)
        stamp = DecisionStamp(
            producer_id="evidence-runtime-01",
            decision_id="decision-bound-0001",
            decision_event_id="event-evidence-bound-0001",
            audit_record_id="audit-evidence-bound-0001",
            audit_event_id="event-audit-bound-0001",
            decided_at="2026-08-25T12:04:00.000Z",
            decision_sequence=6,
            audit_sequence=7,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4739-b7ad6b7169203335-01",
        )
        decision_validator = _validator("schemas/v1/event/evidence-decision.schema.json")
        audit_validator = _validator("schemas/v1/event/audit.schema.json")

        # Act
        artifacts = build_artifacts(proposal, evaluation, stamp)

        # Assert
        decision = cast("JsonObject", canonical.decode(artifacts.decision_event.payload))
        audit = cast("JsonObject", canonical.decode(artifacts.audit_event.payload))
        decision_data = cast("dict[str, object]", decision["data"])
        self.assertEqual(
            (True, True, True),
            (
                decision_validator.is_valid(decision),
                audit_validator.is_valid(audit),
                evidence_decision_digest_matches(decision_data),
            ),
        )

    def test_recorded_origin_rejection_and_audit_are_valid_closed_contract_events(self) -> None:
        # Arrange
        proposal = accept_proposal(bound_proposal_bytes(), BOUND_PROPOSAL_TOPIC)
        fact = provenance_fact(
            "evidence-item-recorded-0001",
            "source-recorded-0001",
            ObservationOrigin.RECORDED,
        )
        evaluation = evaluate(BOUND_MISSION, BOUND_PROPOSAL, (fact,))
        stamp = DecisionStamp(
            producer_id="evidence-runtime-01",
            decision_id="decision-bound-0002",
            decision_event_id="event-evidence-bound-0002",
            audit_record_id="audit-evidence-bound-0002",
            audit_event_id="event-audit-bound-0002",
            decided_at="2026-08-25T12:04:01.000Z",
            decision_sequence=8,
            audit_sequence=9,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203336-01",
        )
        decision_validator = _validator("schemas/v1/event/evidence-decision.schema.json")
        audit_validator = _validator("schemas/v1/event/audit.schema.json")

        # Act
        artifacts = build_artifacts(proposal, evaluation, stamp)

        # Assert
        decision = cast("JsonObject", canonical.decode(artifacts.decision_event.payload))
        audit = cast("JsonObject", canonical.decode(artifacts.audit_event.payload))
        self.assertEqual(
            (True, True),
            (decision_validator.is_valid(decision), audit_validator.is_valid(audit)),
        )
