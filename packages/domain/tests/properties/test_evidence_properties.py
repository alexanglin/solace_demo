"""Property-based invariants of the ADR-0075 evidence lifecycle.

Module-level functions with ``derandomize`` for the same reason as the other property
modules: mutmut re-runs pytest in one process, and a flapping example set would turn the
mutation score into a moving number. The invariant worth the most here is the last one: an
agent that declined to assert can never end up counted, whatever follows.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.evidence import (
    INITIAL_STATE,
    EvidenceError,
    EvidenceEvent,
    EvidenceRefusal,
    EvidenceState,
    is_terminal,
    transition,
)

STATES = st.sampled_from(tuple(EvidenceState))
EVENTS = st.sampled_from(tuple(EvidenceEvent))
SCRIPTS = st.lists(EVENTS, max_size=14)


def _walk(events: list[EvidenceEvent]) -> list[EvidenceState]:
    """Return every state an item occupies while ``events`` are applied, refusals ignored."""
    state = INITIAL_STATE
    seen = [state]
    for event in events:
        try:
            state = transition(state, event)
        except EvidenceError:
            continue
        seen.append(state)
    return seen


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_an_item_never_re_enters_a_state_it_has_left(events: list[EvidenceEvent]) -> None:
    # Arrange
    walked = _walk(events)

    # Act
    distinct = set(walked)

    # Assert
    assert len(distinct) == len(walked)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_an_ending_is_reached_at_most_once_and_only_as_the_last_state(
    events: list[EvidenceEvent],
) -> None:
    # Arrange
    walked = _walk(events)

    # Act
    positions = [index for index, state in enumerate(walked) if is_terminal(state)]

    # Assert
    assert positions in ([], [len(walked) - 1])


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_an_agent_that_declined_to_assert_can_never_be_counted(
    events: list[EvidenceEvent],
) -> None:
    # Arrange
    declined = [EvidenceEvent.ABSTAIN, *events]

    # Act
    walked = _walk(declined)

    # Assert
    assert EvidenceState.CONTRIBUTING not in walked


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_a_counted_item_was_always_observed_and_validated_first(
    events: list[EvidenceEvent],
) -> None:
    # Arrange
    walked = _walk(events)
    required = {EvidenceState.OBSERVED, EvidenceState.VALIDATED}

    # Act
    counted = EvidenceState.CONTRIBUTING in walked

    # Assert
    assert not counted or required.issubset(set(walked))


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(STATES, EVENTS)
def test_a_refused_pair_carries_that_exact_pair_as_its_value(
    state: EvidenceState, event: EvidenceEvent
) -> None:
    # Arrange
    pair = (state, event)

    # Act
    try:
        outcome: object = transition(state, event)
    except EvidenceError as error:
        outcome = (error.refusal, error.value)

    # Assert
    assert outcome in tuple(EvidenceState) or outcome == (EvidenceRefusal.TRANSITION, pair)
