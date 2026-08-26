"""Opaque cursor and finite SSE-client pressure policy."""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_dashboard_api.cursor import CursorCodec
from aerial_rescue_dashboard_api.sse import BufferDisposition, BufferedEvent, ClientBuffer

pytestmark = [pytest.mark.unit]


class CursorCodecTests(unittest.TestCase):
    def test_cursor_is_opaque_and_bound_to_runtime_run_and_ordinal(self) -> None:
        # Arrange
        codec = CursorCodec(runtime_id="runtime-test-0001", key=b"k" * 32)

        # Act
        cursor = codec.issue("run-test-0001", 7)
        accepted = codec.resolve(cursor, "run-test-0001", oldest_ordinal=0, latest_ordinal=8)
        wrong_run = codec.resolve(cursor, "run-test-0002", oldest_ordinal=0, latest_ordinal=8)

        # Assert
        self.assertRegex(cursor, r"^[0-9a-f]{64}$")
        self.assertNotIn("run-test", cursor)
        self.assertEqual(7, accepted)
        self.assertIsNone(wrong_run)

    def test_cursor_outside_the_bounded_reconstruction_window_resnapshots(self) -> None:
        # Arrange
        codec = CursorCodec(runtime_id="runtime-test-0001", key=b"k" * 32)
        cursor = codec.issue("run-test-0001", 3)

        # Act
        resolved = codec.resolve(
            cursor,
            "run-test-0001",
            oldest_ordinal=4,
            latest_ordinal=12,
        )

        # Assert
        self.assertIsNone(resolved)


class ClientBufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_pressure_sheds_the_oldest_telemetry_before_retaining_lifecycle(self) -> None:
        # Arrange
        buffer = ClientBuffer(capacity=3)
        await buffer.push(BufferedEvent("TELEMETRY", b"telemetry-old"))
        await buffer.push(BufferedEvent("MISSION", b"mission-one"))
        await buffer.push(BufferedEvent("TELEMETRY", b"telemetry-new"))

        # Act
        disposition = await buffer.push(BufferedEvent("CONNECTIVITY", b"connectivity"))
        retained = [await buffer.pop(), await buffer.pop(), await buffer.pop()]

        # Assert
        self.assertIs(BufferDisposition.RETAINED, disposition)
        self.assertEqual(
            [b"mission-one", b"telemetry-new", b"connectivity"],
            [item.payload for item in retained if item is not None],
        )

    async def test_non_droppable_pressure_reserves_exactly_one_terminal_frame(self) -> None:
        # Arrange
        buffer = ClientBuffer(capacity=2)
        await buffer.push(BufferedEvent("MISSION", b"mission"))
        await buffer.push(BufferedEvent("CONNECTIVITY", b"connectivity"))

        # Act
        first = await buffer.push(BufferedEvent("MISSION", b"sector"))
        second = await buffer.push(BufferedEvent("MISSION", b"ignored"))
        drained = [await buffer.pop(), await buffer.pop(), await buffer.pop(), await buffer.pop()]

        # Assert
        self.assertIs(BufferDisposition.OVERLOADED, first)
        self.assertIs(BufferDisposition.CLOSED, second)
        self.assertEqual(1, sum(item is not None and item.terminal for item in drained))
        self.assertEqual(3, sum(item is not None for item in drained))

    async def test_incoming_telemetry_is_discarded_when_only_lifecycle_is_buffered(self) -> None:
        # Arrange
        buffer = ClientBuffer(capacity=1)
        await buffer.push(BufferedEvent("MISSION", b"mission"))

        # Act
        disposition = await buffer.push(BufferedEvent("TELEMETRY", b"supersedable"))
        retained = await buffer.pop()

        # Assert
        self.assertIs(BufferDisposition.DROPPED, disposition)
        self.assertIsNotNone(retained)
        self.assertEqual(b"mission", retained.payload if retained is not None else b"")
