"""The mission lifecycle: one deny-by-default transition table over six states.

The states, the events, the seven legal pairs, and the terminal set are the decision in
``docs/adr/0072-mission-lifecycle-states.md``. Reset is not an edge: a reset ends the current
mission and creates a new one, so no mission ever leaves a terminal state. ``ESCALATED``
records that an ``escalate-rescue`` command was published and authorizes nothing -- the
approval record and the command-authority table remain the only things that decide that, and
this machine never reads an approval. This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class MissionState(Enum):
    """Where a mission stands, from planned through to one of its three endings."""

    PLANNED = "planned"
    SEARCHING = "searching"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    ABORTED = "aborted"


class MissionEvent(Enum):
    """What moves a mission between states."""

    START = "start"
    ESCALATE = "escalate"
    EXHAUST = "exhaust"
    COMPLETE = "complete"
    ABORT = "abort"


class MissionRefusal(Enum):
    """Why the lifecycle refuses an operation."""

    TRANSITION = "the mission lifecycle has no such transition"


class MissionError(DomainError):
    """A refused mission operation, carrying the refusal as structured data."""


_TRANSITIONS: Final[Mapping[tuple[MissionState, MissionEvent], MissionState]] = {
    (MissionState.PLANNED, MissionEvent.START): MissionState.SEARCHING,
    (MissionState.SEARCHING, MissionEvent.ESCALATE): MissionState.ESCALATED,
    (MissionState.SEARCHING, MissionEvent.EXHAUST): MissionState.EXHAUSTED,
    (MissionState.ESCALATED, MissionEvent.COMPLETE): MissionState.COMPLETED,
    (MissionState.PLANNED, MissionEvent.ABORT): MissionState.ABORTED,
    (MissionState.SEARCHING, MissionEvent.ABORT): MissionState.ABORTED,
    (MissionState.ESCALATED, MissionEvent.ABORT): MissionState.ABORTED,
}
"""The seven legal pairs; every other pair is refused, and COMPLETED follows only ESCALATED."""

INITIAL_STATE: Final = MissionState.PLANNED
"""Every mission starts planned, before its first sector is assigned."""

TERMINAL_STATES: Final[frozenset[MissionState]] = frozenset(MissionState) - {
    source for source, _ in _TRANSITIONS
}
"""The three endings, derived from the table so terminality is not a second home."""

_EVENT_REACHING: Final[Mapping[MissionState, MissionEvent]] = {
    target: event for (_source, event), target in _TRANSITIONS.items()
}
"""The one event that reaches each reachable state, derived so it cannot go stale.

Every target in the table is reached by exactly one event -- `ABORTED` has three source
states but only `ABORT` reaches it -- so this inversion loses nothing.
"""


def transition(state: MissionState, event: MissionEvent) -> MissionState:
    """Return the state an event leads to, refusing every pair outside the table.

    Args:
        state: The current state.
        event: The event applied to it.

    Returns:
        The target state.

    Raises:
        MissionError: With ``TRANSITION`` when the lifecycle has no such edge, which
            includes every event applied to an ending.
    """
    target = _TRANSITIONS.get((state, event))
    if target is None:
        raise MissionError(MissionRefusal.TRANSITION, (state, event))
    return target


def event_reaching(state: MissionState) -> MissionEvent | None:
    """Return the one event that reaches ``state``, or ``None`` for the initial state.

    A producer that has observed a mission in some state needs the event that would
    explain it before it can ask whether the table admits that edge from where the mission
    durably stands. Only :data:`INITIAL_STATE` has no inbound event: a mission is planned
    by being created, not by an event.

    Args:
        state: The state a producer or consumer has observed.

    Returns:
        The event whose every legal application yields ``state``, or ``None``.
    """
    return _EVENT_REACHING.get(state)


def is_terminal(state: MissionState) -> bool:
    """Return whether ``state`` is an ending a mission cannot leave."""
    return state in TERMINAL_STATES
