"""The drone connectivity machine: CONNECTED, DEGRADED, and OFFLINE over heartbeat intervals.

Every transition is asserted as the full status triple, so a mutated counter or an
off-by-one threshold fails loudly rather than passing on the state alone. The counts are the
provisional operating parameters: 3 misses to DEGRADED, 6 to OFFLINE, 2 heartbeats to recover.
"""

from __future__ import annotations

import unittest
from enum import Enum

import pytest

from aerial_rescue_domain.connectivity import (
    INITIAL_STATUS,
    ConnectivityError,
    ConnectivityRefusal,
    ConnectivityState,
    ConnectivityStatus,
    ConnectivityThresholds,
    heartbeat_missed,
    heartbeat_received,
)

THRESHOLDS = ConnectivityThresholds(3, 6, 2)
CONNECTED = ConnectivityState.CONNECTED
DEGRADED = ConnectivityState.DEGRADED
OFFLINE = ConnectivityState.OFFLINE


def _after(script: str, status: ConnectivityStatus = INITIAL_STATUS) -> ConnectivityStatus:
    """Fold a script of ``m`` (missed) and ``h`` (heard) intervals over ``status``."""
    for interval in script:
        step = heartbeat_received if interval == "h" else heartbeat_missed
        status = step(status, THRESHOLDS)
    return status


def _states_along(script: str) -> tuple[ConnectivityState, ...]:
    """Return the state after each interval of ``script``, starting from the initial status."""
    return tuple(_after(script[: index + 1]).state for index in range(len(script)))


def _threshold_refusal_of(counts: tuple[int, int, int]) -> tuple[Enum, object]:
    """Return the refusal building thresholds from ``counts`` raises, failing if accepted."""
    try:
        ConnectivityThresholds(*counts)
    except ConnectivityError as error:
        return (error.refusal, error.value)
    message = f"accepted: {counts!r}"
    raise AssertionError(message)


class ThresholdTests(unittest.TestCase):
    def test_thresholds_at_their_minimums_are_accepted(self) -> None:
        # Arrange
        counts = (1, 2, 1)

        # Act
        thresholds = ConnectivityThresholds(*counts)

        # Assert
        self.assertEqual(
            counts,
            (
                thresholds.misses_to_degraded,
                thresholds.misses_to_offline,
                thresholds.heartbeats_to_recover,
            ),
        )

    def test_out_of_order_or_zero_thresholds_are_refused_with_their_values(self) -> None:
        # Arrange
        rejected = ((0, 6, 2), (3, 3, 2), (6, 3, 2), (3, 6, 0))

        # Act
        refusals = tuple(_threshold_refusal_of(counts) for counts in rejected)

        # Assert
        self.assertEqual(
            (
                (ConnectivityRefusal.MISS_THRESHOLDS, (0, 6)),
                (ConnectivityRefusal.MISS_THRESHOLDS, (3, 3)),
                (ConnectivityRefusal.MISS_THRESHOLDS, (6, 3)),
                (ConnectivityRefusal.RECOVERY_COUNT, 0),
            ),
            refusals,
        )

    def test_a_threshold_refusal_carries_the_structured_reason(self) -> None:
        # Arrange
        expected = (ConnectivityRefusal.MISS_THRESHOLDS, (3, 3))

        # Act
        with pytest.raises(ConnectivityError) as captured:
            ConnectivityThresholds(3, 3, 2)

        # Assert
        self.assertEqual(expected, (captured.value.refusal, captured.value.value))


class MissTests(unittest.TestCase):
    def test_the_initial_status_is_connected_with_zero_counters(self) -> None:
        # Arrange
        expected = ConnectivityStatus(CONNECTED, 0, 0)

        # Act
        status = INITIAL_STATUS

        # Assert
        self.assertEqual(expected, status)

    def test_misses_below_the_degraded_threshold_keep_the_drone_connected(self) -> None:
        # Arrange
        script = "mm"

        # Act
        status = _after(script)

        # Assert
        self.assertEqual(ConnectivityStatus(CONNECTED, 2, 0), status)

    def test_the_third_consecutive_miss_degrades_the_drone(self) -> None:
        # Arrange
        script = "mmm"

        # Act
        status = _after(script)

        # Assert
        self.assertEqual(ConnectivityStatus(DEGRADED, 3, 0), status)

    def test_the_fifth_miss_is_still_degraded_and_the_sixth_is_offline(self) -> None:
        # Arrange
        scripts = ("mmmmm", "mmmmmm")

        # Act
        statuses = tuple(_after(script) for script in scripts)

        # Assert
        self.assertEqual(
            (ConnectivityStatus(DEGRADED, 5, 0), ConnectivityStatus(OFFLINE, 6, 0)),
            statuses,
        )

    def test_a_miss_after_a_partial_recovery_keeps_the_drone_offline(self) -> None:
        # Arrange
        script = "mmmmmm" + "h" + "m"

        # Act
        status = _after(script)

        # Assert
        self.assertEqual(ConnectivityStatus(OFFLINE, 1, 0), status)

    def test_a_miss_after_a_partial_recovery_keeps_the_drone_degraded(self) -> None:
        # Arrange
        script = "mmm" + "h" + "m"

        # Act
        status = _after(script)

        # Assert
        self.assertEqual(ConnectivityStatus(DEGRADED, 1, 0), status)

    def test_the_documented_script_drops_out_at_six_intervals_and_recovers_after_two(self) -> None:
        # Arrange
        script = "mmmmmm" + "hh"

        # Act
        states = _states_along(script)

        # Assert
        self.assertEqual(
            (CONNECTED, CONNECTED, DEGRADED, DEGRADED, DEGRADED, OFFLINE, OFFLINE, CONNECTED),
            states,
        )


class HeartbeatTests(unittest.TestCase):
    def test_one_heartbeat_does_not_leave_offline_and_two_do(self) -> None:
        # Arrange
        offline = _after("mmmmmm")

        # Act
        statuses = (_after("h", offline), _after("hh", offline))

        # Assert
        self.assertEqual(
            (ConnectivityStatus(OFFLINE, 0, 1), ConnectivityStatus(CONNECTED, 0, 2)),
            statuses,
        )

    def test_one_heartbeat_does_not_leave_degraded_and_two_do(self) -> None:
        # Arrange
        degraded = _after("mmm")

        # Act
        statuses = (_after("h", degraded), _after("hh", degraded))

        # Assert
        self.assertEqual(
            (ConnectivityStatus(DEGRADED, 0, 1), ConnectivityStatus(CONNECTED, 0, 2)),
            statuses,
        )

    def test_a_heartbeat_while_connected_resets_misses_and_counts_heartbeats(self) -> None:
        # Arrange
        script = "mmh"

        # Act
        status = _after(script)

        # Assert
        self.assertEqual(ConnectivityStatus(CONNECTED, 0, 1), status)

    def test_heartbeats_while_connected_keep_counting(self) -> None:
        # Arrange
        script = "hhh"

        # Act
        status = _after(script)

        # Assert
        self.assertEqual(ConnectivityStatus(CONNECTED, 0, 3), status)


if __name__ == "__main__":
    unittest.main()
