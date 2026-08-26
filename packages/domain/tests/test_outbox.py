"""The ADR-0093 outbox publication lifecycle as a pure, deny-by-default transition table.

All six state and event pairs are asserted against the documented table, so a row cannot be
dropped, added, or retargeted without a failing test. Two distinctions the table exists to hold
are asserted apart: an ambiguous outcome is not a confirmation, and a *refused* publication is
not an event at all, so a staged record that the broker declined is still staged.

Terminality is asserted as the absence of an outbound pair rather than as a separate flag, which
is the shape the module implements.
"""

from __future__ import annotations

import unittest
from itertools import product

import pytest

from aerial_rescue_domain.outbox import (
    INITIAL_STATE,
    TERMINAL_STATES,
    OutboxError,
    OutboxEvent,
    OutboxRefusal,
    OutboxState,
    is_terminal,
    transition,
)

LEGAL_TRANSITIONS = {
    (OutboxState.STAGED, OutboxEvent.CONFIRM): OutboxState.CONFIRMED,
    (OutboxState.STAGED, OutboxEvent.AMBIGUOUS): OutboxState.RECONCILIATION_NEEDED,
    (OutboxState.RECONCILIATION_NEEDED, OutboxEvent.CONFIRM): OutboxState.CONFIRMED,
    (OutboxState.RECONCILIATION_NEEDED, OutboxEvent.AMBIGUOUS): (OutboxState.RECONCILIATION_NEEDED),
}
ALL_PAIRS = tuple(product(OutboxState, OutboxEvent))
UNPUBLISHED = (OutboxState.STAGED, OutboxState.RECONCILIATION_NEEDED)


def _transition_outcome_of(state: OutboxState, event: OutboxEvent) -> object:
    """Return the target state, or the refusal and value when the pair is refused."""
    try:
        return transition(state, event)
    except OutboxError as error:
        return (error.refusal, error.value)


class TransitionTableTests(unittest.TestCase):
    def test_every_state_and_event_pair_resolves_exactly_as_the_record_declares(self) -> None:
        # Arrange
        expected = {
            pair: LEGAL_TRANSITIONS.get(pair, (OutboxRefusal.TRANSITION, pair))
            for pair in ALL_PAIRS
        }

        # Act
        outcomes = {pair: _transition_outcome_of(*pair) for pair in ALL_PAIRS}

        # Assert
        self.assertEqual(expected, outcomes)

    def test_a_record_starts_staged_because_that_is_what_the_transaction_wrote(self) -> None:
        # Arrange
        declared = OutboxState.STAGED

        # Act
        initial = INITIAL_STATE

        # Assert
        self.assertEqual(declared, initial)

    def test_confirmation_is_the_only_ending_and_it_absorbs(self) -> None:
        # Arrange
        expected = frozenset({OutboxState.CONFIRMED})

        # Act
        endings = TERMINAL_STATES

        # Assert
        self.assertEqual(expected, endings)

    def test_an_ambiguous_outcome_never_reaches_confirmed(self) -> None:
        # Arrange
        unpublished = UNPUBLISHED

        # Act
        reached = tuple(transition(state, OutboxEvent.AMBIGUOUS) for state in unpublished)

        # Assert
        self.assertEqual((OutboxState.RECONCILIATION_NEEDED,) * len(unpublished), reached)

    def test_a_record_awaiting_reconciliation_can_still_be_confirmed(self) -> None:
        # Arrange
        state = OutboxState.RECONCILIATION_NEEDED

        # Act
        confirmed = transition(state, OutboxEvent.CONFIRM)

        # Assert
        self.assertEqual(OutboxState.CONFIRMED, confirmed)

    def test_a_repeated_ambiguity_leaves_the_record_where_it_was(self) -> None:
        # Arrange
        state = OutboxState.RECONCILIATION_NEEDED

        # Act
        again = transition(transition(state, OutboxEvent.AMBIGUOUS), OutboxEvent.AMBIGUOUS)

        # Assert
        self.assertEqual(state, again)

    def test_a_confirmed_record_refuses_every_event_including_confirmation(self) -> None:
        # Arrange
        state = OutboxState.CONFIRMED

        # Act
        refusals = tuple(_transition_outcome_of(state, event) for event in OutboxEvent)

        # Assert
        self.assertEqual(
            tuple((OutboxRefusal.TRANSITION, (state, event)) for event in OutboxEvent), refusals
        )

    def test_the_refusal_carries_the_pair_that_was_refused(self) -> None:
        # Arrange
        pair = (OutboxState.CONFIRMED, OutboxEvent.CONFIRM)

        # Act
        with pytest.raises(OutboxError) as refused:
            transition(*pair)

        # Assert
        self.assertEqual(
            (OutboxRefusal.TRANSITION, pair), (refused.value.refusal, refused.value.value)
        )

    def test_only_a_confirmed_record_reports_itself_terminal(self) -> None:
        # Arrange
        states = tuple(OutboxState)

        # Act
        terminal = tuple(is_terminal(state) for state in states)

        # Assert
        self.assertEqual(tuple(state is OutboxState.CONFIRMED for state in states), terminal)


if __name__ == "__main__":
    unittest.main()
