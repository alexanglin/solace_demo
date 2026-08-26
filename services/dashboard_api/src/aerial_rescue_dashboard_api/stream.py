"""Bounded SSE session lifecycle over ordered store suffix reads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from aerial_rescue_contracts.view import ReducerCheckpoint

from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import StorePort
from aerial_rescue_dashboard_api.snapshot import SnapshotCapture, SnapshotService
from aerial_rescue_dashboard_api.sse import BufferDisposition, BufferedEvent, ClientBuffer

MAXIMUM_CLIENTS: Final = 8
BUFFER_CAPACITY: Final = 256
SUFFIX_PAGE_SIZE: Final = 512
POLL_SECONDS: Final = 0.250
KEEPALIVE_SECONDS: Final = 15.0


def native_last_event_id(values: tuple[bytes, ...]) -> str | None:
    """Treat every absent, repeated, or non-ASCII native cursor as unverifiable."""
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


class StreamRegistry:
    """Bound concurrent stream ownership and make disconnect cleanup explicit."""

    def __init__(self, maximum_clients: int = MAXIMUM_CLIENTS) -> None:
        """Create one process-local finite registration set."""
        self._maximum = maximum_clients
        self._next = 0
        self._clients: set[int] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> int:
        """Register one stream or refuse before allocating its buffer/task."""
        async with self._lock:
            if len(self._clients) >= self._maximum:
                raise ApiError(ErrorCode.SSE_CAPACITY_EXCEEDED)
            self._next += 1
            token = self._next
            self._clients.add(token)
            return token

    async def release(self, token: int) -> None:
        """Idempotently remove one disconnected client registration."""
        async with self._lock:
            self._clients.discard(token)

    @property
    def count(self) -> int:
        """Return current registrations for lifecycle evidence."""
        return len(self._clients)


@dataclass
class StreamHandle:
    """One registered stream and its bounded producer/consumer resources."""

    token: int
    initial: BufferedEvent | None
    buffer: ClientBuffer
    producer: asyncio.Task[None]
    registry: StreamRegistry
    started: asyncio.Event

    async def body(self) -> AsyncIterator[bytes]:
        """Yield the closed SSE frame vocabulary and bounded keepalive comments."""
        try:
            self.started.set()
            if self.initial is not None:
                yield _data_frame("snapshot", self.initial)
            while True:
                item = await self.buffer.wait_pop(KEEPALIVE_SECONDS)
                if item is None:
                    if self.producer.done() or self.buffer.closed:
                        break
                    yield b": keepalive\n\n"
                    continue
                if item.terminal:
                    yield _data_frame("stream-overloaded", item)
                    break
                yield _data_frame("dashboard-event", item)
        finally:
            self.producer.cancel()
            with suppress(asyncio.CancelledError):
                await self.producer
            await self.buffer.close()
            await self.registry.release(self.token)


class EventStreamer:
    """Open snapshot/resume streams and poll ordered suffixes concurrently with delivery."""

    def __init__(
        self,
        store: StorePort,
        snapshots: SnapshotService,
        registry: StreamRegistry | None = None,
    ) -> None:
        """Retain injected reads, fold service, and one finite process registry."""
        self._store = store
        self._snapshots = snapshots
        self._registry = registry or StreamRegistry()

    async def open(self, last_event_id: str | None) -> StreamHandle:
        """Register before allocation and resnapshot every unverifiable cursor."""
        token = await self._registry.acquire()
        try:
            capture = await self._snapshots.capture()
            checkpoint = capture.checkpoint
            initial: BufferedEvent | None = BufferedEvent("SNAPSHOT", capture.body, capture.cursor)
            if last_event_id is not None and capture.basis is not None:
                latest = capture.basis.audit_watermark
                oldest = max(0, latest - 512)
                resolved = self._snapshots.resolve_cursor(
                    last_event_id,
                    capture.basis.current_run,
                    oldest_ordinal=oldest,
                    latest_ordinal=latest,
                )
                if resolved is not None:
                    checkpoint, _ = await self._snapshots.fold_basis_through(
                        capture.basis,
                        resolved,
                    )
                    initial = None
            buffer = ClientBuffer(capacity=BUFFER_CAPACITY)
            started = asyncio.Event()
            producer = asyncio.create_task(
                self._produce_after_start(capture, checkpoint, buffer, started),
                name=f"dashboard-sse-{token}",
            )
            return StreamHandle(token, initial, buffer, producer, self._registry, started)
        except BaseException:
            await self._registry.release(token)
            raise

    async def _produce_after_start(
        self,
        capture: SnapshotCapture,
        checkpoint: ReducerCheckpoint,
        buffer: ClientBuffer,
        started: asyncio.Event,
    ) -> None:
        """Do not read a suffix until the HTTP body consumer has started."""
        await started.wait()
        await self._produce(capture, checkpoint, buffer)

    async def _produce(
        self,
        capture: SnapshotCapture,
        checkpoint: ReducerCheckpoint,
        buffer: ClientBuffer,
    ) -> None:
        """Poll exact suffix pages while a slow HTTP consumer drains independently."""
        if capture.basis is None:
            while not buffer.closed:
                await asyncio.sleep(POLL_SECONDS)
            return
        current = checkpoint
        run = capture.basis.current_run
        while not buffer.closed:
            page = await self._store.read_events(
                run,
                current.state.latest_audit_ordinal,
                None,
                SUFFIX_PAGE_SIZE,
            )
            for stored in page:
                frame = self._snapshots.fold_frame(run, current, stored)
                current = frame.checkpoint
                disposition = await buffer.push(
                    BufferedEvent(frame.event_class, frame.body, frame.cursor)
                )
                if disposition in {BufferDisposition.OVERLOADED, BufferDisposition.CLOSED}:
                    return
                if disposition is BufferDisposition.RETAINED:
                    await asyncio.sleep(0)
            if len(page) < SUFFIX_PAGE_SIZE:
                await asyncio.sleep(POLL_SECONDS)


def _data_frame(event_name: str, item: BufferedEvent) -> bytes:
    """Serialize one canonical payload with an optional identical native SSE id."""
    lines = [f"event: {event_name}\n".encode()]
    if item.cursor is not None:
        lines.append(f"id: {item.cursor}\n".encode())
    lines.extend((b"data: ", item.payload, b"\n\n"))
    return b"".join(lines)
