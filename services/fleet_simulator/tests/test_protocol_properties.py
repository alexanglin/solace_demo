"""Property-based invariants of the drone's half of the command dispatch protocol.

Module-level functions with ``derandomize`` for the reason the domain's property modules
give: a flapping example set would turn a coverage or mutation number into a moving one.

The invariant these exist for is the one the implementation plan got wrong. It recorded this
member as blocked on the command send budget; every edge a drone applies is blind to it, and
that is asserted here over the whole legal range of budgets rather than at two examples.
"""

from __future__ import annotations

import pytest
from aerial_rescue_domain.commands import (
    CommandEvent,
    CommandProgress,
    CommandState,
    SendBudget,
)
from aerial_rescue_fleet_simulator.protocol import DRONE_EVENTS, ProtocolError, apply, received
from hypothesis import given, settings
from hypothesis import strategies as st

pytestmark = [pytest.mark.unit]

BUDGETS = st.integers(min_value=1, max_value=1_000_000).map(SendBudget)
RESOLUTIONS = st.sampled_from((CommandEvent.SUCCEED, CommandEvent.FAIL))
DISPATCHER_EVENTS = st.sampled_from(
    tuple(event for event in CommandEvent if event not in DRONE_EVENTS)
)
STATES = st.sampled_from(tuple(CommandState))
COUNTS = st.integers(min_value=0, max_value=1_000_000)


@settings(derandomize=True, max_examples=200)
@given(budget=BUDGETS)
def test_a_command_reaches_a_drone_in_flight_whatever_the_budget(budget: SendBudget) -> None:
    """The arrival state is a fact about the wire, not about how many sends are allowed."""
    # Arrange
    expected = CommandProgress(CommandState.IN_FLIGHT, 1)

    # Act
    progress = received(budget)

    # Assert
    assert progress == expected


@settings(derandomize=True, max_examples=200)
@given(budget=BUDGETS, resolution=RESOLUTIONS)
def test_no_drone_side_fold_abandons_a_command_or_sends_it_twice(
    budget: SendBudget, resolution: CommandEvent
) -> None:
    """`ABANDONED` needs `TIME_OUT`, and a second send needs `SEND`; a drone applies neither."""
    # Arrange
    acknowledged = apply(received(budget), CommandEvent.ACKNOWLEDGE, budget)

    # Act
    resolved = apply(acknowledged, resolution, budget)

    # Assert
    assert (resolved.state is not CommandState.ABANDONED, resolved.sends) == (True, 1)


@settings(derandomize=True, max_examples=200)
@given(smaller=BUDGETS, larger=BUDGETS, resolution=RESOLUTIONS)
def test_the_same_fold_under_two_budgets_reaches_the_same_place(
    smaller: SendBudget, larger: SendBudget, resolution: CommandEvent
) -> None:
    """The proof that the send budget was never this member's blocker."""
    # Arrange
    fold = (CommandEvent.ACKNOWLEDGE, resolution)

    # Act
    outcomes = tuple(_resolve(budget, fold) for budget in (smaller, larger))

    # Assert
    assert outcomes[0] == outcomes[1]


@settings(derandomize=True, max_examples=200)
@given(state=STATES, sends=COUNTS, event=DISPATCHER_EVENTS, budget=BUDGETS)
def test_a_dispatcher_event_is_refused_from_every_state_a_command_can_be_in(
    state: CommandState, sends: int, event: CommandEvent, budget: SendBudget
) -> None:
    """The refusal is about who may apply the event, so no state makes it acceptable."""
    # Arrange
    progress = CommandProgress(state, sends)

    # Act
    with pytest.raises(ProtocolError) as captured:
        apply(progress, event, budget)

    # Assert
    assert captured.value.value is event


def _resolve(budget: SendBudget, fold: tuple[CommandEvent, ...]) -> CommandProgress:
    """Fold one command from arrival through every event in ``fold``."""
    progress = received(budget)
    for event in fold:
        progress = apply(progress, event, budget)
    return progress
