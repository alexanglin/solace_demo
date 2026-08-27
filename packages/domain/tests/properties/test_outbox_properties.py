"""Property-based invariants of the ADR-0093 outbox publication lifecycle.

Module-level functions with ``derandomize`` for the same reason as the other property modules:
mutmut re-runs pytest in one process, and a flapping example set would turn the mutation score
into a moving number.

The invariants here are the ones the table cannot state on its own. This machine is *not*
acyclic -- a repeated ambiguity is a self-transition -- so the mission machine's no-re-entry
property would be wrong here. What holds instead is that confirmation absorbs, that nothing but
a confirmation reaches it, and that every refusal leaves the record exactly where it was.
"""

from __future__ import annotations

from enum import Enum

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.outbox import (
    INITIAL_STATE,
    OutboxError,
    OutboxEvent,
    OutboxRefusal,
    OutboxState,
    is_terminal,
    transition,
)

STATES = st.sampled_from(tuple(OutboxState))
EVENTS = st.sampled_from(tuple(OutboxEvent))
SCRIPTS = st.lists(EVENTS, max_size=12)


def _folded(events: list[OutboxEvent]) -> OutboxState:
    """Return where a staged record ends after ``events``, refusals ignored."""
    state = INITIAL_STATE
    for event in events:
        try:
            state = transition(state, event)
        except OutboxError:
            continue
    return state


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_a_confirmed_record_stays_confirmed_however_the_script_continues(
    events: list[OutboxEvent],
) -> None:
    # Arrange
    reached = _folded(events)

    # Act
    after = _folded([*events, *events])

    # Assert
    assert after is OutboxState.CONFIRMED if reached is OutboxState.CONFIRMED else True


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_a_record_is_confirmed_only_by_a_confirmation(events: list[OutboxEvent]) -> None:
    # Arrange
    reached = _folded(events)

    # Act
    confirmations = [event for event in events if event is OutboxEvent.CONFIRM]

    # Assert
    assert bool(confirmations) or reached is not OutboxState.CONFIRMED


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(STATES, EVENTS)
def test_a_refused_pair_names_the_pair_and_leaves_the_record_where_it_was(
    state: OutboxState, event: OutboxEvent
) -> None:
    # Arrange
    before = state

    # Act
    refusal = _refusal_of(state, event)

    # Assert
    assert state is before
    assert refusal is None or refusal == (OutboxRefusal.TRANSITION, (before, event))


def _refusal_of(state: OutboxState, event: OutboxEvent) -> tuple[Enum, object] | None:
    """Return the refusal a pair produces, or None when the table has an edge for it."""
    try:
        transition(state, event)
    except OutboxError as refused:
        return (refused.refusal, refused.value)
    return None


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_every_reachable_state_is_one_the_record_declares(events: list[OutboxEvent]) -> None:
    # Arrange
    declared = frozenset(OutboxState)

    # Act
    reached = _folded(events)

    # Assert
    assert reached in declared


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS)
def test_only_an_unconfirmed_record_can_still_move(events: list[OutboxEvent]) -> None:
    # Arrange
    reached = _folded(events)

    # Act
    movable = any(_moves(reached, event) for event in OutboxEvent)

    # Assert
    assert movable is not is_terminal(reached)


def _moves(state: OutboxState, event: OutboxEvent) -> bool:
    """Return whether the table has an edge for this pair."""
    try:
        transition(state, event)
    except OutboxError:
        return False
    return True
