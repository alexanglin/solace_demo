"""The evidence score, its named ordinal bands, and the two rules that gate escalation.

The bands, the saturating sum, the two-source corroboration floor, and the refusal of recorded
evidence are the decision in ``docs/adr/0076-evidence-score-bands.md``. The band boundaries are
injected with no defaults and their home is ``docs/operating-parameters.md``, where they are
still an open row.

Two enumerated bypass cases are closed here structurally rather than numerically. The
escalating band requires at least two distinct source identifiers, so no arrangement of weights
and no boundary value reaches it from one source (B32). A contribution whose origin is
``RECORDED`` refuses the computation outright rather than scoring zero, so replayed evidence
cannot influence a live escalation (B31). Neither rule reads a run mode, and the source floor
does not read the boundaries. This module is pure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Final

from aerial_rescue_domain import DomainError


class ObservationOrigin(Enum):
    """Where a contribution came from; only the live origins may decide an escalation."""

    LIVE_MODEL = "live-model"
    LIVE_SENSOR = "live-sensor"
    RECORDED = "recorded"


class EvidenceBand(Enum):
    """The named ordinal band escalation eligibility is keyed on, rather than the score."""

    NONE = "none"
    WEAK = "weak"
    SUPPORTED = "supported"
    CORROBORATED = "corroborated"


class ScoreRefusal(Enum):
    """Why the score refuses a value."""

    BOUNDARIES = "band boundaries must ascend strictly inside the score range"
    WEIGHT = "a contribution weight must be inside the score range"
    RECORDED_CONTRIBUTION = "recorded evidence is never decision-eligible in a live run"


class ScoreError(DomainError):
    """A refused scoring value, carrying the refusal as structured data."""


MINIMUM_SCORE: Final = 0
"""The lowest representable score, in hundredths."""

MAXIMUM_SCORE: Final = 100
"""The highest representable score, in hundredths; the sum saturates here."""

SCORE_VERSION: Final = 1
"""The version carried beside every score, so a stored band can be read back correctly."""

MINIMUM_CORROBORATING_SOURCES: Final = 2
"""Distinct sources the escalating band requires. Structural, not a tunable parameter."""

BAND_ORDER: Final[tuple[EvidenceBand, ...]] = (
    EvidenceBand.NONE,
    EvidenceBand.WEAK,
    EvidenceBand.SUPPORTED,
    EvidenceBand.CORROBORATED,
)
"""The bands in ascending order; the ordinal comparison has one home."""

ESCALATING_BAND: Final = EvidenceBand.CORROBORATED
"""The only band on which a candidate is eligible for a rescue escalation."""


@dataclass(frozen=True)
class BandBoundaries:
    """The lower bound in hundredths of each band above ``NONE``, injected with no defaults."""

    weak: int
    supported: int
    corroborated: int

    def __post_init__(self) -> None:
        """Refuse a boundary set that does not ascend strictly inside the score range."""
        _check_boundaries(self)


@dataclass(frozen=True)
class Contribution:
    """One admitted evidence item's source, origin, and weight in hundredths."""

    source_id: str
    origin: ObservationOrigin
    weight: int

    def __post_init__(self) -> None:
        """Refuse a weight outside the representable score range."""
        if not MINIMUM_SCORE <= self.weight <= MAXIMUM_SCORE:
            raise ScoreError(ScoreRefusal.WEIGHT, self.weight)


def _check_boundaries(boundaries: BandBoundaries) -> None:
    """Raise unless the three boundaries ascend strictly above zero and within the range."""
    steps = (MINIMUM_SCORE, boundaries.weak, boundaries.supported, boundaries.corroborated)
    ascending = all(lower < upper for lower, upper in pairwise(steps))
    if not ascending or boundaries.corroborated > MAXIMUM_SCORE:
        raise ScoreError(ScoreRefusal.BOUNDARIES, steps[1:])


def score(contributions: Sequence[Contribution]) -> int:
    """Return the evidence score in hundredths: the weights summed, saturating at the maximum.

    Args:
        contributions: The admitted evidence items.

    Returns:
        The score, which never falls when a contribution is added.
    """
    return min(MAXIMUM_SCORE, sum(item.weight for item in contributions))


def band_for(total: int, boundaries: BandBoundaries) -> EvidenceBand:
    """Return the band a score falls in, without consulting corroboration.

    Args:
        total: The score in hundredths.
        boundaries: The injected lower bounds.

    Returns:
        The band whose boundary the score reaches.
    """
    if total >= boundaries.corroborated:
        return EvidenceBand.CORROBORATED
    if total >= boundaries.supported:
        return EvidenceBand.SUPPORTED
    if total >= boundaries.weak:
        return EvidenceBand.WEAK
    return EvidenceBand.NONE


def corroborating_sources(contributions: Sequence[Contribution]) -> int:
    """Return how many distinct sources contributed, counting each source once."""
    return len({item.source_id for item in contributions})


def decision_band(
    contributions: Sequence[Contribution], boundaries: BandBoundaries
) -> EvidenceBand:
    """Return the band a live candidate is eligible on.

    Args:
        contributions: The admitted evidence items.
        boundaries: The injected lower bounds.

    Returns:
        The band, capped one step below the escalating band when fewer than
        ``MINIMUM_CORROBORATING_SOURCES`` distinct sources contributed. The cap does not read
        the boundaries, so it holds at every boundary value.

    Raises:
        ScoreError: With ``RECORDED_CONTRIBUTION``, naming the source, when any contribution
            was replayed rather than observed live.
    """
    _refuse_recorded(contributions)
    band = band_for(score(contributions), boundaries)
    uncorroborated = corroborating_sources(contributions) < MINIMUM_CORROBORATING_SOURCES
    if band is ESCALATING_BAND and uncorroborated:
        return BAND_ORDER[BAND_ORDER.index(ESCALATING_BAND) - 1]
    return band


def _refuse_recorded(contributions: Sequence[Contribution]) -> None:
    """Raise on the first replayed contribution, naming its source."""
    for item in contributions:
        if item.origin is ObservationOrigin.RECORDED:
            raise ScoreError(ScoreRefusal.RECORDED_CONTRIBUTION, item.source_id)
