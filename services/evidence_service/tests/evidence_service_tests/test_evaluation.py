from __future__ import annotations

import unittest
from unittest import mock

import pytest
from aerial_rescue_domain.scoring import ObservationOrigin, ScoreError, ScoreRefusal
from aerial_rescue_evidence_service.evaluation import evaluate

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
