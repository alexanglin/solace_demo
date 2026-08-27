"""Mission lifecycle recovery reconstructs an exact broker witness after restart."""

from __future__ import annotations

import unittest
from collections.abc import Mapping

import pytest
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_scenario_service.lifecycle import BrokerMissionLifecycle, MissionLifecycle

pytestmark = [pytest.mark.unit]


class _Publisher:
    """Retain exact acknowledged publication attempts."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Record one successful guaranteed publication."""
        self.attempts.append((topic, payload, properties))


class MissionLifecycleRestartTests(unittest.TestCase):
    def test_fresh_publishers_reconstruct_the_same_lost_run_aborted_event(self) -> None:
        # Arrange
        first_transport = _Publisher()
        second_transport = _Publisher()
        first = BrokerMissionLifecycle(
            first_transport,
            maximum_attempts=1,
        )
        restarted = BrokerMissionLifecycle(
            second_transport,
            maximum_attempts=1,
        )

        # Act
        first_payload = first.publish("run-synthetic-0001", "mission-synthetic-0001", "ABORTED")
        restarted_payload = restarted.publish(
            "run-synthetic-0001", "mission-synthetic-0001", "ABORTED"
        )
        event = decode_envelope(first_payload)

        # Assert
        self.assertEqual(first_payload, restarted_payload)
        self.assertEqual(first_transport.attempts, second_transport.attempts)
        self.assertEqual("000000000000001", event.sequence)

    def test_lifecycle_slots_and_synthetic_times_remain_ordered_across_restart(self) -> None:
        # Arrange
        transport = _Publisher()
        publisher = BrokerMissionLifecycle(transport, maximum_attempts=1)
        restarted_transport = _Publisher()
        restarted = BrokerMissionLifecycle(restarted_transport, maximum_attempts=1)
        lifecycles: tuple[MissionLifecycle, ...] = ("PLANNED", "SEARCHING", "EXHAUSTED")

        # Act
        payloads = tuple(
            publisher.publish("run-synthetic-0001", "mission-synthetic-0001", lifecycle)
            for lifecycle in lifecycles
        )
        reconstructed_terminal = restarted.publish(
            "run-synthetic-0001", "mission-synthetic-0001", "EXHAUSTED"
        )
        events = tuple(decode_envelope(payload) for payload in payloads)

        # Assert
        self.assertEqual(
            ["000000000000000", "000000000000001", "000000000000002"],
            [event.sequence for event in events],
        )
        self.assertEqual(sorted(event.time for event in events), [event.time for event in events])
        self.assertEqual(3, len({event.id for event in events}))
        self.assertEqual(payloads[-1], reconstructed_terminal)
