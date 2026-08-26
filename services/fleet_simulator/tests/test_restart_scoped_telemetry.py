"""Restart regression for telemetry producer identities in the durable dashboard path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_fleet_simulator.fleet import Reading
from aerial_rescue_fleet_simulator.results import ResultStamp
from aerial_rescue_fleet_simulator.service import PublishOutcome, _publish
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

pytestmark = [pytest.mark.unit]


@dataclass
class _Publisher:
    payloads: list[bytes]

    def publish_unacknowledged(
        self,
        _topic: str,
        payload: bytes,
        _properties: Mapping[str, object],
    ) -> None:
        self.payloads.append(payload)


@dataclass(frozen=True)
class _RestartedStamps:
    mission_id: str

    def next_stamp(self, _producer: str) -> TelemetryStamp:
        return TelemetryStamp(
            event_id=f"event-{self.mission_id}",
            occurred_at="2026-08-26T15:00:00.000Z",
            sequence=0,
            correlation_id=self.mission_id,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
        )

    def next_result_stamp(
        self,
        _producer: str,
        correlation_id: str,
        causation_id: str,
    ) -> ResultStamp:
        return ResultStamp(
            event_id=f"result-{self.mission_id}",
            occurred_at="2026-08-26T15:00:00.000Z",
            sequence=0,
            correlation_id=correlation_id,
            causation_id=causation_id,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
        )


def test_a_successor_mission_does_not_reuse_a_pre_restart_telemetry_source() -> None:
    # Arrange
    reading = Reading(
        drone_id="drone-sim-07",
        latitude_microdegrees=45_000_000,
        longitude_microdegrees=-79_000_000,
        altitude_metres=80,
        heading_degrees=90,
        ground_speed_centimetres_per_second=700,
        battery_percent=85,
    )
    predecessor = _Publisher([])
    successor = _Publisher([])

    # Act
    outcomes = (
        _publish(predecessor, "mission-predecessor", reading, _RestartedStamps("run-one")),
        _publish(successor, "mission-successor", reading, _RestartedStamps("run-two")),
    )
    envelopes = tuple(
        decode_envelope(payload) for payload in (*predecessor.payloads, *successor.payloads)
    )

    # Assert
    assert outcomes == (PublishOutcome.PUBLISHED, PublishOutcome.PUBLISHED)
    assert tuple(envelope.sequence for envelope in envelopes) == ("000000000000000",) * 2
    assert envelopes[0].source != envelopes[1].source
    assert all(envelope.source.startswith("urn:aerial-rescue:drone-run:") for envelope in envelopes)
