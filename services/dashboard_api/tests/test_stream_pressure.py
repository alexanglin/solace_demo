"""Production-representative suffix pressure over the unchanged client buffer."""

from __future__ import annotations

import unittest
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.ports import CurrentRun, RunMode, SnapshotBasis, StoredEvent
from aerial_rescue_dashboard_api.snapshot import SnapshotService
from aerial_rescue_dashboard_api.stream import (
    BUFFER_CAPACITY,
    SUFFIX_PAGE_SIZE,
    EventStreamer,
    StreamRegistry,
)

from tests.dashboard_api_support import FakeStore, live_prepared_state

pytestmark = [pytest.mark.integration]


def _current() -> CurrentRun:
    return CurrentRun(
        RunMode.DEGRADED_LIVE,
        "wilderness-missing-person",
        1,
        "mission-test-0001",
        "run-pressure-0001",
        None,
    )


def _event(ordinal: int) -> StoredEvent:
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


class StreamPressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_real_store_page_can_overload_the_unchanged_256_event_buffer(self) -> None:
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
            CursorCodec("runtime-pressure-0001", b"p" * 32),
            "runtime-pressure-0001",
        )

        # Act
        handle = await EventStreamer(store, snapshots, registry).open(None)
        body = cast("AsyncGenerator[bytes]", handle.body())
        snapshot = await anext(body)
        await handle.producer
        frames = (snapshot, *[frame async for frame in body])

        # Assert
        self.assertEqual(256, BUFFER_CAPACITY)
        self.assertEqual(512, SUFFIX_PAGE_SIZE)
        self.assertIn(
            f"events:{current.identity}:0:None:512",
            store.calls,
        )
        self.assertEqual(1, sum(frame.startswith(b"event: stream-overloaded") for frame in frames))
        self.assertEqual(0, registry.count)


if __name__ == "__main__":
    unittest.main()
