"""SSE registration, resume, overload, framing, and cleanup lifecycle."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import CurrentRun, RunMode, SnapshotBasis, StoredEvent
from aerial_rescue_dashboard_api.snapshot import SnapshotService
from aerial_rescue_dashboard_api.sse import ClientBuffer
from aerial_rescue_dashboard_api.stream import (
    EventStreamer,
    StreamHandle,
    StreamRegistry,
    native_last_event_id,
)

from tests.dashboard_api_support import FakeStore, live_prepared_state

pytestmark = [pytest.mark.integration]


def _current() -> CurrentRun:
    """Return one live run identity shared by stream cases."""
    return CurrentRun(
        RunMode.DEGRADED_LIVE,
        "wilderness-missing-person",
        1,
        "mission-test-0001",
        "run-test-0001",
        None,
    )


def _event(ordinal: int) -> StoredEvent:
    """Return one valid successor whose normalized payload can be repeated safely."""
    return StoredEvent(
        ordinal,
        "missionLifecycle",
        canonical.canonical_bytes(
            {
                "data": {"lifecycle": "PLANNED"},
                "eventClass": "MISSION",
                "kind": "missionLifecycle",
                "mission": "mission-test-0001",
                "time": "2026-08-25T12:00:01.000Z",
            }
        ),
    )


async def _dispose(handle: StreamHandle) -> None:
    """Release a handle not consumed through its body generator."""
    handle.producer.cancel()
    with suppress(asyncio.CancelledError):
        await handle.producer
    await handle.buffer.close()
    await handle.registry.release(handle.token)


class StreamRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ninth_client_is_refused_until_one_registration_is_released(self) -> None:
        # Arrange
        registry = StreamRegistry(maximum_clients=2)
        first = await registry.acquire()
        second = await registry.acquire()

        # Act
        with pytest.raises(ApiError) as captured:
            await registry.acquire()
        await registry.release(first)
        replacement = await registry.acquire()

        # Assert
        self.assertIs(ErrorCode.SSE_CAPACITY_EXCEEDED, captured.value.code)
        self.assertEqual(2, registry.count)
        self.assertNotIn(replacement, {first, second})


class EventStreamerTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_ascii_or_repeated_native_cursor_is_treated_as_unverifiable(self) -> None:
        # Arrange
        malformed = (b"\xff",)
        repeated = (b"0" * 64, b"1" * 64)

        # Act
        malformed_result = native_last_event_id(malformed)
        repeated_result = native_last_event_id(repeated)
        absent_result = native_last_event_id(())

        # Assert
        self.assertIsNone(malformed_result)
        self.assertIsNone(repeated_result)
        self.assertIsNone(absent_result)

    async def test_no_current_run_emits_one_snapshot_and_generator_close_releases_resources(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        registry = StreamRegistry()
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )
        handle = await EventStreamer(store, snapshots, registry).open(None)
        body = cast("AsyncGenerator[bytes]", handle.body())

        # Act
        first = await anext(body)
        await body.aclose()

        # Assert
        self.assertTrue(first.startswith(b"event: snapshot\nid: "))
        self.assertIn(b'"currentRun":null', first)
        self.assertEqual(0, registry.count)
        self.assertTrue(handle.buffer.closed)

    async def test_source_replacement_after_empty_snapshot_observes_first_start_without_offline(
        self,
    ) -> None:
        # Arrange
        store = FakeStore()
        registry = StreamRegistry()
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )
        streamer = EventStreamer(store, snapshots, registry)
        empty = await streamer.open(None)
        empty_body = cast("AsyncGenerator[bytes]", empty.body())

        # Act
        with patch("aerial_rescue_dashboard_api.stream.KEEPALIVE_SECONDS", 0.001):
            empty_snapshot = await anext(empty_body)
            keepalive = await anext(empty_body)
        no_run_cursor = empty_snapshot.split(b"id: ", 1)[1].split(b"\n", 1)[0].decode()
        current = _current()
        store.current = current
        store.basis = SnapshotBasis(current, live_prepared_state(), 0)
        await empty_body.aclose()
        reconnected = await streamer.open(no_run_cursor)
        reconnected_body = cast("AsyncGenerator[bytes]", reconnected.body())
        activated = await anext(reconnected_body)
        await reconnected_body.aclose()

        # Assert
        self.assertIn(b'"currentRun":null', empty_snapshot)
        self.assertEqual(b": keepalive\n\n", keepalive)
        self.assertIn(b'"runId":"run-test-0001"', activated)
        self.assertEqual(0, registry.count)

    async def test_valid_native_cursor_resumes_without_snapshot_while_stale_cursor_resnapshots(
        self,
    ) -> None:
        # Arrange
        current = _current()
        store = FakeStore(
            current=current,
            basis=SnapshotBasis(current, live_prepared_state(), 1),
            events=(_event(1),),
        )
        registry = StreamRegistry()
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )
        capture = await snapshots.capture()
        streamer = EventStreamer(store, snapshots, registry)

        # Act
        resumed = await streamer.open(capture.cursor)
        stale = await streamer.open("0" * 64)

        # Assert
        self.assertIsNone(resumed.initial)
        self.assertIsNotNone(stale.initial)
        self.assertEqual(2, registry.count)
        await _dispose(resumed)
        await _dispose(stale)
        self.assertEqual(0, registry.count)

    async def test_slow_non_droppable_suffix_gets_one_terminal_frame_then_cleanup(self) -> None:
        # Arrange
        current = _current()
        store = FakeStore(
            current=current,
            basis=SnapshotBasis(current, live_prepared_state(), 0),
            events=tuple(_event(ordinal) for ordinal in range(1, 258)),
        )
        registry = StreamRegistry()
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )
        handle = await EventStreamer(store, snapshots, registry).open(None)
        body = cast("AsyncGenerator[bytes]", handle.body())

        # Act
        snapshot = await anext(body)
        await handle.producer
        frames = [snapshot, *[frame async for frame in body]]

        # Assert
        self.assertEqual(258, len(frames))
        self.assertEqual(1, sum(frame.startswith(b"event: snapshot") for frame in frames))
        self.assertEqual(256, sum(frame.startswith(b"event: dashboard-event") for frame in frames))
        self.assertEqual(1, sum(frame.startswith(b"event: stream-overloaded") for frame in frames))
        self.assertEqual(0, registry.count)

    async def test_active_consumer_drains_one_large_suffix_page_without_artificial_overload(
        self,
    ) -> None:
        # Arrange
        current = _current()
        store = FakeStore(
            current=current,
            basis=SnapshotBasis(current, live_prepared_state(), 0),
            events=tuple(_event(ordinal) for ordinal in range(1, 329)),
        )
        registry = StreamRegistry()
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )
        handle = await EventStreamer(store, snapshots, registry).open(None)
        body = cast("AsyncGenerator[bytes]", handle.body())

        # Act
        await asyncio.sleep(0)
        calls_before_body_started = tuple(store.calls)
        frames = [await asyncio.wait_for(anext(body), 1.0) for _ in range(329)]
        await body.aclose()
        event_frames = tuple(
            frame for frame in frames if frame.startswith(b"event: dashboard-event")
        )

        # Assert
        self.assertFalse(any(call.startswith("events:") for call in calls_before_body_started))
        self.assertEqual(1, sum(frame.startswith(b"event: snapshot") for frame in frames))
        self.assertEqual(328, len(event_frames))
        self.assertTrue(
            all(
                f'"auditOrdinal":{ordinal}'.encode() in frame
                for ordinal, frame in enumerate(event_frames, start=1)
            )
        )
        self.assertFalse(any(frame.startswith(b"event: stream-overloaded") for frame in frames))
        self.assertEqual(0, registry.count)

    async def test_empty_live_buffer_emits_comment_keepalive_until_cancelled(self) -> None:
        # Arrange
        registry = StreamRegistry()
        token = await registry.acquire()
        buffer = ClientBuffer(capacity=1)
        producer = asyncio.create_task(asyncio.sleep(60))
        handle = StreamHandle(token, None, buffer, producer, registry, asyncio.Event())
        body = cast("AsyncGenerator[bytes]", handle.body())

        # Act
        with patch("aerial_rescue_dashboard_api.stream.KEEPALIVE_SECONDS", 0.001):
            keepalive = await anext(body)
        await body.aclose()

        # Assert
        self.assertEqual(b": keepalive\n\n", keepalive)
        self.assertEqual(0, registry.count)

    async def test_snapshot_failure_releases_capacity_before_propagating_refusal(self) -> None:
        # Arrange
        current = _current()
        store = FakeStore(
            current=current,
            basis=SnapshotBasis(current, b"not-json", 0),
        )
        registry = StreamRegistry(maximum_clients=1)
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )

        # Act
        with pytest.raises(ApiError) as captured:
            await EventStreamer(store, snapshots, registry).open(None)

        # Assert
        self.assertIs(ErrorCode.DEPENDENCY_UNAVAILABLE, captured.value.code)
        self.assertEqual(0, registry.count)
