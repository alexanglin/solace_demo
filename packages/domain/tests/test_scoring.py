"""The ADR-0076 evidence score, its ordinal bands, and the two rules that gate escalation.

The two tests that carry the safety weight are the B31 and B32 cases. B32 is asserted under
hostile boundaries as well as ordinary ones -- an escalating boundary set as low as the range
allows, against a single source at the maximum weight -- because the record's whole claim is
that the escalating band is unreachable by construction rather than because of where a number
happens to sit. B31 is asserted as a refusal that names the offending source, not as a zero.
"""

from __future__ import annotations

import unittest
from enum import Enum

import pytest

from aerial_rescue_domain.scoring import (
    BAND_ORDER,
    ESCALATING_BAND,
    MAXIMUM_SCORE,
    MINIMUM_CORROBORATING_SOURCES,
    SCORE_VERSION,
    BandBoundaries,
    Contribution,
    EvidenceBand,
    ObservationOrigin,
    ScoreError,
    ScoreRefusal,
    band_for,
    corroborating_sources,
    decision_band,
    score,
)

BOUNDARIES = BandBoundaries(25, 50, 75)
HOSTILE = BandBoundaries(1, 2, 3)
MODEL = ObservationOrigin.LIVE_MODEL
SENSOR = ObservationOrigin.LIVE_SENSOR


def _live(source: str, weight: int, origin: ObservationOrigin = MODEL) -> Contribution:
    """Build one live contribution."""
    return Contribution(source, origin, weight)


def _boundary_refusal_of(counts: tuple[int, int, int]) -> tuple[Enum, object]:
    """Return the refusal building boundaries from ``counts`` raises, failing if accepted."""
    try:
        BandBoundaries(*counts)
    except ScoreError as error:
        return (error.refusal, error.value)
    message = f"accepted: {counts!r}"
    raise AssertionError(message)


def _weight_refusal_of(weight: int) -> tuple[Enum, object]:
    """Return the refusal building a contribution of ``weight`` raises, failing if accepted."""
    try:
        Contribution("drone-1", MODEL, weight)
    except ScoreError as error:
        return (error.refusal, error.value)
    message = f"accepted: {weight!r}"
    raise AssertionError(message)


class BandBoundaryTests(unittest.TestCase):
    def test_boundaries_that_ascend_inside_the_range_are_accepted(self) -> None:
        # Arrange
        counts = (25, 50, 75)

        # Act
        boundaries = BandBoundaries(*counts)

        # Assert
        self.assertEqual(counts, (boundaries.weak, boundaries.supported, boundaries.corroborated))

    def test_boundaries_that_do_not_ascend_strictly_are_refused(self) -> None:
        # Arrange
        counts = ((0, 50, 75), (25, 25, 75), (25, 50, 50), (75, 50, 25))

        # Act
        refusals = tuple(_boundary_refusal_of(count) for count in counts)

        # Assert
        self.assertEqual(tuple((ScoreRefusal.BOUNDARIES, count) for count in counts), refusals)

    def test_an_escalating_boundary_above_the_score_range_is_refused(self) -> None:
        # Arrange
        counts = (25, 50, MAXIMUM_SCORE + 1)

        # Act
        refusal = _boundary_refusal_of(counts)

        # Assert
        self.assertEqual((ScoreRefusal.BOUNDARIES, counts), refusal)


class ContributionTests(unittest.TestCase):
    def test_a_weight_inside_the_range_is_accepted(self) -> None:
        # Arrange
        weights = (0, MAXIMUM_SCORE)

        # Act
        built = tuple(_live("drone-1", weight).weight for weight in weights)

        # Assert
        self.assertEqual(weights, built)

    def test_a_weight_outside_the_range_is_refused(self) -> None:
        # Arrange
        weights = (-1, MAXIMUM_SCORE + 1)

        # Act
        refusals = tuple(_weight_refusal_of(weight) for weight in weights)

        # Assert
        self.assertEqual(tuple((ScoreRefusal.WEIGHT, weight) for weight in weights), refusals)


class ScoreTests(unittest.TestCase):
    def test_no_contributions_score_nothing(self) -> None:
        # Arrange
        contributions: tuple[Contribution, ...] = ()

        # Act
        total = score(contributions)

        # Assert
        self.assertEqual(0, total)

    def test_the_score_is_the_sum_of_the_weights(self) -> None:
        # Arrange
        contributions = (_live("drone-1", 20), _live("drone-2", 30, SENSOR))

        # Act
        total = score(contributions)

        # Assert
        self.assertEqual(50, total)

    def test_the_score_saturates_at_the_maximum(self) -> None:
        # Arrange
        contributions = (_live("drone-1", 80), _live("drone-2", 80), _live("drone-3", 80))

        # Act
        total = score(contributions)

        # Assert
        self.assertEqual(MAXIMUM_SCORE, total)

    def test_the_score_version_is_carried_beside_the_score(self) -> None:
        # Arrange
        expected = 1

        # Act
        version = SCORE_VERSION

        # Assert
        self.assertEqual(expected, version)


class BandTests(unittest.TestCase):
    def test_each_band_begins_exactly_at_its_boundary(self) -> None:
        # Arrange
        totals = (0, BOUNDARIES.weak, BOUNDARIES.supported, BOUNDARIES.corroborated)

        # Act
        bands = tuple(band_for(total, BOUNDARIES) for total in totals)

        # Assert
        self.assertEqual(BAND_ORDER, bands)

    def test_one_hundredth_below_a_boundary_is_the_band_below(self) -> None:
        # Arrange
        totals = (BOUNDARIES.weak - 1, BOUNDARIES.supported - 1, BOUNDARIES.corroborated - 1)

        # Act
        bands = tuple(band_for(total, BOUNDARIES) for total in totals)

        # Assert
        self.assertEqual(BAND_ORDER[:3], bands)

    def test_the_bands_ascend_and_the_escalating_one_is_the_highest(self) -> None:
        # Arrange
        expected = (
            EvidenceBand.NONE,
            EvidenceBand.WEAK,
            EvidenceBand.SUPPORTED,
            ESCALATING_BAND,
        )

        # Act
        order = BAND_ORDER

        # Assert
        self.assertEqual(expected, order)


class CorroborationTests(unittest.TestCase):
    def test_distinct_sources_are_counted_once_each(self) -> None:
        # Arrange
        contributions = (_live("drone-1", 10), _live("drone-1", 10), _live("drone-2", 10))

        # Act
        sources = corroborating_sources(contributions)

        # Assert
        self.assertEqual(2, sources)

    def test_b32_one_source_cannot_reach_the_escalating_band_at_the_maximum_score(self) -> None:
        # Arrange
        contributions = (_live("drone-1", MAXIMUM_SCORE),)

        # Act
        band = decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertIs(EvidenceBand.SUPPORTED, band)

    def test_b32_one_source_cannot_reach_it_even_at_the_lowest_possible_boundary(self) -> None:
        # Arrange
        contributions = (_live("drone-1", MAXIMUM_SCORE),)

        # Act
        band = decision_band(contributions, HOSTILE)

        # Assert
        self.assertIs(EvidenceBand.SUPPORTED, band)

    def test_b32_repeated_observations_from_one_source_do_not_corroborate(self) -> None:
        # Arrange
        contributions = tuple(_live("drone-1", 40) for _ in range(4))

        # Act
        band = decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertIs(EvidenceBand.SUPPORTED, band)

    def test_two_distinct_sources_reach_the_escalating_band(self) -> None:
        # Arrange
        contributions = (_live("drone-1", 40), _live("drone-2", 40, SENSOR))

        # Act
        band = decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertIs(ESCALATING_BAND, band)

    def test_the_floor_is_two_distinct_sources(self) -> None:
        # Arrange
        expected = 2

        # Act
        floor = MINIMUM_CORROBORATING_SOURCES

        # Assert
        self.assertEqual(expected, floor)

    def test_a_band_below_the_escalating_one_is_unaffected_by_the_source_count(self) -> None:
        # Arrange
        contributions = (_live("drone-1", 30),)

        # Act
        band = decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertIs(EvidenceBand.WEAK, band)


class RecordedEvidenceTests(unittest.TestCase):
    def test_b31_a_recorded_contribution_refuses_the_computation_outright(self) -> None:
        # Arrange
        contributions = (
            _live("drone-1", 40),
            Contribution("replay-1", ObservationOrigin.RECORDED, 40),
        )

        # Act
        with pytest.raises(ScoreError) as captured:
            decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertEqual(
            (ScoreRefusal.RECORDED_CONTRIBUTION, "replay-1"),
            (captured.value.refusal, captured.value.value),
        )

    def test_b31_a_recorded_contribution_is_refused_even_at_a_score_of_zero(self) -> None:
        # Arrange
        contributions = (Contribution("replay-1", ObservationOrigin.RECORDED, 0),)

        # Act
        with pytest.raises(ScoreError) as captured:
            decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertIs(ScoreRefusal.RECORDED_CONTRIBUTION, captured.value.refusal)

    def test_live_origins_are_not_refused(self) -> None:
        # Arrange
        contributions = (_live("drone-1", 10), _live("drone-2", 10, SENSOR))

        # Act
        band = decision_band(contributions, BOUNDARIES)

        # Assert
        self.assertIs(EvidenceBand.NONE, band)


class ScoreErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = ScoreError(ScoreRefusal.RECORDED_CONTRIBUTION, "replay-1")

        # Act
        message = str(error)

        # Assert
        self.assertEqual(
            "recorded evidence is never decision-eligible in a live run: 'replay-1'", message
        )


if __name__ == "__main__":
    unittest.main()
