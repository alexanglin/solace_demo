"""The command dispatch lifecycle: one table over six states plus one counted bound.

The states, the events, the five legal pairs, and the send budget are the decision in
``docs/adr/0074-command-dispatch-lifecycle.md``. The budget has no default and its value is an
open row in ``docs/operating-parameters.md``, so a composition root supplies it. The domain
counts sends; the adapter owns the acknowledgement timer, the backoff, and the jitter, and
applies one event per decision. This module reads no clock.

This is not the command-authority table, which closes ``commandType`` and decides who may
publish each kind, and it is not the idempotency rule that returns a prior result for a known
command identifier. Both live in their own modules. This module is pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class CommandState(Enum):
    """Where one dispatched command stands, from accepted to one of its three endings."""

    ACCEPTED = "accepted"
    IN_FLIGHT = "in-flight"
    ACKNOWLEDGED = "acknowledged"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


class CommandEvent(Enum):
    """What moves a command between states."""

    SEND = "send"
    TIME_OUT = "time-out"
    ACKNOWLEDGE = "acknowledge"
    SUCCEED = "succeed"
    FAIL = "fail"


class CommandRefusal(Enum):
    """Why the lifecycle refuses an operation."""

    TRANSITION = "the command dispatch lifecycle has no such transition"
    SEND_BUDGET = "a command send budget must allow at least one send"


class CommandError(DomainError):
    """A refused command operation, carrying the refusal as structured data."""


_MINIMUM_SENDS: Final = 1


@dataclass(frozen=True)
class SendBudget:
    """How many times the gateway may put one command on the wire.

    The count is injected and has no default; its home is ``docs/operating-parameters.md``,
    where it is still an open row.
    """

    max_sends: int

    def __post_init__(self) -> None:
        """Refuse a budget under which the command could never be sent."""
        if self.max_sends < _MINIMUM_SENDS:
            raise CommandError(CommandRefusal.SEND_BUDGET, self.max_sends)


@dataclass(frozen=True)
class CommandProgress:
    """One command's state together with the number of times it has been on the wire."""

    state: CommandState
    sends: int


_TRANSITIONS: Final[Mapping[tuple[CommandState, CommandEvent], CommandState]] = {
    (CommandState.ACCEPTED, CommandEvent.SEND): CommandState.IN_FLIGHT,
    (CommandState.IN_FLIGHT, CommandEvent.TIME_OUT): CommandState.ACCEPTED,
    (CommandState.IN_FLIGHT, CommandEvent.ACKNOWLEDGE): CommandState.ACKNOWLEDGED,
    (CommandState.ACKNOWLEDGED, CommandEvent.SUCCEED): CommandState.SUCCEEDED,
    (CommandState.ACKNOWLEDGED, CommandEvent.FAIL): CommandState.FAILED,
}
"""The five legal pairs; ABANDONED is the one state no row targets, because the budget does."""

INITIAL_PROGRESS: Final = CommandProgress(CommandState.ACCEPTED, 0)
"""Every command starts accepted and unsent, validated and persisted by the gateway."""

TERMINAL_STATES: Final[frozenset[CommandState]] = frozenset(CommandState) - {
    source for source, _ in _TRANSITIONS
}
"""The three endings, derived from the table so terminality is not a second home."""


def advance(progress: CommandProgress, event: CommandEvent, budget: SendBudget) -> CommandProgress:
    """Return the progress an event leads to, refusing every pair outside the table.

    Args:
        progress: The state and send count before the event.
        event: The event applied to them.
        budget: The injected send budget, consulted only by a timeout.

    Returns:
        The progress after the event. A send increments the count; a timeout at the budget
        abandons the command and every other event leaves the count alone.

    Raises:
        CommandError: With ``TRANSITION`` when the lifecycle has no such edge, which includes
            every event applied to an ending and failing a command before it is acknowledged.
    """
    target = _TRANSITIONS.get((progress.state, event))
    if target is None:
        raise CommandError(CommandRefusal.TRANSITION, (progress.state, event))
    if event is CommandEvent.SEND:
        return CommandProgress(target, progress.sends + 1)
    if event is CommandEvent.TIME_OUT and progress.sends >= budget.max_sends:
        return CommandProgress(CommandState.ABANDONED, progress.sends)
    return CommandProgress(target, progress.sends)


def is_terminal(state: CommandState) -> bool:
    """Return whether ``state`` is an ending a command cannot leave."""
    return state in TERMINAL_STATES
