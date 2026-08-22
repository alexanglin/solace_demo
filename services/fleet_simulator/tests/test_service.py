"""The composition root: wire the ports, fold until the mission ends, and shut down.

There is no environment read and no filesystem read here. ADR-0077 puts the scenario at
the composition boundary, and the same reasoning puts the endpoint and the credential
there: the caller holds them, so this member opens nothing it was not handed.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Final

import pytest
from aerial_rescue_broker.messaging import BrokerEndpoint, MessagingError, MessagingRefusal
from aerial_rescue_contracts.envelope import MAX_SEQUENCE
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_domain.principals import Principal
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.service import (
    CountingStamps,
    PublishOutcome,
    Runtime,
    ServeReport,
    run,
)
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

pytestmark = [pytest.mark.unit]

ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn="default", trust_store="deploy/certs"
)
CREDENTIAL: Final = "not-a-real-credential"
CORRELATION: Final = "c-2026-0001"

VISION: Final = DroneStart(
    drone_id="drone-vision-01",
    sector_id="sector-north",
    latitude_microdegrees=47_000_000,
    longitude_microdegrees=-122_000_000,
    altitude_metres=400,
    heading_degrees=0,
    ground_speed_centimetres_per_second=850,
    battery_permille=1_000,
    north_microdegrees_per_tick=10,
    east_microdegrees_per_tick=0,
    battery_drain_permille_per_tick=5,
)
THERMAL: Final = replace(
    VISION,
    drone_id="drone-thermal-02",
    sector_id="sector-south",
    heading_degrees=180,
    north_microdegrees_per_tick=-10,
)


def _scenario(ticks_to_sweep: int = 3) -> FleetScenario:
    """Return an accepted two-drone scenario."""
    return FleetScenario(
        mission_id="m-2026-0001",
        drones=(VISION, THERMAL),
        tick_interval_milliseconds=1_000,
        thresholds=ConnectivityThresholds(2, 3, 2),
        ticks_to_sweep=ticks_to_sweep,
        absent_heartbeats={},
    )


class FakeDirectPublisher:
    """Records every publication, and can be told to refuse a given number of them."""

    def __init__(self, refuse_first: int = 0) -> None:
        """Record how many leading publications this publisher refuses."""
        self.published: list[tuple[str, bytes]] = []
        self.properties: list[Mapping[str, object]] = []
        self.closed = 0
        self._refusals = refuse_first

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object]
    ) -> None:
        """Record one publication, or refuse it the way the broker adapter does."""
        if self._refusals > 0:
            self._refusals -= 1
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)
        self.published.append((topic, bytes(payload)))
        self.properties.append(properties)

    def close(self) -> None:
        """Record that the publisher was terminated."""
        self.closed += 1


class FakeSession:
    """A publish-only session that records its shutdown."""

    def __init__(self, publisher: FakeDirectPublisher) -> None:
        """Record which publisher this session hands out."""
        self.publisher = publisher
        self.closed = 0

    def close(self) -> None:
        """Record that the session was closed."""
        self.closed += 1


class FakeOpener:
    """Records the endpoint, role, and credential one run connected with."""

    def __init__(self, session: FakeSession) -> None:
        """Record which session this opener yields."""
        self.session = session
        self.calls: list[tuple[BrokerEndpoint, Principal, str]] = []

    def __call__(self, endpoint: BrokerEndpoint, role: Principal, credential: str) -> FakeSession:
        """Record one connection and return the session."""
        self.calls.append((endpoint, role, credential))
        return self.session


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
    """Return a stamp source over a fixed clock and a counting identifier source."""
    counter = iter(f"{index:x}".rjust(32, "c") for index in range(1, 10_000))
    return CountingStamps(
        clock=lambda: datetime(2026, 8, 20, 14, 3, 7, 250_000, tzinfo=UTC),
        identifiers=lambda: next(counter),
        correlation_id=CORRELATION,
    )


class OverflowingStamps:
    """A stamp source whose producer sequence the envelope form cannot carry."""

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return a stamp naming its producer and a sequence one past the maximum."""
        return TelemetryStamp(
            event_id=producer,
            occurred_at="2026-08-20T14:03:07.250Z",
            sequence=MAX_SEQUENCE + 1,
            correlation_id=CORRELATION,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
        )


def _runtime(
    *,
    publisher: FakeDirectPublisher | None = None,
    scenario: FleetScenario | None = None,
    asks: int = 100,
) -> tuple[Runtime, FakeOpener, FakeDirectPublisher]:
    """Return a runtime with every boundary injected, beside the fakes it holds."""
    resolved = FakeDirectPublisher() if publisher is None else publisher
    opener = FakeOpener(FakeSession(resolved))
    runtime = Runtime(
        endpoint=ENDPOINT,
        credential=CREDENTIAL,
        open_broker=opener,
        scenario=_scenario() if scenario is None else scenario,
        stamps=_stamps(),
        running=Countdown(asks),
    )
    return runtime, opener, resolved


def _topics(publisher: FakeDirectPublisher) -> Sequence[str]:
    """Return the topics one run published on, in order."""
    return [topic for topic, _ in publisher.published]


class ConnectionTests(unittest.TestCase):
    def test_the_run_connects_on_the_fleet_simulator_role_it_was_handed(self) -> None:
        # Arrange
        runtime, opener, _ = _runtime()

        # Act
        run(runtime)

        # Assert
        self.assertEqual([(ENDPOINT, Principal.FLEET_SIMULATOR, CREDENTIAL)], opener.calls)

    def test_the_session_is_closed_even_when_the_fold_raises(self) -> None:
        # Arrange
        off_the_map = replace(VISION, latitude_microdegrees=90_000_000 - 5)
        runtime, opener, _ = _runtime(scenario=replace(_scenario(), drones=(off_the_map, THERMAL)))

        # Act
        with pytest.raises(ValueError, match="coordinate range"):
            run(runtime)

        # Assert
        self.assertEqual(1, opener.session.closed)


class PublicationTests(unittest.TestCase):
    def test_every_drone_reports_once_per_tick_on_its_own_telemetry_topic(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=2))

        # Act
        run(runtime)

        # Assert
        self.assertEqual(
            [
                "aerial-rescue/v1/m-2026-0001/drone/drone-thermal-02/telemetry",
                "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry",
            ]
            * 2,
            list(_topics(publisher)),
        )

    def test_the_producer_sequence_is_scoped_to_the_drone_that_minted_it(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=2))

        # Act
        run(runtime)

        # Assert
        self.assertEqual(
            ["000000000000000", "000000000000000", "000000000000001", "000000000000001"],
            sorted(
                payload.decode().split('"sequence":"')[1][:15] for _, payload in publisher.published
            ),
        )


class EndingTests(unittest.TestCase):
    def test_the_run_stops_at_the_ending_rather_than_asking_for_another_tick(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=2))

        # Act
        report = run(runtime)

        # Assert
        self.assertEqual(
            (MissionState.EXHAUSTED, 4), (report.state.mission, len(publisher.published))
        )

    def test_the_run_stops_when_the_runtime_says_to_stop(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=99), asks=3)

        # Act
        report = run(runtime)

        # Assert
        self.assertEqual((3, 6), (report.state.tick, len(publisher.published)))


class RefusedPublicationTests(unittest.TestCase):
    def test_a_refused_publication_is_counted_and_the_run_carries_on(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(
            publisher=FakeDirectPublisher(refuse_first=1), scenario=_scenario(ticks_to_sweep=2)
        )

        # Act
        report = run(runtime)

        # Assert
        self.assertEqual(
            ({PublishOutcome.REFUSED: 1, PublishOutcome.PUBLISHED: 3}, 3),
            (dict(report.outcomes), len(publisher.published)),
        )


class UnrecordableReadingTests(unittest.TestCase):
    def test_a_reading_that_cannot_become_a_record_is_counted_and_nothing_is_sent(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=1))
        overflowing = replace(runtime, stamps=OverflowingStamps())

        # Act
        report = run(overflowing)

        # Assert
        self.assertEqual(
            ({PublishOutcome.UNRECORDABLE: 2}, []),
            (dict(report.outcomes), publisher.published),
        )


class PropertyTests(unittest.TestCase):
    def test_telemetry_carries_no_user_properties(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=1))

        # Act
        run(runtime)

        # Assert
        self.assertEqual([{}, {}], [dict(entry) for entry in publisher.properties])


class RedactionTests(unittest.TestCase):
    def test_a_runtime_never_renders_the_credential_it_holds(self) -> None:
        # Arrange
        runtime, _, _ = _runtime()

        # Act
        rendered = repr(runtime)

        # Assert
        self.assertNotIn(CREDENTIAL, rendered)


class ReportTests(unittest.TestCase):
    def test_a_report_carries_the_state_the_run_reached(self) -> None:
        # Arrange
        runtime, _, _ = _runtime(scenario=_scenario(ticks_to_sweep=1))

        # Act
        report = run(runtime)

        # Assert
        self.assertIsInstance(report, ServeReport)


if __name__ == "__main__":
    unittest.main()
