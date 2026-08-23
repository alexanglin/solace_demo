"""The retry schedule one dispatched command follows before it is abandoned.

Four values decide it, and ``docs/adr/0081-give-command-dispatch-one-interval.md`` derives
all four together because none of them means anything alone: the abandon instant is a sum of
intervals, so a send budget without an acknowledgement timeout and a backoff is a number with
a hidden derivation. These tests are the instrument the operating-parameters row names -- they
fold the four values rather than restating the 120-second and 144-second envelope, so a
changed value fails here instead of quietly disagreeing with the table.

The schedule is pure. The jitter draw arrives as an argument because the composition root owns
the random source, in the same way the record's clock and identifier arrive as a stamp.
"""

from __future__ import annotations

import unittest
from enum import Enum
from typing import Final

from aerial_rescue_command_gateway.dispatch import (
    ACKNOWLEDGEMENT_TIMEOUT_SECONDS,
    BACKOFF_BASE_SECONDS,
    JITTER_BOUND_MILLISECONDS,
    JITTER_BOUND_SECONDS,
    MAX_COMMAND_SENDS,
    ScheduleError,
    ScheduleRefusal,
    abandon_instant_milliseconds,
    wait_milliseconds,
)

NO_JITTER: Final = (0, 0, 0, 0)
LARGEST_JITTER: Final = JITTER_BOUND_MILLISECONDS - 1
UNJITTERED_ABANDON_MILLISECONDS: Final = 120_000
ENVELOPE_CEILING_MILLISECONDS: Final = 144_000
DECLARED_FAULT_MILLISECONDS: Final = 102_000
QUEUE_EXPIRY_MILLISECONDS: Final = 300_000


def _wait_refusal(timeouts: int, jitter: int) -> tuple[Enum, object]:
    """Return the refusal a wait raises, failing the test if it is accepted instead."""
    try:
        wait_milliseconds(timeouts, jitter)
    except ScheduleError as error:
        return (error.refusal, error.value)
    message = f"accepted: timeouts={timeouts!r} jitter={jitter!r}"
    raise AssertionError(message)


def _abandon_refusal(jitter: tuple[int, ...]) -> tuple[Enum, object]:
    """Return the refusal an abandon instant raises, failing the test if it is accepted."""
    try:
        abandon_instant_milliseconds(jitter)
    except ScheduleError as error:
        return (error.refusal, error.value)
    message = f"accepted: {jitter!r}"
    raise AssertionError(message)


class DerivedValueTests(unittest.TestCase):
    def test_command_dispatch_has_one_interval_rather_than_three(self) -> None:
        """ADR-0081's decision, as a value: the three durations are the same number."""
        # Arrange
        expected = (6, 6, 6)

        # Act
        actual = (
            ACKNOWLEDGEMENT_TIMEOUT_SECONDS,
            BACKOFF_BASE_SECONDS,
            JITTER_BOUND_SECONDS,
        )

        # Assert
        self.assertEqual(expected, actual)

    def test_the_send_budget_is_the_smallest_that_clears_the_declared_fault(self) -> None:
        """Four sends abandon at 66 s, inside the 102 s envelope; five is the answer."""
        # Arrange
        expected = 5

        # Act
        actual = MAX_COMMAND_SENDS

        # Assert
        self.assertEqual(expected, actual)


class ScheduleTests(unittest.TestCase):
    def test_the_backoff_doubles_before_each_later_send(self) -> None:
        """The four waits the operating-parameters row names, in milliseconds."""
        # Arrange
        expected = (6_000, 12_000, 24_000, 48_000)

        # Act
        actual = tuple(wait_milliseconds(timeouts, 0) for timeouts in range(1, MAX_COMMAND_SENDS))

        # Assert
        self.assertEqual(expected, actual)

    def test_jitter_lengthens_a_wait_and_never_shortens_it(self) -> None:
        """The sign is the decision: a subtracted draw would put the floor below itself."""
        # Arrange
        unjittered = wait_milliseconds(1, 0)

        # Act
        jittered = wait_milliseconds(1, 250)

        # Assert
        self.assertEqual((6_000, 6_250), (unjittered, jittered))

    def test_the_largest_representable_draw_is_one_below_the_bound(self) -> None:
        """The bound is exclusive, which is what keeps the ceiling under 144 seconds."""
        # Arrange
        expected = 6_000 + LARGEST_JITTER

        # Act
        actual = wait_milliseconds(1, LARGEST_JITTER)

        # Assert
        self.assertEqual(expected, actual)


class ScheduleRefusalTests(unittest.TestCase):
    def test_there_is_no_wait_before_the_first_send(self) -> None:
        """A send that has not timed out yet has no backoff to serve."""
        # Arrange
        expected = (ScheduleRefusal.TIMEOUT_COUNT, 0)

        # Act
        actual = _wait_refusal(0, 0)

        # Assert
        self.assertEqual(expected, actual)

    def test_there_is_no_wait_after_the_budget_s_last_timeout(self) -> None:
        """The fifth timeout abandons the command; it does not schedule a sixth send."""
        # Arrange
        expected = (ScheduleRefusal.TIMEOUT_COUNT, MAX_COMMAND_SENDS)

        # Act
        actual = _wait_refusal(MAX_COMMAND_SENDS, 0)

        # Assert
        self.assertEqual(expected, actual)

    def test_a_negative_draw_is_refused_rather_than_clamped(self) -> None:
        """Clamping would hide a broken random source behind a correct-looking schedule."""
        # Arrange
        expected = (ScheduleRefusal.JITTER_RANGE, -1)

        # Act
        actual = _wait_refusal(1, -1)

        # Assert
        self.assertEqual(expected, actual)

    def test_a_draw_at_the_bound_is_refused(self) -> None:
        """The bound is exclusive, so the derived ceiling is a strict one."""
        # Arrange
        expected = (ScheduleRefusal.JITTER_RANGE, JITTER_BOUND_MILLISECONDS)

        # Act
        actual = _wait_refusal(1, JITTER_BOUND_MILLISECONDS)

        # Assert
        self.assertEqual(expected, actual)


class AbandonEnvelopeTests(unittest.TestCase):
    def test_the_unjittered_schedule_abandons_at_two_minutes(self) -> None:
        """Five 6 s timeouts and four doubling backoffs: 30 s plus 90 s."""
        # Arrange
        expected = UNJITTERED_ABANDON_MILLISECONDS

        # Act
        actual = abandon_instant_milliseconds(NO_JITTER)

        # Assert
        self.assertEqual(expected, actual)

    def test_the_unjittered_schedule_clears_the_declared_fault_envelope(self) -> None:
        """102 s of edge disconnect, restart recovery, drain, and command path."""
        # Arrange
        expected = True

        # Act
        actual = abandon_instant_milliseconds(NO_JITTER) >= DECLARED_FAULT_MILLISECONDS

        # Assert
        self.assertEqual(expected, actual)

    def test_the_largest_draws_stay_inside_the_ceiling_and_the_queue_expiry(self) -> None:
        """144 s is the row's ceiling and 300 s is when the broker dead-letters a copy."""
        # Arrange
        largest = (LARGEST_JITTER,) * (MAX_COMMAND_SENDS - 1)

        # Act
        instant = abandon_instant_milliseconds(largest)

        # Assert
        self.assertEqual(
            (True, True, 143_996),
            (
                instant <= ENVELOPE_CEILING_MILLISECONDS,
                instant < QUEUE_EXPIRY_MILLISECONDS,
                instant,
            ),
        )

    def test_one_draw_per_backoff_is_required(self) -> None:
        """Four backoffs separate five sends; a shorter run would silently skip one."""
        # Arrange
        expected = (ScheduleRefusal.DRAW_COUNT, 3)

        # Act
        actual = _abandon_refusal((0, 0, 0))

        # Assert
        self.assertEqual(expected, actual)

    def test_a_draw_outside_the_bound_refuses_the_whole_envelope(self) -> None:
        """One bad draw is a broken random source, not a slightly longer schedule."""
        # Arrange
        expected = (ScheduleRefusal.JITTER_RANGE, JITTER_BOUND_MILLISECONDS)

        # Act
        actual = _abandon_refusal((0, JITTER_BOUND_MILLISECONDS, 0, 0))

        # Assert
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
