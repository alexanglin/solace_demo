"""The ADR-0074 command dispatch lifecycle: a table plus one counted bound.

All thirty state and event pairs are asserted against the documented table, and the send
count is asserted as part of every progress record rather than on the state alone, so an
off-by-one in the budget comparison or a dropped increment fails loudly. The budget is
injected with no default, as the record requires, and the tests supply their own.
"""

from __future__ import annotations

import unittest
from enum import Enum
from itertools import product

import pytest

from aerial_rescue_domain.commands import (
    INITIAL_PROGRESS,
    TERMINAL_STATES,
    CommandError,
    CommandEvent,
    CommandProgress,
    CommandRefusal,
    CommandState,
    SendBudget,
    advance,
    is_terminal,
)

BUDGET = SendBudget(3)
LEGAL_TRANSITIONS = {
    (CommandState.ACCEPTED, CommandEvent.SEND): CommandState.IN_FLIGHT,
    (CommandState.IN_FLIGHT, CommandEvent.TIME_OUT): CommandState.ACCEPTED,
    (CommandState.IN_FLIGHT, CommandEvent.ACKNOWLEDGE): CommandState.ACKNOWLEDGED,
    (CommandState.ACKNOWLEDGED, CommandEvent.SUCCEED): CommandState.SUCCEEDED,
    (CommandState.ACKNOWLEDGED, CommandEvent.FAIL): CommandState.FAILED,
}
ALL_PAIRS = tuple(product(CommandState, CommandEvent))
ENDINGS = (CommandState.SUCCEEDED, CommandState.FAILED, CommandState.ABANDONED)


def _outcome_of(state: CommandState, event: CommandEvent) -> object:
    """Return the target state with no sends spent, or the refusal and the value."""
    try:
        return advance(CommandProgress(state, 0), event, BUDGET).state
    except CommandError as error:
        return (error.refusal, error.value)


def _fold(*events: CommandEvent, budget: SendBudget = BUDGET) -> CommandProgress:
    """Fold ``events`` over a command that has just been accepted."""
    progress = INITIAL_PROGRESS
    for event in events:
        progress = advance(progress, event, budget)
    return progress


def _budget_refusal_of(count: int) -> tuple[Enum, object]:
    """Return the refusal building a budget of ``count`` raises, failing if accepted."""
    try:
        SendBudget(count)
    except CommandError as error:
        return (error.refusal, error.value)
    message = f"accepted: {count!r}"
    raise AssertionError(message)


class SendBudgetTests(unittest.TestCase):
    def test_a_budget_of_one_send_is_accepted(self) -> None:
        # Arrange
        count = 1

        # Act
        budget = SendBudget(count)

        # Assert
        self.assertEqual(1, budget.max_sends)

    def test_a_budget_that_could_never_send_is_refused_at_construction(self) -> None:
        # Arrange
        counts = (0, -1, -7)

        # Act
        refusals = tuple(_budget_refusal_of(count) for count in counts)

        # Assert
        self.assertEqual(tuple((CommandRefusal.SEND_BUDGET, count) for count in counts), refusals)


class AdvanceTests(unittest.TestCase):
    def test_the_thirty_state_event_pairs_resolve_to_the_documented_table(self) -> None:
        # Arrange
        expected = tuple(
            LEGAL_TRANSITIONS.get(pair, (CommandRefusal.TRANSITION, pair)) for pair in ALL_PAIRS
        )

        # Act
        outcomes = tuple(_outcome_of(state, event) for state, event in ALL_PAIRS)

        # Assert
        self.assertEqual(expected, outcomes)

    def test_the_first_send_puts_the_command_on_the_wire_and_counts_it(self) -> None:
        # Arrange
        expected = CommandProgress(CommandState.IN_FLIGHT, 1)

        # Act
        progress = _fold(CommandEvent.SEND)

        # Assert
        self.assertEqual(expected, progress)

    def test_a_timeout_below_the_budget_returns_the_command_for_another_send(self) -> None:
        # Arrange
        expected = CommandProgress(CommandState.ACCEPTED, 1)

        # Act
        progress = _fold(CommandEvent.SEND, CommandEvent.TIME_OUT)

        # Assert
        self.assertEqual(expected, progress)

    def test_a_command_is_abandoned_after_exactly_the_budgeted_sends(self) -> None:
        # Arrange
        attempt = (CommandEvent.SEND, CommandEvent.TIME_OUT)

        # Act
        progress = _fold(*attempt * BUDGET.max_sends)

        # Assert
        self.assertEqual(CommandProgress(CommandState.ABANDONED, BUDGET.max_sends), progress)

    def test_a_single_send_budget_abandons_on_the_first_timeout(self) -> None:
        # Arrange
        budget = SendBudget(1)

        # Act
        progress = _fold(CommandEvent.SEND, CommandEvent.TIME_OUT, budget=budget)

        # Assert
        self.assertEqual(CommandProgress(CommandState.ABANDONED, 1), progress)

    def test_only_a_send_changes_the_count(self) -> None:
        # Arrange
        steps = (
            (CommandProgress(CommandState.IN_FLIGHT, 2), CommandEvent.TIME_OUT),
            (CommandProgress(CommandState.IN_FLIGHT, 2), CommandEvent.ACKNOWLEDGE),
            (CommandProgress(CommandState.ACKNOWLEDGED, 2), CommandEvent.SUCCEED),
            (CommandProgress(CommandState.ACKNOWLEDGED, 2), CommandEvent.FAIL),
        )

        # Act
        counts = tuple(advance(progress, event, BUDGET).sends for progress, event in steps)

        # Assert
        self.assertEqual((2, 2, 2, 2), counts)

    def test_an_acknowledged_command_that_reports_success_succeeds(self) -> None:
        # Arrange
        scenario = (CommandEvent.SEND, CommandEvent.ACKNOWLEDGE, CommandEvent.SUCCEED)

        # Act
        progress = _fold(*scenario)

        # Assert
        self.assertEqual(CommandProgress(CommandState.SUCCEEDED, 1), progress)

    def test_an_acknowledged_command_that_reports_failure_fails(self) -> None:
        # Arrange
        scenario = (CommandEvent.SEND, CommandEvent.ACKNOWLEDGE, CommandEvent.FAIL)

        # Act
        progress = _fold(*scenario)

        # Assert
        self.assertEqual(CommandProgress(CommandState.FAILED, 1), progress)

    def test_a_redelivered_command_acknowledged_late_keeps_every_send_it_cost(self) -> None:
        # Arrange
        scenario = (
            CommandEvent.SEND,
            CommandEvent.TIME_OUT,
            CommandEvent.SEND,
            CommandEvent.ACKNOWLEDGE,
        )

        # Act
        progress = _fold(*scenario)

        # Assert
        self.assertEqual(CommandProgress(CommandState.ACKNOWLEDGED, 2), progress)

    def test_a_command_cannot_fail_before_it_is_acknowledged(self) -> None:
        # Arrange
        pair = (CommandState.IN_FLIGHT, CommandEvent.FAIL)

        # Act
        with pytest.raises(CommandError) as captured:
            advance(CommandProgress(CommandState.IN_FLIGHT, 1), CommandEvent.FAIL, BUDGET)

        # Assert
        self.assertEqual(
            (CommandRefusal.TRANSITION, pair), (captured.value.refusal, captured.value.value)
        )


class TerminalTests(unittest.TestCase):
    def test_a_command_begins_accepted_with_no_sends(self) -> None:
        # Arrange
        expected = CommandProgress(CommandState.ACCEPTED, 0)

        # Act
        initial = INITIAL_PROGRESS

        # Assert
        self.assertEqual(expected, initial)

    def test_the_three_endings_are_the_terminal_set(self) -> None:
        # Arrange
        expected = frozenset(ENDINGS)

        # Act
        terminal = TERMINAL_STATES

        # Assert
        self.assertEqual(expected, terminal)

    def test_no_event_moves_a_command_out_of_an_ending_state(self) -> None:
        # Arrange
        pairs = tuple(product(ENDINGS, CommandEvent))

        # Act
        outcomes = tuple(_outcome_of(state, event) for state, event in pairs)

        # Assert
        self.assertEqual(tuple((CommandRefusal.TRANSITION, pair) for pair in pairs), outcomes)

    def test_every_live_state_reports_not_terminal(self) -> None:
        # Arrange
        states = (CommandState.ACCEPTED, CommandState.IN_FLIGHT, CommandState.ACKNOWLEDGED)

        # Act
        verdicts = tuple(is_terminal(state) for state in states)

        # Assert
        self.assertEqual((False, False, False), verdicts)

    def test_the_terminal_set_is_exactly_the_states_with_no_outbound_transition(self) -> None:
        # Arrange
        sources = {state for state, _ in LEGAL_TRANSITIONS}

        # Act
        without_outbound = {state for state in CommandState if state not in sources}

        # Assert
        self.assertEqual(without_outbound, set(TERMINAL_STATES))


class CommandErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_pair(self) -> None:
        # Arrange
        pair = (CommandState.ACCEPTED, CommandEvent.SUCCEED)

        # Act
        message = str(CommandError(CommandRefusal.TRANSITION, pair))

        # Assert
        self.assertEqual(
            "the command dispatch lifecycle has no such transition: "
            "(<CommandState.ACCEPTED: 'accepted'>, <CommandEvent.SUCCEED: 'succeed'>)",
            message,
        )

    def test_the_budget_refusal_names_the_count_it_refused(self) -> None:
        # Arrange
        error = CommandError(CommandRefusal.SEND_BUDGET, 0)

        # Act
        message = str(error)

        # Assert
        self.assertEqual("a command send budget must allow at least one send: 0", message)


if __name__ == "__main__":
    unittest.main()
