"""Audit-ordered broker projection and finite server-sent-event fan-out."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import Envelope, decode_envelope, envelope_document
from aerial_rescue_contracts.view import (
    MAX_BUFFERED_EVENTS,
    FoldApplied,
    FoldDuplicate,
    FoldRefused,
    OrderedDashboardEvent,
    ReducerCheckpoint,
    ViewError,
    droppable,
    fold_ordered_event,
    ordered_event_document,
    project,
    state_digest,
    state_document,
)
from aerial_rescue_store.audit import StoredAuditRecord

from aerial_rescue_dashboard_api.boundary.application import DataFrame, EventFrame, Keepalive
from aerial_rescue_dashboard_api.boundary.wire import parse_wire_document
from aerial_rescue_dashboard_api.sse_buffer import (
    BufferDecision,
    BufferedFrame,
    ClientEventBuffer,
)

_SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/dashboard/"
_TIMELINE_CAPACITY: Final = 256
_OVERLOAD_BODY: Final = canonical.canonical_bytes(
    {
        "controlVersion": "dashboard-stream-overloaded/v1",
        "reason": "NON_DROPPABLE_BUFFER_FULL",
    }
)

type CursorIssuer = Callable[[int, str | None], str]


class ApplyDecision(Enum):
    """Whether an audit record advanced the projection or proved an exact duplicate."""

    APPLIED = "applied"
    DUPLICATE = "exact duplicate"


class ProjectionRefusal(Enum):
    """Why an audit record, client, or configuration cannot enter the live projection."""

    CONFIGURATION = "projection bounds or identity are invalid"
    CLIENT_CAPACITY = "the bounded SSE client set is full"
    AUDIT_BINDING = "the audit columns do not bind to their canonical envelope"
    PAYLOAD_SCHEMA = "the recovered payload fails its registered runtime schema"
    EVENT_PROJECTION = "the recovered envelope has no valid normalized projection"
    REDUCER = "the ordered event cannot advance the reducer checkpoint"


class ProjectionError(ValueError):
    """A typed projection refusal carrying no raw event or payload bytes."""

    def __init__(self, refusal: ProjectionRefusal, value: object) -> None:
        """Retain one stable refusal and already-redacted diagnostic value."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class ProjectionEventStream:
    """One registered client with a snapshot-first, finite async iterator."""

    def __init__(
        self,
        hub: DashboardProjectionHub,
        client_id: int,
        snapshot: bytes,
        capacity: int,
        keepalive_milliseconds: int,
    ) -> None:
        """Register a snapshot and an empty finite suffix buffer."""
        self._hub = hub
        self._client_id = client_id
        self._snapshot: bytes | None = snapshot
        self._buffer = ClientEventBuffer(capacity)
        self._keepalive_seconds = keepalive_milliseconds / 1_000
        self._pending: deque[BufferedFrame] = deque()
        self._available = asyncio.Event()
        self._closed = False
        self._terminal_emitted = False

    def __aiter__(self) -> ProjectionEventStream:
        """Return this stream as its own finite async iterator."""
        return self

    async def __anext__(self) -> EventFrame:
        """Return the snapshot, retained suffix frames, or one terminal control."""
        if self._closed:
            raise StopAsyncIteration
        if self._snapshot is not None:
            body = self._snapshot
            self._snapshot = None
            return DataFrame("snapshot", body)
        while True:
            ready = await self._next_ready_frame()
            if ready is not None:
                return ready
            if not await self._wait_for_output():
                return Keepalive()
            self._available.clear()
            if self._closed:
                raise StopAsyncIteration

    async def _next_ready_frame(self) -> EventFrame | None:
        """Return one retained or terminal frame without waiting for new output."""
        if self._pending:
            return DataFrame("dashboard-event", self._pending.popleft().payload)
        self._pending.extend(self._buffer.drain())
        if self._pending:
            return DataFrame("dashboard-event", self._pending.popleft().payload)
        if self._buffer.terminal_required and not self._terminal_emitted:
            self._terminal_emitted = True
            await self.close()
            return DataFrame("stream-overloaded", _OVERLOAD_BODY)
        return None

    async def _wait_for_output(self) -> bool:
        """Wait one bounded idle interval and report whether a producer woke the stream."""
        try:
            await asyncio.wait_for(
                self._available.wait(),
                timeout=self._keepalive_seconds,
            )
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        """Release this client registration and wake a blocked iterator exactly once."""
        if self._closed:
            return
        self._closed = True
        self._available.set()
        await self._hub._remove_client(self._client_id)

    def offer(self, frame: BufferedFrame) -> BufferDecision:
        """Offer one serialized successor and wake only when output became available."""
        decision = self._buffer.push(frame)
        if decision not in {BufferDecision.CLOSED, BufferDecision.DROPPED_TELEMETRY}:
            self._available.set()
        return decision


@dataclass(frozen=True)
class ProjectionHubSettings:
    """Explicit process bounds selected by the live composition."""

    max_clients: int
    client_buffer_capacity: int = MAX_BUFFERED_EVENTS
    keepalive_milliseconds: int = 15_000


class DashboardProjectionHub:
    """Own one reducer checkpoint and fan out its validated ordered suffix."""

    def __init__(
        self,
        *,
        runtime_id: str,
        checkpoint: ReducerCheckpoint,
        current_run: Mapping[str, object] | None,
        cursor: CursorIssuer,
        settings: ProjectionHubSettings,
    ) -> None:
        """Create one hub after validating all allocation bounds."""
        if (
            not runtime_id
            or type(settings.max_clients) is not int
            or settings.max_clients <= 0
            or type(settings.client_buffer_capacity) is not int
            or type(settings.keepalive_milliseconds) is not int
            or settings.keepalive_milliseconds <= 0
        ):
            raise ProjectionError(ProjectionRefusal.CONFIGURATION, "redacted")
        try:
            ClientEventBuffer(settings.client_buffer_capacity)
        except ValueError as error:
            raise ProjectionError(ProjectionRefusal.CONFIGURATION, "redacted") from error
        self._runtime_id = runtime_id
        self._checkpoint = checkpoint
        self._current_run = None if current_run is None else dict(current_run)
        self._cursor = cursor
        self._max_clients = settings.max_clients
        self._client_buffer_capacity = settings.client_buffer_capacity
        self._keepalive_milliseconds = settings.keepalive_milliseconds
        self._timeline: list[dict[str, object]] = []
        self._clients: dict[int, ProjectionEventStream] = {}
        self._next_client_id = 1
        self._lock = asyncio.Lock()

    @property
    def checkpoint(self) -> ReducerCheckpoint:
        """Return the current immutable reducer checkpoint."""
        return self._checkpoint

    @property
    def latest_audit_ordinal(self) -> int:
        """Return the current mission's durable recovery high-water mark."""
        return self._checkpoint.state.latest_audit_ordinal

    @property
    def client_count(self) -> int:
        """Return the number of currently registered finite client buffers."""
        return len(self._clients)

    async def apply_audit(
        self,
        record: StoredAuditRecord,
        schemas: PayloadSchemaExecutor,
    ) -> ApplyDecision:
        """Validate and fold one durable audit row before broadcasting its suffix frame."""
        async with self._lock:
            envelope = _validated_audit_envelope(record, schemas)
            try:
                projected = project(envelope)
            except ViewError as error:
                raise ProjectionError(
                    ProjectionRefusal.EVENT_PROJECTION,
                    record.kind,
                ) from error
            ordered = OrderedDashboardEvent(record.ordinal, projected)
            before = self._checkpoint
            outcome = fold_ordered_event(before, ordered)
            if isinstance(outcome, FoldDuplicate):
                return ApplyDecision.DUPLICATE
            if isinstance(outcome, FoldRefused):
                raise ProjectionError(ProjectionRefusal.REDUCER, outcome.refusal.name)
            if not isinstance(outcome, FoldApplied):
                raise ProjectionError(ProjectionRefusal.REDUCER, "unknown-outcome")
            after = outcome.checkpoint
            self._checkpoint = after
            normalized = ordered_event_document(ordered)
            ordered_document = {
                "auditOrdinal": ordered.audit_ordinal,
                "event": normalized["event"],
            }
            if not droppable(projected.event_class):
                self._timeline.append(ordered_document)
                if len(self._timeline) > _TIMELINE_CAPACITY:
                    del self._timeline[0]
            frame = _event_frame(after, ordered_document, self._cursor)
            serialized = canonical.canonical_bytes(frame)
            _validate_output("dashboard-event-frame", serialized)
            buffered = BufferedFrame(serialized, droppable(projected.event_class))
            for stream in tuple(self._clients.values()):
                stream.offer(buffered)
            return ApplyDecision.APPLIED

    async def open_events(self) -> ProjectionEventStream:
        """Register one bounded client atomically with its complete snapshot."""
        async with self._lock:
            if len(self._clients) >= self._max_clients:
                raise ProjectionError(ProjectionRefusal.CLIENT_CAPACITY, self._max_clients)
            client_id = self._next_client_id
            self._next_client_id += 1
            snapshot = self._snapshot_bytes()
            stream = ProjectionEventStream(
                self,
                client_id,
                snapshot,
                self._client_buffer_capacity,
                self._keepalive_milliseconds,
            )
            self._clients[client_id] = stream
            return stream

    async def replace_run(
        self,
        checkpoint: ReducerCheckpoint,
        current_run: Mapping[str, object] | None,
    ) -> None:
        """Atomically replace mission state and release every client of the prior run."""
        async with self._lock:
            streams = tuple(self._clients.values())
            self._clients.clear()
            self._checkpoint = checkpoint
            self._current_run = None if current_run is None else dict(current_run)
            self._timeline.clear()
        for stream in streams:
            await stream.close()

    async def close(self) -> None:
        """Close every registered stream without retaining its buffer."""
        async with self._lock:
            streams = tuple(self._clients.values())
            self._clients.clear()
        for stream in streams:
            await stream.close()

    async def _remove_client(self, client_id: int) -> None:
        """Remove one exact registration without affecting another stream."""
        async with self._lock:
            self._clients.pop(client_id, None)

    def _snapshot_bytes(self) -> bytes:
        checkpoint = self._checkpoint
        snapshot = {
            "snapshotVersion": "dashboard-snapshot/v1",
            "runtimeId": self._runtime_id,
            "cursor": self._cursor(
                checkpoint.state.latest_audit_ordinal,
                checkpoint.latest_event_digest,
            ),
            "digest": state_digest(checkpoint.state),
            "latestEventDigest": checkpoint.latest_event_digest,
            "currentRun": self._current_run,
            "state": state_document(checkpoint.state),
            "timeline": list(self._timeline),
        }
        serialized = canonical.canonical_bytes(snapshot)
        _validate_output("dashboard-snapshot", serialized)
        return serialized


def _validated_audit_envelope(
    record: StoredAuditRecord,
    schemas: PayloadSchemaExecutor,
) -> Envelope:
    try:
        envelope = decode_envelope(record.payload)
        canonical_payload = canonical.canonical_bytes(envelope_document(envelope))
    except (TypeError, ValueError) as error:
        raise ProjectionError(ProjectionRefusal.AUDIT_BINDING, record.kind) from error
    columns_match = (
        canonical_payload == record.payload
        and record.mission_id == envelope.subject
        and record.kind == envelope.type
        and record.occurred_at == envelope.time
        and record.correlation_id == envelope.correlation_id
        and record.causation_id == envelope.causation_id
        and record.traceparent == envelope.traceparent
    )
    if not columns_match:
        raise ProjectionError(ProjectionRefusal.AUDIT_BINDING, record.kind)
    try:
        schemas.validate(envelope.dataschema, envelope.data)
    except (TypeError, ValueError) as error:
        raise ProjectionError(ProjectionRefusal.PAYLOAD_SCHEMA, record.kind) from error
    return envelope


def _event_frame(
    checkpoint: ReducerCheckpoint,
    ordered_document: dict[str, object],
    cursor: CursorIssuer,
) -> dict[str, object]:
    """Build one closed successor frame from contracts-owned reducer values."""
    return {
        "frameVersion": "ordered-dashboard-event-frame/v1",
        "cursor": cursor(checkpoint.state.latest_audit_ordinal, checkpoint.latest_event_digest),
        "digest": state_digest(checkpoint.state),
        "event": ordered_document,
    }


def _validate_output(name: str, payload: bytes) -> None:
    """Execute the strict service-local output model before bytes reach ASGI."""
    parse_wire_document(f"{_SCHEMA_PREFIX}{name}.schema.json", payload)
