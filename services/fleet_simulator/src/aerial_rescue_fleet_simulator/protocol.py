"""The drone's half of the command dispatch protocol, folded through the real machine.

``docs/adr/0074-command-dispatch-lifecycle.md`` models the **dispatcher's** view of a
command: ``SEND`` is the gateway putting it on the wire, ``TIME_OUT`` is its acknowledgement
timer elapsing, and ``ABANDONED`` is what the send budget decides. The broker's grant tables
make all three unobservable here -- this role holds no publish grant on the drone command
family and owns no timer -- so this module applies only the three events a drone can cause
and refuses the two it cannot.

It folds ``aerial_rescue_domain.commands.advance`` rather than checking the rules itself.
That is what preserves the protocol constraint ADR-0074 imposes and this member's guide
repeats: a simulated drone that rejects a received command acknowledges it before reporting
failure, because the table has no ``IN_FLIGHT``-to-``FAILED`` edge. The refusal a caller sees
for that is the domain's own, not a second copy of the table.

**On the seed.** ADR-0074 gives a drone no entry point: the only source of ``IN_FLIGHT`` is
``(ACCEPTED, SEND)``, and ``ACCEPTED`` means the gateway validated and persisted the command,
which this process cannot claim. :func:`received` therefore derives the arrival state through
the domain's own table rather than constructing a state and a count by hand, and the count it
yields means "on the wire at least once, as this drone can see". It is not the gateway's
tally, which no envelope member carries. Retries and redeliveries never apply a second
``SEND``, so the count never grows and never pretends to.

**On the send budget.** ``advance`` reads the budget on one line, guarding ``TIME_OUT``
alone, so every edge this module applies is blind to it. The budget is still injected with no
default, because the parameter is positional and a caller should supply the real one; a test
folds every drone-side sequence under the smallest and largest legal budgets and asserts they
agree, which turns that reading of the machine into a proven claim.

This module is pure.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from aerial_rescue_domain.commands import (
    INITIAL_PROGRESS,
    CommandEvent,
    CommandProgress,
    SendBudget,
    advance,
)

from aerial_rescue_fleet_simulator import FleetSimulatorError

DRONE_EVENTS: Final = frozenset({CommandEvent.ACKNOWLEDGE, CommandEvent.SUCCEED, CommandEvent.FAIL})
"""The three events a drone causes. ``SEND`` and ``TIME_OUT`` belong to the dispatcher."""


class ProtocolRefusal(Enum):
    """Why an event is not one this drone may apply."""

    NOT_A_DRONE_EVENT = "event belongs to the dispatching gateway rather than to a drone"


class ProtocolError(FleetSimulatorError):
    """An event this side of the protocol refuses, carrying the refusal as structured data."""


def received(budget: SendBudget) -> CommandProgress:
    """Return the progress of a command that has reached this drone off its own queue.

    Args:
        budget: The send budget the composition root supplies. No edge here reads it.

    Returns:
        The command in flight, having been on the wire once as this drone can see.
    """
    return advance(INITIAL_PROGRESS, CommandEvent.SEND, budget)


def apply(progress: CommandProgress, event: CommandEvent, budget: SendBudget) -> CommandProgress:
    """Apply one drone-caused event, refusing one only the dispatching gateway may apply.

    Args:
        progress: Where the command has reached.
        event: What this drone did about it.
        budget: The send budget the composition root supplies. No edge here reads it.

    Returns:
        The command's new progress.

    Raises:
        ProtocolError: With ``NOT_A_DRONE_EVENT`` for ``SEND`` or ``TIME_OUT``.
        CommandError: From the domain, for an event the transition table refuses at that
            state -- including the ``IN_FLIGHT``-to-``FAILED`` shortcut that does not exist.
    """
    if event not in DRONE_EVENTS:
        raise ProtocolError(ProtocolRefusal.NOT_A_DRONE_EVENT, event)
    return advance(progress, event, budget)
