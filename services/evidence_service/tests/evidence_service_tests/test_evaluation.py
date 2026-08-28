from __future__ import annotations

import unittest
from unittest import mock

import pytest
from aerial_rescue_domain.scoring import ObservationOrigin, ScoreError, ScoreRefusal
from aerial_rescue_evidence_service.evaluation import evaluate
from aerial_rescue_store.database.schema import IDENTIFIER_LENGTH

from .support import (
    BOUND_MISSION,
    BOUND_PROPOSAL,
    provenance_fact,
)


class DomainRefusalTests(unittest.TestCase):
    def test_an_unexpected_domain_refusal_is_not_misreported_as_recorded_origin(self) -> None:
        # Arrange
        fact = provenance_fact(
            "evidence-item-model-0001",
            "source-model-0001",
            ObservationOrigin.LIVE_MODEL,
        )
        error = ScoreError(ScoreRefusal.WEIGHT, 101)

        # Act
        with (
            mock.patch(
                "aerial_rescue_evidence_service.evaluation.decision_band",
                side_effect=error,
            ),
            pytest.raises(ScoreError) as captured,
        ):
            evaluate(BOUND_MISSION, BOUND_PROPOSAL, (fact,))

        # Assert
        self.assertIs(error, captured.value)


class ItemIdentityTests(unittest.TestCase):
    def test_two_proposals_scored_against_one_fact_store_distinct_evidence_items(self) -> None:
        # Arrange
        fact = provenance_fact(
            "sensor-synthetic-0001", "drone-synthetic-01", ObservationOrigin.LIVE_SENSOR
        )

        # Act
        first = evaluate(BOUND_MISSION, "proposal-first-0001", (fact,))
        second = evaluate(BOUND_MISSION, "proposal-second-0002", (fact,))

        # Assert
        identities = (first.items[0].evidence_id, second.items[0].evidence_id)
        self.assertEqual(
            (2, True, ("proposal-first-0001", "proposal-second-0002")),
            (
                len(set(identities)),
                all(len(identity) <= IDENTIFIER_LENGTH for identity in identities),
                (first.items[0].proposal_id, second.items[0].proposal_id),
            ),
        )
