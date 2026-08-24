"""The outbox publication lifecycle: one deny-by-default transition table over three states.

The states, the events, the four legal pairs, and the terminal set are the decision in
``docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md``.

Two distinctions this table exists to hold. **A refused publication is not an event**: the
broker answering "no" is evidence that it did not take the command, so the record is still
staged and still recoverable for bounded retry under its original command identifier. Only an
*ambiguous* outcome moves it, because only that leaves the question unanswered. And **a
confirmation means the broker accepted the bytes** -- not that a drone received the command,
acknowledged it, or completed it. Those are ``commands.py``'s states and are persisted apart;
letting one stand for the other would report a command as delivered when it was merely spooled.

``RECONCILIATION_NEEDED`` is never promoted to ``CONFIRMED`` by a timeout or a guess. The only
way in is a confirmation, which is what makes the confirmed state a durable fact rather than an
inference. This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class OutboxState(Enum):
    """Where one staged command stands with respect to the broker, and nothing more."""

    STAGED = "staged"
    RECONCILIATION_NEEDED = "reconciliation needed"
    CONFIRMED = "confirmed"


class OutboxEvent(Enum):
    """What the broker adapter reports about a publication attempt."""

    CONFIRM = "confirm"
    AMBIGUOUS = "ambiguous"


class OutboxRefusal(Enum):
    """Why the lifecycle refuses an operation."""

    TRANSITION = "the outbox publication lifecycle has no such transition"


class OutboxError(DomainError):
    """A refused outbox operation, carrying the refusal as structured data."""


_TRANSITIONS: Final[Mapping[tuple[OutboxState, OutboxEvent], OutboxState]] = {
    (OutboxState.STAGED, OutboxEvent.CONFIRM): OutboxState.CONFIRMED,
    (OutboxState.STAGED, OutboxEvent.AMBIGUOUS): OutboxState.RECONCILIATION_NEEDED,
    (OutboxState.RECONCILIATION_NEEDED, OutboxEvent.CONFIRM): OutboxState.CONFIRMED,
    (OutboxState.RECONCILIATION_NEEDED, OutboxEvent.AMBIGUOUS): (OutboxState.RECONCILIATION_NEEDED),
}
"""The four legal pairs; every other pair is refused, and CONFIRMED is reached only by CONFIRM."""

INITIAL_STATE: Final = OutboxState.STAGED
"""What the transaction wrote. A record exists because a command was staged, never before."""

TERMINAL_STATES: Final[frozenset[OutboxState]] = frozenset(OutboxState) - {
    source for source, _ in _TRANSITIONS
}
"""The one ending, derived from the table so terminality is not a second home."""


def transition(state: OutboxState, event: OutboxEvent) -> OutboxState:
    """Return the state an event leads to, refusing every pair outside the table.

    Args:
        state: The current state.
        event: What the broker adapter reported.

    Returns:
        The target state.

    Raises:
        OutboxError: With ``TRANSITION`` when the lifecycle has no such edge, which includes
            every event applied to a confirmed record.
    """
    target = _TRANSITIONS.get((state, event))
    if target is None:
        raise OutboxError(OutboxRefusal.TRANSITION, (state, event))
    return target


def is_terminal(state: OutboxState) -> bool:
    """Return whether ``state`` is an ending a staged command cannot leave."""
    return state in TERMINAL_STATES
