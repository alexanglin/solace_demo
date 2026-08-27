"""Guaranteed connectivity and sector lifecycle records owned by the fleet run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_broker.messaging import MessagePublisher
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_domain.connectivity import ConnectivityState
from aerial_rescue_domain.sectors import SectorState

from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

_NO_PROPERTIES: Final[dict[str, object]] = {}
_CONNECTIVITY_PRODUCER: Final = "connectivity-lifecycle"
_SECTOR_PRODUCER: Final = "sector-lifecycle"


class LifecycleRefusal(Enum):
    """Why a lifecycle transition cannot become a publishable record."""

    SEQUENCE_RANGE = "producer sequence outside the representable range"
    UNPUBLISHABLE = "lifecycle record does not satisfy its envelope and topic binding"


class LifecycleError(FleetSimulatorError):
    """A lifecycle record refused before it reaches the guaranteed publisher."""


class LifecycleStampSource(Protocol):
    """Supply producer-scoped stamps without granting the fold a clock or identifier source."""

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return the next stamp for the named lifecycle producer."""


class FleetLifecyclePort(Protocol):
    """Publish only the lifecycle edges the fleet simulator owns."""

    def connectivity_changed(
        self, mission_id: str, drone_id: str, state: ConnectivityState
    ) -> bytes:
        """Publish one connectivity transition and return its canonical bytes."""

    def sector_changed(
        self,
        mission_id: str,
        sector_id: str,
        assigned_member_id: str,
        state: SectorState,
    ) -> bytes:
        """Publish one sector transition and return its canonical bytes."""


def _source(producer: str, producer_id: str) -> str:
    """Return one lifecycle source under its contract-bound producer kind."""
    return f"urn:aerial-rescue:{producer}:{producer_id}"


def _record(
    topic: Topic,
    source: str,
    stamp: TelemetryStamp,
    payload: dict[str, object],
) -> tuple[str, bytes]:
    """Build and read back one lifecycle CloudEvent through the contracts boundary."""
    rendered = sequence_text(stamp.sequence)
    if rendered is None:
        raise LifecycleError(LifecycleRefusal.SEQUENCE_RANGE, stamp.sequence)
    declared = event_type(topic)
    document = envelope_document(
        Envelope(
            id=stamp.event_id,
            source=source,
            type=declared,
            subject=topic.mission_id,
            time=stamp.occurred_at,
            dataschema=binding_for(declared).dataschema,
            sequence=rendered,
            correlation_id=stamp.correlation_id,
            traceparent=stamp.traceparent,
            data=payload,
        )
    )
    try:
        check_topic_binding(parse_envelope(document), topic)
    except ValueError as error:
        raise LifecycleError(LifecycleRefusal.UNPUBLISHABLE, declared) from error
    return (format_topic(topic), canonical_bytes(document))


@dataclass
class BrokerFleetLifecycle:
    """Publish fleet-owned lifecycle changes through an acknowledged broker port."""

    publisher: MessagePublisher
    producer_id: str
    stamps: LifecycleStampSource

    def connectivity_changed(
        self, mission_id: str, drone_id: str, state: ConnectivityState
    ) -> bytes:
        """Publish a schema-bound connectivity transition."""
        topic = Topic(
            Family.DRONE_EVENT,
            mission_id,
            {"droneId": drone_id, "eventType": "connectivity-changed"},
        )
        rendered, payload = _record(
            topic,
            _source(_CONNECTIVITY_PRODUCER, self.producer_id),
            self.stamps.next_stamp(_CONNECTIVITY_PRODUCER),
            {"missionId": mission_id, "droneId": drone_id, "connectivity": state.name},
        )
        self.publisher.publish(rendered, payload, _NO_PROPERTIES)
        return payload

    def sector_changed(
        self,
        mission_id: str,
        sector_id: str,
        assigned_member_id: str,
        state: SectorState,
    ) -> bytes:
        """Publish a schema-bound sector transition with its explicit holder."""
        topic = Topic(
            Family.SECTOR_EVENT,
            mission_id,
            {"sectorId": sector_id, "eventType": "lifecycle"},
        )
        rendered, payload = _record(
            topic,
            _source(_SECTOR_PRODUCER, self.producer_id),
            self.stamps.next_stamp(_SECTOR_PRODUCER),
            {
                "missionId": mission_id,
                "sectorId": sector_id,
                "state": state.name,
                "assignedMemberId": assigned_member_id,
            },
        )
        self.publisher.publish(rendered, payload, _NO_PROPERTIES)
        return payload
