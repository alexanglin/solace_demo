"""Property-based invariants of the ADR-0072 mission lifecycle.

Module-level functions with ``derandomize`` for the same reason as the other property
modules: mutmut re-runs pytest in one process, and a flapping example set would turn the
mutation score into a moving number. The invariants asserted here are the ones the table
cannot state on its own -- that the machine is acyclic, that an ending absorbs, and that
every refusal leaves the mission exactly where it was.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.mission import (
    INITIAL_STATE,
    MissionError,
    MissionEvent,
    MissionRefusal,
    MissionState,
    is_terminal,
    transition,
)

STATES = st.sampled_from(tuple(MissionState))
EVENTS = st.sampled_from(tuple(MissionEvent))
SCRIPTS = st.lists(EVENTS, max_size=12)


def _visited(events: list[MissionEvent]) -> list[MissionState]:
    """Return every state a mission occupies while ``events`` are applied, refusals ignored."""
    state = INITIAL_STATE
    seen = [state]
    for event in events:
        try:
            state = transition(state, event)
        except MissionError:
            continue
        seen.append(state)
    return seen


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_a_mission_never_re_enters_a_state_it_has_left(events: list[MissionEvent]) -> None:
    # Arrange
    visited = _visited(events)

    # Act
    distinct = set(visited)

    # Assert
    assert len(distinct) == len(visited)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_an_ending_is_reached_at_most_once_and_only_as_the_last_state(
    events: list[MissionEvent],
) -> None:
    # Arrange
    visited = _visited(events)

    # Act
    positions = [index for index, state in enumerate(visited) if is_terminal(state)]

    # Assert
    assert positions in ([], [len(visited) - 1])


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(STATES, EVENTS)
def test_a_refused_pair_carries_that_exact_pair_as_its_value(
    state: MissionState, event: MissionEvent
) -> None:
    # Arrange
    pair = (state, event)

    # Act
    try:
        outcome: object = transition(state, event)
    except MissionError as error:
        outcome = (error.refusal, error.value)

    # Assert
    assert outcome in tuple(MissionState) or outcome == (MissionRefusal.TRANSITION, pair)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(EVENTS)
def test_an_ending_state_refuses_every_event(event: MissionEvent) -> None:
    # Arrange
    endings = tuple(state for state in MissionState if is_terminal(state))

    # Act
    refused = tuple(_is_refused(state, event) for state in endings)

    # Assert
    assert refused == tuple(True for _ in endings)


def _is_refused(state: MissionState, event: MissionEvent) -> bool:
    """Return whether applying ``event`` to ``state`` is refused."""
    try:
        transition(state, event)
    except MissionError:
        return True
    return False
