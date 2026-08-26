"""Empty dashboard streams remain connected without inventing data frames."""

from __future__ import annotations

import unittest
from collections.abc import AsyncGenerator
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.snapshot import SnapshotService
from aerial_rescue_dashboard_api.stream import EventStreamer, StreamHandle, StreamRegistry

from tests.dashboard_api_support import FakeStore

pytestmark = [pytest.mark.integration]


class EmptyStreamLivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_current_run_keeps_the_connection_alive_with_comments(self) -> None:
        # Arrange
        store = FakeStore()
        registry = StreamRegistry()
        snapshots = SnapshotService(
            store,
            CursorCodec("runtime-test-0001", b"c" * 32),
            "runtime-test-0001",
        )
        handle: StreamHandle = await EventStreamer(store, snapshots, registry).open(None)
        body = cast("AsyncGenerator[bytes]", handle.body())

        # Act
        with patch("aerial_rescue_dashboard_api.stream.KEEPALIVE_SECONDS", 0.001):
            snapshot = await anext(body)
            keepalive = await anext(body)
        await body.aclose()

        # Assert
        self.assertTrue(snapshot.startswith(b"event: snapshot\n"))
        self.assertIn(b'"currentRun":null', snapshot)
        self.assertEqual(b": keepalive\n\n", keepalive)
        self.assertEqual(0, registry.count)
        self.assertTrue(handle.buffer.closed)
