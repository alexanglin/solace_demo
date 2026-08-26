"""Normalized dashboard events projected from validated application envelopes.

``docs/CONTRACTS.md`` names the server-sent-event stream and
``docs/adr/0067-normalized-dashboard-events-and-reduced-state.md`` records the shape it
carries: the projection's kind, its class, the mission, the envelope's instant, and the
projected fields. Nothing from the transport crosses this boundary, so the browser reads a
dashboard event without knowing the CloudEvents profile or the topic grammar.

Every event carries exactly one class, and the class decides whether a server under
back-pressure may discard the event. Telemetry is droppable because routine telemetry
already uses direct delivery and a newer position supersedes a stale one; every other class
is never dropped, and a full buffer closes the stream instead.

The reduced state is the fold of every dashboard event so far. It is the replay determinism
oracle of ``docs/adr/0009-isolated-side-effect-free-replay.md``, so it carries no wall-clock
instant, no event identifier, and no trace context: those legitimately differ between runs of
one seeded scenario. Collections are held in ascending byte order of their identifier rather
than keyed by it, because a canonical object key admits no hyphen and an identifier may carry
one.

This module is pure: it performs no input or output, reads no clock, and consumes no random
source.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts.digest import CANONICALIZATION_VERSION, Context, digest
from aerial_rescue_contracts.envelope import Envelope

MAX_BUFFERED_EVENTS: Final = 256
"""Dashboard events held per server-sent-event client; see ``docs/operating-parameters.md``."""

MISSION_KEY: Final = "missionId"

VIEW_VERSION: Final = 1
"""The reduced state's shape version, carried inside the hashed bytes."""


class EventClass(Enum):
    """What kind of change a dashboard event reports, and so whether it may be dropped."""

    TELEMETRY = "routine position, battery, and attitude"
    CONNECTIVITY = "a drone's link state changed"
    MISSION = "mission or sector lifecycle"
    COMMAND = "a command was issued, acknowledged, or resulted"
    EVIDENCE = "an observation, a fusion result, or an abstention"
    APPROVAL = "an approval was requested, decided, consumed, or denied"
    AUDIT = "an append-only audit record"


DROPPABLE_CLASSES: Final = frozenset({EventClass.TELEMETRY})
"""The only class a full per-client buffer may discard (ADR-0067)."""


class ViewRefusal(Enum):
    """Why an envelope does not become a dashboard event."""

    UNPROJECTED = "event type has no dashboard projection"
    MISSION_MISMATCH = "event belongs to a mission other than the state's"
    FIELD_FORM = "projected field is absent or outside its type"


class ViewError(ValueError):
    """An envelope that does not project, carrying the refusal as structured data."""

    def __init__(self, refusal: ViewRefusal, attribute: str, value: object) -> None:
        """Record the refusal, the member at fault, and the value it carried."""
        super().__init__(f"{refusal.value}: {attribute}={value!r}")
        self.refusal = refusal
        self.attribute = attribute
        self.value = value


@dataclass(frozen=True)
class Projection:
    """The dashboard kind and class an application event type projects to."""

    kind: str
    event_class: EventClass


PROJECTIONS: Final[Mapping[str, Projection]] = {
    "aerial-rescue.v1.drone.telemetry": Projection("droneTelemetry", EventClass.TELEMETRY),
}
"""A new row lands together with its state rule, golden fixtures, and manifest entry."""


@dataclass(frozen=True)
class DashboardEvent:
    """One normalized change, carrying no transport member. ``time`` is presentation only."""

    kind: str
    event_class: EventClass
    mission: str
    time: str
    data: Mapping[str, object]


def droppable(event_class: EventClass) -> bool:
    """Return whether a full per-client buffer may discard an event of this class."""
    return event_class in DROPPABLE_CLASSES


def projection_for(event_type_value: str) -> Projection:
    """Return the projection of an event type, refusing a type nothing projects."""
    projection = PROJECTIONS.get(event_type_value)
    if projection is None:
        raise ViewError(ViewRefusal.UNPROJECTED, "type", event_type_value)
    return projection


def project(envelope: Envelope) -> DashboardEvent:
    """Project one validated envelope into the dashboard event it reports.

    Args:
        envelope: An envelope the profile has already accepted.

    Returns:
        The normalized event, carrying the mission once and no transport member.

    Raises:
        ViewError: The envelope's type has no projection.
    """
    projection = projection_for(envelope.type)
    data = {key: value for key, value in envelope.data.items() if key != MISSION_KEY}
    return DashboardEvent(
        projection.kind,
        projection.event_class,
        envelope.subject,
        envelope.time,
        data,
    )


@dataclass(frozen=True)
class Drone:
    """One drone as the dashboard holds it, in the integer units of the canonical profile."""

    drone_id: str
    latitude_microdegrees: int
    longitude_microdegrees: int
    battery_percent: int
    altitude_metres: int
    heading_degrees: int
    ground_speed_centimetres_per_second: int


@dataclass(frozen=True)
class DashboardState:
    """The fold of every dashboard event so far. ``drones`` is ordered by identifier."""

    mission: str | None = None
    drones: tuple[Drone, ...] = ()


EMPTY_STATE: Final = DashboardState()
"""The state before the first event; a mission is adopted from the first one folded."""


def _identifier(data: Mapping[str, object], key: str) -> str:
    """Return a projected string field, refusing an absent one or one of another type."""
    value = data.get(key)
    if type(value) is not str:
        raise ViewError(ViewRefusal.FIELD_FORM, key, value)
    return value


def _integer(data: Mapping[str, object], key: str) -> int:
    """Return a projected integer field, refusing an absent one, a boolean, or another type."""
    value = data.get(key)
    if type(value) is not int:
        raise ViewError(ViewRefusal.FIELD_FORM, key, value)
    return value


def _drone_from(data: Mapping[str, object]) -> Drone:
    """Read one drone from a telemetry projection, refusing the first field at fault."""
    return Drone(
        _identifier(data, "droneId"),
        _integer(data, "latitudeMicrodegrees"),
        _integer(data, "longitudeMicrodegrees"),
        _integer(data, "batteryPercent"),
        _integer(data, "altitudeMetres"),
        _integer(data, "headingDegrees"),
        _integer(data, "groundSpeedCentimetresPerSecond"),
    )


def _ordered(drones: tuple[Drone, ...]) -> tuple[Drone, ...]:
    """Return the drones in ascending byte order of their identifier, which the digest needs."""
    return tuple(sorted(drones, key=lambda drone: drone.drone_id.encode()))


def _apply_drone_telemetry(state: DashboardState, event: DashboardEvent) -> DashboardState:
    """Supersede the reporting drone's held reading and adopt the mission."""
    drone = _drone_from(event.data)
    others = tuple(held for held in state.drones if held.drone_id != drone.drone_id)
    return DashboardState(event.mission, _ordered((*others, drone)))


_STATE_RULES: Final[Mapping[str, Callable[[DashboardState, DashboardEvent], DashboardState]]] = {
    "droneTelemetry": _apply_drone_telemetry,
}
"""One rule per projection kind; a kind without one is refused rather than ignored."""


def apply(state: DashboardState, event: DashboardEvent) -> DashboardState:
    """Fold one dashboard event into the state.

    Args:
        state: The state as last carried forward.
        event: The event to fold.

    Returns:
        The state after the event, leaving the argument unchanged.

    Raises:
        ViewError: The kind has no state rule, the event belongs to another mission, or a
            projected field is absent or outside its type.
    """
    rule = _STATE_RULES.get(event.kind)
    if rule is None:
        raise ViewError(ViewRefusal.UNPROJECTED, "kind", event.kind)
    if state.mission is not None and state.mission != event.mission:
        raise ViewError(ViewRefusal.MISSION_MISMATCH, "mission", event.mission)
    return rule(state, event)


def reduce_events(events: Iterable[DashboardEvent]) -> DashboardState:
    """Fold a sequence of dashboard events, in order, onto the empty state."""
    state = EMPTY_STATE
    for event in events:
        state = apply(state, event)
    return state


def _drone_document(drone: Drone) -> dict[str, object]:
    """Return one drone as canonical members."""
    return {
        "altitudeMetres": drone.altitude_metres,
        "batteryPercent": drone.battery_percent,
        "droneId": drone.drone_id,
        "groundSpeedCentimetresPerSecond": drone.ground_speed_centimetres_per_second,
        "headingDegrees": drone.heading_degrees,
        "latitudeMicrodegrees": drone.latitude_microdegrees,
        "longitudeMicrodegrees": drone.longitude_microdegrees,
    }


def state_document(state: DashboardState) -> dict[str, object]:
    """Return the state as a canonical document, omitting a mission the state has not adopted."""
    document: dict[str, object] = {
        "canonicalizationVersion": CANONICALIZATION_VERSION,
        "drones": [_drone_document(drone) for drone in state.drones],
        "viewVersion": VIEW_VERSION,
    }
    if state.mission is not None:
        document["mission"] = state.mission
    return document


def state_digest(state: DashboardState) -> str:
    """Return the replay determinism digest of the state, under the replay-state context."""
    return digest(Context.REPLAY_STATE, state_document(state))
