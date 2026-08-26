"""Bounded normalized recording export and isolated replay validation."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Literal, NoReturn, cast, overload

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.view import (
    CheckpointAccepted,
    Connectivity,
    DashboardEvent,
    DashboardReducedState,
    DeclaredOnlyFleetMember,
    EventClass,
    FoldApplied,
    Mission,
    MissionLifecycle,
    OrderedDashboardEvent,
    ReducerCheckpoint,
    Sector,
    SectorState,
    SimulatedFleetMember,
    Telemetry,
    checkpoint_from_replay,
    fold_ordered_event,
    state_digest,
    state_document,
)

MAX_RECORDING_BYTES = 1_048_576
MAX_LINE_BYTES = 65_536
MAX_EVENTS = 512
MAX_DOCUMENT_DEPTH = 16
MAX_SAFE_INTEGER = 9_007_199_254_740_991
VALIDATION_FOLD_RUNS = 10
REPLAY_BUNDLE_FILENAME = "wilderness-missing-person.r1.replay.json"
NORMALIZED_RECORDING_FILENAME = "wilderness-missing-person.r1.ndjson"
_IDENTIFIER = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RecordingRefusal(Enum):
    """Why normalized recording input cannot become a replay bundle."""

    SIZE = "recording exceeds its byte bound"
    FINAL_NEWLINE = "recording does not end in one LF"
    LINE_ENDING = "recording contains a non-LF line ending"
    BLANK_LINE = "recording contains a blank line"
    LINE_SIZE = "recording line exceeds its byte bound"
    EVENT_COUNT = "recording exceeds its event-count bound"
    CANONICAL = "recording line is not canonical JSON"
    DEPTH = "recording document exceeds its depth bound"
    HEADER = "recording header is invalid"
    RECORD = "recording event record is invalid"
    CHECKSUM = "recording checksum does not match"
    ANCHOR = "recording initial checkpoint is invalid"
    ORDERED_EVENT = "recording event cannot be folded"
    FINAL_DIGEST = "recording final digest does not match"
    NONDETERMINISTIC = "recording fold did not reproduce one checkpoint"
    INPUT_PATH = "recording input is not one regular non-symlink file"
    OUTPUT_PATH = "replay output is not one regular non-symlink directory"
    OUTPUT_EXISTS = "validated replay output already exists"
    RECORDING_OUTPUT_PATH = "recording output is not one regular non-symlink directory"
    RECORDING_OUTPUT_EXISTS = "normalized recording output already exists"


class RecordingError(ValueError):
    """A typed, redacted normalized-recording refusal."""

    def __init__(self, refusal: RecordingRefusal) -> None:
        """Retain only the structured refusal, never rejected recording bytes."""
        super().__init__(refusal.value)
        self.refusal = refusal


def _refuse(refusal: RecordingRefusal) -> NoReturn:
    raise RecordingError(refusal)


def _mapping(
    value: object, members: frozenset[str], refusal: RecordingRefusal
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _refuse(refusal)
    document = cast("Mapping[str, object]", value)
    if frozenset(document) != members:
        _refuse(refusal)
    return document


def _sequence(value: object, maximum: int, refusal: RecordingRefusal) -> Sequence[object]:
    if not isinstance(value, list) or len(value) > maximum:
        _refuse(refusal)
    return cast("list[object]", value)


def _identifier(value: object, refusal: RecordingRefusal) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _refuse(refusal)
    return value


def _digest(value: object, refusal: RecordingRefusal) -> str:
    if not isinstance(value, str) or _LOWERCASE_SHA256.fullmatch(value) is None:
        _refuse(refusal)
    return value


def _integer(value: object, minimum: int, maximum: int, refusal: RecordingRefusal) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _refuse(refusal)
    return value


def _enum_member[EnumT: Enum](
    enum_type: type[EnumT], value: object, refusal: RecordingRefusal
) -> EnumT:
    try:
        return enum_type(value)
    except TypeError, ValueError:
        _refuse(refusal)


def _telemetry(value: object) -> Telemetry | None:
    if value is None:
        return None
    refusal = RecordingRefusal.HEADER
    document = _mapping(
        value,
        frozenset(
            {
                "latitudeMicrodegrees",
                "longitudeMicrodegrees",
                "batteryPercent",
                "altitudeMetres",
                "headingDegrees",
                "groundSpeedCentimetresPerSecond",
            }
        ),
        refusal,
    )
    return Telemetry(
        _integer(document["latitudeMicrodegrees"], -90_000_000, 90_000_000, refusal),
        _integer(document["longitudeMicrodegrees"], -180_000_000, 180_000_000, refusal),
        _integer(document["batteryPercent"], 0, 100, refusal),
        _integer(document["altitudeMetres"], -500, 20_000, refusal),
        _integer(document["headingDegrees"], 0, 359, refusal),
        _integer(document["groundSpeedCentimetresPerSecond"], 0, 10_000, refusal),
    )


def _fleet_member(value: object) -> SimulatedFleetMember | DeclaredOnlyFleetMember:
    refusal = RecordingRefusal.HEADER
    candidate = cast("Mapping[object, object]", value) if isinstance(value, Mapping) else None
    if candidate is None:
        _refuse(refusal)
    participation = candidate.get("participation")
    if participation == "DECLARED_ONLY":
        document = _mapping(value, frozenset({"identifier", "participation"}), refusal)
        return DeclaredOnlyFleetMember(_identifier(document["identifier"], refusal))
    document = _mapping(
        value,
        frozenset({"identifier", "participation", "connectivity", "telemetry"}),
        refusal,
    )
    if document["participation"] != "SIMULATED":
        _refuse(refusal)
    return SimulatedFleetMember(
        _identifier(document["identifier"], refusal),
        _enum_member(Connectivity, document["connectivity"], refusal),
        _telemetry(document["telemetry"]),
    )


def _state(value: object) -> DashboardReducedState:
    refusal = RecordingRefusal.HEADER
    document = _mapping(
        value,
        frozenset(
            {
                "canonicalizationVersion",
                "stateVersion",
                "currentMission",
                "fleet",
                "latestAuditOrdinal",
                "sectors",
            }
        ),
        refusal,
    )
    if document["canonicalizationVersion"] != 1 or document["stateVersion"] != 1:
        _refuse(refusal)
    mission_value = document["currentMission"]
    mission: Mission | None = None
    if mission_value is not None:
        mission_document = _mapping(
            mission_value,
            frozenset({"identifier", "lifecycle", "predecessorIdentifier"}),
            refusal,
        )
        predecessor = mission_document["predecessorIdentifier"]
        if predecessor is not None:
            predecessor = _identifier(predecessor, refusal)
        mission = Mission(
            _identifier(mission_document["identifier"], refusal),
            _enum_member(MissionLifecycle, mission_document["lifecycle"], refusal),
            predecessor,
        )
    fleet = tuple(_fleet_member(item) for item in _sequence(document["fleet"], 23, refusal))
    sectors = tuple(_sector(item) for item in _sequence(document["sectors"], 20, refusal))
    return DashboardReducedState(
        mission,
        fleet,
        _integer(document["latestAuditOrdinal"], 0, MAX_SAFE_INTEGER, refusal),
        sectors,
    )


def _sector(value: object) -> Sector:
    refusal = RecordingRefusal.HEADER
    document = _mapping(
        value,
        frozenset({"identifier", "state", "assignedMemberId"}),
        refusal,
    )
    assignee = document["assignedMemberId"]
    if assignee is not None:
        assignee = _identifier(assignee, refusal)
    return Sector(
        _identifier(document["identifier"], refusal),
        _enum_member(SectorState, document["state"], refusal),
        assignee,
    )


def _event(value: object) -> OrderedDashboardEvent:
    refusal = RecordingRefusal.RECORD
    ordered = _mapping(value, frozenset({"auditOrdinal", "event"}), refusal)
    event = _mapping(
        ordered["event"],
        frozenset({"kind", "eventClass", "mission", "time", "data"}),
        refusal,
    )
    event_class_value = event["eventClass"]
    if not isinstance(event_class_value, str):
        _refuse(refusal)
    try:
        normalized_class = EventClass[event_class_value]
    except KeyError:
        _refuse(refusal)
    kind = event["kind"]
    instant = event["time"]
    data = event["data"]
    if not isinstance(kind, str) or not isinstance(instant, str) or not isinstance(data, Mapping):
        _refuse(refusal)
    return OrderedDashboardEvent(
        _integer(ordered["auditOrdinal"], 1, MAX_SAFE_INTEGER, refusal),
        DashboardEvent(
            kind,
            normalized_class,
            _identifier(event["mission"], refusal),
            instant,
            cast("Mapping[str, object]", data),
        ),
    )


def _event_document(ordered: OrderedDashboardEvent) -> dict[str, object]:
    event = ordered.event
    return {
        "auditOrdinal": ordered.audit_ordinal,
        "event": {
            "kind": event.kind,
            "eventClass": event.event_class.name,
            "mission": event.mission,
            "time": event.time,
            "data": dict(event.data),
        },
    }


def _record_documents(events: Sequence[OrderedDashboardEvent]) -> list[dict[str, object]]:
    return [
        {"recordVersion": "dashboard-record/v1", "orderedEvent": _event_document(event)}
        for event in events
    ]


def _checksum(header_without_checksum: Mapping[str, object], records: Sequence[object]) -> str:
    material = canonical.canonical_bytes(header_without_checksum) + b"\n"
    material += b"".join(canonical.canonical_bytes(record) + b"\n" for record in records)
    return hashlib.sha256(material).hexdigest()


def _fold(
    checkpoint: ReducerCheckpoint, events: Sequence[OrderedDashboardEvent]
) -> ReducerCheckpoint:
    current = checkpoint
    for event in events:
        outcome = fold_ordered_event(current, event)
        if not isinstance(outcome, FoldApplied):
            _refuse(RecordingRefusal.ORDERED_EVENT)
        current = outcome.checkpoint
    return current


def export_normalized_recording(
    scenario_id: str,
    scenario_revision: int,
    initial_checkpoint: ReducerCheckpoint,
    events: Sequence[OrderedDashboardEvent],
) -> bytes:
    """Return one complete canonical normalized recording."""
    _identifier(scenario_id, RecordingRefusal.HEADER)
    if scenario_revision != 1 or len(events) > MAX_EVENTS:
        _refuse(RecordingRefusal.EVENT_COUNT)
    anchor = checkpoint_from_replay(
        initial_checkpoint.state,
        initial_checkpoint.latest_event_digest,
    )
    if not isinstance(anchor, CheckpointAccepted):
        _refuse(RecordingRefusal.ANCHOR)
    final_checkpoint = _fold(initial_checkpoint, events)
    records = _record_documents(events)
    header_without_checksum: dict[str, object] = {
        "recordingVersion": "dashboard-recording/v1",
        "scenarioId": scenario_id,
        "scenarioRevision": 1,
        "initialState": state_document(initial_checkpoint.state),
        "latestEventDigest": initial_checkpoint.latest_event_digest,
        "expectedFinalDigest": state_digest(final_checkpoint.state),
        "eventCount": len(events),
        "checksumAlgorithm": "sha256",
    }
    header = {**header_without_checksum, "checksum": _checksum(header_without_checksum, records)}
    raw = b"".join(canonical.canonical_bytes(document) + b"\n" for document in (header, *records))
    if len(raw) > MAX_RECORDING_BYTES or any(
        len(line) > MAX_LINE_BYTES for line in raw.splitlines()
    ):
        _refuse(RecordingRefusal.SIZE)
    return raw


def checkpoint_from_prepared_bytes(raw: bytes) -> ReducerCheckpoint:
    """Validate exact canonical prepared-state bytes into an ordinal-zero checkpoint."""
    if not raw or len(raw) > MAX_LINE_BYTES:
        _refuse(RecordingRefusal.SIZE)
    state = _state(_decode_line(raw))
    accepted = checkpoint_from_replay(state, None)
    if not isinstance(accepted, CheckpointAccepted):
        _refuse(RecordingRefusal.ANCHOR)
    return accepted.checkpoint


def ordered_event_from_payload(audit_ordinal: int, raw: bytes) -> OrderedDashboardEvent:
    """Validate one exact canonical audit payload under its durable ordering witness."""
    if not raw or len(raw) > MAX_LINE_BYTES:
        _refuse(RecordingRefusal.SIZE)
    return _event({"auditOrdinal": audit_ordinal, "event": _decode_line(raw)})


def _document_depth(value: object) -> int:
    if isinstance(value, Mapping):
        return 1 + max((_document_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_document_depth(item) for item in value), default=0)
    return 1


def _framed_lines(raw: bytes) -> list[bytes]:
    if len(raw) > MAX_RECORDING_BYTES:
        _refuse(RecordingRefusal.SIZE)
    if not raw.endswith(b"\n"):
        _refuse(RecordingRefusal.FINAL_NEWLINE)
    if b"\r" in raw:
        _refuse(RecordingRefusal.LINE_ENDING)
    lines = raw[:-1].split(b"\n")
    if any(not line for line in lines):
        _refuse(RecordingRefusal.BLANK_LINE)
    if any(len(line) > MAX_LINE_BYTES for line in lines):
        _refuse(RecordingRefusal.LINE_SIZE)
    if len(lines) - 1 > MAX_EVENTS:
        _refuse(RecordingRefusal.EVENT_COUNT)
    return lines


def _decode_line(line: bytes) -> object:
    try:
        document = canonical.decode(line)
        if canonical.canonical_bytes(document) != line:
            _refuse(RecordingRefusal.CANONICAL)
    except canonical.CanonicalizationError:
        _refuse(RecordingRefusal.CANONICAL)
    if _document_depth(document) > MAX_DOCUMENT_DEPTH:
        _refuse(RecordingRefusal.DEPTH)
    return document


def _decode_lines(raw: bytes) -> list[object]:
    lines = _framed_lines(raw)
    documents: list[object] = []
    for line in lines:
        documents.append(_decode_line(line))
    return documents


def _header(value: object) -> tuple[Mapping[str, object], DashboardReducedState, str | None]:
    refusal = RecordingRefusal.HEADER
    document = _mapping(
        value,
        frozenset(
            {
                "recordingVersion",
                "scenarioId",
                "scenarioRevision",
                "initialState",
                "latestEventDigest",
                "expectedFinalDigest",
                "eventCount",
                "checksumAlgorithm",
                "checksum",
            }
        ),
        refusal,
    )
    if (
        document["recordingVersion"] != "dashboard-recording/v1"
        or document["scenarioRevision"] != 1
        or document["checksumAlgorithm"] != "sha256"
    ):
        _refuse(refusal)
    _identifier(document["scenarioId"], refusal)
    _digest(document["expectedFinalDigest"], refusal)
    _digest(document["checksum"], refusal)
    _integer(document["eventCount"], 0, MAX_EVENTS, refusal)
    witness = document["latestEventDigest"]
    if witness is not None:
        witness = _digest(witness, refusal)
    return document, _state(document["initialState"]), witness


def _validated_recording(
    raw: bytes,
) -> tuple[Mapping[str, object], ReducerCheckpoint, list[OrderedDashboardEvent]]:
    documents = _decode_lines(raw)
    header, state, witness = _header(documents[0])
    records: list[Mapping[str, object]] = []
    events: list[OrderedDashboardEvent] = []
    for value in documents[1:]:
        record = _mapping(
            value,
            frozenset({"recordVersion", "orderedEvent"}),
            RecordingRefusal.RECORD,
        )
        if record["recordVersion"] != "dashboard-record/v1":
            _refuse(RecordingRefusal.RECORD)
        records.append(record)
        events.append(_event(record["orderedEvent"]))
    if header["eventCount"] != len(events):
        _refuse(RecordingRefusal.EVENT_COUNT)
    checksum_header = {key: value for key, value in header.items() if key != "checksum"}
    if _checksum(checksum_header, records) != header["checksum"]:
        _refuse(RecordingRefusal.CHECKSUM)
    anchor = checkpoint_from_replay(state, witness)
    if not isinstance(anchor, CheckpointAccepted):
        _refuse(RecordingRefusal.ANCHOR)
    return header, anchor.checkpoint, events


def _bundle(
    header: Mapping[str, object],
    initial: ReducerCheckpoint,
    events: Sequence[OrderedDashboardEvent],
) -> bytes:
    expected = cast(str, header["expectedFinalDigest"])
    integrity_without_checksum: dict[str, object] = {
        "integrityVersion": "dashboard-replay-integrity/v1",
        "algorithm": "sha256",
        "expectedFinalDigest": expected,
    }
    material: dict[str, object] = {
        "bundleVersion": "dashboard-replay-bundle/v1",
        "scenarioId": header["scenarioId"],
        "scenarioRevision": 1,
        "initialState": state_document(initial.state),
        "latestEventDigest": initial.latest_event_digest,
        "events": [_event_document(event) for event in events],
        "integrity": integrity_without_checksum,
    }
    checksum = hashlib.sha256(canonical.canonical_bytes(material)).hexdigest()
    material["integrity"] = {**integrity_without_checksum, "checksum": checksum}
    return canonical.canonical_bytes(material)


@overload
def validate_recording(raw: bytes, *, return_checkpoint: Literal[False] = False) -> bytes: ...


@overload
def validate_recording(raw: bytes, *, return_checkpoint: Literal[True]) -> ReducerCheckpoint: ...


def validate_recording(
    raw: bytes,
    *,
    return_checkpoint: bool = False,
) -> bytes | ReducerCheckpoint:
    """Validate, fold, and normalize one hostile recording without external effects."""
    header, initial, events = _validated_recording(raw)
    folds = tuple(_fold(initial, events) for _ in range(VALIDATION_FOLD_RUNS))
    final = folds[0]
    if any(candidate != final for candidate in folds[1:]):
        _refuse(RecordingRefusal.NONDETERMINISTIC)
    if state_digest(final.state) != header["expectedFinalDigest"]:
        _refuse(RecordingRefusal.FINAL_DIGEST)
    if return_checkpoint:
        return final
    return _bundle(header, initial, events)


def write_validated_replay(raw: bytes, output_directory: Path) -> Path:
    """Publish once, or accept an existing byte-identical regular replay bundle."""
    bundle = validate_recording(raw)
    if output_directory.is_symlink() or not output_directory.is_dir():
        _refuse(RecordingRefusal.OUTPUT_PATH)
    output_parent = output_directory.resolve(strict=True)
    output = output_parent / REPLAY_BUNDLE_FILENAME
    if os.path.lexists(output):
        if _regular_output_bytes(output, len(bundle)) == bundle:
            return output
        _refuse(RecordingRefusal.OUTPUT_EXISTS)
    file_descriptor, temporary_name = tempfile.mkstemp(dir=output_parent, prefix=".replay-")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(bundle)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, output)
        except FileExistsError:
            if _regular_output_bytes(output, len(bundle)) == bundle:
                return output
            _refuse(RecordingRefusal.OUTPUT_EXISTS)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output


def write_normalized_recording(raw: bytes, output_directory: Path) -> Path:
    """Validate and atomically publish one fixed recording without replacing any path."""
    validate_recording(raw)
    if output_directory.is_symlink() or not output_directory.is_dir():
        _refuse(RecordingRefusal.RECORDING_OUTPUT_PATH)
    output_parent = output_directory.resolve(strict=True)
    output = output_parent / NORMALIZED_RECORDING_FILENAME
    if os.path.lexists(output):
        _refuse(RecordingRefusal.RECORDING_OUTPUT_EXISTS)
    file_descriptor, temporary_name = tempfile.mkstemp(dir=output_parent, prefix=".recording-")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, output)
        except FileExistsError:
            _refuse(RecordingRefusal.RECORDING_OUTPUT_EXISTS)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_directory / NORMALIZED_RECORDING_FILENAME


def _regular_output_bytes(path: Path, maximum_bytes: int) -> bytes | None:
    """Read one exact-size regular output without following a symbolic link."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size != maximum_bytes:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read(maximum_bytes + 1)
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
