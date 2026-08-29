"""Whether the broker accepts what the fleet simulator publishes, and delivers it intact.

The member's own suite proves the fold and the record against fakes. That is evidence about
a plan. This probe is the other kind: it opens two real connections to the container in
``deploy/compose.yaml``, publishes on the least-privilege `fleet-simulator` identity, and
reads the result back on the `dashboard-api` identity, so what is asserted is the broker's
answer rather than the project's intention.

The reader is the allowed positive control. A denial proves nothing on its own, and the
authorization-negative side -- the `fleet-simulator` role refused the drone command topic --
is already proven by ``tests/security/test_broker_authorization.py`` and is referenced here
rather than duplicated.

Two claims are kept apart deliberately. The serve report proves what was **sent**, exactly.
The reader proves what the broker **accepted and delivered**, and routine telemetry is
direct and droppable under ``docs/CONTRACTS.md``, so the arrival assertions are written to
survive a drop rather than to deny one is possible.

The simulator binds a durable command queue for every drone its scenario declares, so this
probe now needs those three queues provisioned as well as publishing rights. The one
invocation every live probe shares is in ``test_command_dispatch_live.PROVISIONING``.

Carries the ``integration``, ``docker``, and ``broker`` markers, so no blocking suite runs
it (``docs/TESTING.md``).
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Final, override
from uuid import uuid4

import pytest
from aerial_rescue_broker.messaging import (
    DIRECT_TELEMETRY_RECEIVER_CAPACITY,
    BrokerSession,
    open_fleet_session,
    open_session,
)
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.envelope import check_topic_binding, decode_envelope
from aerial_rescue_contracts.topics import Family, parse_topic
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.connectivity import ConnectivityState, ConnectivityThresholds
from aerial_rescue_domain.principals import Principal
from aerial_rescue_domain.sectors import SectorState
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.service import (
    CountingStamps,
    IntakeBounds,
    MonotonicPacer,
    PublishOutcome,
    Runtime,
    run,
)

from tests.broker_live_support import LOCAL_BROKER_ENDPOINT as ENDPOINT
from tests.broker_live_support import SHARED_PROBE_DRONES, role_credential

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]

MISSION: Final = "m-live-0001"
TICKS: Final = 4
STEP: Final = 10
RECEIVE_WINDOW_MILLISECONDS: Final = 2_000
DRAIN_WINDOW_MILLISECONDS: Final = 500

VISION: Final = DroneStart(
    drone_id=SHARED_PROBE_DRONES[2],
    sector_id="sector-north",
    latitude_microdegrees=47_000_000,
    longitude_microdegrees=-122_000_000,
    altitude_metres=400,
    heading_degrees=0,
    ground_speed_centimetres_per_second=850,
    battery_permille=1_000,
    north_microdegrees_per_tick=STEP,
    east_microdegrees_per_tick=0,
    battery_drain_permille_per_tick=5,
)
THERMAL: Final = replace(VISION, drone_id=SHARED_PROBE_DRONES[3], sector_id="sector-south")
GUARD: Final = replace(VISION, drone_id=SHARED_PROBE_DRONES[4], sector_id="sector-east")
ROSTER: Final = (VISION, THERMAL, GUARD)

SCENARIO: Final = FleetScenario(
    mission_id=MISSION,
    drones=ROSTER,
    tick_interval_milliseconds=1_000,
    thresholds=ConnectivityThresholds(
        misses_to_degraded=2, misses_to_offline=3, heartbeats_to_recover=2
    ),
    ticks_to_sweep=99,
    absent_heartbeats={GUARD.drone_id: frozenset(range(TICKS))},
)


class Countdown:
    """A ``running`` predicate that holds for a fixed number of asks."""

    def __init__(self, asks: int) -> None:
        """Record how many asks this predicate answers affirmatively."""
        self.remaining = asks

    def __call__(self) -> bool:
        """Return whether the run should continue, and consume one ask."""
        keep = self.remaining > 0
        self.remaining -= 1
        return keep


def _stamps() -> CountingStamps:
    """Return a stamp source over the wall clock and a real identifier source."""
    return CountingStamps(
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        correlation_id=f"c-{uuid4().hex[:16]}",
    )


def _positions(payloads: list[Mapping[str, object]]) -> dict[str, set[int]]:
    """Return every latitude each drone was seen at, keyed by drone."""
    seen: dict[str, set[int]] = {}
    for payload in payloads:
        drone = str(payload["droneId"])
        seen.setdefault(drone, set()).add(int(str(payload["latitudeMicrodegrees"])))
    return seen


class FleetSimulatorLiveTests(unittest.TestCase):
    """One run against the container, read back on a role permitted to read it."""

    payloads: list[Mapping[str, object]]
    outcomes: Mapping[PublishOutcome, int]
    sectors: Mapping[str, SectorState]
    connectivity: Mapping[str, ConnectivityState]

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Subscribe, run the simulator, and drain what the broker delivered."""
        reader = open_session(
            ENDPOINT,
            Principal.DASHBOARD_API,
            role_credential(Principal.DASHBOARD_API),
            (subscription_for(Family.DRONE_TELEMETRY),),
            direct_receiver_capacity=DIRECT_TELEMETRY_RECEIVER_CAPACITY,
        )
        try:
            report = run(
                Runtime(
                    endpoint=ENDPOINT,
                    credential=role_credential(Principal.FLEET_SIMULATOR),
                    open_broker=open_fleet_session,
                    scenario=SCENARIO,
                    stamps=_stamps(),
                    running=Countdown(TICKS),
                    send_budget=SendBudget(max_sends=5),
                    intake=IntakeBounds(commands_per_drone_per_tick=3),
                    pacer=MonotonicPacer(),
                )
            )
            cls.payloads = _drain(reader)
        finally:
            reader.close()
        cls.outcomes = report.outcomes
        cls.sectors = {name: runtime.state for name, runtime in report.state.sectors.items()}
        cls.connectivity = {
            name: runtime.connectivity.state for name, runtime in report.state.drones.items()
        }

    def test_the_broker_accepted_every_reading_the_run_sent(self) -> None:
        # Arrange
        expected = {PublishOutcome.PUBLISHED: len(ROSTER) * TICKS}

        # Act
        counted = dict(self.outcomes)

        # Assert
        self.assertEqual(expected, counted)

    def test_the_broker_delivered_events_and_the_drain_accepted_every_one(self) -> None:
        # Arrange
        expected_members = {
            "missionId",
            "droneId",
            "latitudeMicrodegrees",
            "longitudeMicrodegrees",
            "batteryPercent",
            "altitudeMetres",
            "headingDegrees",
            "groundSpeedCentimetresPerSecond",
        }

        # Act
        shapes = {frozenset(payload) for payload in self.payloads}

        # Assert
        self.assertEqual({frozenset(expected_members)}, shapes)

    def test_every_drone_was_heard_from_at_its_own_identifier(self) -> None:
        # Arrange
        expected = {drone.drone_id for drone in ROSTER}

        # Act
        heard = set(_positions(self.payloads))

        # Assert
        self.assertEqual(expected, heard)

    def test_every_delivered_position_is_a_whole_number_of_steps_from_the_start(self) -> None:
        # Arrange
        allowed = {
            drone.drone_id: {
                drone.latitude_microdegrees + STEP * tick for tick in range(1, TICKS + 1)
            }
            for drone in ROSTER
        }

        # Act
        seen = _positions(self.payloads)

        # Assert
        self.assertEqual(
            {},
            {
                name: values - allowed[name]
                for name, values in seen.items()
                if values - allowed[name]
            },
        )

    def test_the_drone_the_schedule_silenced_went_offline_and_imperilled_its_sector(self) -> None:
        # Arrange
        expected = (ConnectivityState.OFFLINE, SectorState.AT_RISK, SectorState.ASSIGNED)

        # Act
        observed = (
            self.connectivity[GUARD.drone_id],
            self.sectors[GUARD.sector_id],
            self.sectors[VISION.sector_id],
        )

        # Assert
        self.assertEqual(expected, observed)


def _drain(reader: BrokerSession) -> list[Mapping[str, object]]:
    """Read every telemetry event the broker delivered, checking each against its topic.

    Decoding and the topic binding are checked here rather than in an assertion, so a
    malformed or misrouted event fails the drain and no test can pass over it.
    """
    receiver = reader.receiver
    payloads: list[Mapping[str, object]] = []
    window = RECEIVE_WINDOW_MILLISECONDS
    while True:
        message = receiver.receive(window)
        if message is None:
            return payloads
        window = DRAIN_WINDOW_MILLISECONDS
        body = message.get_payload_as_bytes()
        assert body is not None
        envelope = decode_envelope(bytes(body))
        check_topic_binding(envelope, parse_topic(message.get_destination_name()))
        payloads.append(envelope.data)


if __name__ == "__main__":
    unittest.main()
