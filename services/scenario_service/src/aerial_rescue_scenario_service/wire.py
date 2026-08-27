"""Strict scenario-service models for committed file and private-control schemas."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal

from aerial_rescue_contracts import canonical
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/"
MAX_WIRE_DOCUMENT_BYTES: Final = 256 * 1024
MAX_SCENARIO_CATALOG_BYTES: Final = 512 * 1024
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$"),
]
DefinitionPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=4096,
        pattern=r"^v1/(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])\.r[1-9][0-9]*\.json$",
    ),
]
LowercaseSha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonemptyText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
Kind = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=32),
]
SafePositiveInteger = Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
SafeNonnegativeInteger = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
LatitudeMicrodegrees = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
LongitudeMicrodegrees = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]
AltitudeMetres = Annotated[int, Field(ge=-500, le=20_000)]
HeadingDegrees = Annotated[int, Field(ge=0, le=359)]
GroundSpeedCentimetresPerSecond = Annotated[int, Field(ge=0, le=10_000)]
BatteryPermille = Annotated[int, Field(ge=0, le=1000)]
NorthMicrodegreesPerTick = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
EastMicrodegreesPerTick = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]


def _strict_literal_one(value: object) -> object:
    """Keep a JSON boolean from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "Input should be the integer 1"
        raise ValueError(message)
    return value


StrictOne = Annotated[Literal[1], BeforeValidator(_strict_literal_one)]


class _WireModel(BaseModel):
    """Apply the strict closed alias-only policy to every scenario-service twin."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )


class ScenarioCatalogEntry(_WireModel):
    """One confined definition reference in the version-one catalog."""

    identifier: Identifier
    revision: SafePositiveInteger
    definition_path: DefinitionPath = Field(alias="definitionPath")
    definition_sha256: LowercaseSha256 = Field(alias="definitionSha256")


class ScenarioCatalog(_WireModel):
    """The bounded inventory of committed scenario definitions."""

    catalog_version: StrictOne = Field(alias="catalogVersion")
    scenarios: Annotated[list[ScenarioCatalogEntry], Field(min_length=1, max_length=20)]


class LastKnownLocation(_WireModel):
    """Synthetic presentation metadata for the last-known point."""

    label: NonemptyText
    latitude_microdegrees: LatitudeMicrodegrees = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: LongitudeMicrodegrees = Field(alias="longitudeMicrodegrees")


class Vertex(_WireModel):
    """One integer coordinate in a committed presentation polygon."""

    latitude_microdegrees: LatitudeMicrodegrees = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: LongitudeMicrodegrees = Field(alias="longitudeMicrodegrees")


VertexList = Annotated[list[Vertex], Field(min_length=4, max_length=256)]


class Polygon(_WireModel):
    """A bounded polygon whose closure remains explicit in the document."""

    vertices: VertexList


class ScenarioSector(_WireModel):
    """One presentation-sector geometry."""

    identifier: Identifier
    vertices: VertexList


class SimulatedMember(_WireModel):
    """One lossless deterministic simulator member in a scenario definition."""

    identifier: Identifier
    participation: Literal["SIMULATED_DRONE"]
    sector_id: Identifier = Field(alias="sectorId")
    latitude_microdegrees: LatitudeMicrodegrees = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: LongitudeMicrodegrees = Field(alias="longitudeMicrodegrees")
    altitude_metres: AltitudeMetres = Field(alias="altitudeMetres")
    heading_degrees: HeadingDegrees = Field(alias="headingDegrees")
    ground_speed_centimetres_per_second: GroundSpeedCentimetresPerSecond = Field(
        alias="groundSpeedCentimetresPerSecond"
    )
    battery_permille: BatteryPermille = Field(alias="batteryPermille")
    north_microdegrees_per_tick: NorthMicrodegreesPerTick = Field(alias="northMicrodegreesPerTick")
    east_microdegrees_per_tick: EastMicrodegreesPerTick = Field(alias="eastMicrodegreesPerTick")
    battery_drain_permille_per_tick: BatteryPermille = Field(alias="batteryDrainPermillePerTick")


class DeclaredOnlyMember(_WireModel):
    """One truthful descriptor that can never acquire telemetry or connectivity."""

    identifier: Identifier
    participation: Literal["DECLARED_ONLY"]
    role: Literal["vision", "navigation", "communications"]
    execution_label: Literal["DECLARED ONLY — NOT EXECUTED"] = Field(alias="executionLabel")


ScenarioMember = Annotated[
    SimulatedMember | DeclaredOnlyMember,
    Field(discriminator="participation"),
]


class ConnectivityThresholds(_WireModel):
    """Explicit integer thresholds consumed by the connectivity state machine."""

    misses_to_degraded: SafePositiveInteger = Field(alias="missesToDegraded")
    misses_to_offline: SafePositiveInteger = Field(alias="missesToOffline")
    heartbeats_to_recover: SafePositiveInteger = Field(alias="heartbeatsToRecover")


class AbsentHeartbeat(_WireModel):
    """One explicit absent-heartbeat observation."""

    drone_id: Identifier = Field(alias="droneId")
    tick_ordinal: SafeNonnegativeInteger = Field(alias="tickOrdinal")


class ScenarioDefinition(_WireModel):
    """One strict version-one scenario definition."""

    definition_version: StrictOne = Field(alias="definitionVersion")
    identifier: Identifier
    revision: SafePositiveInteger
    title: NonemptyText
    summary: NonemptyText
    search_area_square_metres: SafePositiveInteger = Field(alias="searchAreaSquareMetres")
    last_known_location: LastKnownLocation = Field(alias="lastKnownLocation")
    search_polygon: Polygon = Field(alias="searchPolygon")
    sectors: Annotated[list[ScenarioSector], Field(min_length=1, max_length=20)]
    members: Annotated[list[ScenarioMember], Field(min_length=1, max_length=64)]
    tick_interval_milliseconds: SafePositiveInteger = Field(alias="tickIntervalMilliseconds")
    connectivity_thresholds: ConnectivityThresholds = Field(alias="connectivityThresholds")
    ticks_to_sweep: SafePositiveInteger = Field(alias="ticksToSweep")
    absent_heartbeats: Annotated[list[AbsentHeartbeat], Field(max_length=4096)] = Field(
        alias="absentHeartbeats"
    )


class ScenarioCatalogSimulatedMember(_WireModel):
    """One simulated participant in the browser-facing catalog projection."""

    identifier: Identifier
    participation: Literal["SIMULATED"]


class ScenarioCatalogDeclaredOnlyMember(_WireModel):
    """One non-executed participant in the browser-facing catalog projection."""

    identifier: Identifier
    participation: Literal["DECLARED_ONLY"]
    role: Kind
    execution_label: Literal["DECLARED ONLY — NOT EXECUTED"] = Field(alias="executionLabel")


ScenarioCatalogMember = Annotated[
    ScenarioCatalogSimulatedMember | ScenarioCatalogDeclaredOnlyMember,
    Field(discriminator="participation"),
]


class ScenarioCatalogScenario(_WireModel):
    """One validated prepared scenario exposed through private catalog discovery."""

    identifier: Identifier
    revision: StrictOne
    title: NonemptyText
    summary: NonemptyText
    declared_count: Literal[23] = Field(alias="declaredCount")
    simulated_count: Literal[20] = Field(alias="simulatedCount")
    declared_only_count: Literal[3] = Field(alias="declaredOnlyCount")
    search_area_square_metres: SafePositiveInteger = Field(alias="searchAreaSquareMetres")
    last_known_location: LastKnownLocation = Field(alias="lastKnownLocation")
    search_polygon: Polygon = Field(alias="searchPolygon")
    sectors: Annotated[list[ScenarioSector], Field(min_length=20, max_length=20)]
    members: Annotated[list[ScenarioCatalogMember], Field(min_length=23, max_length=23)]


class ScenarioCatalogResponse(_WireModel):
    """The existing dashboard scenario-catalog/v1 document served privately."""

    catalog_version: Literal["scenario-catalog/v1"] = Field(alias="catalogVersion")
    scenarios: Annotated[list[ScenarioCatalogScenario], Field(max_length=20)]


class ScenarioControlStartRequest(_WireModel):
    """A stable private scenario-run start request."""

    control_version: StrictOne = Field(alias="controlVersion")
    scenario_id: Identifier = Field(alias="scenarioId")
    scenario_revision: StrictOne = Field(alias="scenarioRevision")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")


class ScenarioControlCancelRequest(_WireModel):
    """A private cancellation request for one exact mission and run."""

    control_version: StrictOne = Field(alias="controlVersion")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")


class ScenarioControlRecoveryRequest(_WireModel):
    """A request to reconcile one durable mission whose fleet run may be lost."""

    control_version: StrictOne = Field(alias="controlVersion")
    scenario_id: Identifier = Field(alias="scenarioId")
    scenario_revision: StrictOne = Field(alias="scenarioRevision")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")


class ScenarioControlRunStatus(_WireModel):
    """The scenario service's mission-facing run status."""

    control_version: StrictOne = Field(alias="controlVersion")
    scenario_id: Identifier = Field(alias="scenarioId")
    scenario_revision: StrictOne = Field(alias="scenarioRevision")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")
    state: Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]


class ScenarioControlRefusal(_WireModel):
    """A redacted private refusal from scenario control."""

    control_version: StrictOne = Field(alias="controlVersion")
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
    message: NonemptyText


class FleetControlDroneStart(_WireModel):
    """The caller-owned twin of one simulator-bound drone start."""

    drone_id: Identifier = Field(alias="droneId")
    sector_id: Identifier = Field(alias="sectorId")
    latitude_microdegrees: LatitudeMicrodegrees = Field(alias="latitudeMicrodegrees")
    longitude_microdegrees: LongitudeMicrodegrees = Field(alias="longitudeMicrodegrees")
    altitude_metres: AltitudeMetres = Field(alias="altitudeMetres")
    heading_degrees: HeadingDegrees = Field(alias="headingDegrees")
    ground_speed_centimetres_per_second: GroundSpeedCentimetresPerSecond = Field(
        alias="groundSpeedCentimetresPerSecond"
    )
    battery_permille: BatteryPermille = Field(alias="batteryPermille")
    north_microdegrees_per_tick: NorthMicrodegreesPerTick = Field(alias="northMicrodegreesPerTick")
    east_microdegrees_per_tick: EastMicrodegreesPerTick = Field(alias="eastMicrodegreesPerTick")
    battery_drain_permille_per_tick: BatteryPermille = Field(alias="batteryDrainPermillePerTick")


class FleetControlScenario(_WireModel):
    """The caller-owned lossless fleet scenario projection."""

    mission_id: Identifier = Field(alias="missionId")
    drones: Annotated[list[FleetControlDroneStart], Field(min_length=20, max_length=20)]
    tick_interval_milliseconds: SafePositiveInteger = Field(alias="tickIntervalMilliseconds")
    connectivity_thresholds: ConnectivityThresholds = Field(alias="connectivityThresholds")
    ticks_to_sweep: SafePositiveInteger = Field(alias="ticksToSweep")
    absent_heartbeats: Annotated[list[AbsentHeartbeat], Field(max_length=4096)] = Field(
        alias="absentHeartbeats"
    )


class FleetControlStartRequest(_WireModel):
    """The caller-owned fleet-run start request."""

    control_version: StrictOne = Field(alias="controlVersion")
    run_id: Identifier = Field(alias="runId")
    scenario: FleetControlScenario


class FleetControlCancelRequest(_WireModel):
    """The caller-owned fleet cancellation request."""

    control_version: StrictOne = Field(alias="controlVersion")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")


class FleetControlRunStatus(_WireModel):
    """The caller-owned twin of the fleet worker's run status."""

    control_version: StrictOne = Field(alias="controlVersion")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")
    state: Literal["ACCEPTED", "RUNNING", "EXHAUSTED", "CANCELLED", "FAILED"]
    completed_tick_count: SafeNonnegativeInteger = Field(alias="completedTickCount")
    telemetry_publication_count: SafeNonnegativeInteger = Field(alias="telemetryPublicationCount")


class FleetControlRefusal(_WireModel):
    """The caller-owned twin of a redacted fleet refusal."""

    control_version: StrictOne = Field(alias="controlVersion")
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
        "CAPACITY_EXCEEDED",
        "RUN_FAILED",
        "INTERNAL_FAILURE",
    ] = Field(alias="errorCode")
    message: NonemptyText


def _scenario_schema(name: str) -> str:
    return f"{SCHEMA_PREFIX}scenario/{name}.schema.json"


def _dashboard_schema(name: str) -> str:
    return f"{SCHEMA_PREFIX}dashboard/{name}.schema.json"


def _rpc_schema(name: str) -> str:
    return f"{SCHEMA_PREFIX}rpc/{name}.schema.json"


SERVER_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _dashboard_schema("scenario-catalog"): ScenarioCatalogResponse,
        _rpc_schema("scenario-control-cancel-request"): ScenarioControlCancelRequest,
        _rpc_schema("scenario-control-recovery-request"): ScenarioControlRecoveryRequest,
        _rpc_schema("scenario-control-refusal"): ScenarioControlRefusal,
        _rpc_schema("scenario-control-run-status"): ScenarioControlRunStatus,
        _rpc_schema("scenario-control-start-request"): ScenarioControlStartRequest,
    }
)
CLIENT_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _rpc_schema("fleet-control-cancel-request"): FleetControlCancelRequest,
        _rpc_schema("fleet-control-refusal"): FleetControlRefusal,
        _rpc_schema("fleet-control-run-status"): FleetControlRunStatus,
        _rpc_schema("fleet-control-start-request"): FleetControlStartRequest,
    }
)
FILE_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        _scenario_schema("catalog"): ScenarioCatalog,
        _scenario_schema("definition"): ScenarioDefinition,
    }
)
BROWSER_ONLY_SCHEMA_IDS: frozenset[str] = frozenset()

_MODEL_BY_SCHEMA_ID: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        **SERVER_MODEL_BY_SCHEMA_ID,
        **CLIENT_MODEL_BY_SCHEMA_ID,
        **FILE_MODEL_BY_SCHEMA_ID,
    }
)


def parse_wire_document(schema_id: str, raw: str | bytes) -> BaseModel:
    """Canonical-decode and strictly validate one scenario-service-owned document."""
    try:
        model = _MODEL_BY_SCHEMA_ID[schema_id]
    except KeyError as error:
        message = f"schema is not owned by the scenario service: {schema_id}"
        raise ValueError(message) from error

    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    maximum_bytes = (
        MAX_SCENARIO_CATALOG_BYTES
        if schema_id == _dashboard_schema("scenario-catalog")
        else MAX_WIRE_DOCUMENT_BYTES
    )
    if len(encoded) > maximum_bytes:
        message = f"wire document exceeds {maximum_bytes} bytes"
        raise ValueError(message)
    return model.model_validate(canonical.decode(encoded))
