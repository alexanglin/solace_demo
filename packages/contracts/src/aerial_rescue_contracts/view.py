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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Final, NoReturn, cast

from aerial_rescue_contracts.digest import Context, digest, matches
from aerial_rescue_contracts.envelope import Envelope
from aerial_rescue_contracts.instant import InstantError, parse_instant
from aerial_rescue_contracts.topics import IDENTIFIER_PATTERN

MAX_BUFFERED_EVENTS: Final = 256
"""Dashboard events held per server-sent-event client; see ``docs/operating-parameters.md``."""

MISSION_KEY: Final = "missionId"
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
LOWERCASE_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


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

    def __post_init__(self) -> None:
        """Snapshot the normalized payload so a caller cannot mutate reducer input later."""
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class OrderedDashboardEvent:
    """One normalized event paired with its durable audit ordinal."""

    audit_ordinal: int
    event: DashboardEvent


class Participation(Enum):
    """How a declared fleet member participates in this dashboard scenario."""

    SIMULATED = "SIMULATED"
    DECLARED_ONLY = "DECLARED_ONLY"


class MissionLifecycle(Enum):
    """The deliberately narrow lifecycle vocabulary of the dashboard slice."""

    PLANNED = "PLANNED"
    SEARCHING = "SEARCHING"
    EXHAUSTED = "EXHAUSTED"
    ABORTED = "ABORTED"


class Connectivity(Enum):
    """Explicit simulated-member connectivity; telemetry never infers this value."""

    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class SectorState(Enum):
    """Sector lifecycle owned only by the sector projection."""

    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    AT_RISK = "AT_RISK"
    SEARCHED = "SEARCHED"


@dataclass(frozen=True)
class Mission:
    """The current mission identity and reduced lifecycle."""

    identifier: str
    lifecycle: MissionLifecycle
    predecessor_identifier: str | None


@dataclass(frozen=True)
class Telemetry:
    """The latest schema-validated integer telemetry for one simulated member."""

    latitude_microdegrees: int
    longitude_microdegrees: int
    battery_percent: int
    altitude_metres: int
    heading_degrees: int
    ground_speed_centimetres_per_second: int


@dataclass(frozen=True)
class SimulatedFleetMember:
    """A member whose explicit connectivity and latest telemetry may be reduced."""

    identifier: str
    participation: Participation = field(default=Participation.SIMULATED, init=False)
    connectivity: Connectivity = Connectivity.CONNECTED
    telemetry: Telemetry | None = None


@dataclass(frozen=True)
class DeclaredOnlyFleetMember:
    """An external declaration that owns no connectivity or telemetry state."""

    identifier: str
    participation: Participation = field(default=Participation.DECLARED_ONLY, init=False)


type FleetMember = SimulatedFleetMember | DeclaredOnlyFleetMember


@dataclass(frozen=True)
class Sector:
    """The sole reduced authority for sector lifecycle and assignment."""

    identifier: str
    state: SectorState
    assigned_member_id: str | None


@dataclass(frozen=True)
class DashboardReducedState:
    """Tuple-backed deterministic mission state shared by live and replay folds."""

    current_mission: Mission | None
    fleet: tuple[FleetMember, ...]
    latest_audit_ordinal: int
    sectors: tuple[Sector, ...]


@dataclass(frozen=True)
class ReducerCheckpoint:
    """Reduced state plus the ordered event witnessing its latest ordinal."""

    state: DashboardReducedState
    latest_event_digest: str | None


@dataclass(frozen=True)
class PreparedMission:
    """Validated scenario identity used to initialize a deterministic checkpoint."""

    identifier: str
    predecessor_identifier: str | None
    simulated_member_ids: tuple[str, ...]
    declared_only_member_ids: tuple[str, ...]
    sector_ids: tuple[str, ...]


class ReducerRefusal(Enum):
    """Stable structured reasons the reducer or checkpoint boundary refuses input."""

    DUPLICATE_MEMBER = "prepared mission repeats a fleet member"
    DUPLICATE_SECTOR = "prepared mission repeats a sector"
    NONCANONICAL_ANCHOR_STATE = "anchor state is not the canonical reduced representation"
    WITNESS_FORM = "latest event witness is not lowercase SHA-256"
    ORDINAL_WITNESS = "latest ordinal and event witness disagree"
    ORDINAL_DIVERGENCE = "same ordinal carries different event content"
    ORDINAL_REGRESSION = "event ordinal precedes the reduced state"
    ORDINAL_GAP = "event ordinal is not the next durable audit position"
    MISSION_UNPREPARED = "no current mission can receive the event"
    MISSION_MISMATCH = "event belongs to another mission"
    UNKNOWN_MEMBER = "event targets no declared fleet member"
    DECLARED_ONLY_MEMBER = "declared-only member cannot own live state"
    UNKNOWN_SECTOR = "event targets no prepared sector"
    ASSIGNMENT_FORBIDDEN = "unassigned sector must carry a null assignment"
    ASSIGNMENT_REQUIRED = "active sector must carry an assignment"
    INVALID_ASSIGNEE = "sector assignee is not a simulated member"
    EVENT_DATA = "normalized event does not match its state rule"
    UNPROJECTED = "normalized event kind has no state rule"
    SERVER_DIGEST_FORM = "server replay-state digest is not lowercase SHA-256"
    SERVER_DIGEST_MISMATCH = "server replay-state digest does not match the local fold"


class ReducerError(ValueError):
    """Preparation failure with a stable refusal and offending member."""

    def __init__(self, refusal: ReducerRefusal, attribute: str, value: object) -> None:
        """Record the refusal without converting an invalid value."""
        super().__init__(f"{refusal.value}: {attribute}={value!r}")
        self.refusal = refusal
        self.attribute = attribute
        self.value = value


@dataclass(frozen=True)
class CheckpointAccepted:
    """A snapshot or replay anchor accepted as a reducer checkpoint."""

    checkpoint: ReducerCheckpoint


@dataclass(frozen=True)
class CheckpointRefused:
    """A snapshot or replay anchor refused without constructing a checkpoint."""

    refusal: ReducerRefusal
    attribute: str
    value: object


type CheckpointOutcome = CheckpointAccepted | CheckpointRefused


@dataclass(frozen=True)
class FoldApplied:
    """A successor event applied to a fresh immutable checkpoint."""

    checkpoint: ReducerCheckpoint


@dataclass(frozen=True)
class FoldDuplicate:
    """An exact same-ordinal event proved by the prior witness."""

    checkpoint: ReducerCheckpoint


@dataclass(frozen=True)
class FoldRefused:
    """An event refused while retaining the exact prior checkpoint object."""

    checkpoint: ReducerCheckpoint
    refusal: ReducerRefusal
    attribute: str
    value: object


type FoldOutcome = FoldApplied | FoldDuplicate | FoldRefused


EMPTY_REDUCED_STATE: Final = DashboardReducedState(None, (), 0, ())
EMPTY_CHECKPOINT: Final = ReducerCheckpoint(EMPTY_REDUCED_STATE, None)


class _ReductionProblemError(Exception):
    """Internal state-rule refusal converted into a total fold outcome."""

    def __init__(self, refusal: ReducerRefusal, attribute: str, value: object) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal
        self.attribute = attribute
        self.value = value


def _byte_key(identifier: str) -> bytes:
    """Return the contract's UTF-8 byte-order key for one identifier."""
    return identifier.encode("utf-8")


def _is_lowercase_sha256(value: object) -> bool:
    """Report whether a runtime value has the exact digest witness form."""
    return isinstance(value, str) and LOWERCASE_SHA256_PATTERN.fullmatch(value) is not None


def _duplicate(values: Iterable[str]) -> str | None:
    """Return the first repeated identifier, or ``None`` when all are distinct."""
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def prepare_checkpoint(prepared: PreparedMission) -> ReducerCheckpoint:
    """Create the canonical planned checkpoint for one validated scenario mission."""
    member_ids = (*prepared.simulated_member_ids, *prepared.declared_only_member_ids)
    repeated_member = _duplicate(member_ids)
    if repeated_member is not None:
        raise ReducerError(ReducerRefusal.DUPLICATE_MEMBER, "identifier", repeated_member)
    repeated_sector = _duplicate(prepared.sector_ids)
    if repeated_sector is not None:
        raise ReducerError(ReducerRefusal.DUPLICATE_SECTOR, "identifier", repeated_sector)
    members: tuple[FleetMember, ...] = tuple(
        sorted(
            (
                *(SimulatedFleetMember(identifier) for identifier in prepared.simulated_member_ids),
                *(
                    DeclaredOnlyFleetMember(identifier)
                    for identifier in prepared.declared_only_member_ids
                ),
            ),
            key=lambda member: _byte_key(member.identifier),
        )
    )
    sectors = tuple(
        Sector(identifier, SectorState.UNASSIGNED, None)
        for identifier in sorted(prepared.sector_ids, key=_byte_key)
    )
    state = DashboardReducedState(
        Mission(
            prepared.identifier,
            MissionLifecycle.PLANNED,
            prepared.predecessor_identifier,
        ),
        members,
        0,
        sectors,
    )
    return ReducerCheckpoint(state, None)


def _strictly_sorted_identifiers(items: Iterable[FleetMember | Sector]) -> bool:
    """Report whether identifier-bearing values are unique and in ascending byte order."""
    identifiers = tuple(item.identifier for item in items)
    return identifiers == tuple(sorted(set(identifiers), key=_byte_key))


def _noncanonical_anchor(state: DashboardReducedState) -> tuple[str, object] | None:
    """Return the first semantic defect that a wire schema cannot express."""
    fleet_order = None if _strictly_sorted_identifiers(state.fleet) else ("fleet", state.fleet)
    sector_order = (
        None if _strictly_sorted_identifiers(state.sectors) else ("sectors", state.sectors)
    )
    simulated = {
        member.identifier for member in state.fleet if isinstance(member, SimulatedFleetMember)
    }
    sector_semantics: tuple[str, object] | None = None
    for sector in state.sectors:
        if sector.state is SectorState.UNASSIGNED and sector.assigned_member_id is not None:
            sector_semantics = ("assignedMemberId", sector.assigned_member_id)
            break
        if sector.state is not SectorState.UNASSIGNED and sector.assigned_member_id is None:
            sector_semantics = ("assignedMemberId", None)
            break
        if sector.assigned_member_id is not None and sector.assigned_member_id not in simulated:
            sector_semantics = ("assignedMemberId", sector.assigned_member_id)
            break
    mission_semantics = (
        ("currentMission", None)
        if state.current_mission is None
        and (state.fleet or state.sectors or state.latest_audit_ordinal != 0)
        else None
    )
    return fleet_order or sector_order or sector_semantics or mission_semantics


def _anchor_digest_outcome(
    checkpoint: ReducerCheckpoint,
    expected_state_digest: str | None,
) -> CheckpointOutcome:
    """Verify an optional snapshot state digest after all anchor semantics."""
    if expected_state_digest is None:
        return CheckpointAccepted(checkpoint)
    if not _is_lowercase_sha256(expected_state_digest):
        return CheckpointRefused(
            ReducerRefusal.SERVER_DIGEST_FORM,
            "digest",
            expected_state_digest,
        )
    actual = state_digest(checkpoint.state)
    if not matches(expected_state_digest, actual):
        return CheckpointRefused(
            ReducerRefusal.SERVER_DIGEST_MISMATCH,
            "digest",
            expected_state_digest,
        )
    return CheckpointAccepted(checkpoint)


def _checkpoint_from_anchor(
    state: DashboardReducedState,
    latest_event_digest: str | None,
    expected_state_digest: str | None,
) -> CheckpointOutcome:
    """Validate one schema-accepted snapshot or replay anchor."""
    if latest_event_digest is not None and not _is_lowercase_sha256(latest_event_digest):
        return CheckpointRefused(
            ReducerRefusal.WITNESS_FORM,
            "latestEventDigest",
            latest_event_digest,
        )
    defect = _noncanonical_anchor(state)
    if defect is not None:
        attribute, value = defect
        return CheckpointRefused(ReducerRefusal.NONCANONICAL_ANCHOR_STATE, attribute, value)
    witness_required = state.latest_audit_ordinal != 0
    if witness_required != (latest_event_digest is not None):
        return CheckpointRefused(
            ReducerRefusal.ORDINAL_WITNESS,
            "latestEventDigest",
            latest_event_digest,
        )
    checkpoint = ReducerCheckpoint(state, latest_event_digest)
    return _anchor_digest_outcome(checkpoint, expected_state_digest)


def checkpoint_from_snapshot(
    state: DashboardReducedState,
    latest_event_digest: str | None,
    *,
    expected_state_digest: str | None = None,
) -> CheckpointOutcome:
    """Validate and anchor a dashboard snapshot without mutating prior state."""
    return _checkpoint_from_anchor(state, latest_event_digest, expected_state_digest)


def checkpoint_from_replay(
    state: DashboardReducedState,
    latest_event_digest: str | None,
    *,
    expected_state_digest: str | None = None,
) -> CheckpointOutcome:
    """Validate and anchor a replay bundle through the same production boundary."""
    return _checkpoint_from_anchor(state, latest_event_digest, expected_state_digest)


def ordered_event_document(ordered_event: OrderedDashboardEvent) -> dict[str, object]:
    """Return the exact versioned canonical document witnessing an ordered event."""
    event = ordered_event.event
    return {
        "canonicalizationVersion": 1,
        "auditOrdinal": ordered_event.audit_ordinal,
        "event": {
            "kind": event.kind,
            "eventClass": event.event_class.name,
            "mission": event.mission,
            "time": event.time,
            "data": dict(event.data),
        },
    }


def ordered_event_digest(ordered_event: OrderedDashboardEvent) -> str:
    """Return the ordered-dashboard-event witness for one normalized event."""
    return digest(Context.ORDERED_DASHBOARD_EVENT, ordered_event_document(ordered_event))


def _telemetry_document(telemetry: Telemetry) -> dict[str, int]:
    """Return one latest telemetry reading in the closed wire shape."""
    return {
        "latitudeMicrodegrees": telemetry.latitude_microdegrees,
        "longitudeMicrodegrees": telemetry.longitude_microdegrees,
        "batteryPercent": telemetry.battery_percent,
        "altitudeMetres": telemetry.altitude_metres,
        "headingDegrees": telemetry.heading_degrees,
        "groundSpeedCentimetresPerSecond": telemetry.ground_speed_centimetres_per_second,
    }


def _member_document(member: FleetMember) -> dict[str, object]:
    """Return one discriminated fleet member without manufacturing external state."""
    if isinstance(member, DeclaredOnlyFleetMember):
        return {
            "identifier": member.identifier,
            "participation": member.participation.value,
        }
    return {
        "identifier": member.identifier,
        "participation": member.participation.value,
        "connectivity": member.connectivity.value,
        "telemetry": (None if member.telemetry is None else _telemetry_document(member.telemetry)),
    }


def state_document(state: DashboardReducedState) -> dict[str, object]:
    """Return the canonical reduced-state document; checkpoint witness is excluded."""
    mission = state.current_mission
    return {
        "canonicalizationVersion": 1,
        "stateVersion": 1,
        "currentMission": (
            None
            if mission is None
            else {
                "identifier": mission.identifier,
                "lifecycle": mission.lifecycle.value,
                "predecessorIdentifier": mission.predecessor_identifier,
            }
        ),
        "fleet": [_member_document(member) for member in state.fleet],
        "latestAuditOrdinal": state.latest_audit_ordinal,
        "sectors": [
            {
                "identifier": sector.identifier,
                "state": sector.state.value,
                "assignedMemberId": sector.assigned_member_id,
            }
            for sector in state.sectors
        ],
    }


def state_digest(state: DashboardReducedState) -> str:
    """Return the domain-separated replay determinism digest for reduced state."""
    return digest(Context.REPLAY_STATE, state_document(state))


def _problem(refusal: ReducerRefusal, attribute: str, value: object) -> NoReturn:
    """Raise one internal reduction problem for conversion into a total outcome."""
    raise _ReductionProblemError(refusal, attribute, value)


def _closed_event_data(data: Mapping[str, object], required: tuple[str, ...]) -> None:
    """Require exact projected members in deterministic UTF-8 refusal order."""
    allowed = frozenset(required)
    unknown = sorted((name for name in data if name not in allowed), key=_byte_key)
    if unknown:
        name = unknown[0]
        _problem(ReducerRefusal.EVENT_DATA, name, data[name])
    for name in required:
        if name not in data:
            _problem(ReducerRefusal.EVENT_DATA, name, None)


def _require_event_class(event: DashboardEvent, expected: EventClass) -> None:
    """Require the class bound to a normalized kind."""
    if event.event_class is not expected:
        _problem(
            ReducerRefusal.EVENT_DATA,
            "eventClass",
            _event_class_value(event.event_class),
        )


def _event_class_value(value: object) -> object:
    """Return an enum name for diagnostics while retaining malformed raw values."""
    return value.name if isinstance(value, EventClass) else value


def _enum_member[EnumT: Enum](
    data: Mapping[str, object],
    name: str,
    enum_type: type[EnumT],
) -> EnumT:
    """Return one exact closed-vocabulary event member."""
    value = data[name]
    try:
        return enum_type(value)
    except TypeError, ValueError:
        _problem(ReducerRefusal.EVENT_DATA, name, value)


def _is_identifier(value: object) -> bool:
    """Report whether a runtime value has the canonical identifier form."""
    return isinstance(value, str) and re.fullmatch(IDENTIFIER_PATTERN, value) is not None


def _event_identifier(data: Mapping[str, object], name: str) -> str:
    """Return one exact identifier event member without coercion."""
    value = data[name]
    if not _is_identifier(value):
        _problem(ReducerRefusal.EVENT_DATA, name, value)
    return cast(str, value)


def _bounded_integer(
    data: Mapping[str, object],
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    """Return one exact integer event member inside its schema range."""
    value = data[name]
    if type(value) is not int or not minimum <= value <= maximum:
        _problem(ReducerRefusal.EVENT_DATA, name, value)
    return value


def _validate_mission_event(event: DashboardEvent) -> None:
    """Validate the normalized mission-lifecycle boundary."""
    _require_event_class(event, EventClass.MISSION)
    _closed_event_data(event.data, ("lifecycle",))
    _enum_member(event.data, "lifecycle", MissionLifecycle)


def _validate_connectivity_event(event: DashboardEvent) -> None:
    """Validate the normalized connectivity boundary."""
    _require_event_class(event, EventClass.CONNECTIVITY)
    _closed_event_data(event.data, ("droneId", "connectivity"))
    _event_identifier(event.data, "droneId")
    _enum_member(event.data, "connectivity", Connectivity)


def _validate_telemetry_event(event: DashboardEvent) -> None:
    """Validate the normalized integer telemetry boundary."""
    fields = (
        "droneId",
        "latitudeMicrodegrees",
        "longitudeMicrodegrees",
        "batteryPercent",
        "altitudeMetres",
        "headingDegrees",
        "groundSpeedCentimetresPerSecond",
    )
    _require_event_class(event, EventClass.TELEMETRY)
    _closed_event_data(event.data, fields)
    _event_identifier(event.data, "droneId")
    _bounded_integer(event.data, "latitudeMicrodegrees", -90_000_000, 90_000_000)
    _bounded_integer(event.data, "longitudeMicrodegrees", -180_000_000, 180_000_000)
    _bounded_integer(event.data, "batteryPercent", 0, 100)
    _bounded_integer(event.data, "altitudeMetres", -500, 20_000)
    _bounded_integer(event.data, "headingDegrees", 0, 359)
    _bounded_integer(event.data, "groundSpeedCentimetresPerSecond", 0, 10_000)


def _validate_sector_event(event: DashboardEvent) -> None:
    """Validate the normalized sector variant before roster semantics."""
    _require_event_class(event, EventClass.MISSION)
    _closed_event_data(event.data, ("sectorId", "state", "assignedMemberId"))
    _event_identifier(event.data, "sectorId")
    sector_state = _enum_member(event.data, "state", SectorState)
    assignee = event.data["assignedMemberId"]
    if sector_state is SectorState.UNASSIGNED:
        if assignee is not None:
            _problem(ReducerRefusal.ASSIGNMENT_FORBIDDEN, "assignedMemberId", assignee)
    elif assignee is None:
        _problem(ReducerRefusal.ASSIGNMENT_REQUIRED, "assignedMemberId", None)
    else:
        _event_identifier(event.data, "assignedMemberId")


type BoundaryValidator = Callable[[DashboardEvent], None]

BOUNDARY_VALIDATORS: Final[Mapping[str, BoundaryValidator]] = {
    "missionLifecycle": _validate_mission_event,
    "connectivityChanged": _validate_connectivity_event,
    "droneTelemetry": _validate_telemetry_event,
    "sectorLifecycle": _validate_sector_event,
}


def _validate_ordered_event_boundary(ordered_event: OrderedDashboardEvent) -> None:
    """Validate the complete normalized event before order, witness, or state semantics."""
    ordinal = ordered_event.audit_ordinal
    if type(ordinal) is not int or not 1 <= ordinal <= MAX_SAFE_INTEGER:
        _problem(ReducerRefusal.EVENT_DATA, "auditOrdinal", ordinal)
    event = ordered_event.event
    if not isinstance(event.kind, str):
        _problem(ReducerRefusal.UNPROJECTED, "kind", event.kind)
    validator = BOUNDARY_VALIDATORS.get(event.kind)
    if validator is None:
        _problem(ReducerRefusal.UNPROJECTED, "kind", event.kind)
    if not _is_identifier(event.mission):
        _problem(ReducerRefusal.EVENT_DATA, "mission", event.mission)
    try:
        parse_instant(event.time)
    except InstantError:
        _problem(ReducerRefusal.EVENT_DATA, "time", event.time)
    validator(event)


def _replace_member(
    fleet: tuple[FleetMember, ...],
    index: int,
    member: SimulatedFleetMember,
) -> tuple[FleetMember, ...]:
    """Return a tuple replacing exactly one simulated member."""
    return (*fleet[:index], member, *fleet[index + 1 :])


def _simulated_target(
    state: DashboardReducedState,
    identifier: str,
) -> tuple[int, SimulatedFleetMember]:
    """Return a simulated target or raise the explicit roster refusal."""
    for index, member in enumerate(state.fleet):
        if member.identifier == identifier:
            if isinstance(member, DeclaredOnlyFleetMember):
                _problem(ReducerRefusal.DECLARED_ONLY_MEMBER, "droneId", identifier)
            return index, member
    _problem(ReducerRefusal.UNKNOWN_MEMBER, "droneId", identifier)


def _reduce_mission_lifecycle(
    state: DashboardReducedState,
    event: DashboardEvent,
) -> DashboardReducedState:
    """Reduce the mission lifecycle without touching fleet or sectors."""
    lifecycle = MissionLifecycle(event.data["lifecycle"])
    mission = cast(Mission, state.current_mission)
    return replace(state, current_mission=replace(mission, lifecycle=lifecycle))


def _reduce_connectivity(
    state: DashboardReducedState,
    event: DashboardEvent,
) -> DashboardReducedState:
    """Reduce explicit connectivity for one simulated member."""
    identifier = cast(str, event.data["droneId"])
    index, member = _simulated_target(state, identifier)
    connectivity = Connectivity(event.data["connectivity"])
    return replace(
        state,
        fleet=_replace_member(state.fleet, index, replace(member, connectivity=connectivity)),
    )


def _reduce_telemetry(
    state: DashboardReducedState,
    event: DashboardEvent,
) -> DashboardReducedState:
    """Supersede one latest telemetry reading without inferring connectivity."""
    identifier = cast(str, event.data["droneId"])
    index, member = _simulated_target(state, identifier)
    telemetry = Telemetry(
        cast(int, event.data["latitudeMicrodegrees"]),
        cast(int, event.data["longitudeMicrodegrees"]),
        cast(int, event.data["batteryPercent"]),
        cast(int, event.data["altitudeMetres"]),
        cast(int, event.data["headingDegrees"]),
        cast(int, event.data["groundSpeedCentimetresPerSecond"]),
    )
    return replace(
        state,
        fleet=_replace_member(state.fleet, index, replace(member, telemetry=telemetry)),
    )


def _sector_index(state: DashboardReducedState, identifier: str) -> int:
    """Return a prepared sector index or raise its explicit refusal."""
    for index, sector in enumerate(state.sectors):
        if sector.identifier == identifier:
            return index
    _problem(ReducerRefusal.UNKNOWN_SECTOR, "sectorId", identifier)


def _simulated_assignee(state: DashboardReducedState, identifier: str) -> bool:
    """Report whether the assignment target is one of the simulated members."""
    return any(
        member.identifier == identifier and isinstance(member, SimulatedFleetMember)
        for member in state.fleet
    )


def _reduce_sector(
    state: DashboardReducedState,
    event: DashboardEvent,
) -> DashboardReducedState:
    """Reduce sector lifecycle and assignment at the sole assignment authority."""
    identifier = cast(str, event.data["sectorId"])
    index = _sector_index(state, identifier)
    sector_state = SectorState(event.data["state"])
    assignee = cast(str | None, event.data["assignedMemberId"])
    if assignee is not None and not _simulated_assignee(state, assignee):
        _problem(ReducerRefusal.INVALID_ASSIGNEE, "assignedMemberId", assignee)
    sector = Sector(identifier, sector_state, assignee)
    sectors = (*state.sectors[:index], sector, *state.sectors[index + 1 :])
    return replace(state, sectors=sectors)


type StateRule = Callable[[DashboardReducedState, DashboardEvent], DashboardReducedState]

STATE_RULES: Final[Mapping[str, StateRule]] = {
    "missionLifecycle": _reduce_mission_lifecycle,
    "connectivityChanged": _reduce_connectivity,
    "droneTelemetry": _reduce_telemetry,
    "sectorLifecycle": _reduce_sector,
}


def _refused(
    checkpoint: ReducerCheckpoint,
    refusal: ReducerRefusal,
    attribute: str,
    value: object,
) -> FoldRefused:
    """Build a rollback outcome retaining the exact prior checkpoint."""
    return FoldRefused(checkpoint, refusal, attribute, value)


def _verify_server_digest(
    prior: ReducerCheckpoint,
    outcome: FoldApplied | FoldDuplicate,
    expected_state_digest: str | None,
) -> FoldOutcome:
    """Verify the server state identity last, rolling back on malformed or unequal input."""
    if expected_state_digest is None:
        return outcome
    if not _is_lowercase_sha256(expected_state_digest):
        return _refused(
            prior,
            ReducerRefusal.SERVER_DIGEST_FORM,
            "digest",
            expected_state_digest,
        )
    actual = state_digest(outcome.checkpoint.state)
    if not matches(expected_state_digest, actual):
        return _refused(
            prior,
            ReducerRefusal.SERVER_DIGEST_MISMATCH,
            "digest",
            expected_state_digest,
        )
    return outcome


def _fold_ordinal_control(
    checkpoint: ReducerCheckpoint,
    ordered_event: OrderedDashboardEvent,
    expected_state_digest: str | None,
) -> FoldOutcome | None:
    """Resolve duplicate, regression, and gap outcomes after boundary validation."""
    ordinal = ordered_event.audit_ordinal
    current_ordinal = checkpoint.state.latest_audit_ordinal
    if ordinal == current_ordinal:
        incoming_digest = ordered_event_digest(ordered_event)
        if checkpoint.latest_event_digest is not None and matches(
            checkpoint.latest_event_digest, incoming_digest
        ):
            duplicate = FoldDuplicate(checkpoint)
            return _verify_server_digest(checkpoint, duplicate, expected_state_digest)
        return _refused(
            checkpoint,
            ReducerRefusal.ORDINAL_DIVERGENCE,
            "auditOrdinal",
            ordinal,
        )
    if ordinal < current_ordinal:
        return _refused(checkpoint, ReducerRefusal.ORDINAL_REGRESSION, "auditOrdinal", ordinal)
    if ordinal != current_ordinal + 1:
        return _refused(checkpoint, ReducerRefusal.ORDINAL_GAP, "auditOrdinal", ordinal)
    return None


def _checkpoint_fold_refusal(checkpoint: ReducerCheckpoint) -> FoldRefused | None:
    """Return a structured fold refusal when the supplied checkpoint is not a valid anchor."""
    outcome = _checkpoint_from_anchor(
        checkpoint.state,
        checkpoint.latest_event_digest,
        None,
    )
    if isinstance(outcome, CheckpointRefused):
        return _refused(checkpoint, outcome.refusal, outcome.attribute, outcome.value)
    return None


def _fold_preflight_refusal(
    checkpoint: ReducerCheckpoint,
    ordered_event: OrderedDashboardEvent,
) -> FoldRefused | None:
    """Validate event boundary first, then checkpoint anchor semantics."""
    try:
        _validate_ordered_event_boundary(ordered_event)
    except _ReductionProblemError as problem:
        return _refused(checkpoint, problem.refusal, problem.attribute, problem.value)
    return _checkpoint_fold_refusal(checkpoint)


def fold_ordered_event(
    checkpoint: ReducerCheckpoint,
    ordered_event: OrderedDashboardEvent,
    *,
    expected_state_digest: str | None = None,
) -> FoldOutcome:
    """Apply, deduplicate, or refuse one audit-ordered event as a pure total function."""
    preflight_refusal = _fold_preflight_refusal(checkpoint, ordered_event)
    if preflight_refusal is not None:
        return preflight_refusal
    ordinal_outcome = _fold_ordinal_control(
        checkpoint,
        ordered_event,
        expected_state_digest,
    )
    if ordinal_outcome is not None:
        return ordinal_outcome
    mission = checkpoint.state.current_mission
    if mission is None:
        return _refused(checkpoint, ReducerRefusal.MISSION_UNPREPARED, "mission", None)
    event = ordered_event.event
    if event.mission != mission.identifier:
        return _refused(
            checkpoint,
            ReducerRefusal.MISSION_MISMATCH,
            "mission",
            event.mission,
        )
    rule = STATE_RULES[event.kind]
    try:
        reduced = rule(checkpoint.state, event)
    except _ReductionProblemError as problem:
        return _refused(
            checkpoint,
            problem.refusal,
            problem.attribute,
            problem.value,
        )
    witness = ordered_event_digest(ordered_event)
    successor = replace(reduced, latest_audit_ordinal=ordered_event.audit_ordinal)
    applied = FoldApplied(ReducerCheckpoint(successor, witness))
    return _verify_server_digest(checkpoint, applied, expected_state_digest)


def is_timeline_event(event: DashboardEvent) -> bool:
    """Return whether a normalized event belongs in the meaningful operator timeline."""
    return event.event_class is not EventClass.TELEMETRY


def timeline_from_events(
    events: Iterable[OrderedDashboardEvent],
) -> tuple[OrderedDashboardEvent, ...]:
    """Retain non-telemetry normalized events in their supplied audit order."""
    return tuple(ordered for ordered in events if is_timeline_event(ordered.event))


def replace_timeline_from_snapshot(
    events: Iterable[OrderedDashboardEvent],
) -> tuple[OrderedDashboardEvent, ...]:
    """Copy, sort, deduplicate, and remove telemetry from a snapshot timeline."""
    ordered = sorted(events, key=lambda item: item.audit_ordinal)
    retained: list[OrderedDashboardEvent] = []
    seen_ordinals: set[int] = set()
    for item in ordered:
        if item.audit_ordinal not in seen_ordinals and is_timeline_event(item.event):
            retained.append(item)
            seen_ordinals.add(item.audit_ordinal)
    return tuple(retained)


def append_meaningful_timeline_event(
    timeline: tuple[OrderedDashboardEvent, ...],
    ordered_event: OrderedDashboardEvent,
) -> tuple[OrderedDashboardEvent, ...]:
    """Insert one unique meaningful suffix event without mutating presentation history."""
    if not is_timeline_event(ordered_event.event) or any(
        item.audit_ordinal == ordered_event.audit_ordinal for item in timeline
    ):
        return timeline
    return tuple(sorted((*timeline, ordered_event), key=lambda item: item.audit_ordinal))


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
