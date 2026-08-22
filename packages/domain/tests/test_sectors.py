"""The ADR-0073 sector lifecycle as a pure, deny-by-default transition table.

All twenty state and event pairs are asserted against the documented table. Unlike the
mission machine this one is cyclic -- a sector may be imperilled and reassigned as often as
the fleet loses drones over it -- so the tests assert repetition explicitly rather than
progress. Two of them drive the connectivity machine and apply the edge-to-event mapping the
record assigns to the adapter, so the coupling the record claims is evidenced rather than
asserted in prose alone.
"""

from __future__ import annotations

import unittest
from itertools import product

import pytest

from aerial_rescue_domain.connectivity import (
    INITIAL_STATUS,
    ConnectivityState,
    ConnectivityThresholds,
    heartbeat_missed,
    heartbeat_received,
)
from aerial_rescue_domain.sectors import (
    INITIAL_STATE,
    TERMINAL_STATES,
    SectorError,
    SectorEvent,
    SectorRefusal,
    SectorState,
    is_terminal,
    transition,
)

LEGAL_TRANSITIONS = {
    (SectorState.UNASSIGNED, SectorEvent.ASSIGN): SectorState.ASSIGNED,
    (SectorState.ASSIGNED, SectorEvent.IMPERIL): SectorState.AT_RISK,
    (SectorState.AT_RISK, SectorEvent.REASSIGN): SectorState.ASSIGNED,
    (SectorState.AT_RISK, SectorEvent.RECOVER): SectorState.ASSIGNED,
    (SectorState.ASSIGNED, SectorEvent.SWEEP): SectorState.SEARCHED,
}
ALL_PAIRS = tuple(product(SectorState, SectorEvent))
THRESHOLDS = ConnectivityThresholds(3, 6, 2)


def _transition_outcome_of(state: SectorState, event: SectorEvent) -> object:
    """Return the target state, or the refusal and value when the pair is refused."""
    try:
        return transition(state, event)
    except SectorError as error:
        return (error.refusal, error.value)


def _after(*events: SectorEvent) -> SectorState:
    """Fold ``events`` over a sector that begins in the initial state."""
    state = INITIAL_STATE
    for event in events:
        state = transition(state, event)
    return state


def _sector_event_for(before: ConnectivityState, after: ConnectivityState) -> SectorEvent | None:
    """Return the sector event ADR-0073 assigns to a connectivity edge, or ``None``.

    This is the mapping the record gives the Tier 2 adapter, reproduced here so the coupling
    it claims is exercised. The domain modules stay independent of each other.
    """
    offline = ConnectivityState.OFFLINE
    if after is offline and before is not offline:
        return SectorEvent.IMPERIL
    if before is offline and after is not offline:
        return SectorEvent.RECOVER
    return None


def _sector_after_link(script: str, state: SectorState) -> SectorState:
    """Drive the link through ``script`` and apply the sector event of each edge to ``state``."""
    status = INITIAL_STATUS
    for interval in script:
        step = heartbeat_received if interval == "h" else heartbeat_missed
        moved = step(status, THRESHOLDS)
        event = _sector_event_for(status.state, moved.state)
        if event is not None:
            state = transition(state, event)
        status = moved
    return state


class TransitionTests(unittest.TestCase):
    def test_the_twenty_state_event_pairs_resolve_to_the_documented_table(self) -> None:
        # Arrange
        expected = tuple(
            LEGAL_TRANSITIONS.get(pair, (SectorRefusal.TRANSITION, pair)) for pair in ALL_PAIRS
        )

        # Act
        outcomes = tuple(_transition_outcome_of(state, event) for state, event in ALL_PAIRS)

        # Assert
        self.assertEqual(expected, outcomes)

    def test_searched_is_reachable_only_from_an_assigned_sector(self) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        sweeping = {pair for pair in pairs if _transition_outcome_of(*pair) is SectorState.SEARCHED}

        # Assert
        self.assertEqual({(SectorState.ASSIGNED, SectorEvent.SWEEP)}, sweeping)

    def test_a_sector_at_risk_cannot_be_swept(self) -> None:
        # Arrange
        pair = (SectorState.AT_RISK, SectorEvent.SWEEP)

        # Act
        with pytest.raises(SectorError) as captured:
            transition(*pair)

        # Assert
        self.assertEqual(
            (SectorRefusal.TRANSITION, pair), (captured.value.refusal, captured.value.value)
        )

    def test_the_reassignment_scenario_folds_from_unassigned_to_searched(self) -> None:
        # Arrange
        scenario = (
            SectorEvent.ASSIGN,
            SectorEvent.IMPERIL,
            SectorEvent.REASSIGN,
            SectorEvent.SWEEP,
        )

        # Act
        state = _after(*scenario)

        # Assert
        self.assertIs(SectorState.SEARCHED, state)

    def test_a_returning_drone_recovers_its_own_sector_without_a_reassignment(self) -> None:
        # Arrange
        scenario = (SectorEvent.ASSIGN, SectorEvent.IMPERIL, SectorEvent.RECOVER)

        # Act
        state = _after(*scenario)

        # Assert
        self.assertIs(SectorState.ASSIGNED, state)

    def test_a_sector_can_be_imperilled_and_reassigned_repeatedly(self) -> None:
        # Arrange
        losses = (SectorEvent.IMPERIL, SectorEvent.REASSIGN) * 4

        # Act
        state = _after(SectorEvent.ASSIGN, *losses)

        # Assert
        self.assertIs(SectorState.ASSIGNED, state)

    def test_no_event_returns_a_sector_to_unassigned(self) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        returning = {
            pair for pair in pairs if _transition_outcome_of(*pair) is SectorState.UNASSIGNED
        }

        # Assert
        self.assertEqual(set(), returning)


class TerminalTests(unittest.TestCase):
    def test_a_sector_begins_unassigned(self) -> None:
        # Arrange
        expected = SectorState.UNASSIGNED

        # Act
        initial = INITIAL_STATE

        # Assert
        self.assertIs(expected, initial)

    def test_a_swept_sector_is_the_only_terminal_state(self) -> None:
        # Arrange
        expected = frozenset({SectorState.SEARCHED})

        # Act
        terminal = TERMINAL_STATES

        # Assert
        self.assertEqual(expected, terminal)

    def test_no_event_moves_a_swept_sector(self) -> None:
        # Arrange
        pairs = tuple((SectorState.SEARCHED, event) for event in SectorEvent)

        # Act
        outcomes = tuple(_transition_outcome_of(state, event) for state, event in pairs)

        # Assert
        self.assertEqual(tuple((SectorRefusal.TRANSITION, pair) for pair in pairs), outcomes)

    def test_every_unswept_state_reports_not_terminal(self) -> None:
        # Arrange
        states = (SectorState.UNASSIGNED, SectorState.ASSIGNED, SectorState.AT_RISK)

        # Act
        verdicts = tuple(is_terminal(state) for state in states)

        # Assert
        self.assertEqual((False, False, False), verdicts)

    def test_the_terminal_set_is_exactly_the_states_with_no_outbound_transition(self) -> None:
        # Arrange
        sources = {state for state, _ in LEGAL_TRANSITIONS}

        # Act
        without_outbound = {state for state in SectorState if state not in sources}

        # Assert
        self.assertEqual(without_outbound, set(TERMINAL_STATES))


class ConnectivityEdgeTests(unittest.TestCase):
    def test_a_drone_lost_and_returned_imperils_then_recovers_its_sector(self) -> None:
        # Arrange
        script = "m" * THRESHOLDS.misses_to_offline + "h" * THRESHOLDS.heartbeats_to_recover

        # Act
        state = _sector_after_link(script, SectorState.ASSIGNED)

        # Assert
        self.assertIs(SectorState.ASSIGNED, state)

    def test_a_lost_drone_leaves_its_sector_at_risk_until_it_returns(self) -> None:
        # Arrange
        script = "m" * THRESHOLDS.misses_to_offline

        # Act
        state = _sector_after_link(script, SectorState.ASSIGNED)

        # Assert
        self.assertIs(SectorState.AT_RISK, state)

    def test_a_degraded_drone_does_not_imperil_its_sector(self) -> None:
        # Arrange
        script = "m" * THRESHOLDS.misses_to_degraded

        # Act
        state = _sector_after_link(script, SectorState.ASSIGNED)

        # Assert
        self.assertIs(SectorState.ASSIGNED, state)


class SectorErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_pair(self) -> None:
        # Arrange
        pair = (SectorState.UNASSIGNED, SectorEvent.SWEEP)

        # Act
        message = str(SectorError(SectorRefusal.TRANSITION, pair))

        # Assert
        self.assertEqual(
            "the sector lifecycle has no such transition: "
            "(<SectorState.UNASSIGNED: 'unassigned'>, <SectorEvent.SWEEP: 'sweep'>)",
            message,
        )


if __name__ == "__main__":
    unittest.main()
