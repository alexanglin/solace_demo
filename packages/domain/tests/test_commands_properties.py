"""Property-based invariants of the ADR-0074 command dispatch lifecycle.

Module-level functions with ``derandomize`` for the same reason as the other property
modules: mutmut re-runs pytest in one process, and a flapping example set would turn the
mutation score into a moving number. The invariants here are about the counted bound, which
is the part of this machine the table cannot express on its own.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.commands import (
    INITIAL_PROGRESS,
    CommandError,
    CommandEvent,
    CommandProgress,
    CommandRefusal,
    CommandState,
    SendBudget,
    advance,
    is_terminal,
)

STATES = st.sampled_from(tuple(CommandState))
EVENTS = st.sampled_from(tuple(CommandEvent))
SCRIPTS = st.lists(EVENTS, max_size=20)
BUDGETS = st.integers(min_value=1, max_value=6).map(SendBudget)


def _fold(events: list[CommandEvent], budget: SendBudget) -> CommandProgress:
    """Return the progress after applying ``events``, leaving a refused event with no effect."""
    progress = INITIAL_PROGRESS
    for event in events:
        try:
            progress = advance(progress, event, budget)
        except CommandError:
            continue
    return progress


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS, BUDGETS)
def test_the_send_count_never_exceeds_the_budget(
    events: list[CommandEvent], budget: SendBudget
) -> None:
    # Arrange
    ceiling = budget.max_sends

    # Act
    progress = _fold(events, budget)

    # Assert
    assert progress.sends <= ceiling


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS, BUDGETS)
def test_the_count_equals_the_sends_the_machine_accepted(
    events: list[CommandEvent], budget: SendBudget
) -> None:
    # Arrange
    progress = INITIAL_PROGRESS
    accepted = 0

    # Act
    for event in events:
        try:
            moved = advance(progress, event, budget)
        except CommandError:
            continue
        accepted += event is CommandEvent.SEND
        progress = moved

    # Assert
    assert progress.sends == accepted


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(SCRIPTS, BUDGETS)
def test_a_command_that_reaches_an_ending_stays_there(
    events: list[CommandEvent], budget: SendBudget
) -> None:
    # Arrange
    progress = _fold(events, budget)

    # Act
    after = _fold([*events, *events], budget)

    # Assert
    assert after == progress or not is_terminal(progress.state)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(STATES, EVENTS, BUDGETS)
def test_a_refused_pair_carries_that_exact_pair_as_its_value(
    state: CommandState, event: CommandEvent, budget: SendBudget
) -> None:
    # Arrange
    pair = (state, event)

    # Act
    try:
        outcome: object = advance(CommandProgress(state, 0), event, budget).state
    except CommandError as error:
        outcome = (error.refusal, error.value)

    # Assert
    assert outcome in tuple(CommandState) or outcome == (CommandRefusal.TRANSITION, pair)
