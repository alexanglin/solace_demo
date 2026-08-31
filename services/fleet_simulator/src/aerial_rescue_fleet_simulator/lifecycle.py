"""Guaranteed connectivity and sector lifecycle records owned by the fleet run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_broker.messaging import MessagePublisher
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.digest import source_event_digest
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

from aerial_rescue_fleet_simulator import FleetSimulatorError, event_source
from aerial_rescue_fleet_simulator.fleet import FleetState
from aerial_rescue_fleet_simulator.scenario import FleetScenario, ordered_drones
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

_NO_PROPERTIES: Final[dict[str, object]] = {}
_CONNECTIVITY_PRODUCER: Final = "connectivity-lifecycle"
_SECTOR_PRODUCER: Final = "sector-lifecycle"
_SALIENT_PRODUCER: Final = "salient"
SOURCE_DIGEST_PROPERTY: Final = "aerial-rescue-source-event-digest"
"""The user property the Event Mesh Gateway reads the proposal binding from.

Every other record here publishes with no properties. A salient event without this one is
accepted by the broker and reaches no proposal, because the gateway's ``forward_context``
takes ``sourceEventDigest`` from it (``agent-mesh/configs/event-mesh-gateway.yaml``).
"""
SALIENT_OBSERVATION: Final = "artifact-sighting"
"""The one observation kind a simulated sweep reports, inside the topic grammar's kind rule."""


@dataclass(frozen=True)
class SalientObservation:
    """One observation a simulated drone judged worth an agent's attention.

    The position travels with the observation rather than beside it: an observation reported at
    another drone's coordinates is the defect this value makes hard to write.
    """

    observation: str
    latitude_microdegrees: int
    longitude_microdegrees: int
    detail: str


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

    def salient_observed(
        self, mission_id: str, drone_id: str, observed: SalientObservation
    ) -> bytes:
        """Publish one salient observation and return its canonical bytes."""


def _source(producer: str, producer_id: str) -> str:
    """Return one lifecycle source under its contract-bound producer kind."""
    return f"urn:aerial-rescue:{producer}:{producer_id}"


def _record(
    topic: Topic,
    source: str,
    stamp: TelemetryStamp,
    payload: dict[str, object],
) -> tuple[str, bytes, Envelope]:
    """Build and read back one lifecycle CloudEvent through the contracts boundary.

    The parsed envelope is returned as well as its bytes: a salient record binds a proposal by
    the digest of the whole accepted envelope, and recomputing it from the bytes would parse the
    same document twice.
    """
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
        envelope = parse_envelope(document)
        check_topic_binding(envelope, topic)
    except ValueError as error:
        raise LifecycleError(LifecycleRefusal.UNPUBLISHABLE, declared) from error
    return (format_topic(topic), canonical_bytes(document), envelope)


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
        rendered, payload, _envelope = _record(
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
        rendered, payload, _envelope = _record(
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

    def salient_observed(
        self, mission_id: str, drone_id: str, observed: SalientObservation
    ) -> bytes:
        """Publish one salient observation under the drone's own producer identity.

        Two bindings differ from every other record in this module, and both fail silently when
        wrong. The source is the drone rather than a lifecycle producer, because the evidence
        service compares it against ``urn:aerial-rescue:drone:{droneId}`` exactly and refuses
        anything else as invalid ingress. The publication carries the source-event digest as a user
        property, because that is where the Event Mesh Gateway reads the proposal binding from.

        The stamp is drawn from a producer key of its own rather than the drone identifier, which
        telemetry already uses for a stream published under a different source. One counter per
        source is what keeps the envelope's producer-scoped sequence meaningful. The key stays
        inside the identifier grammar because a stamp source may derive an event identifier from
        it, and a separator the grammar refuses would fail envelope validation rather than here.
        """
        topic = Topic(
            Family.DRONE_EVENT,
            mission_id,
            {"droneId": drone_id, "eventType": "salient"},
        )
        rendered, payload, envelope = _record(
            topic,
            event_source(drone_id),
            self.stamps.next_stamp(f"{_SALIENT_PRODUCER}-{drone_id}"),
            {
                "missionId": mission_id,
                "droneId": drone_id,
                "observation": observed.observation,
                "latitudeMicrodegrees": observed.latitude_microdegrees,
                "longitudeMicrodegrees": observed.longitude_microdegrees,
                "detail": observed.detail,
            },
        )
        self.publisher.publish(
            rendered, payload, {SOURCE_DIGEST_PROPERTY: source_event_digest(envelope)}
        )
        return payload


def _salient_observer(scenario: FleetScenario) -> str:
    """Return the one drone whose completed sweep reports the run's salient observation.

    Every sector reaches ``SEARCHED`` -- that is what exhausts the mission -- so an ungated trigger
    would report twenty observations, spend twenty model turns, and contradict the single placed
    artifact ``docs/LIMITATIONS.md`` describes. The scenario already singles out one drone through
    its heartbeat absences, so that drone is the observer: derived from validated scenario data
    rather than named by a constant this module chose. A scenario declaring no absence falls back to
    the holder of its first sector in identifier order, which is equally derived and equally stable.
    """
    absent = sorted(scenario.absent_heartbeats)
    if absent:
        return absent[0]
    return min(scenario.drones, key=lambda drone: drone.sector_id).drone_id


def publish_transitions(
    lifecycle: FleetLifecyclePort | None,
    scenario: FleetScenario,
    before: FleetState,
    after: FleetState,
) -> None:
    """Publish the fleet-owned connectivity, sector, and observation edges one tick crossed.

    Both compositions fold the same tick, so both derive their edges here rather than each
    comparing the two states its own way.
    """
    if lifecycle is None:
        return
    for drone in ordered_drones(scenario):
        connectivity_previous = before.drones[drone.drone_id].connectivity.state
        connectivity_reached = after.drones[drone.drone_id].connectivity.state
        if connectivity_reached is not connectivity_previous:
            lifecycle.connectivity_changed(
                scenario.mission_id, drone.drone_id, connectivity_reached
            )
    holder_by_sector = {drone.sector_id: drone.drone_id for drone in scenario.drones}
    observer = _salient_observer(scenario)
    for sector_id in sorted(after.sectors):
        sector_previous = before.sectors[sector_id].state
        sector_reached = after.sectors[sector_id].state
        if sector_reached is not sector_previous:
            lifecycle.sector_changed(
                scenario.mission_id,
                sector_id,
                holder_by_sector[sector_id],
                sector_reached,
            )
        searched = sector_reached is SectorState.SEARCHED
        if (
            searched
            and sector_previous is not sector_reached
            and holder_by_sector[sector_id] == observer
        ):
            position = after.drones[observer].state
            lifecycle.salient_observed(
                scenario.mission_id,
                observer,
                SalientObservation(
                    observation=SALIENT_OBSERVATION,
                    latitude_microdegrees=position.latitude_microdegrees,
                    longitude_microdegrees=position.longitude_microdegrees,
                    detail=f"A high-visibility artifact held across the sweep of {sector_id}.",
                ),
            )
