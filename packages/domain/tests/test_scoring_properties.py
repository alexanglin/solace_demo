"""Property-based invariants of the ADR-0076 evidence score and its bands.

Module-level functions with ``derandomize`` for the same reason as the other property
modules: mutmut re-runs pytest in one process, and a flapping example set would turn the
mutation score into a moving number. Two of these are the general forms of the enumerated
bypass cases: B32 is asserted over arbitrary weights and arbitrary valid boundaries, which is
what "impossible by construction" has to mean, and B31 over any position of a recorded
contribution in the list.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.scoring import (
    BAND_ORDER,
    ESCALATING_BAND,
    MAXIMUM_SCORE,
    MINIMUM_SCORE,
    BandBoundaries,
    Contribution,
    ObservationOrigin,
    ScoreError,
    band_for,
    decision_band,
    score,
)

WEIGHTS = st.integers(min_value=MINIMUM_SCORE, max_value=MAXIMUM_SCORE)
SOURCES = st.text(alphabet="abcdef", min_size=1, max_size=3)
LIVE_ORIGINS = st.sampled_from((ObservationOrigin.LIVE_MODEL, ObservationOrigin.LIVE_SENSOR))
LIVE_CONTRIBUTIONS = st.lists(st.builds(Contribution, SOURCES, LIVE_ORIGINS, WEIGHTS), max_size=8)
TOTALS = st.integers(min_value=MINIMUM_SCORE, max_value=MAXIMUM_SCORE)


@st.composite
def boundaries(draw: st.DrawFn) -> BandBoundaries:
    """Draw a valid, strictly ascending boundary record inside the score range."""
    weak = draw(st.integers(min_value=1, max_value=MAXIMUM_SCORE - 2))
    supported = draw(st.integers(min_value=weak + 1, max_value=MAXIMUM_SCORE - 1))
    corroborated = draw(st.integers(min_value=supported + 1, max_value=MAXIMUM_SCORE))
    return BandBoundaries(weak, supported, corroborated)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(LIVE_CONTRIBUTIONS)
def test_the_score_never_leaves_the_documented_range(
    contributions: list[Contribution],
) -> None:
    # Arrange
    bounds = (MINIMUM_SCORE, MAXIMUM_SCORE)

    # Act
    total = score(contributions)

    # Assert
    assert bounds[0] <= total <= bounds[1]


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(LIVE_CONTRIBUTIONS, st.builds(Contribution, SOURCES, LIVE_ORIGINS, WEIGHTS))
def test_admitting_a_contribution_never_lowers_the_score(
    contributions: list[Contribution], extra: Contribution
) -> None:
    # Arrange
    before = score(contributions)

    # Act
    after = score([*contributions, extra])

    # Assert
    assert after >= before


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(TOTALS, TOTALS, boundaries())
def test_the_band_never_falls_as_the_score_rises(
    first: int, second: int, limits: BandBoundaries
) -> None:
    # Arrange
    lower, upper = sorted((first, second))

    # Act
    bands = (band_for(lower, limits), band_for(upper, limits))

    # Assert
    assert BAND_ORDER.index(bands[0]) <= BAND_ORDER.index(bands[1])


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(st.lists(WEIGHTS, min_size=1, max_size=6), LIVE_ORIGINS, boundaries())
def test_b32_a_single_source_never_reaches_the_escalating_band(
    weights: list[int], origin: ObservationOrigin, limits: BandBoundaries
) -> None:
    # Arrange
    contributions = [Contribution("the-only-drone", origin, weight) for weight in weights]

    # Act
    band = decision_band(contributions, limits)

    # Assert
    assert band is not ESCALATING_BAND


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(LIVE_CONTRIBUTIONS, st.integers(min_value=0, max_value=8), WEIGHTS, boundaries())
def test_b31_a_recorded_contribution_anywhere_refuses_the_computation(
    contributions: list[Contribution], position: int, weight: int, limits: BandBoundaries
) -> None:
    # Arrange
    recorded = Contribution("replayed", ObservationOrigin.RECORDED, weight)
    index = min(position, len(contributions))
    mixed = [*contributions[:index], recorded, *contributions[index:]]

    # Act
    try:
        outcome: object = decision_band(mixed, limits)
    except ScoreError as error:
        outcome = error.refusal

    # Assert
    assert outcome not in BAND_ORDER
