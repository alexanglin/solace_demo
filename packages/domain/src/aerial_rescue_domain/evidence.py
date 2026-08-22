"""The evidence lifecycle: one deny-by-default transition table over seven states.

The states, the events, the eight legal pairs, and the three terminals are the decision in
``docs/adr/0075-evidence-lifecycle-states.md``. Abstention is a state rather than a score, so
an agent that declined to assert cannot be read as a weak result -- which is what
``docs/adr/0008-abstention-over-recorded-substitution.md`` requires and what makes the
operator display's distinction structural rather than cosmetic.

This module carries no provenance, no artifact hash, no score, and no band. The evidence score
and the bands that gate escalation eligibility are a separate Tier 1 concern with their own
decision. This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class EvidenceState(Enum):
    """Where one evidence item stands, from a request to one of its three endings."""

    REQUESTED = "requested"
    OBSERVED = "observed"
    VALIDATED = "validated"
    MANUAL_REVIEW = "manual-review"
    CONTRIBUTING = "contributing"
    ABSTAINED = "abstained"
    REJECTED = "rejected"


class EvidenceEvent(Enum):
    """What moves an evidence item between states."""

    OBSERVE = "observe"
    ABSTAIN = "abstain"
    VALIDATE = "validate"
    REJECT = "reject"
    REFER = "refer"
    ADMIT = "admit"
    DISMISS = "dismiss"


class EvidenceRefusal(Enum):
    """Why the lifecycle refuses an operation."""

    TRANSITION = "the evidence lifecycle has no such transition"


class EvidenceError(DomainError):
    """A refused evidence operation, carrying the refusal as structured data."""


_TRANSITIONS: Final[Mapping[tuple[EvidenceState, EvidenceEvent], EvidenceState]] = {
    (EvidenceState.REQUESTED, EvidenceEvent.OBSERVE): EvidenceState.OBSERVED,
    (EvidenceState.REQUESTED, EvidenceEvent.ABSTAIN): EvidenceState.ABSTAINED,
    (EvidenceState.OBSERVED, EvidenceEvent.VALIDATE): EvidenceState.VALIDATED,
    (EvidenceState.OBSERVED, EvidenceEvent.REJECT): EvidenceState.REJECTED,
    (EvidenceState.VALIDATED, EvidenceEvent.ADMIT): EvidenceState.CONTRIBUTING,
    (EvidenceState.VALIDATED, EvidenceEvent.REFER): EvidenceState.MANUAL_REVIEW,
    (EvidenceState.MANUAL_REVIEW, EvidenceEvent.ADMIT): EvidenceState.CONTRIBUTING,
    (EvidenceState.MANUAL_REVIEW, EvidenceEvent.DISMISS): EvidenceState.REJECTED,
}
"""The eight legal pairs; an agent that declines abstains, and only an admitted item counts."""

INITIAL_STATE: Final = EvidenceState.REQUESTED
"""Every item starts as an analysis asked of an edge agent, before any response arrives."""

TERMINAL_STATES: Final[frozenset[EvidenceState]] = frozenset(EvidenceState) - {
    source for source, _ in _TRANSITIONS
}
"""The three endings, derived from the table so terminality is not a second home."""


def transition(state: EvidenceState, event: EvidenceEvent) -> EvidenceState:
    """Return the state an event leads to, refusing every pair outside the table.

    Args:
        state: The current state.
        event: The event applied to it.

    Returns:
        The target state.

    Raises:
        EvidenceError: With ``TRANSITION`` when the lifecycle has no such edge, which includes
            every event applied to an ending, so a contributing item is never withdrawn.
    """
    target = _TRANSITIONS.get((state, event))
    if target is None:
        raise EvidenceError(EvidenceRefusal.TRANSITION, (state, event))
    return target


def is_terminal(state: EvidenceState) -> bool:
    """Return whether ``state`` is an ending an evidence item cannot leave."""
    return state in TERMINAL_STATES
