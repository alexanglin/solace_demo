"""The one salient observation a run reports, and the two bindings that make it consumable.

The scenario `docs/LIMITATIONS.md` documents is detection-driven: "A single artifact is placed and
the fleet looks for it." Every consumer of that observation exists -- the Event Mesh Gateway
subscription, the coordinator's structured invocation, proposal normalisation, evidence scoring --
and nothing produced one, so the chain never ran outside a Phase 0 probe.

Two bindings decide whether a published salient event reaches anything, and both fail silently when
wrong. The evidence service compares the envelope source against `urn:aerial-rescue:drone:{droneId}`
exactly, and refuses anything else as invalid ingress with a body-free refusal row. The Event Mesh
Gateway reads the proposal binding from the `aerial-rescue-source-event-digest` user property, which
the other lifecycle records do not carry. Each is asserted here rather than left to a live run.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Final

from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.mission import is_terminal as mission_is_terminal
from aerial_rescue_fleet_simulator.fleet import FleetState, advance_tick, initial_fleet
from aerial_rescue_fleet_simulator.lifecycle import (
    SOURCE_DIGEST_PROPERTY,
    BrokerFleetLifecycle,
    SalientObservation,
    publish_transitions,
)
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

MISSION: Final = "m-2026-0001"
OBSERVER: Final = "drone-sim-07"
THRESHOLDS: Final = ConnectivityThresholds(
    misses_to_degraded=3, misses_to_offline=6, heartbeats_to_recover=2
)


def _drone(drone_id: str, sector_id: str, latitude: int) -> DroneStart:
    """Return one drone holding one sector, moving north on a constant leg."""
    return DroneStart(
        drone_id=drone_id,
        sector_id=sector_id,
        latitude_microdegrees=latitude,
        longitude_microdegrees=-79_228_400,
        altitude_metres=80,
        heading_degrees=0,
        ground_speed_centimetres_per_second=1_100,
        battery_permille=990,
        north_microdegrees_per_tick=120,
        east_microdegrees_per_tick=0,
        battery_drain_permille_per_tick=1,
    )


def _scenario(*, absent: dict[str, frozenset[int]] | None = None) -> FleetScenario:
    """Return a two-drone scenario whose sectors both reach searched."""
    return FleetScenario(
        mission_id=MISSION,
        drones=(
            _drone(OBSERVER, "sector-07", 44_493_100),
            _drone("drone-sim-13", "sector-13", 44_494_500),
        ),
        tick_interval_milliseconds=1_000,
        thresholds=THRESHOLDS,
        ticks_to_sweep=2,
        absent_heartbeats={} if absent is None else absent,
    )


def _observed() -> SalientObservation:
    """Return one accepted observation, so a binding test states only what it asserts."""
    return SalientObservation(
        observation="artifact-sighting",
        latitude_microdegrees=44_493_100,
        longitude_microdegrees=-79_228_400,
        detail="A marker on open rock.",
    )


class _RecordingLifecycle:
    """Record every lifecycle call the fold makes, in order, without publishing."""

    def __init__(self) -> None:
        self.salient: list[tuple[str, str, SalientObservation]] = []

    def connectivity_changed(self, _mission_id: str, _drone_id: str, _state: object) -> bytes:
        return b""

    def sector_changed(
        self, _mission_id: str, _sector_id: str, _assigned_member_id: str, _state: object
    ) -> bytes:
        return b""

    def salient_observed(
        self, mission_id: str, drone_id: str, observed: SalientObservation
    ) -> bytes:
        self.salient.append((mission_id, drone_id, observed))
        return b""


class _RecordingPublisher:
    """Capture the topic, payload, and properties of every publication."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, dict[str, object]]] = []

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        self.published.append((topic, payload, dict(properties)))


class _Stamps:
    """Mint deterministic producer-scoped stamps, counting each producer separately."""

    def __init__(self) -> None:
        self.sequences: dict[str, int] = {}
        self.minted = 0

    def next_stamp(self, producer: str) -> TelemetryStamp:
        self.sequences[producer] = self.sequences.get(producer, 0) + 1
        self.minted += 1
        return TelemetryStamp(
            event_id=f"e-{self.minted:04d}",
            occurred_at="2026-08-31T00:00:00.000Z",
            sequence=self.sequences[producer],
            correlation_id="c-0000",
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        )


def _fold_until_searched(scenario: FleetScenario) -> list[tuple[FleetState, FleetState]]:
    """Fold to the mission's ending, returning each before/after pair the run crossed.

    The fold refuses a tick after a terminal mission, so the walk stops at the ending rather than
    choosing a tick count that would have to track ``ticks_to_sweep``.
    """
    pairs: list[tuple[FleetState, FleetState]] = []
    state = initial_fleet(scenario)
    while not mission_is_terminal(state.mission):
        before, state = state, advance_tick(scenario, state).state
        pairs.append((before, state))
    return pairs


class SalientObservationTriggerTests(unittest.TestCase):
    def test_only_the_absent_heartbeat_drone_reports_a_salient_observation(self) -> None:
        # Arrange
        scenario = _scenario(absent={OBSERVER: frozenset({1})})
        lifecycle = _RecordingLifecycle()

        # Act
        for before, after in _fold_until_searched(scenario):
            publish_transitions(lifecycle, scenario, before, after)

        # Assert
        self.assertEqual([OBSERVER], [call[1] for call in lifecycle.salient])

    def test_a_run_reports_its_salient_observation_at_the_observing_drones_position(self) -> None:
        # Arrange
        scenario = _scenario(absent={OBSERVER: frozenset({1})})
        lifecycle = _RecordingLifecycle()

        # Act
        for before, after in _fold_until_searched(scenario):
            publish_transitions(lifecycle, scenario, before, after)

        # Assert
        self.assertEqual(
            (MISSION, OBSERVER, -79_228_400),
            (
                lifecycle.salient[0][0],
                lifecycle.salient[0][1],
                lifecycle.salient[0][2].longitude_microdegrees,
            ),
        )


class SalientObservationBindingTests(unittest.TestCase):
    def test_a_salient_observation_publishes_under_the_drone_source_evidence_requires(self) -> None:
        # Arrange
        publisher = _RecordingPublisher()
        lifecycle = BrokerFleetLifecycle(publisher, "run-0001", _Stamps())

        # Act
        lifecycle.salient_observed(MISSION, OBSERVER, _observed())

        # Assert
        self.assertEqual(
            f"urn:aerial-rescue:drone:{OBSERVER}",
            decode_envelope(publisher.published[0][1]).source,
        )

    def test_a_salient_observation_carries_the_source_event_digest_the_gateway_binds(self) -> None:
        # Arrange
        publisher = _RecordingPublisher()
        lifecycle = BrokerFleetLifecycle(publisher, "run-0001", _Stamps())

        # Act
        lifecycle.salient_observed(MISSION, OBSERVER, _observed())

        # Assert
        self.assertEqual(
            source_event_digest(decode_envelope(publisher.published[0][1])),
            publisher.published[0][2][SOURCE_DIGEST_PROPERTY],
        )

    def test_a_salient_observation_draws_its_sequence_from_its_own_producer_stream(self) -> None:
        # Arrange
        stamps = _Stamps()
        lifecycle = BrokerFleetLifecycle(_RecordingPublisher(), "run-0001", stamps)

        # Act
        lifecycle.salient_observed(MISSION, OBSERVER, _observed())

        # Assert
        self.assertNotIn(OBSERVER, stamps.sequences)


if __name__ == "__main__":
    unittest.main()
