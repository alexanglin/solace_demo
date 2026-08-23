"""The retry schedule one dispatched command follows, and the four values it is made of.

``docs/adr/0081-give-command-dispatch-one-interval.md`` decides the shape: command dispatch
has one interval rather than three, so the acknowledgement timeout is also the backoff base
and the jitter bound, and the jitter is added and never subtracted. The unjittered schedule
is therefore an exact floor on the instant a command is abandoned, which is what lets the
derivation in ``docs/operating-parameters.md`` hold for every draw rather than on average.

The four values are derived from the service-level rows rather than measured, and this module
is where they live so that no service-local constant owns half of the arithmetic.
:func:`abandon_instant_milliseconds` is the instrument the operating-parameters row names: it
folds the four values, so a changed value fails a test instead of quietly disagreeing with the
table.

``packages/domain`` counts sends and reads no clock, which is why every duration here belongs
to this service. This module reads no clock and consumes no random source either -- the jitter
draw arrives as an argument, in the same way the record's clock and identifier arrive as a
stamp. This module is pure.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Final

from aerial_rescue_command_gateway import CommandGatewayError

MAX_COMMAND_SENDS: Final = 5
"""How many times the gateway may put one command on the wire; the last timeout abandons it."""

ACKNOWLEDGEMENT_TIMEOUT_SECONDS: Final = 6
"""The window from one publication to the arriving command-result."""

BACKOFF_BASE_SECONDS: Final = ACKNOWLEDGEMENT_TIMEOUT_SECONDS
"""The first wait between sends, doubled at each later timeout. One interval, not three."""

JITTER_BOUND_SECONDS: Final = BACKOFF_BASE_SECONDS
"""The exclusive upper bound on a draw, which is the same interval again."""

MILLISECONDS_PER_SECOND: Final = 1000
JITTER_BOUND_MILLISECONDS: Final = JITTER_BOUND_SECONDS * MILLISECONDS_PER_SECOND

MINIMUM_JITTER_MILLISECONDS: Final = 0
FIRST_TIMEOUT: Final = 1
BACKOFF_FACTOR: Final = 2
REQUIRED_DRAWS: Final = MAX_COMMAND_SENDS - FIRST_TIMEOUT
"""One draw per backoff, and the backoffs separate the sends, so there is one fewer."""


class ScheduleRefusal(Enum):
    """Why a schedule cannot be computed."""

    TIMEOUT_COUNT = "no wait exists before the first send or after the budget's last timeout"
    JITTER_RANGE = "jitter draw outside zero up to the exclusive jitter bound"
    DRAW_COUNT = "the envelope needs exactly one jitter draw per backoff"


class ScheduleError(CommandGatewayError):
    """A schedule the derived envelope refuses, carrying the refusal as structured data."""


def _checked_draw(value: int) -> int:
    """Return a jitter draw, refusing one no random source inside the bound could produce.

    A draw is refused rather than clamped: clamping would hide a broken random source behind
    a schedule that still looked correct.
    """
    if not MINIMUM_JITTER_MILLISECONDS <= value < JITTER_BOUND_MILLISECONDS:
        raise ScheduleError(ScheduleRefusal.JITTER_RANGE, value)
    return value


def wait_milliseconds(timeouts: int, jitter_milliseconds: int) -> int:
    """Return how long to wait after ``timeouts`` acknowledgement timeouts before sending again.

    Args:
        timeouts: How many acknowledgement timeouts this command has already had. One after
            the first send, and at most one below the send budget: the budget's last timeout
            abandons the command instead of scheduling another send.
        jitter_milliseconds: The draw to add, from zero up to but not including the jitter
            bound.

    Returns:
        The wait in milliseconds, jitter included.

    Raises:
        ScheduleError: ``TIMEOUT_COUNT`` when no wait exists for that many timeouts, or
            ``JITTER_RANGE`` for a draw outside the bound.
    """
    if not FIRST_TIMEOUT <= timeouts < MAX_COMMAND_SENDS:
        raise ScheduleError(ScheduleRefusal.TIMEOUT_COUNT, timeouts)
    backoff = BACKOFF_BASE_SECONDS * MILLISECONDS_PER_SECOND
    for _ in range(timeouts - FIRST_TIMEOUT):
        backoff *= BACKOFF_FACTOR
    return backoff + _checked_draw(jitter_milliseconds)


def abandon_instant_milliseconds(jitter_milliseconds: Sequence[int]) -> int:
    """Return when a command issued at zero is abandoned, given one draw per backoff.

    This is the derivation, executable: every acknowledgement window the command waits out
    plus every backoff between its sends. With no jitter it is the floor the operating
    parameters record, and with the largest draws it is the ceiling.

    Args:
        jitter_milliseconds: One draw per backoff, so one fewer than the send budget.

    Returns:
        The instant of abandonment in milliseconds after the first send.

    Raises:
        ScheduleError: ``DRAW_COUNT`` for the wrong number of draws, or ``JITTER_RANGE`` for
            any draw outside the bound.
    """
    if len(jitter_milliseconds) != REQUIRED_DRAWS:
        raise ScheduleError(ScheduleRefusal.DRAW_COUNT, len(jitter_milliseconds))
    acknowledging = MAX_COMMAND_SENDS * ACKNOWLEDGEMENT_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND
    waiting = sum(
        wait_milliseconds(timeouts, draw)
        for timeouts, draw in enumerate(jitter_milliseconds, start=FIRST_TIMEOUT)
    )
    return acknowledging + waiting
