"""The ADR-0072 mission lifecycle as a pure, deny-by-default transition table.

All thirty state and event pairs are asserted against the documented table, so a row cannot
be dropped, added, or retargeted without a failing test. Terminality is asserted as the
absence of an outbound pair rather than as a separate flag, which is the shape the module
implements, and the two endings a wilderness search can have are asserted apart.
"""

from __future__ import annotations

import unittest
from itertools import product

import pytest

from aerial_rescue_domain.mission import (
    INITIAL_STATE,
    TERMINAL_STATES,
    MissionError,
    MissionEvent,
    MissionRefusal,
    MissionState,
    event_reaching,
    is_terminal,
    transition,
)

LEGAL_TRANSITIONS = {
    (MissionState.PLANNED, MissionEvent.START): MissionState.SEARCHING,
    (MissionState.SEARCHING, MissionEvent.ESCALATE): MissionState.ESCALATED,
    (MissionState.SEARCHING, MissionEvent.EXHAUST): MissionState.EXHAUSTED,
    (MissionState.ESCALATED, MissionEvent.COMPLETE): MissionState.COMPLETED,
    (MissionState.PLANNED, MissionEvent.ABORT): MissionState.ABORTED,
    (MissionState.SEARCHING, MissionEvent.ABORT): MissionState.ABORTED,
    (MissionState.ESCALATED, MissionEvent.ABORT): MissionState.ABORTED,
}
ALL_PAIRS = tuple(product(MissionState, MissionEvent))
ENDINGS = (MissionState.COMPLETED, MissionState.EXHAUSTED, MissionState.ABORTED)
RUNNING = (MissionState.PLANNED, MissionState.SEARCHING, MissionState.ESCALATED)


def _transition_outcome_of(state: MissionState, event: MissionEvent) -> object:
    """Return the target state, or the refusal and value when the pair is refused."""
    try:
        return transition(state, event)
    except MissionError as error:
        return (error.refusal, error.value)


def _after(*events: MissionEvent) -> MissionState:
    """Fold ``events`` over a mission that begins in the initial state."""
    state = INITIAL_STATE
    for event in events:
        state = transition(state, event)
    return state


class TransitionTests(unittest.TestCase):
    def test_the_thirty_state_event_pairs_resolve_to_the_documented_table(self) -> None:
        # Arrange
        expected = tuple(
            LEGAL_TRANSITIONS.get(pair, (MissionRefusal.TRANSITION, pair)) for pair in ALL_PAIRS
        )

        # Act
        outcomes = tuple(_transition_outcome_of(state, event) for state, event in ALL_PAIRS)

        # Assert
        self.assertEqual(expected, outcomes)

    def test_completed_is_reachable_only_from_an_escalated_mission(self) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        completing = {
            pair for pair in pairs if _transition_outcome_of(*pair) is MissionState.COMPLETED
        }

        # Assert
        self.assertEqual({(MissionState.ESCALATED, MissionEvent.COMPLETE)}, completing)

    def test_exhausted_is_reachable_only_from_a_searching_mission(self) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        exhausting = {
            pair for pair in pairs if _transition_outcome_of(*pair) is MissionState.EXHAUSTED
        }

        # Assert
        self.assertEqual({(MissionState.SEARCHING, MissionEvent.EXHAUST)}, exhausting)

    def test_no_event_moves_a_mission_out_of_an_ending_state(self) -> None:
        # Arrange
        pairs = tuple(product(ENDINGS, MissionEvent))

        # Act
        outcomes = tuple(_transition_outcome_of(state, event) for state, event in pairs)

        # Assert
        self.assertEqual(tuple((MissionRefusal.TRANSITION, pair) for pair in pairs), outcomes)

    def test_a_refused_transition_names_the_state_and_the_event(self) -> None:
        # Arrange
        pair = (MissionState.PLANNED, MissionEvent.COMPLETE)

        # Act
        with pytest.raises(MissionError) as captured:
            transition(*pair)

        # Assert
        self.assertEqual(
            (MissionRefusal.TRANSITION, pair), (captured.value.refusal, captured.value.value)
        )

    def test_the_release_scenario_runs_from_planned_through_escalation_to_completed(self) -> None:
        # Arrange
        scenario = (MissionEvent.START, MissionEvent.ESCALATE, MissionEvent.COMPLETE)

        # Act
        state = _after(*scenario)

        # Assert
        self.assertIs(MissionState.COMPLETED, state)

    def test_a_swept_search_that_finds_nothing_ends_exhausted_rather_than_aborted(self) -> None:
        # Arrange
        scenario = (MissionEvent.START, MissionEvent.EXHAUST)

        # Act
        state = _after(*scenario)

        # Assert
        self.assertIs(MissionState.EXHAUSTED, state)

    def test_an_operator_can_end_a_mission_from_every_running_state(self) -> None:
        # Arrange
        states = RUNNING

        # Act
        outcomes = tuple(_transition_outcome_of(state, MissionEvent.ABORT) for state in states)

        # Assert
        self.assertEqual(tuple(MissionState.ABORTED for _ in states), outcomes)


class TerminalTests(unittest.TestCase):
    def test_a_mission_begins_planned(self) -> None:
        # Arrange
        expected = MissionState.PLANNED

        # Act
        initial = INITIAL_STATE

        # Assert
        self.assertIs(expected, initial)

    def test_the_three_ending_states_are_the_terminal_set(self) -> None:
        # Arrange
        expected = frozenset(ENDINGS)

        # Act
        terminal = TERMINAL_STATES

        # Assert
        self.assertEqual(expected, terminal)

    def test_every_ending_state_reports_terminal(self) -> None:
        # Arrange
        states = ENDINGS

        # Act
        verdicts = tuple(is_terminal(state) for state in states)

        # Assert
        self.assertEqual((True, True, True), verdicts)

    def test_every_running_state_reports_not_terminal(self) -> None:
        # Arrange
        states = RUNNING

        # Act
        verdicts = tuple(is_terminal(state) for state in states)

        # Assert
        self.assertEqual((False, False, False), verdicts)

    def test_the_terminal_set_is_exactly_the_states_with_no_outbound_transition(self) -> None:
        # Arrange
        sources = {state for state, _ in LEGAL_TRANSITIONS}

        # Act
        without_outbound = {state for state in MissionState if state not in sources}

        # Assert
        self.assertEqual(without_outbound, set(TERMINAL_STATES))


class EventReachingTests(unittest.TestCase):
    def test_every_state_names_the_one_event_that_reaches_it_or_none(self) -> None:
        """A producer that observed a state needs the event that would explain it."""
        # Arrange
        expected = {
            MissionState.PLANNED: None,
            MissionState.SEARCHING: MissionEvent.START,
            MissionState.ESCALATED: MissionEvent.ESCALATE,
            MissionState.COMPLETED: MissionEvent.COMPLETE,
            MissionState.EXHAUSTED: MissionEvent.EXHAUST,
            MissionState.ABORTED: MissionEvent.ABORT,
        }

        # Act
        observed = {state: event_reaching(state) for state in MissionState}

        # Assert
        self.assertEqual(expected, observed)

    def test_the_named_event_actually_reaches_that_state_in_the_table(self) -> None:
        """Derived from the table, so a retargeted row cannot leave this answer stale."""
        # Arrange
        reachable = tuple(state for state in MissionState if event_reaching(state) is not None)

        # Act
        reached = {
            state: {
                transition(source, event)
                for source in MissionState
                for event in (event_reaching(state),)
                if event is not None and LEGAL_TRANSITIONS.get((source, event)) is not None
            }
            for state in reachable
        }

        # Assert
        self.assertEqual({state: {state} for state in reachable}, reached)

    def test_only_the_initial_state_has_no_inbound_event(self) -> None:
        # Arrange
        expected = {INITIAL_STATE}

        # Act
        unreachable = {state for state in MissionState if event_reaching(state) is None}

        # Assert
        self.assertEqual(expected, unreachable)


class MissionErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_pair(self) -> None:
        # Arrange
        pair = (MissionState.PLANNED, MissionEvent.COMPLETE)

        # Act
        message = str(MissionError(MissionRefusal.TRANSITION, pair))

        # Assert
        self.assertEqual(
            "the mission lifecycle has no such transition: "
            "(<MissionState.PLANNED: 'planned'>, <MissionEvent.COMPLETE: 'complete'>)",
            message,
        )


if __name__ == "__main__":
    unittest.main()
