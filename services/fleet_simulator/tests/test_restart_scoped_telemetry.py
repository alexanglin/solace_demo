"""Restart regression for telemetry producer identities in the deployed fleet composition.

ADR-0140 scopes a live telemetry producer to one mission, because the fleet's sequence counter is
process-local while the recorder's high-water mark is durable. This test drives the composition
Compose runs -- ``console.py`` -> ``FleetExecutor`` -- rather than the parallel
``service._publish``, because a test against the unreachable twin is not evidence that
production complies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

import pytest
from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    BrokerLifecycleState,
    MessagePublisher,
)
from aerial_rescue_broker.routing import DeliveryRouter, PublicationPorts
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.principals import Principal
from aerial_rescue_fleet_simulator.control_plane.wire import FleetControlStartRequest
from aerial_rescue_fleet_simulator.fleet import Reading
from aerial_rescue_fleet_simulator.runtime import (
    ExecutorDependencies,
    FleetExecutor,
    FleetRuntimeSession,
    FleetRuntimeStore,
    FleetSessionOpener,
    RunStampSource,
    _RunCheckpoint,
    _scenario,
)
from aerial_rescue_fleet_simulator.service import IntakeBounds, Pacer
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

pytestmark = [pytest.mark.unit]

_READING = Reading(
    drone_id="drone-00",
    latitude_microdegrees=45_000_000,
    longitude_microdegrees=-79_000_000,
    altitude_metres=80,
    heading_degrees=90,
    ground_speed_centimetres_per_second=700,
    battery_percent=85,
)


class _Publisher:
    """A direct port that records every exact publication."""

    def __init__(self) -> None:
        """Begin with no publications."""
        self.sent: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish_unacknowledged(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Record one direct publication."""
        self.sent.append((topic, payload, properties))


class _Readiness:
    """Report the complete application readiness an active run requires."""

    state = BrokerLifecycleState.CONNECTED

    def is_ready(self) -> bool:
        """Report the readiness the executor checks before every reading."""
        return True


class _Session:
    """The one session member ``FleetExecutor.ready`` reads."""

    def __init__(self) -> None:
        """Expose a session that is always operable."""
        self.readiness = _Readiness()


class _RestartedStamps:
    """Mint the sequence a freshly restarted fleet process would begin at."""

    def __init__(self) -> None:
        """Begin every producer at the post-restart sequence zero."""
        self.run_id: str | None = None

    def begin_run(self, run_id: str) -> None:
        """Retain the run the executor bound."""
        self.run_id = run_id

    def next_stamp(self, _producer: str) -> TelemetryStamp:
        """Return the exact stamp a restarted process mints first."""
        return TelemetryStamp(
            event_id="event-restarted-0001",
            occurred_at="2026-08-26T15:00:00.000Z",
            sequence=0,
            correlation_id="run-restarted-0001",
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
        )


def _request(mission_id: str) -> FleetControlStartRequest:
    """Return one accepted single-drone run for the named mission."""
    return FleetControlStartRequest.model_validate(
        {
            "controlVersion": 1,
            "runId": "run-2026-0001",
            "scenario": {
                "missionId": mission_id,
                "drones": [
                    {
                        "droneId": f"drone-{index:02d}",
                        "sectorId": f"sector-{index:02d}",
                        "latitudeMicrodegrees": 47_000_000 + index,
                        "longitudeMicrodegrees": -122_000_000,
                        "altitudeMetres": 400,
                        "headingDegrees": 0,
                        "groundSpeedCentimetresPerSecond": 850,
                        "batteryPermille": 1_000,
                        "northMicrodegreesPerTick": 10,
                        "eastMicrodegreesPerTick": 0,
                        "batteryDrainPermillePerTick": 5,
                    }
                    for index in range(20)
                ],
                "tickIntervalMilliseconds": 1_000,
                "connectivityThresholds": {
                    "missesToDegraded": 3,
                    "missesToOffline": 6,
                    "heartbeatsToRecover": 2,
                },
                "ticksToSweep": 1,
                "absentHeartbeats": [],
            },
        }
    )


async def _published_source(mission_id: str) -> str:
    """Return the producer source the deployed executor publishes one reading under."""
    publisher = _Publisher()
    executor = FleetExecutor(
        ExecutorDependencies(
            endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
            credential="secret",
            configured_drone_ids=tuple(f"drone-{index:02d}" for index in range(20)),
            open_broker=cast("FleetSessionOpener", lambda *_arguments: None),
            store=cast("FleetRuntimeStore", None),
            schemas=cast("PayloadSchemaExecutor", None),
            stamps=cast("RunStampSource", _RestartedStamps()),
            pacer=cast("Pacer", None),
            send_budget=SendBudget(max_sends=5),
            intake=IntakeBounds(commands_per_drone_per_tick=3),
            confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
            recovery_pause=cast("Callable[[], Awaitable[None]]", None),
        )
    )
    executor._session = cast("FleetRuntimeSession", _Session())
    executor._router = DeliveryRouter(
        Principal.FLEET_SIMULATOR,
        PublicationPorts(direct=publisher, guaranteed=cast("MessagePublisher", publisher)),
    )
    request = _request(mission_id)
    checkpoint = _RunCheckpoint(request, asyncio.Event(), 1, 0)
    published, interrupted = await executor._publish_readings(
        _scenario(request),
        (_READING,),
        checkpoint,
    )
    assert (published, interrupted) == (1, None)
    return decode_envelope(publisher.sent[0][1]).source


def test_a_successor_mission_does_not_reuse_a_pre_restart_telemetry_source() -> None:
    # Arrange
    missions = ("mission-predecessor", "mission-successor")

    # Act
    sources = tuple(asyncio.run(_published_source(mission)) for mission in missions)

    # Assert
    assert sources[0] != sources[1]
    assert all(source.startswith("urn:aerial-rescue:drone-run:") for source in sources)
