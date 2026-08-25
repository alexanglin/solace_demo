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

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, NoReturn

from aerial_rescue_contracts.envelope import Envelope
from aerial_rescue_contracts.topics import IDENTIFIER_PATTERN

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
    MALFORMED_PAYLOAD = "event payload does not match its bound lifecycle contract"


class ViewError(ValueError):
    """An envelope that does not project, carrying the refusal as structured data."""

    def __init__(self, refusal: ViewRefusal, attribute: str, value: object) -> None:
        """Record the refusal, the member at fault, and the value it carried."""
        super().__init__(f"{refusal.value}: {attribute}={value!r}")
        self.refusal = refusal
        self.attribute = attribute
        self.value = value


PayloadValidator = Callable[[Mapping[str, object]], None]


def _accept_payload(_data: Mapping[str, object]) -> None:
    """Accept a payload whose projection predates lifecycle-specific validation."""


def _malformed(attribute: str, value: object) -> NoReturn:
    """Refuse one member of a lifecycle payload at the normalized-view boundary."""
    raise ViewError(ViewRefusal.MALFORMED_PAYLOAD, attribute, value)


def _closed_members(data: Mapping[str, object], required: tuple[str, ...]) -> None:
    """Require exactly the declared lifecycle members in deterministic refusal order."""
    allowed = frozenset(required)
    unknown = sorted((name for name in data if name not in allowed), key=lambda name: name.encode())
    if unknown:
        name = unknown[0]
        _malformed(name, data[name])
    for name in required:
        if name not in data:
            _malformed(name, None)


def _identifier(data: Mapping[str, object], name: str) -> str:
    """Return one identifier-form lifecycle member or refuse it."""
    value = data[name]
    if not isinstance(value, str) or re.fullmatch(IDENTIFIER_PATTERN, value) is None:
        _malformed(name, value)
    return value


def _choice(data: Mapping[str, object], name: str, allowed: frozenset[str]) -> str:
    """Return one closed-vocabulary lifecycle member or refuse it."""
    value = data[name]
    if not isinstance(value, str) or value not in allowed:
        _malformed(name, value)
    return value


def _validate_connectivity(data: Mapping[str, object]) -> None:
    """Validate the connectivity-change payload owned by ADR-0111."""
    _closed_members(data, ("missionId", "droneId", "connectivity"))
    _identifier(data, "missionId")
    _identifier(data, "droneId")
    _choice(data, "connectivity", frozenset({"CONNECTED", "DEGRADED", "OFFLINE"}))


def _validate_mission_lifecycle(data: Mapping[str, object]) -> None:
    """Validate the mission-lifecycle payload owned by ADR-0111."""
    _closed_members(data, ("missionId", "lifecycle"))
    _identifier(data, "missionId")
    _choice(data, "lifecycle", frozenset({"PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"}))


def _validate_sector_lifecycle(data: Mapping[str, object]) -> None:
    """Validate the sector lifecycle and its state-dependent assignment."""
    _closed_members(data, ("missionId", "sectorId", "state", "assignedMemberId"))
    _identifier(data, "missionId")
    _identifier(data, "sectorId")
    state = _choice(data, "state", frozenset({"UNASSIGNED", "ASSIGNED", "AT_RISK", "SEARCHED"}))
    assigned = data["assignedMemberId"]
    if state == "UNASSIGNED":
        if assigned is not None:
            _malformed("assignedMemberId", assigned)
    else:
        _identifier(data, "assignedMemberId")


@dataclass(frozen=True)
class Projection:
    """The dashboard kind and class an application event type projects to."""

    kind: str
    event_class: EventClass
    _validate_payload: PayloadValidator = field(default=_accept_payload, repr=False, compare=False)


PROJECTIONS: Final[Mapping[str, Projection]] = {
    "aerial-rescue.v1.drone.telemetry": Projection("droneTelemetry", EventClass.TELEMETRY),
    "aerial-rescue.v1.drone.event.connectivity-changed": Projection(
        "connectivityChanged", EventClass.CONNECTIVITY, _validate_connectivity
    ),
    "aerial-rescue.v1.mission.event.lifecycle": Projection(
        "missionLifecycle", EventClass.MISSION, _validate_mission_lifecycle
    ),
    "aerial-rescue.v1.sector.event.lifecycle": Projection(
        "sectorLifecycle", EventClass.MISSION, _validate_sector_lifecycle
    ),
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
    projection._validate_payload(envelope.data)
    if envelope.data.get(MISSION_KEY) != envelope.subject:
        _malformed(MISSION_KEY, envelope.data.get(MISSION_KEY))
    data = {key: value for key, value in envelope.data.items() if key != MISSION_KEY}
    return DashboardEvent(
        projection.kind,
        projection.event_class,
        envelope.subject,
        envelope.time,
        data,
    )
