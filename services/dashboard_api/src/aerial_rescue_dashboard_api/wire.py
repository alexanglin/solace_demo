"""Strict service-local twins of dashboard and scenario-control wire schemas."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal, Self

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.instant import parse_instant
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    model_validator,
)

_SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/"
_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    validate_by_alias=True,
    validate_by_name=False,
    serialize_by_alias=True,
)
_ROOT_MODEL_CONFIG = ConfigDict(
    frozen=True,
    strict=True,
    validate_by_alias=True,
    validate_by_name=False,
    serialize_by_alias=True,
)


def _calendar_instant(value: str) -> str:
    """Require an instant to name a real calendar date."""
    parse_instant(value)
    return value


def _strict_literal_one(value: object) -> object:
    """Keep a JSON boolean from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "Input should be the integer 1"
        raise ValueError(message)
    return value


def _require_ordinal_witness(latest_audit_ordinal: int, latest_event_digest: str | None) -> None:
    """Require a checkpoint witness exactly when its state has accepted an event."""
    if (latest_audit_ordinal == 0) != (latest_event_digest is None):
        message = "latestEventDigest must be null exactly when latestAuditOrdinal is zero"
        raise ValueError(message)


type _Identifier = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$",
        max_length=64,
    ),
]
type _Kind = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=32),
]
type _Instant = Annotated[str, AfterValidator(_calendar_instant)]
type _Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type _NonEmptyString = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
type _Cursor = _NonEmptyString
type _SafePositiveInteger = Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]
type _SafeNonNegativeInteger = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
type _Latitude = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
type _Longitude = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]
type _Percent = Annotated[int, Field(ge=0, le=100)]
type _Altitude = Annotated[int, Field(ge=-500, le=20_000)]
type _Heading = Annotated[int, Field(ge=0, le=359)]
type _GroundSpeed = Annotated[int, Field(ge=0, le=10_000)]
type _StrictOne = Annotated[Literal[1], BeforeValidator(_strict_literal_one)]
type _MissionLifecycle = Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]
type _Connectivity = Literal["CONNECTED", "DEGRADED", "OFFLINE"]
type _SectorState = Literal["UNASSIGNED", "ASSIGNED", "AT_RISK", "SEARCHED"]


class _WireModel(BaseModel):
    model_config = _MODEL_CONFIG


class _Bootstrap(_WireModel):
    bootstrap_version: Literal["dashboard-bootstrap/v1"] = Field(alias="bootstrapVersion")
    bearer: _NonEmptyString
    runtime_id: _Identifier = Field(alias="runtimeId")


class _TelemetryReading(_WireModel):
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")
    battery_percent: _Percent = Field(alias="batteryPercent")
    altitude_metres: _Altitude = Field(alias="altitudeMetres")
    heading_degrees: _Heading = Field(alias="headingDegrees")
    ground_speed_centimetres_per_second: _GroundSpeed = Field(
        alias="groundSpeedCentimetresPerSecond"
    )


class _TelemetryData(_TelemetryReading):
    drone_id: _Identifier = Field(alias="droneId")


class _DroneTelemetryEvent(_WireModel):
    kind: Literal["droneTelemetry"]
    event_class: Literal["TELEMETRY"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _TelemetryData


class _ConnectivityData(_WireModel):
    drone_id: _Identifier = Field(alias="droneId")
    connectivity: _Connectivity


class _ConnectivityChangedEvent(_WireModel):
    kind: Literal["connectivityChanged"]
    event_class: Literal["CONNECTIVITY"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _ConnectivityData


class _MissionLifecycleData(_WireModel):
    lifecycle: _MissionLifecycle


class _MissionLifecycleEvent(_WireModel):
    kind: Literal["missionLifecycle"]
    event_class: Literal["MISSION"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _MissionLifecycleData


class _SectorLifecycleData(_WireModel):
    sector_id: _Identifier = Field(alias="sectorId")
    state: _SectorState
    assigned_member_id: _Identifier | None = Field(alias="assignedMemberId")


class _SectorLifecycleEvent(_WireModel):
    kind: Literal["sectorLifecycle"]
    event_class: Literal["MISSION"] = Field(alias="eventClass")
    mission: _Identifier
    time: _Instant
    data: _SectorLifecycleData


type _DashboardEventValue = Annotated[
    _DroneTelemetryEvent
    | _ConnectivityChangedEvent
    | _MissionLifecycleEvent
    | _SectorLifecycleEvent,
    Field(discriminator="kind"),
]
type _TimelineEventValue = Annotated[
    _ConnectivityChangedEvent | _MissionLifecycleEvent | _SectorLifecycleEvent,
    Field(discriminator="kind"),
]


class _DashboardEvent(RootModel[_DashboardEventValue]):
    model_config = _ROOT_MODEL_CONFIG


class _OrderedDashboardEvent(_WireModel):
    audit_ordinal: _SafePositiveInteger = Field(alias="auditOrdinal")
    event: _DashboardEventValue


class _TimelineOrderedEvent(_WireModel):
    audit_ordinal: _SafePositiveInteger = Field(alias="auditOrdinal")
    event: _TimelineEventValue


class _DashboardEventFrame(_WireModel):
    frame_version: Literal["ordered-dashboard-event-frame/v1"] = Field(alias="frameVersion")
    cursor: _Cursor
    digest: _Digest
    event: _OrderedDashboardEvent


class _Mission(_WireModel):
    identifier: _Identifier
    lifecycle: _MissionLifecycle
    predecessor_identifier: _Identifier | None = Field(alias="predecessorIdentifier")


class _LatestTelemetry(_TelemetryReading):
    pass


class _SimulatedFleetMember(_WireModel):
    identifier: _Identifier
    participation: Literal["SIMULATED"]
    connectivity: _Connectivity
    telemetry: _LatestTelemetry | None


class _DeclaredOnlyFleetMember(_WireModel):
    identifier: _Identifier
    participation: Literal["DECLARED_ONLY"]


type _ReducedFleetMember = Annotated[
    _SimulatedFleetMember | _DeclaredOnlyFleetMember,
    Field(discriminator="participation"),
]


class _ReducedSector(_WireModel):
    identifier: _Identifier
    state: _SectorState
    assigned_member_id: _Identifier | None = Field(alias="assignedMemberId")


class _DashboardReducedState(_WireModel):
    canonicalization_version: _StrictOne = Field(alias="canonicalizationVersion")
    state_version: _StrictOne = Field(alias="stateVersion")
    current_mission: _Mission | None = Field(alias="currentMission")
    fleet: Annotated[list[_ReducedFleetMember], Field(max_length=23)]
    latest_audit_ordinal: _SafeNonNegativeInteger = Field(alias="latestAuditOrdinal")
    sectors: Annotated[list[_ReducedSector], Field(max_length=20)]


class _LiveRun(_WireModel):
    mode: Literal["degradedLive"]
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ReplayRun(_WireModel):
    mode: Literal["replay"]
    session_id: _Identifier = Field(alias="sessionId")


type _CurrentRun = Annotated[_LiveRun | _ReplayRun, Field(discriminator="mode")]


class _DashboardSnapshot(_WireModel):
    snapshot_version: Literal["dashboard-snapshot/v1"] = Field(alias="snapshotVersion")
    runtime_id: _Identifier = Field(alias="runtimeId")
    cursor: _Cursor
    digest: _Digest
    current_run: _CurrentRun | None = Field(alias="currentRun")
    state: _DashboardReducedState
    latest_event_digest: _Digest | None = Field(alias="latestEventDigest")
    timeline: Annotated[list[_TimelineOrderedEvent], Field(max_length=256)]

    @model_validator(mode="after")
    def validate_ordinal_witness(self) -> Self:
        """Reject a snapshot whose state ordinal and event witness disagree."""
        _require_ordinal_witness(self.state.latest_audit_ordinal, self.latest_event_digest)
        return self


class _DashboardError(_WireModel):
    error_version: Literal["dashboard-error/v1"] = Field(alias="errorVersion")
    error_code: Annotated[
        str,
        StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$", max_length=64),
    ] = Field(alias="errorCode")
    message: _NonEmptyString


class _Health(_WireModel):
    health_version: Literal["dashboard-health/v1"] = Field(alias="healthVersion")
    status: Literal["alive"]
    runtime_id: _Identifier = Field(alias="runtimeId")


class _Readiness(_WireModel):
    readiness_version: Literal["dashboard-readiness/v1"] = Field(alias="readinessVersion")
    mode: Literal["degradedLive", "replay"]
    ready: bool
    reasons: Annotated[list[_Kind], Field(max_length=20)]


class _ReplayIntegrity(_WireModel):
    integrity_version: Literal["dashboard-replay-integrity/v1"] = Field(alias="integrityVersion")
    algorithm: Literal["sha256"]
    checksum: _Digest
    expected_final_digest: _Digest = Field(alias="expectedFinalDigest")


class _ReplayBundle(_WireModel):
    bundle_version: Literal["dashboard-replay-bundle/v1"] = Field(alias="bundleVersion")
    session_id: _Identifier = Field(alias="sessionId")
    scenario_id: _Identifier = Field(alias="scenarioId")
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")
    initial_state: _DashboardReducedState = Field(alias="initialState")
    latest_event_digest: _Digest | None = Field(alias="latestEventDigest")
    events: Annotated[list[_OrderedDashboardEvent], Field(max_length=512)]
    integrity: _ReplayIntegrity

    @model_validator(mode="after")
    def validate_ordinal_witness(self) -> Self:
        """Reject a replay whose initial-state ordinal and event witness disagree."""
        _require_ordinal_witness(
            self.initial_state.latest_audit_ordinal,
            self.latest_event_digest,
        )
        return self


class _ResetRequest(_WireModel):
    pass


class _RosterCounts(_WireModel):
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")


class _LiveResetResponse(_RosterCounts):
    operation_version: Literal["dashboard-reset-response/v1"] = Field(alias="operationVersion")
    mode: Literal["degradedLive"]
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")
    predecessor_mission_id: _Identifier = Field(alias="predecessorMissionId")


class _ReplayResetResponse(_RosterCounts):
    operation_version: Literal["dashboard-reset-response/v1"] = Field(alias="operationVersion")
    mode: Literal["replay"]
    session_id: _Identifier = Field(alias="sessionId")


type _ResetResponseValue = Annotated[
    _LiveResetResponse | _ReplayResetResponse,
    Field(discriminator="mode"),
]


class _ResetResponse(RootModel[_ResetResponseValue]):
    model_config = _ROOT_MODEL_CONFIG


class _Vertex(_WireModel):
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


class _LastKnownLocation(_WireModel):
    label: _NonEmptyString
    latitude_microdegrees: _Latitude = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: _Longitude = Field(alias="longitudeMicrodegrees")


class _Polygon(_WireModel):
    vertices: Annotated[list[_Vertex], Field(min_length=4, max_length=256)]


class _SectorPolygon(_WireModel):
    identifier: _Identifier
    vertices: Annotated[list[_Vertex], Field(min_length=4, max_length=256)]


class _SimulatedCatalogMember(_WireModel):
    identifier: _Identifier
    participation: Literal["SIMULATED"]


class _DeclaredOnlyCatalogMember(_WireModel):
    identifier: _Identifier
    participation: Literal["DECLARED_ONLY"]
    role: _Kind
    execution_label: Literal["DECLARED ONLY — NOT EXECUTED"] = Field(alias="executionLabel")


type _CatalogMember = Annotated[
    _SimulatedCatalogMember | _DeclaredOnlyCatalogMember,
    Field(discriminator="participation"),
]


class _Scenario(_WireModel):
    identifier: _Identifier
    revision: _StrictOne
    title: _NonEmptyString
    summary: _NonEmptyString
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")
    search_area_square_metres: _SafePositiveInteger = Field(alias="searchAreaSquareMetres")
    last_known_location: _LastKnownLocation = Field(alias="lastKnownLocation")
    search_polygon: _Polygon = Field(alias="searchPolygon")
    sectors: Annotated[list[_SectorPolygon], Field(min_length=20, max_length=20)]
    members: Annotated[list[_CatalogMember], Field(min_length=23, max_length=23)]


class _ScenarioCatalog(_WireModel):
    catalog_version: Literal["scenario-catalog/v1"] = Field(alias="catalogVersion")
    scenarios: Annotated[list[_Scenario], Field(max_length=20)]


class _StartRequest(_WireModel):
    mode: Literal["degradedLive", "replay"]
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")


class _LiveStartResponse(_RosterCounts):
    operation_version: Literal["dashboard-start-response/v1"] = Field(alias="operationVersion")
    mode: Literal["degradedLive"]
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ReplayStartResponse(_RosterCounts):
    operation_version: Literal["dashboard-start-response/v1"] = Field(alias="operationVersion")
    mode: Literal["replay"]
    session_id: _Identifier = Field(alias="sessionId")


type _StartResponseValue = Annotated[
    _LiveStartResponse | _ReplayStartResponse,
    Field(discriminator="mode"),
]


class _StartResponse(RootModel[_StartResponseValue]):
    model_config = _ROOT_MODEL_CONFIG


class _StreamOverloaded(_WireModel):
    control_version: Literal["dashboard-stream-overloaded/v1"] = Field(alias="controlVersion")
    reason: Literal["NON_DROPPABLE_BUFFER_FULL"]


class _ScenarioControlStartRequest(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    scenario_id: _Identifier = Field(alias="scenarioId")
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ScenarioControlRunStatus(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    scenario_id: _Identifier = Field(alias="scenarioId")
    scenario_revision: _StrictOne = Field(alias="scenarioRevision")
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")
    state: _MissionLifecycle
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")
    completed_tick_count: _SafeNonNegativeInteger = Field(alias="completedTickCount")
    telemetry_publication_count: _SafeNonNegativeInteger = Field(alias="telemetryPublicationCount")


class _ScenarioControlCancelRequest(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    mission_id: _Identifier = Field(alias="missionId")
    run_id: _Identifier = Field(alias="runId")


class _ScenarioControlRefusal(_WireModel):
    control_version: _StrictOne = Field(alias="controlVersion")
    error_code: Literal[
        "HOST_INVALID",
        "AUTHENTICATION_FAILED",
        "UNSUPPORTED_MEDIA_TYPE",
        "BODY_TOO_LARGE",
        "CANONICAL_JSON_INVALID",
        "SCHEMA_INVALID",
        "PATH_BODY_MISMATCH",
        "RUN_CONFLICT",
        "RUN_NOT_FOUND",
        "CANCELLATION_NOT_ESTABLISHED",
        "SCENARIO_NOT_FOUND",
        "SCENARIO_REVISION_MISMATCH",
        "FLEET_UNAVAILABLE",
        "INTERNAL_FAILURE",
    ] = Field(alias="errorCode")
    message: _NonEmptyString


def _dashboard_schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}dashboard/{name}.schema.json"


def _rpc_schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}rpc/{name}.schema.json"


SERVER_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _dashboard_schema("bootstrap"): _Bootstrap,
        _dashboard_schema("dashboard-event-frame"): _DashboardEventFrame,
        _dashboard_schema("dashboard-event"): _DashboardEvent,
        _dashboard_schema("dashboard-reduced-state"): _DashboardReducedState,
        _dashboard_schema("dashboard-snapshot"): _DashboardSnapshot,
        _dashboard_schema("error"): _DashboardError,
        _dashboard_schema("health"): _Health,
        _dashboard_schema("ordered-dashboard-event"): _OrderedDashboardEvent,
        _dashboard_schema("readiness"): _Readiness,
        _dashboard_schema("replay-bundle"): _ReplayBundle,
        _dashboard_schema("replay-integrity"): _ReplayIntegrity,
        _dashboard_schema("reset-request"): _ResetRequest,
        _dashboard_schema("reset-response"): _ResetResponse,
        _dashboard_schema("scenario-catalog"): _ScenarioCatalog,
        _dashboard_schema("start-request"): _StartRequest,
        _dashboard_schema("start-response"): _StartResponse,
        _dashboard_schema("stream-overloaded"): _StreamOverloaded,
    }
)
CLIENT_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _rpc_schema("scenario-control-cancel-request"): _ScenarioControlCancelRequest,
        _rpc_schema("scenario-control-refusal"): _ScenarioControlRefusal,
        _rpc_schema("scenario-control-run-status"): _ScenarioControlRunStatus,
        _rpc_schema("scenario-control-start-request"): _ScenarioControlStartRequest,
    }
)
FILE_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType({})
BROWSER_ONLY_SCHEMA_IDS = frozenset(
    {
        _dashboard_schema("mutation-outcome"),
        _dashboard_schema("source-signal"),
    }
)


def parse_wire_document(schema_id: str, raw: str | bytes) -> BaseModel:
    """Canonical-decode and strictly validate one dashboard-owned wire document."""
    model = SERVER_MODEL_BY_SCHEMA_ID.get(schema_id) or CLIENT_MODEL_BY_SCHEMA_ID.get(schema_id)
    if model is None:
        message = f"schema is not owned by dashboard API: {schema_id}"
        raise ValueError(message)
    return model.model_validate(canonical.decode(raw), strict=True)
