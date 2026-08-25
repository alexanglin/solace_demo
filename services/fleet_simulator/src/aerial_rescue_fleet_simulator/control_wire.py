"""Strict canonical wire models for the fleet simulator's private control boundary.

The committed JSON Schemas own these shapes.  This module is the fleet process's local
Pydantic projection of them: it performs no HTTP work and imports no caller service.
Canonical decoding deliberately happens before model validation so duplicate keys and
floating-point values cannot be hidden by framework parsing or coercion.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal

from aerial_rescue_contracts import canonical
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/rpc/"
MAXIMUM_WIRE_BYTES: Final = 256 * 1024
MAXIMUM_SAFE_INTEGER: Final = 9_007_199_254_740_991

Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$"),
]
SafeNonNegativeInteger = Annotated[int, Field(ge=0, le=MAXIMUM_SAFE_INTEGER)]
SafePositiveInteger = Annotated[int, Field(ge=1, le=MAXIMUM_SAFE_INTEGER)]
LatitudeMicrodegrees = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
LongitudeMicrodegrees = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]
AltitudeMetres = Annotated[int, Field(ge=-500, le=20_000)]
HeadingDegrees = Annotated[int, Field(ge=0, le=359)]
GroundSpeedCentimetresPerSecond = Annotated[int, Field(ge=0, le=10_000)]
BatteryPermille = Annotated[int, Field(ge=0, le=1_000)]
NorthMicrodegreesPerTick = Annotated[int, Field(ge=-90_000_000, le=90_000_000)]
EastMicrodegreesPerTick = Annotated[int, Field(ge=-180_000_000, le=180_000_000)]
Message = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


def _strict_literal_one(value: object) -> object:
    """Keep JSON booleans from satisfying Python's equality with integer one."""
    if type(value) is not int:
        message = "Input should be the integer 1"
        raise ValueError(message)
    return value


StrictOne = Annotated[Literal[1], BeforeValidator(_strict_literal_one)]


class _WireModel(BaseModel):
    """Apply ADR-0108's closed, frozen, strict, alias-only model policy."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
    )


class FleetDroneStart(_WireModel):
    """One explicit simulated drone in a fleet-control start request."""

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


class FleetConnectivityThresholds(_WireModel):
    """The explicit heartbeat thresholds carried into one deterministic run."""

    misses_to_degraded: SafePositiveInteger = Field(alias="missesToDegraded")
    misses_to_offline: SafePositiveInteger = Field(alias="missesToOffline")
    heartbeats_to_recover: SafePositiveInteger = Field(alias="heartbeatsToRecover")


class FleetAbsentHeartbeat(_WireModel):
    """One scheduled heartbeat absence in the accepted deterministic input."""

    drone_id: Identifier = Field(alias="droneId")
    tick_ordinal: SafeNonNegativeInteger = Field(alias="tickOrdinal")


class FleetScenarioDocument(_WireModel):
    """The lossless, seed-free FleetScenario projection accepted by this service."""

    mission_id: Identifier = Field(alias="missionId")
    drones: Annotated[list[FleetDroneStart], Field(min_length=20, max_length=20)]
    tick_interval_milliseconds: SafePositiveInteger = Field(alias="tickIntervalMilliseconds")
    connectivity_thresholds: FleetConnectivityThresholds = Field(alias="connectivityThresholds")
    ticks_to_sweep: SafePositiveInteger = Field(alias="ticksToSweep")
    absent_heartbeats: Annotated[list[FleetAbsentHeartbeat], Field(max_length=4096)] = Field(
        alias="absentHeartbeats"
    )


class FleetControlStartRequest(_WireModel):
    """Start one stable fleet run from one explicit scenario projection."""

    control_version: StrictOne = Field(alias="controlVersion")
    run_id: Identifier = Field(alias="runId")
    scenario: FleetScenarioDocument


class FleetControlCancelRequest(_WireModel):
    """Request cancellation for one exact mission and fleet run."""

    control_version: StrictOne = Field(alias="controlVersion")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")


class FleetControlRunStatus(_WireModel):
    """Report the fleet run state and its separate publication instruments."""

    control_version: StrictOne = Field(alias="controlVersion")
    mission_id: Identifier = Field(alias="missionId")
    run_id: Identifier = Field(alias="runId")
    state: Literal["ACCEPTED", "RUNNING", "EXHAUSTED", "CANCELLED", "FAILED"]
    completed_tick_count: SafeNonNegativeInteger = Field(alias="completedTickCount")
    telemetry_publication_count: SafeNonNegativeInteger = Field(alias="telemetryPublicationCount")


class FleetControlRefusal(_WireModel):
    """Return one redacted refusal from the fleet-control boundary."""

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
    message: Message


ModelType = type[BaseModel]

SERVER_MODEL_BY_SCHEMA_ID: Final[Mapping[str, ModelType]] = MappingProxyType(
    {
        f"{SCHEMA_PREFIX}fleet-control-cancel-request.schema.json": FleetControlCancelRequest,
        f"{SCHEMA_PREFIX}fleet-control-refusal.schema.json": FleetControlRefusal,
        f"{SCHEMA_PREFIX}fleet-control-run-status.schema.json": FleetControlRunStatus,
        f"{SCHEMA_PREFIX}fleet-control-start-request.schema.json": FleetControlStartRequest,
    }
)
CLIENT_MODEL_BY_SCHEMA_ID: Final[Mapping[str, ModelType]] = MappingProxyType({})
FILE_MODEL_BY_SCHEMA_ID: Final[Mapping[str, ModelType]] = MappingProxyType({})
BROWSER_ONLY_SCHEMA_IDS: Final[frozenset[str]] = frozenset()


def parse_wire_document(schema_id: str, raw: str | bytes) -> BaseModel:
    """Canonical-decode and strictly validate one fleet-owned wire document.

    The byte bound is applied before parsing.  The canonical decoder then preserves the
    evidence needed to refuse duplicate keys and floats before Pydantic sees the value.
    """
    size = len(raw) if isinstance(raw, bytes) else len(raw.encode())
    if size > MAXIMUM_WIRE_BYTES:
        message = f"wire document exceeds {MAXIMUM_WIRE_BYTES} bytes"
        raise ValueError(message)
    value = canonical.decode(raw)
    try:
        model = SERVER_MODEL_BY_SCHEMA_ID[schema_id]
    except KeyError as error:
        message = f"schema is not owned by the fleet simulator: {schema_id}"
        raise ValueError(message) from error
    return model.model_validate(value)
