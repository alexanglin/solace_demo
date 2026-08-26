"""Property-based invariants of the ADR-0073 sector lifecycle.

Module-level functions with ``derandomize`` for the same reason as the other property
modules: mutmut re-runs pytest in one process, and a flapping example set would turn the
mutation score into a moving number. The sector machine is cyclic, so the invariants here are
about absorption and reachability rather than the progress the mission machine has.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.sectors import (
    INITIAL_STATE,
    SectorError,
    SectorEvent,
    SectorRefusal,
    SectorState,
    is_terminal,
    transition,
)

STATES = st.sampled_from(tuple(SectorState))
EVENTS = st.sampled_from(tuple(SectorEvent))
SCRIPTS = st.lists(EVENTS, max_size=16)
HELD = (SectorState.ASSIGNED, SectorState.AT_RISK)


def _fold(events: list[SectorEvent]) -> SectorState:
    """Return the state after applying ``events``, leaving a refused event with no effect."""
    state = INITIAL_STATE
    for event in events:
        try:
            state = transition(state, event)
        except SectorError:
            continue
    return state


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_a_swept_sector_absorbs_every_later_event(events: list[SectorEvent]) -> None:
    # Arrange
    swept = _fold([SectorEvent.ASSIGN, SectorEvent.SWEEP])

    # Act
    after = _fold([SectorEvent.ASSIGN, SectorEvent.SWEEP, *events])

    # Assert
    assert after is swept


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_an_assigned_sector_is_only_ever_held_or_swept(events: list[SectorEvent]) -> None:
    # Arrange
    unswept = [event for event in events if event is not SectorEvent.SWEEP]

    # Act
    state = _fold([SectorEvent.ASSIGN, *unswept])

    # Assert
    assert state in HELD


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_a_sector_is_never_returned_to_unassigned_once_it_is_assigned(
    events: list[SectorEvent],
) -> None:
    # Arrange
    assigned = [SectorEvent.ASSIGN, *events]

    # Act
    state = _fold(assigned)

    # Assert
    assert state is not SectorState.UNASSIGNED


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(STATES, EVENTS)
def test_a_refused_pair_carries_that_exact_pair_as_its_value(
    state: SectorState, event: SectorEvent
) -> None:
    # Arrange
    pair = (state, event)

    # Act
    try:
        outcome: object = transition(state, event)
    except SectorError as error:
        outcome = (error.refusal, error.value)

    # Assert
    assert outcome in tuple(SectorState) or outcome == (SectorRefusal.TRANSITION, pair)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(EVENTS)
def test_only_the_terminal_state_refuses_every_event(event: SectorEvent) -> None:
    # Arrange
    absorbing = tuple(state for state in SectorState if is_terminal(state))

    # Act
    refused = tuple(_is_refused(state, event) for state in absorbing)

    # Assert
    assert refused == tuple(True for _ in absorbing)


def _is_refused(state: SectorState, event: SectorEvent) -> bool:
    """Return whether applying ``event`` to ``state`` is refused."""
    try:
        transition(state, event)
    except SectorError:
        return True
    return False
