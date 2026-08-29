from __future__ import annotations

import unittest
from dataclasses import replace
from typing import cast

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import (
    Context,
    digest,
    proposal_digest,
    source_event_digest,
)
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_evidence_service.ports import ProvenanceFact, SourceEvidence
from aerial_rescue_evidence_service.source import (
    ProvenanceError,
    ProvenanceRefusal,
    validate_source,
)
from aerial_rescue_evidence_service.wire import AcceptedProposal, accept_proposal

from .support import (
    BOUND_DRONE,
    BOUND_MISSION,
    BOUND_PROPOSAL_TOPIC,
    SOURCE_TOPIC,
    bound_proposal_bytes,
    bound_proposal_document,
    provenance_fact,
    source_document,
    source_evidence,
)


def _proposal() -> AcceptedProposal:
    """Return the accepted proposal used by every source-binding case."""
    return accept_proposal(bound_proposal_bytes(), BOUND_PROPOSAL_TOPIC)


def _source_refusal(
    source: SourceEvidence | None,
    proposal: AcceptedProposal | None = None,
) -> ProvenanceRefusal:
    """Return a source refusal, failing if validation succeeds."""
    try:
        validate_source(proposal or _proposal(), source)
    except ProvenanceError as error:
        return error.refusal
    message = "source evidence unexpectedly accepted"
    raise AssertionError(message)


def _proposal_bound_to(source: dict[str, object]) -> AcceptedProposal:
    """Return an otherwise valid proposal that binds the supplied source bytes."""
    source_envelope = decode_envelope(canonical.canonical_bytes(source))
    proposal = bound_proposal_document()
    data = cast("dict[str, object]", proposal["data"])
    data["sourceEventDigest"] = source_event_digest(source_envelope)
    data["proposalDigest"] = proposal_digest(data)
    return accept_proposal(canonical.canonical_bytes(proposal), BOUND_PROPOSAL_TOPIC)


def _covered_fact(**changes: object) -> ProvenanceFact:
    """Return a fact whose document and digest reflect any changed identity fields."""
    fact = provenance_fact(
        "evidence-item-model-0001",
        "source-model-0001",
        ObservationOrigin.LIVE_MODEL,
    )
    document = dict(fact.document)
    document.update(
        {
            "evidenceItemId": changes.get("evidence_item_id", fact.evidence_item_id),
            "sourceId": changes.get("source_id", fact.source_id),
        }
    )
    return replace(
        fact,
        evidence_item_id=cast("str", changes.get("evidence_item_id", fact.evidence_item_id)),
        source_id=cast("str", changes.get("source_id", fact.source_id)),
        provenance_digest=digest(Context.EVIDENCE, document),
        document=document,
    )


class SourceEnvelopeRefusalTests(unittest.TestCase):
    def test_unreadable_topic_event_and_payload_forms_are_all_fail_closed(self) -> None:
        # Arrange
        fact = _covered_fact()
        missing_detail = source_document()
        data = cast("dict[str, object]", missing_detail["data"])
        del data["detail"]
        cases = (
            replace(source_evidence(fact), topic="not-a-topic"),
            replace(source_evidence(fact), event=b"not-json"),
            replace(source_evidence(fact), event=canonical.canonical_bytes(missing_detail)),
        )

        # Act
        refusals = tuple(_source_refusal(source) for source in cases)

        # Assert
        self.assertEqual((ProvenanceRefusal.MISMATCH,) * len(cases), refusals)

    def test_every_source_identity_binding_is_checked_independently(self) -> None:
        # Arrange
        fact = _covered_fact()
        family = source_document()
        family["type"] = "aerial-rescue.v1.drone.telemetry"
        family["dataschema"] = (
            "https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
        )
        event = source_document()
        event["id"] = "source-event-other-0001"
        mission = source_document()
        mission["subject"] = "m-2026-9999"
        cast("dict[str, object]", mission["data"])["missionId"] = "m-2026-9999"
        drone = source_document()
        drone["source"] = "urn:aerial-rescue:drone:drone-vision-99"
        cast("dict[str, object]", drone["data"])["droneId"] = "drone-vision-99"
        cases = (
            SourceEvidence(
                f"aerial-rescue/v1/{BOUND_MISSION}/drone/{BOUND_DRONE}/telemetry",
                canonical.canonical_bytes(family),
                (fact,),
            ),
            SourceEvidence(SOURCE_TOPIC, canonical.canonical_bytes(event), (fact,)),
            SourceEvidence(
                f"aerial-rescue/v1/m-2026-9999/drone/{BOUND_DRONE}/event/salient",
                canonical.canonical_bytes(mission),
                (fact,),
            ),
            SourceEvidence(
                f"aerial-rescue/v1/{BOUND_MISSION}/drone/drone-vision-99/event/salient",
                canonical.canonical_bytes(drone),
                (fact,),
            ),
        )

        # Act
        refusals = tuple(_source_refusal(source) for source in cases)

        # Assert
        self.assertEqual((ProvenanceRefusal.MISMATCH,) * len(cases), refusals)

    def test_even_a_digest_bound_source_must_be_the_salient_event_from_its_named_drone(
        self,
    ) -> None:
        # Arrange
        fact = _covered_fact()
        wrong_type = source_document()
        wrong_type["source"] = "urn:aerial-rescue:connectivity-lifecycle:connectivity-runtime-01"
        wrong_type["type"] = "aerial-rescue.v1.drone.event.connectivity-changed"
        wrong_type["dataschema"] = (
            "https://aerial-rescue.invalid/schemas/v1/payload/"
            "drone-event-connectivity-changed.schema.json"
        )
        wrong_source = source_document()
        wrong_source["source"] = "urn:aerial-rescue:drone:drone-thermal-99"
        cases = (
            (
                SourceEvidence(
                    f"aerial-rescue/v1/{BOUND_MISSION}/drone/{BOUND_DRONE}/"
                    "event/connectivity-changed",
                    canonical.canonical_bytes(wrong_type),
                    (fact,),
                ),
                _proposal_bound_to(wrong_type),
            ),
            (
                SourceEvidence(SOURCE_TOPIC, canonical.canonical_bytes(wrong_source), (fact,)),
                _proposal_bound_to(wrong_source),
            ),
        )

        # Act
        refusals = tuple(_source_refusal(source, proposal) for source, proposal in cases)

        # Assert
        self.assertEqual((ProvenanceRefusal.MISMATCH,) * len(cases), refusals)


class ProvenanceRowRefusalTests(unittest.TestCase):
    def test_empty_provenance_is_missing_without_creating_an_item(self) -> None:
        # Arrange
        source = source_evidence()

        # Act
        refusal = _source_refusal(source)

        # Assert
        self.assertEqual(ProvenanceRefusal.MISSING, refusal)

    def test_malformed_identity_digest_and_instant_rows_are_refused(self) -> None:
        # Arrange
        valid = _covered_fact()
        invalid_identifier = _covered_fact(source_id="INVALID SOURCE")
        cases = (
            invalid_identifier,
            replace(valid, provenance_digest="not-a-digest"),
            replace(valid, observed_at="tomorrow"),
        )

        # Act
        refusals = tuple(_source_refusal(source_evidence(fact)) for fact in cases)

        # Assert
        self.assertEqual((ProvenanceRefusal.MISMATCH,) * len(cases), refusals)

    def test_a_digest_document_without_its_canonicalization_version_is_refused(self) -> None:
        # Arrange
        valid = _covered_fact()
        document = dict(valid.document)
        del document["canonicalizationVersion"]
        missing_version = replace(valid, document=document)

        # Act
        refusal = _source_refusal(source_evidence(missing_version))

        # Assert
        self.assertEqual(ProvenanceRefusal.MISMATCH, refusal)

    def test_duplicate_evidence_item_identities_are_refused_before_store_contention(self) -> None:
        # Arrange
        fact = _covered_fact()
        source = source_evidence(fact, fact)

        # Act
        refusal = _source_refusal(source)

        # Assert
        self.assertEqual(ProvenanceRefusal.MISMATCH, refusal)
