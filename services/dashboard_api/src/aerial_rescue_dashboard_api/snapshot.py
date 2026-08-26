"""Atomic prepared-state folding and ordered dashboard snapshot construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.view import (
    CheckpointAccepted,
    Connectivity,
    DashboardEvent,
    DashboardReducedState,
    DeclaredOnlyFleetMember,
    EventClass,
    FoldApplied,
    FoldDuplicate,
    Mission,
    MissionLifecycle,
    OrderedDashboardEvent,
    Participation,
    ReducerCheckpoint,
    Sector,
    SectorState,
    SimulatedFleetMember,
    Telemetry,
    checkpoint_from_snapshot,
    fold_ordered_event,
    state_digest,
    state_document,
)

from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.documents import (
    ORDERED_EVENT_SCHEMA,
    REDUCED_STATE_SCHEMA,
    SCHEMA_PREFIX,
    validated_document,
)
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import CurrentRun, SnapshotBasis, StoredEvent, StorePort

DASHBOARD_EVENT_SCHEMA: Final = f"{SCHEMA_PREFIX}dashboard-event.schema.json"
SNAPSHOT_SCHEMA: Final = f"{SCHEMA_PREFIX}dashboard-snapshot.schema.json"
FRAME_SCHEMA: Final = f"{SCHEMA_PREFIX}dashboard-event-frame.schema.json"
EVENT_PAGE_SIZE: Final = 256
MAXIMUM_RECONSTRUCTION_EVENTS: Final = 512
MAXIMUM_DOCUMENT_BYTES: Final = 512 * 1024


@dataclass(frozen=True)
class SnapshotCapture:
    """A folded checkpoint and its exact public snapshot representation."""

    basis: SnapshotBasis | None
    checkpoint: ReducerCheckpoint
    timeline: tuple[Mapping[str, object], ...]
    cursor: str
    body: bytes

    @property
    def audit_ordinal(self) -> int:
        """Return the durable ordinal reached by this capture."""
        return self.checkpoint.state.latest_audit_ordinal


@dataclass(frozen=True)
class FoldedFrame:
    """One successor checkpoint and serialized dashboard-event frame."""

    checkpoint: ReducerCheckpoint
    event_class: str
    cursor: str
    body: bytes


class SnapshotService:
    """Fold only bounded audit reads from an atomically captured snapshot basis."""

    def __init__(self, store: StorePort, cursor: CursorCodec, runtime_id: str) -> None:
        """Retain injected durable reads and an independent cursor codec."""
        self._store = store
        self._cursor = cursor
        self._runtime_id = runtime_id

    async def capture(self) -> SnapshotCapture:
        """Capture an atomic watermark, fold through it, and serialize one snapshot."""
        basis = await self._store.capture_snapshot_basis()
        if basis is None:
            checkpoint = _empty_checkpoint()
            run_identity = "no-run"
            timeline: tuple[Mapping[str, object], ...] = ()
        else:
            checkpoint, timeline = await self.fold_basis_through(basis, basis.audit_watermark)
            run_identity = basis.current_run.identity
        cursor = self._cursor.issue(run_identity, checkpoint.state.latest_audit_ordinal)
        body = _snapshot_document(
            self._runtime_id,
            cursor,
            basis.current_run if basis is not None else None,
            checkpoint,
            timeline,
        )
        return SnapshotCapture(basis, checkpoint, timeline, cursor, body)

    async def fold_basis_through(
        self, basis: SnapshotBasis, through_ordinal: int
    ) -> tuple[ReducerCheckpoint, tuple[Mapping[str, object], ...]]:
        """Reconstruct no more than 512 events from exact prepared bytes through a watermark."""
        if through_ordinal < 0 or through_ordinal > basis.audit_watermark:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        checkpoint = _checkpoint_from_bytes(basis.prepared_initial_state)
        timeline: list[Mapping[str, object]] = []
        read_count = 0
        while checkpoint.state.latest_audit_ordinal < through_ordinal:
            remaining = through_ordinal - checkpoint.state.latest_audit_ordinal
            page = await self._store.read_events(
                basis.current_run,
                checkpoint.state.latest_audit_ordinal,
                through_ordinal,
                min(EVENT_PAGE_SIZE, remaining),
            )
            if not page:
                raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
            checkpoint, read_count = _fold_snapshot_page(
                checkpoint,
                page,
                timeline,
                read_count,
            )
        return checkpoint, tuple(timeline)

    def fold_frame(
        self,
        run: CurrentRun,
        checkpoint: ReducerCheckpoint,
        stored: StoredEvent,
    ) -> FoldedFrame:
        """Fold and serialize one ordered suffix event."""
        ordered, document = _ordered_event(stored)
        successor = _fold(checkpoint, ordered)
        cursor = self._cursor.issue(run.identity, successor.state.latest_audit_ordinal)
        frame = {
            "cursor": cursor,
            "digest": state_digest(successor.state),
            "event": document,
            "frameVersion": "ordered-dashboard-event-frame/v1",
        }
        encoded = canonical.canonical_bytes(frame)
        validated_document(FRAME_SCHEMA, encoded, maximum_bytes=MAXIMUM_DOCUMENT_BYTES)
        return FoldedFrame(successor, ordered.event.event_class.name, cursor, encoded)

    def resolve_cursor(
        self,
        cursor: str,
        run: CurrentRun,
        *,
        oldest_ordinal: int,
        latest_ordinal: int,
    ) -> int | None:
        """Resolve one cursor only within the supplied current-run reconstruction window."""
        return self._cursor.resolve(
            cursor,
            run.identity,
            oldest_ordinal=oldest_ordinal,
            latest_ordinal=latest_ordinal,
        )


def _snapshot_document(
    runtime_id: str,
    cursor: str,
    run: CurrentRun | None,
    checkpoint: ReducerCheckpoint,
    timeline: Sequence[Mapping[str, object]],
) -> bytes:
    """Build and validate the public snapshot around the contracts-owned state document."""
    document = {
        "currentRun": _current_run_document(run),
        "cursor": cursor,
        "digest": state_digest(checkpoint.state),
        "latestEventDigest": checkpoint.latest_event_digest,
        "runtimeId": runtime_id,
        "snapshotVersion": "dashboard-snapshot/v1",
        "state": state_document(checkpoint.state),
        "timeline": list(timeline),
    }
    encoded = canonical.canonical_bytes(document)
    validated_document(SNAPSHOT_SCHEMA, encoded, maximum_bytes=MAXIMUM_DOCUMENT_BYTES)
    return encoded


def _current_run_document(run: CurrentRun | None) -> Mapping[str, object] | None:
    """Project only the public identity selected by one current run mode."""
    if run is None:
        return None
    if run.mode.value == "replay":
        return {"mode": "replay", "sessionId": run.session_id}
    return {"missionId": run.mission_id, "mode": "degradedLive", "runId": run.run_id}


def _empty_checkpoint() -> ReducerCheckpoint:
    """Return the canonical no-current-run snapshot anchor."""
    return ReducerCheckpoint(DashboardReducedState(None, (), 0, ()), None)


def _fold_snapshot_page(
    checkpoint: ReducerCheckpoint,
    page: Sequence[StoredEvent],
    timeline: list[Mapping[str, object]],
    read_count: int,
) -> tuple[ReducerCheckpoint, int]:
    """Fold one bounded store page and append its non-telemetry timeline entries."""
    for stored in page:
        read_count += 1
        if read_count > MAXIMUM_RECONSTRUCTION_EVENTS:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
        ordered, document = _ordered_event(stored)
        checkpoint = _fold(checkpoint, ordered)
        if ordered.event.event_class is EventClass.TELEMETRY:
            continue
        timeline.append(document)
        if len(timeline) > EVENT_PAGE_SIZE:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return checkpoint, read_count


def _checkpoint_from_bytes(raw: bytes) -> ReducerCheckpoint:
    """Validate and adapt exact prepared reduced-state bytes into one checkpoint."""
    document = validated_document(
        REDUCED_STATE_SCHEMA,
        raw,
        maximum_bytes=MAXIMUM_DOCUMENT_BYTES,
    )
    outcome = checkpoint_from_snapshot(_reduced_state(document), None)
    if not isinstance(outcome, CheckpointAccepted):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return outcome.checkpoint


def _ordered_event(stored: StoredEvent) -> tuple[OrderedDashboardEvent, Mapping[str, object]]:
    """Validate raw normalized input before adapting it to the contracts-owned reducer."""
    event_document = validated_document(
        DASHBOARD_EVENT_SCHEMA,
        stored.payload,
        maximum_bytes=64 * 1024,
    )
    if stored.kind != _string(event_document.get("kind")):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    document: Mapping[str, object] = {
        "auditOrdinal": stored.audit_ordinal,
        "event": dict(event_document),
    }
    validated_document(
        ORDERED_EVENT_SCHEMA,
        canonical.canonical_bytes(document),
        maximum_bytes=64 * 1024,
    )
    return OrderedDashboardEvent(stored.audit_ordinal, _dashboard_event(event_document)), document


def _fold(checkpoint: ReducerCheckpoint, event: OrderedDashboardEvent) -> ReducerCheckpoint:
    """Accept only applied or exact-duplicate reducer outcomes."""
    outcome = fold_ordered_event(checkpoint, event)
    if isinstance(outcome, (FoldApplied, FoldDuplicate)):
        return outcome.checkpoint
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)


def _dashboard_event(document: Mapping[str, object]) -> DashboardEvent:
    """Adapt a strictly validated event document without reimplementing its domain rule."""
    return DashboardEvent(
        kind=_string(document.get("kind")),
        event_class=EventClass[_string(document.get("eventClass"))],
        mission=_string(document.get("mission")),
        time=_string(document.get("time")),
        data=_mapping(document.get("data")),
    )


def _reduced_state(document: Mapping[str, object]) -> DashboardReducedState:
    """Adapt the strict Pydantic boundary into immutable reducer domain values."""
    mission_document = document.get("currentMission")
    mission = None if mission_document is None else _mission(_mapping(mission_document))
    fleet = tuple(_fleet_member(_mapping(item)) for item in _sequence(document.get("fleet")))
    sectors = tuple(_sector(_mapping(item)) for item in _sequence(document.get("sectors")))
    ordinal = _integer(document.get("latestAuditOrdinal"))
    return DashboardReducedState(mission, fleet, ordinal, sectors)


def _mission(document: Mapping[str, object]) -> Mission:
    """Adapt one validated mission member."""
    predecessor = document.get("predecessorIdentifier")
    return Mission(
        _string(document.get("identifier")),
        MissionLifecycle(_string(document.get("lifecycle"))),
        None if predecessor is None else _string(predecessor),
    )


def _fleet_member(document: Mapping[str, object]) -> SimulatedFleetMember | DeclaredOnlyFleetMember:
    """Adapt one participation-discriminated validated fleet member."""
    identifier = _string(document.get("identifier"))
    participation = Participation(_string(document.get("participation")))
    if participation is Participation.DECLARED_ONLY:
        return DeclaredOnlyFleetMember(identifier)
    telemetry_document = document.get("telemetry")
    telemetry = None if telemetry_document is None else _telemetry(_mapping(telemetry_document))
    return SimulatedFleetMember(
        identifier,
        connectivity=Connectivity(_string(document.get("connectivity"))),
        telemetry=telemetry,
    )


def _telemetry(document: Mapping[str, object]) -> Telemetry:
    """Adapt one integer-only latest telemetry member."""
    return Telemetry(
        _integer(document.get("latitudeMicrodegrees")),
        _integer(document.get("longitudeMicrodegrees")),
        _integer(document.get("batteryPercent")),
        _integer(document.get("altitudeMetres")),
        _integer(document.get("headingDegrees")),
        _integer(document.get("groundSpeedCentimetresPerSecond")),
    )


def _sector(document: Mapping[str, object]) -> Sector:
    """Adapt one validated sector lifecycle member."""
    assigned = document.get("assignedMemberId")
    return Sector(
        _string(document.get("identifier")),
        SectorState(_string(document.get("state"))),
        None if assigned is None else _string(assigned),
    )


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow one already validated object."""
    if not isinstance(value, Mapping):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object]:
    """Narrow one already validated array."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return cast(Sequence[object], value)


def _string(value: object) -> str:
    """Narrow one already validated string."""
    if not isinstance(value, str):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return value


def _integer(value: object) -> int:
    """Narrow one already validated non-boolean integer."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return value
