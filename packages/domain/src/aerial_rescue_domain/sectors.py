"""The sector lifecycle: one deny-by-default transition table over four states.

The states, the events, the five legal pairs, and the terminal state are the decision in
``docs/adr/0073-sector-lifecycle-states.md``. Unlike the mission machine this one is cyclic: a
sector may be imperilled and reassigned as often as the fleet loses drones over it, and only a
swept sector absorbs. ``IMPERIL`` and ``RECOVER`` are applied by the adapter when the holding
drone's connectivity machine enters and leaves ``OFFLINE``; this module reads no connectivity
status, carries no geometry, and names no drone. This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class SectorState(Enum):
    """Where one sector of the search area stands, from unassigned to swept."""

    UNASSIGNED = "unassigned"
    ASSIGNED = "assigned"
    AT_RISK = "at-risk"
    SEARCHED = "searched"


class SectorEvent(Enum):
    """What moves a sector between states."""

    ASSIGN = "assign"
    IMPERIL = "imperil"
    REASSIGN = "reassign"
    RECOVER = "recover"
    SWEEP = "sweep"


class SectorRefusal(Enum):
    """Why the lifecycle refuses an operation."""

    TRANSITION = "the sector lifecycle has no such transition"


class SectorError(DomainError):
    """A refused sector operation, carrying the refusal as structured data."""


_TRANSITIONS: Final[Mapping[tuple[SectorState, SectorEvent], SectorState]] = {
    (SectorState.UNASSIGNED, SectorEvent.ASSIGN): SectorState.ASSIGNED,
    (SectorState.ASSIGNED, SectorEvent.IMPERIL): SectorState.AT_RISK,
    (SectorState.AT_RISK, SectorEvent.REASSIGN): SectorState.ASSIGNED,
    (SectorState.AT_RISK, SectorEvent.RECOVER): SectorState.ASSIGNED,
    (SectorState.ASSIGNED, SectorEvent.SWEEP): SectorState.SEARCHED,
}
"""The five legal pairs; a sector at risk cannot be swept, and nothing returns it unassigned."""

INITIAL_STATE: Final = SectorState.UNASSIGNED
"""Every sector starts unassigned, before any drone holds it."""

TERMINAL_STATES: Final[frozenset[SectorState]] = frozenset(SectorState) - {
    source for source, _ in _TRANSITIONS
}
"""The one ending, derived from the table so terminality is not a second home."""


def transition(state: SectorState, event: SectorEvent) -> SectorState:
    """Return the state an event leads to, refusing every pair outside the table.

    Args:
        state: The current state.
        event: The event applied to it.

    Returns:
        The target state.

    Raises:
        SectorError: With ``TRANSITION`` when the lifecycle has no such edge, which includes
            sweeping a sector whose holding drone is offline.
    """
    target = _TRANSITIONS.get((state, event))
    if target is None:
        raise SectorError(SectorRefusal.TRANSITION, (state, event))
    return target


def is_terminal(state: SectorState) -> bool:
    """Return whether ``state`` is an ending a sector cannot leave."""
    return state in TERMINAL_STATES
