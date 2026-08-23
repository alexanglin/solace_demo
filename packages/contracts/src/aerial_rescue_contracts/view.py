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

This module is pure: it performs no input or output, reads no clock, and consumes no random
source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts.envelope import Envelope

MAX_BUFFERED_EVENTS: Final = 256
"""Dashboard events held per server-sent-event client; see ``docs/operating-parameters.md``."""

MISSION_KEY: Final = "missionId"


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
