"""The drone's half of the command dispatch protocol, folded through the real machine.

ADR-0074 models the dispatcher's view of a command: `SEND` is the gateway putting it on the
wire, `TIME_OUT` is its acknowledgement timer elapsing, and `ABANDONED` is what the send
budget decides. The broker's grant tables make it impossible for this process to observe any
of the three -- it holds no publish grant on the command family and no timer -- so these
tests hold the simulator to the half it can reach, and to the fact that it reaches it
through `packages/domain` rather than through a second copy of the table.

The send budget is the parameter the implementation plan called this member's blocker. Every
edge a drone applies is blind to it, and the last test here is what makes that a proven
claim rather than a reading of the machine.
"""

from __future__ import annotations

import unittest
from enum import Enum
from typing import Final

import pytest
from aerial_rescue_domain.commands import (
    CommandError,
    CommandEvent,
    CommandProgress,
    CommandRefusal,
    CommandState,
    SendBudget,
)
from aerial_rescue_fleet_simulator.protocol import (
    DRONE_EVENTS,
    ProtocolError,
    ProtocolRefusal,
    apply,
    received,
)

BUDGET: Final = SendBudget(max_sends=5)
SMALLEST: Final = SendBudget(max_sends=1)
LARGEST: Final = SendBudget(max_sends=10_000)


def _refusal(progress: CommandProgress, event: CommandEvent) -> tuple[Enum, object]:
    """Return the refusal applying an event raises, failing the test if it is accepted."""
    try:
        apply(progress, event, BUDGET)
    except (ProtocolError, CommandError) as error:
        return (error.refusal, error.value)
    message = f"accepted: progress={progress!r} event={event!r}"
    raise AssertionError(message)


def _resolve(event: CommandEvent, budget: SendBudget = BUDGET) -> CommandProgress:
    """Fold one command from arrival through acknowledgement to its final report."""
    return apply(apply(received(budget), CommandEvent.ACKNOWLEDGE, budget), event, budget)


class ArrivalTests(unittest.TestCase):
    def test_a_command_that_reached_this_drone_has_been_on_the_wire_once(self) -> None:
        """The seed is derived through the domain's own table, not written as a state."""
        # Arrange
        expected = CommandProgress(CommandState.IN_FLIGHT, 1)

        # Act
        progress = received(BUDGET)

        # Assert
        self.assertEqual(expected, progress)


class ProtocolTests(unittest.TestCase):
    def test_a_drone_that_carries_a_command_out_acknowledges_then_succeeds(self) -> None:
        # Arrange
        expected = CommandProgress(CommandState.SUCCEEDED, 1)

        # Act
        progress = _resolve(CommandEvent.SUCCEED)

        # Assert
        self.assertEqual(expected, progress)

    def test_a_drone_that_refuses_a_command_acknowledges_then_fails(self) -> None:
        # Arrange
        expected = CommandProgress(CommandState.FAILED, 1)

        # Act
        progress = _resolve(CommandEvent.FAIL)

        # Assert
        self.assertEqual(expected, progress)

    def test_there_is_no_shortcut_from_in_flight_to_failed(self) -> None:
        """The domain refuses the edge; this member does not re-tabulate the rule."""
        # Arrange
        expected = (CommandRefusal.TRANSITION, (CommandState.IN_FLIGHT, CommandEvent.FAIL))

        # Act
        actual = _refusal(received(BUDGET), CommandEvent.FAIL)

        # Assert
        self.assertEqual(expected, actual)

    def test_the_refusal_of_that_shortcut_comes_from_the_domain(self) -> None:
        """Asserting the type is what proves the guard is not a local copy of the table."""
        # Arrange
        progress = received(BUDGET)

        # Act
        with pytest.raises(CommandError) as captured:
            apply(progress, CommandEvent.FAIL, BUDGET)

        # Assert
        self.assertEqual(
            (CommandRefusal.TRANSITION, True),
            (captured.value.refusal, isinstance(captured.value, CommandError)),
        )


class DispatcherEventTests(unittest.TestCase):
    def test_a_drone_applies_only_the_three_events_it_can_cause(self) -> None:
        # Arrange
        expected = frozenset({CommandEvent.ACKNOWLEDGE, CommandEvent.SUCCEED, CommandEvent.FAIL})

        # Act
        allowed = DRONE_EVENTS

        # Assert
        self.assertEqual(expected, allowed)

    def test_putting_a_command_on_the_wire_is_not_this_process_s_to_claim(self) -> None:
        """The broker gives this role no publish grant on the command family."""
        # Arrange
        expected = (ProtocolRefusal.NOT_A_DRONE_EVENT, CommandEvent.SEND)

        # Act
        actual = _refusal(received(BUDGET), CommandEvent.SEND)

        # Assert
        self.assertEqual(expected, actual)

    def test_the_acknowledgement_timer_is_not_this_process_s_to_run(self) -> None:
        """`TIME_OUT` is the only event that reads the budget, and the gateway owns it."""
        # Arrange
        expected = (ProtocolRefusal.NOT_A_DRONE_EVENT, CommandEvent.TIME_OUT)

        # Act
        actual = _refusal(received(BUDGET), CommandEvent.TIME_OUT)

        # Assert
        self.assertEqual(expected, actual)


class SendBudgetIrrelevanceTests(unittest.TestCase):
    def test_every_drone_side_fold_is_identical_under_any_legal_budget(self) -> None:
        """The proof that this member was never blocked on the send budget."""
        # Arrange
        events = (CommandEvent.SUCCEED, CommandEvent.FAIL)

        # Act
        smallest = tuple(_resolve(event, SMALLEST) for event in events)
        largest = tuple(_resolve(event, LARGEST) for event in events)

        # Assert
        self.assertEqual(smallest, largest)

    def test_no_drone_side_fold_ever_abandons_a_command(self) -> None:
        """`ABANDONED` is reachable only from `TIME_OUT`, which a drone may not apply."""
        # Arrange
        events = (CommandEvent.SUCCEED, CommandEvent.FAIL)

        # Act
        reached = frozenset(_resolve(event, SMALLEST).state for event in events)

        # Assert
        self.assertEqual(frozenset({CommandState.SUCCEEDED, CommandState.FAILED}), reached)

    def test_a_command_this_drone_holds_has_been_sent_exactly_once_as_it_can_see(self) -> None:
        """The count is what this drone can observe, never the gateway's own tally."""
        # Arrange
        events = (CommandEvent.SUCCEED, CommandEvent.FAIL)

        # Act
        counts = frozenset(_resolve(event, LARGEST).sends for event in events)

        # Assert
        self.assertEqual(frozenset({1}), counts)


if __name__ == "__main__":
    unittest.main()
