"""Strict, confined filesystem loader for versioned synthetic scenarios."""

from __future__ import annotations

import asyncio
import hashlib
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final, Protocol, cast

from aerial_rescue_contracts import canonical, digest
from pydantic import BaseModel, ValidationError

from aerial_rescue_scenario_service.wire import (
    MAX_WIRE_DOCUMENT_BYTES,
    AbsentHeartbeat,
    DeclaredOnlyMember,
    FleetControlDroneStart,
    FleetControlScenario,
    ScenarioCatalog,
    ScenarioCatalogDeclaredOnlyMember,
    ScenarioCatalogEntry,
    ScenarioCatalogResponse,
    ScenarioCatalogScenario,
    ScenarioCatalogSimulatedMember,
    ScenarioDefinition,
    SimulatedMember,
    Vertex,
    parse_wire_document,
)

from .http_runtime import ControlError, ControlRefusal

CATALOG_FILENAME: Final = "catalog.v1.json"
MAX_DOCUMENT_DEPTH: Final = 16

_SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/"
_CATALOG_SCHEMA_ID: Final = f"{_SCHEMA_PREFIX}scenario/catalog.schema.json"
_DEFINITION_SCHEMA_ID: Final = f"{_SCHEMA_PREFIX}scenario/definition.schema.json"
_PATH_SEPARATOR: Final = "/"
_WINDOWS_SEPARATOR: Final = "\\"
_EMPTY_SEGMENTS: Final = frozenset({"", ".", ".."})
_OPENING_CONTAINERS: Final = frozenset("[{")
_CLOSING_CONTAINERS: Final = frozenset("]}")
_MINIMUM_DISTINCT_POLYGON_VERTICES: Final = 3


_CATALOG_NAME: Final = "catalog.v1.json"
_MAX_DOCUMENT_DEPTH: Final = 16


class FilesystemScenarioCatalog:
    """Load all accepted definitions from one injected, confined catalog root."""

    def __init__(self, root: Path) -> None:
        """Remember the injected root without reading it at import or construction time."""
        self._configured_root = root
        self._definitions: dict[tuple[str, int], ScenarioDefinition] = {}
        self._failure: ControlRefusal | None = ControlRefusal.SCENARIO_NOT_FOUND
        self._ready = False

    @property
    def ready(self) -> bool:
        """Report ready only after every catalog entry validates in one epoch."""
        return self._ready

    async def startup(self) -> None:
        """Validate the bounded catalog off the event loop and fail readiness closed."""
        self._ready = False
        self._definitions.clear()
        try:
            definitions = await asyncio.to_thread(_validated_definitions, self._configured_root)
        except ControlError as error:
            self._failure = error.refusal
            return
        except OSError, UnicodeError, ValueError:
            self._failure = ControlRefusal.SCENARIO_NOT_FOUND
            return
        self._definitions = definitions
        self._failure = None
        self._ready = True

    async def shutdown(self) -> None:
        """Drop cached untrusted documents and end this catalog epoch."""
        self._ready = False
        self._definitions.clear()
        self._failure = ControlRefusal.SCENARIO_NOT_FOUND

    async def load(self, scenario_id: str, revision: int) -> ScenarioDefinition:
        """Resolve an exact validated catalog identity without treating input as a path."""
        if self._failure is not None:
            raise ControlError(self._failure)
        exact = self._definitions.get((scenario_id, revision))
        if exact is not None:
            return exact
        if any(identifier == scenario_id for identifier, _revision in self._definitions):
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        raise ControlError(ControlRefusal.SCENARIO_NOT_FOUND)


def _validated_definitions(root: Path) -> dict[tuple[str, int], ScenarioDefinition]:
    confined_root = root.resolve(strict=True)
    catalog_bytes = _read_regular(
        confined_root / _CATALOG_NAME,
        confined_root,
        ControlRefusal.SCENARIO_NOT_FOUND,
    )
    catalog_value = _decode(catalog_bytes, ControlRefusal.SCENARIO_NOT_FOUND)
    try:
        catalog = ScenarioCatalog.model_validate(catalog_value)
    except ValidationError as error:
        raise ControlError(ControlRefusal.SCENARIO_NOT_FOUND) from error

    definitions: dict[tuple[str, int], ScenarioDefinition] = {}
    for entry in sorted(catalog.scenarios, key=lambda item: (item.identifier, item.revision)):
        identity = (entry.identifier, entry.revision)
        if identity in definitions:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        definition_path = confined_root / entry.definition_path
        definition_bytes = _read_regular(
            definition_path,
            confined_root,
            ControlRefusal.SCENARIO_REVISION_MISMATCH,
        )
        if hashlib.sha256(definition_bytes).hexdigest() != entry.definition_sha256:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        definition_value = _decode(definition_bytes, ControlRefusal.SCENARIO_REVISION_MISMATCH)
        try:
            definition = ScenarioDefinition.model_validate(definition_value)
        except ValidationError as error:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH) from error
        if definition.identifier != entry.identifier or definition.revision != entry.revision:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        definitions[identity] = definition
    return definitions


def _read_regular(path: Path, root: Path, refusal: ControlRefusal) -> bytes:
    if path.is_symlink():
        raise ControlError(refusal)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ControlError(refusal) from error
    if not resolved.is_file():
        raise ControlError(refusal)
    try:
        with resolved.open("rb") as stream:
            content = stream.read(MAX_WIRE_DOCUMENT_BYTES + 1)
    except OSError as error:
        raise ControlError(refusal) from error
    if len(content) > MAX_WIRE_DOCUMENT_BYTES:
        raise ControlError(refusal)
    return content


def _decode(raw: bytes, refusal: ControlRefusal) -> object:
    try:
        value = canonical.decode(raw)
    except canonical.CanonicalizationError as error:
        raise ControlError(refusal) from error
    _enforce_depth(value, 0, refusal)
    return value


def _enforce_depth(value: object, depth: int, refusal: ControlRefusal) -> None:
    if depth > _MAX_DOCUMENT_DEPTH:
        raise ControlError(refusal)
    if isinstance(value, Mapping):
        for member in value.values():
            _enforce_depth(member, depth + 1, refusal)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for member in value:
            _enforce_depth(member, depth + 1, refusal)


class CatalogRefusal(Enum):
    """Why a scenario catalog operation was refused."""

    PATH_INVALID = "catalog-relative path is invalid"
    PATH_OUTSIDE_ROOT = "resolved artifact is outside the injected root"
    FILE_MISSING = "catalog artifact does not exist"
    FILE_NOT_REGULAR = "catalog artifact is not a regular file"
    FILE_UNREADABLE = "catalog artifact could not be read"
    DOCUMENT_TOO_LARGE = "catalog artifact exceeds its byte bound"
    DOCUMENT_ENCODING = "catalog artifact is not UTF-8"
    DOCUMENT_TOO_DEEP = "catalog artifact exceeds its nesting bound"
    DOCUMENT_INVALID = "catalog artifact violates its strict wire model"
    DUPLICATE_CATALOG_IDENTITY = "catalog identity is ambiguous"
    SCENARIO_NOT_FOUND = "catalog does not declare the scenario"
    REVISION_NOT_FOUND = "catalog does not declare the requested revision"
    DIGEST_MISMATCH = "definition bytes do not match the catalog digest"
    DEFINITION_IDENTITY_MISMATCH = "definition identity differs from its catalog entry"
    POLYGON_NOT_CLOSED = "polygon does not repeat its first vertex at the end"
    POLYGON_DEGENERATE = "polygon has fewer than three distinct boundary vertices"
    DUPLICATE_SECTOR = "definition repeats a sector identifier"
    DUPLICATE_MEMBER = "definition repeats a member identifier"
    UNKNOWN_SECTOR = "simulated member names an unknown sector"
    DUPLICATE_SECTOR_ASSIGNMENT = "two simulated members name one sector"
    HEARTBEAT_MEMBER_NOT_SIMULATED = "heartbeat absence names a non-simulated member"
    DUPLICATE_HEARTBEAT = "definition repeats one heartbeat absence"
    CATALOG_RESPONSE_INVALID = "definition cannot enter scenario-catalog/v1"


class ScenarioCatalogError(ValueError):
    """A typed, redacted catalog refusal."""

    def __init__(self, reason: CatalogRefusal, value: object) -> None:
        """Retain the structured reason and a bounded diagnostic value."""
        super().__init__(f"{reason.value}: {value!r}")
        self.reason = reason
        self.value = value


class ScenarioSource(Protocol):
    """Supply complete bytes for one catalog-relative artifact."""

    def read(self, relative_name: str) -> bytes:
        """Return the named artifact's exact bytes."""


def _relative_path(relative_name: str) -> PurePosixPath:
    """Validate one catalog-relative path before consulting the filesystem."""
    segments = relative_name.split(_PATH_SEPARATOR)
    invalid = (
        relative_name.startswith(_PATH_SEPARATOR)
        or _WINDOWS_SEPARATOR in relative_name
        or any(segment in _EMPTY_SEGMENTS for segment in segments)
    )
    if invalid:
        raise ScenarioCatalogError(CatalogRefusal.PATH_INVALID, relative_name)
    return PurePosixPath(relative_name)


@dataclass(frozen=True)
class RootedScenarioSource:
    """Read bounded regular files from one injected root and nowhere else."""

    root: Path
    maximum_bytes: int = MAX_WIRE_DOCUMENT_BYTES

    def read(self, relative_name: str) -> bytes:
        """Return exact bytes after path, confinement, type, and size checks."""
        resolved = self._resolve(_relative_path(relative_name), relative_name)
        try:
            with resolved.open("rb") as handle:
                content = handle.read(self.maximum_bytes + 1)
        except OSError as error:
            raise ScenarioCatalogError(CatalogRefusal.FILE_UNREADABLE, relative_name) from error
        if len(content) > self.maximum_bytes:
            raise ScenarioCatalogError(CatalogRefusal.DOCUMENT_TOO_LARGE, relative_name)
        return content

    def _resolve(self, relative: PurePosixPath, name: str) -> Path:
        """Resolve and classify one artifact without opening a non-regular file."""
        try:
            root = self.root.resolve(strict=True)
            resolved = (root / relative).resolve(strict=True)
        except FileNotFoundError as error:
            raise ScenarioCatalogError(CatalogRefusal.FILE_MISSING, name) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise ScenarioCatalogError(CatalogRefusal.FILE_UNREADABLE, name) from error
        if not resolved.is_relative_to(root):
            raise ScenarioCatalogError(CatalogRefusal.PATH_OUTSIDE_ROOT, name)
        try:
            mode = resolved.stat().st_mode
        except OSError as error:
            raise ScenarioCatalogError(CatalogRefusal.FILE_UNREADABLE, name) from error
        if not stat.S_ISREG(mode):
            raise ScenarioCatalogError(CatalogRefusal.FILE_NOT_REGULAR, name)
        return resolved


@dataclass(frozen=True)
class LoadedScenario:
    """One definition accepted through its exact catalog entry."""

    entry: ScenarioCatalogEntry
    definition: ScenarioDefinition


@dataclass(frozen=True)
class ScenarioCatalogLoader:
    """Resolve validated catalog identities through one injected source."""

    source: ScenarioSource
    maximum_depth: int = MAX_DOCUMENT_DEPTH

    def catalog(self) -> ScenarioCatalog:
        """Return the strict catalog after rejecting ambiguous identities."""
        catalog = _parse_model(
            _CATALOG_SCHEMA_ID,
            self.source.read(CATALOG_FILENAME),
            self.maximum_depth,
            ScenarioCatalog,
        )
        _validate_catalog(catalog)
        return catalog

    def load(self, identifier: str, revision: int) -> LoadedScenario:
        """Resolve one identity without deriving a path from caller input."""
        catalog = self.catalog()
        entry = _select_entry(catalog, identifier, revision)
        return self._load_entry(entry)

    def catalog_response(self) -> ScenarioCatalogResponse:
        """Project every validated definition into dashboard scenario-catalog/v1."""
        catalog = self.catalog()
        scenarios = [
            _project_catalog_scenario(self._load_entry(entry).definition)
            for entry in catalog.scenarios
        ]
        return ScenarioCatalogResponse(catalogVersion="scenario-catalog/v1", scenarios=scenarios)

    def _load_entry(self, entry: ScenarioCatalogEntry) -> LoadedScenario:
        """Verify and parse the exact definition one catalog entry names."""
        content = self.source.read(entry.definition_path)
        produced = hashlib.sha256(content).hexdigest()
        if not digest.matches(entry.definition_sha256, produced):
            identity = (entry.identifier, entry.revision)
            raise ScenarioCatalogError(CatalogRefusal.DIGEST_MISMATCH, identity)
        definition = _parse_model(
            _DEFINITION_SCHEMA_ID, content, self.maximum_depth, ScenarioDefinition
        )
        declared = (definition.identifier, definition.revision)
        expected = (entry.identifier, entry.revision)
        if declared != expected:
            raise ScenarioCatalogError(
                CatalogRefusal.DEFINITION_IDENTITY_MISMATCH, (*expected, *declared)
            )
        _validate_definition(definition)
        return LoadedScenario(entry, definition)


def _utf8(content: bytes) -> str:
    """Decode exact UTF-8 without JSON's encoding auto-detection."""
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ScenarioCatalogError(CatalogRefusal.DOCUMENT_ENCODING, error.reason) from error


def _deepest_container(text: str) -> int:
    """Count JSON containers outside string literals without recursive parsing."""
    depth = 0
    deepest = 0
    inside_string = False
    escaped = False
    for character in text:
        if escaped:
            escaped = False
        elif inside_string and character == _WINDOWS_SEPARATOR:
            escaped = True
        elif character == '"':
            inside_string = not inside_string
        elif not inside_string and character in _OPENING_CONTAINERS:
            depth += 1
            deepest = max(deepest, depth)
        elif not inside_string and character in _CLOSING_CONTAINERS:
            depth -= 1
    return deepest


def _parse_model[ModelT: BaseModel](
    schema_id: str, content: bytes, maximum_depth: int, model: type[ModelT]
) -> ModelT:
    """Apply UTF-8 and depth bounds before canonical and strict model validation."""
    text = _utf8(content)
    if _deepest_container(text) > maximum_depth:
        raise ScenarioCatalogError(CatalogRefusal.DOCUMENT_TOO_DEEP, maximum_depth)
    try:
        parsed = parse_wire_document(schema_id, text)
    except canonical.CanonicalizationError as error:
        canonical_detail = (model.__name__, error.refusal)
        raise ScenarioCatalogError(CatalogRefusal.DOCUMENT_INVALID, canonical_detail) from error
    except ValidationError as error:
        first = error.errors(include_input=False)[0]
        validation_detail = (model.__name__, first["loc"], first["type"])
        raise ScenarioCatalogError(CatalogRefusal.DOCUMENT_INVALID, validation_detail) from error
    return cast("ModelT", parsed)


def _validate_catalog(catalog: ScenarioCatalog) -> None:
    """Refuse two entries that claim the same scenario revision."""
    seen: set[tuple[str, int]] = set()
    for entry in catalog.scenarios:
        identity = (entry.identifier, entry.revision)
        if identity in seen:
            raise ScenarioCatalogError(CatalogRefusal.DUPLICATE_CATALOG_IDENTITY, identity)
        seen.add(identity)


def _select_entry(catalog: ScenarioCatalog, identifier: str, revision: int) -> ScenarioCatalogEntry:
    """Select one exact identity while distinguishing scenario and revision absence."""
    matching_identifier = [entry for entry in catalog.scenarios if entry.identifier == identifier]
    if not matching_identifier:
        raise ScenarioCatalogError(CatalogRefusal.SCENARIO_NOT_FOUND, identifier)
    for entry in matching_identifier:
        if entry.revision == revision:
            return entry
    raise ScenarioCatalogError(CatalogRefusal.REVISION_NOT_FOUND, (identifier, revision))


def _coordinates(vertices: Sequence[Vertex]) -> tuple[tuple[int, int], ...]:
    """Return an immutable coordinate sequence for cross-field geometry checks."""
    return tuple(
        (vertex.latitude_microdegrees, vertex.longitude_microdegrees) for vertex in vertices
    )


def _validate_polygon(vertices: Sequence[Vertex], identifier: str) -> None:
    """Require the explicit, non-degenerate closure the file model describes."""
    coordinates = _coordinates(vertices)
    if coordinates[0] != coordinates[-1]:
        raise ScenarioCatalogError(CatalogRefusal.POLYGON_NOT_CLOSED, identifier)
    if len(set(coordinates[:-1])) < _MINIMUM_DISTINCT_POLYGON_VERTICES:
        raise ScenarioCatalogError(CatalogRefusal.POLYGON_DEGENERATE, identifier)


def _validate_definition(definition: ScenarioDefinition) -> None:
    """Enforce geometry, identity, roster, and heartbeat relationships."""
    _validate_polygon(definition.search_polygon.vertices, definition.identifier)
    sector_ids: set[str] = set()
    for sector in definition.sectors:
        if sector.identifier in sector_ids:
            raise ScenarioCatalogError(CatalogRefusal.DUPLICATE_SECTOR, sector.identifier)
        _validate_polygon(sector.vertices, sector.identifier)
        sector_ids.add(sector.identifier)
    _validate_roster(definition, sector_ids)


def _validate_roster(definition: ScenarioDefinition, sector_ids: set[str]) -> None:
    """Refuse ambiguous members, holdings, and heartbeat schedules."""
    member_ids: set[str] = set()
    simulated_ids: set[str] = set()
    assigned_sectors: set[str] = set()
    for member in definition.members:
        if member.identifier in member_ids:
            raise ScenarioCatalogError(CatalogRefusal.DUPLICATE_MEMBER, member.identifier)
        member_ids.add(member.identifier)
        if isinstance(member, SimulatedMember):
            _validate_simulated_member(member, sector_ids, assigned_sectors)
            simulated_ids.add(member.identifier)
    _validate_heartbeat_schedule(definition.absent_heartbeats, simulated_ids)


def _validate_simulated_member(
    member: SimulatedMember, sector_ids: set[str], assigned_sectors: set[str]
) -> None:
    """Require one known, uniquely held sector for each simulated member."""
    if member.sector_id not in sector_ids:
        raise ScenarioCatalogError(CatalogRefusal.UNKNOWN_SECTOR, member.sector_id)
    if member.sector_id in assigned_sectors:
        raise ScenarioCatalogError(CatalogRefusal.DUPLICATE_SECTOR_ASSIGNMENT, member.sector_id)
    assigned_sectors.add(member.sector_id)


def _validate_heartbeat_schedule(
    absences: Sequence[AbsentHeartbeat], simulated_ids: set[str]
) -> None:
    """Require each explicit absence to name a simulated member exactly once."""
    seen: set[tuple[str, int]] = set()
    for absence in absences:
        if absence.drone_id not in simulated_ids:
            raise ScenarioCatalogError(
                CatalogRefusal.HEARTBEAT_MEMBER_NOT_SIMULATED, absence.drone_id
            )
        identity = (absence.drone_id, absence.tick_ordinal)
        if identity in seen:
            raise ScenarioCatalogError(CatalogRefusal.DUPLICATE_HEARTBEAT, identity)
        seen.add(identity)


def _project_catalog_scenario(definition: ScenarioDefinition) -> ScenarioCatalogScenario:
    """Remove simulator-only values while preserving all catalog presentation facts."""
    members = [
        ScenarioCatalogSimulatedMember(identifier=member.identifier, participation="SIMULATED")
        if isinstance(member, SimulatedMember)
        else ScenarioCatalogDeclaredOnlyMember(
            identifier=member.identifier,
            participation="DECLARED_ONLY",
            role=member.role,
            executionLabel=member.execution_label,
        )
        for member in definition.members
    ]
    simulated_count = sum(isinstance(member, SimulatedMember) for member in definition.members)
    declared_only_count = sum(
        isinstance(member, DeclaredOnlyMember) for member in definition.members
    )
    counts = (
        len(definition.members),
        simulated_count,
        declared_only_count,
        len(definition.sectors),
    )
    if definition.revision != 1 or counts != (23, 20, 3, 20):
        raise ScenarioCatalogError(
            CatalogRefusal.CATALOG_RESPONSE_INVALID, (definition.identifier, definition.revision)
        )
    return ScenarioCatalogScenario(
        identifier=definition.identifier,
        revision=1,
        title=definition.title,
        summary=definition.summary,
        declaredCount=23,
        simulatedCount=20,
        declaredOnlyCount=3,
        searchAreaSquareMetres=definition.search_area_square_metres,
        lastKnownLocation=definition.last_known_location,
        searchPolygon=definition.search_polygon,
        sectors=definition.sectors,
        members=members,
    )


def _project_drone(member: SimulatedMember) -> FleetControlDroneStart:
    """Copy every simulator-bound member without deriving or defaulting a value."""
    return FleetControlDroneStart(
        droneId=member.identifier,
        sectorId=member.sector_id,
        latitudeMicrodegrees=member.latitude_microdegrees,
        longitudeMicrodegrees=member.longitude_microdegrees,
        altitudeMetres=member.altitude_metres,
        headingDegrees=member.heading_degrees,
        groundSpeedCentimetresPerSecond=member.ground_speed_centimetres_per_second,
        batteryPermille=member.battery_permille,
        northMicrodegreesPerTick=member.north_microdegrees_per_tick,
        eastMicrodegreesPerTick=member.east_microdegrees_per_tick,
        batteryDrainPermillePerTick=member.battery_drain_permille_per_tick,
    )


def project_fleet_scenario(definition: ScenarioDefinition, mission_id: str) -> FleetControlScenario:
    """Build ADR-0107's lossless FleetScenario projection from simulated members only."""
    drones = [
        _project_drone(member)
        for member in definition.members
        if isinstance(member, SimulatedMember)
    ]
    return FleetControlScenario(
        missionId=mission_id,
        drones=drones,
        tickIntervalMilliseconds=definition.tick_interval_milliseconds,
        connectivityThresholds=definition.connectivity_thresholds,
        ticksToSweep=definition.ticks_to_sweep,
        absentHeartbeats=definition.absent_heartbeats,
    )
