"""The ADR-0075 evidence lifecycle: seven states, and two endings that are not the same.

All forty-nine state and event pairs are asserted against the documented table. The tests that
matter most are the ones separating the three terminals: an agent that declines abstains, an
assertion the system refuses is rejected, and only an admitted item contributes. An abstention
is asserted to be neither a rejection nor a contribution, because the plan requires it to read
as a refusal to assert rather than as a weak result.
"""

from __future__ import annotations

import unittest
from itertools import product

import pytest

from aerial_rescue_domain.evidence import (
    INITIAL_STATE,
    TERMINAL_STATES,
    EvidenceError,
    EvidenceEvent,
    EvidenceRefusal,
    EvidenceState,
    is_terminal,
    transition,
)

LEGAL_TRANSITIONS = {
    (EvidenceState.REQUESTED, EvidenceEvent.OBSERVE): EvidenceState.OBSERVED,
    (EvidenceState.REQUESTED, EvidenceEvent.ABSTAIN): EvidenceState.ABSTAINED,
    (EvidenceState.OBSERVED, EvidenceEvent.VALIDATE): EvidenceState.VALIDATED,
    (EvidenceState.OBSERVED, EvidenceEvent.REJECT): EvidenceState.REJECTED,
    (EvidenceState.VALIDATED, EvidenceEvent.ADMIT): EvidenceState.CONTRIBUTING,
    (EvidenceState.VALIDATED, EvidenceEvent.REFER): EvidenceState.MANUAL_REVIEW,
    (EvidenceState.MANUAL_REVIEW, EvidenceEvent.ADMIT): EvidenceState.CONTRIBUTING,
    (EvidenceState.MANUAL_REVIEW, EvidenceEvent.DISMISS): EvidenceState.REJECTED,
}
ALL_PAIRS = tuple(product(EvidenceState, EvidenceEvent))
ENDINGS = (EvidenceState.CONTRIBUTING, EvidenceState.ABSTAINED, EvidenceState.REJECTED)
LIVE = (
    EvidenceState.REQUESTED,
    EvidenceState.OBSERVED,
    EvidenceState.VALIDATED,
    EvidenceState.MANUAL_REVIEW,
)
ADMITTED_AUTOMATICALLY = (
    EvidenceEvent.OBSERVE,
    EvidenceEvent.VALIDATE,
    EvidenceEvent.ADMIT,
)
ADMITTED_BY_A_HUMAN = (
    EvidenceEvent.OBSERVE,
    EvidenceEvent.VALIDATE,
    EvidenceEvent.REFER,
    EvidenceEvent.ADMIT,
)


def _outcome_of(state: EvidenceState, event: EvidenceEvent) -> object:
    """Return the target state, or the refusal and value when the pair is refused."""
    try:
        return transition(state, event)
    except EvidenceError as error:
        return (error.refusal, error.value)


def _resolve(*events: EvidenceEvent) -> EvidenceState:
    """Fold ``events`` over an item that has just been requested of an edge agent."""
    state = INITIAL_STATE
    for event in events:
        state = transition(state, event)
    return state


class TransitionTests(unittest.TestCase):
    def test_the_forty_nine_state_event_pairs_resolve_to_the_documented_table(self) -> None:
        # Arrange
        expected = tuple(
            LEGAL_TRANSITIONS.get(pair, (EvidenceRefusal.TRANSITION, pair)) for pair in ALL_PAIRS
        )

        # Act
        outcomes = tuple(_outcome_of(state, event) for state, event in ALL_PAIRS)

        # Assert
        self.assertEqual(expected, outcomes)

    def test_contributing_is_reachable_only_by_admitting_a_validated_or_reviewed_item(
        self,
    ) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        admitting = {pair for pair in pairs if _outcome_of(*pair) is EvidenceState.CONTRIBUTING}

        # Assert
        self.assertEqual(
            {
                (EvidenceState.VALIDATED, EvidenceEvent.ADMIT),
                (EvidenceState.MANUAL_REVIEW, EvidenceEvent.ADMIT),
            },
            admitting,
        )

    def test_abstention_is_reachable_only_from_a_request_that_produced_no_observation(
        self,
    ) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        abstaining = {pair for pair in pairs if _outcome_of(*pair) is EvidenceState.ABSTAINED}

        # Assert
        self.assertEqual({(EvidenceState.REQUESTED, EvidenceEvent.ABSTAIN)}, abstaining)

    def test_an_abstention_is_neither_a_rejection_nor_a_contribution(self) -> None:
        # Arrange
        abstained = _resolve(EvidenceEvent.ABSTAIN)

        # Act
        others = (
            _resolve(*ADMITTED_AUTOMATICALLY),
            _resolve(EvidenceEvent.OBSERVE, EvidenceEvent.REJECT),
        )

        # Assert
        self.assertNotIn(abstained, others)

    def test_an_agent_that_asserts_something_valid_ends_up_contributing(self) -> None:
        # Arrange
        scenario = ADMITTED_AUTOMATICALLY

        # Act
        state = _resolve(*scenario)

        # Assert
        self.assertIs(EvidenceState.CONTRIBUTING, state)

    def test_a_human_who_admits_a_referred_item_reaches_the_same_state(self) -> None:
        # Arrange
        automatic = _resolve(*ADMITTED_AUTOMATICALLY)

        # Act
        reviewed = _resolve(*ADMITTED_BY_A_HUMAN)

        # Assert
        self.assertIs(automatic, reviewed)

    def test_a_human_who_dismisses_a_referred_item_rejects_it(self) -> None:
        # Arrange
        scenario = (
            EvidenceEvent.OBSERVE,
            EvidenceEvent.VALIDATE,
            EvidenceEvent.REFER,
            EvidenceEvent.DISMISS,
        )

        # Act
        state = _resolve(*scenario)

        # Assert
        self.assertIs(EvidenceState.REJECTED, state)

    def test_an_assertion_that_fails_validation_is_rejected(self) -> None:
        # Arrange
        scenario = (EvidenceEvent.OBSERVE, EvidenceEvent.REJECT)

        # Act
        state = _resolve(*scenario)

        # Assert
        self.assertIs(EvidenceState.REJECTED, state)

    def test_nothing_withdraws_a_contributing_item(self) -> None:
        # Arrange
        pairs = tuple((EvidenceState.CONTRIBUTING, event) for event in EvidenceEvent)

        # Act
        outcomes = tuple(_outcome_of(state, event) for state, event in pairs)

        # Assert
        self.assertEqual(tuple((EvidenceRefusal.TRANSITION, pair) for pair in pairs), outcomes)

    def test_a_refused_transition_names_the_state_and_the_event(self) -> None:
        # Arrange
        pair = (EvidenceState.REQUESTED, EvidenceEvent.ADMIT)

        # Act
        with pytest.raises(EvidenceError) as captured:
            transition(*pair)

        # Assert
        self.assertEqual(
            (EvidenceRefusal.TRANSITION, pair), (captured.value.refusal, captured.value.value)
        )


class TerminalTests(unittest.TestCase):
    def test_an_evidence_item_begins_requested(self) -> None:
        # Arrange
        expected = EvidenceState.REQUESTED

        # Act
        initial = INITIAL_STATE

        # Assert
        self.assertIs(expected, initial)

    def test_the_three_endings_are_the_terminal_set(self) -> None:
        # Arrange
        expected = frozenset(ENDINGS)

        # Act
        terminal = TERMINAL_STATES

        # Assert
        self.assertEqual(expected, terminal)

    def test_no_event_moves_an_item_out_of_an_ending(self) -> None:
        # Arrange
        pairs = tuple(product(ENDINGS, EvidenceEvent))

        # Act
        outcomes = tuple(_outcome_of(state, event) for state, event in pairs)

        # Assert
        self.assertEqual(tuple((EvidenceRefusal.TRANSITION, pair) for pair in pairs), outcomes)

    def test_every_live_state_reports_not_terminal(self) -> None:
        # Arrange
        states = LIVE

        # Act
        verdicts = tuple(is_terminal(state) for state in states)

        # Assert
        self.assertEqual((False, False, False, False), verdicts)

    def test_the_terminal_set_is_exactly_the_states_with_no_outbound_transition(self) -> None:
        # Arrange
        sources = {state for state, _ in LEGAL_TRANSITIONS}

        # Act
        without_outbound = {state for state in EvidenceState if state not in sources}

        # Assert
        self.assertEqual(without_outbound, set(TERMINAL_STATES))


class EvidenceErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_pair(self) -> None:
        # Arrange
        pair = (EvidenceState.ABSTAINED, EvidenceEvent.ADMIT)

        # Act
        message = str(EvidenceError(EvidenceRefusal.TRANSITION, pair))

        # Assert
        self.assertEqual(
            "the evidence lifecycle has no such transition: "
            "(<EvidenceState.ABSTAINED: 'abstained'>, <EvidenceEvent.ADMIT: 'admit'>)",
            message,
        )


if __name__ == "__main__":
    unittest.main()
