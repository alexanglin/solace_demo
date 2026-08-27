"""Finite per-client dashboard SSE buffering tests."""

from __future__ import annotations

import pytest
from aerial_rescue_contracts.view import MAX_BUFFERED_EVENTS
from aerial_rescue_dashboard_api.sse_buffer import (
    BufferDecision,
    BufferedFrame,
    ClientEventBuffer,
)


def _frame(index: int, *, telemetry: bool) -> BufferedFrame:
    return BufferedFrame(payload=f"frame-{index}".encode(), telemetry=telemetry)


def test_full_buffer_discards_oldest_telemetry_for_newest_event() -> None:
    # Arrange
    buffer = ClientEventBuffer()
    first = _frame(0, telemetry=True)
    buffer.push(first)
    for index in range(1, MAX_BUFFERED_EVENTS):
        buffer.push(_frame(index, telemetry=False))
    newest = _frame(MAX_BUFFERED_EVENTS, telemetry=False)

    # Act
    decision = buffer.push(newest)

    # Assert
    assert decision is BufferDecision.EVICTED_TELEMETRY
    assert first not in buffer.frames
    assert buffer.frames[-1] is newest
    assert buffer.terminal_required is False


def test_full_nondroppable_buffer_drops_incoming_telemetry_without_closing() -> None:
    # Arrange
    buffer = ClientEventBuffer()
    for index in range(MAX_BUFFERED_EVENTS):
        buffer.push(_frame(index, telemetry=False))
    telemetry = _frame(MAX_BUFFERED_EVENTS, telemetry=True)

    # Act
    decision = buffer.push(telemetry)

    # Assert
    assert decision is BufferDecision.DROPPED_TELEMETRY
    assert telemetry not in buffer.frames
    assert len(buffer.frames) == MAX_BUFFERED_EVENTS
    assert buffer.terminal_required is False


def test_full_nondroppable_buffer_reserves_one_terminal_and_closes() -> None:
    # Arrange
    buffer = ClientEventBuffer()
    for index in range(MAX_BUFFERED_EVENTS):
        buffer.push(_frame(index, telemetry=False))
    critical = _frame(MAX_BUFFERED_EVENTS, telemetry=False)

    # Act
    first_decision = buffer.push(critical)
    second_decision = buffer.push(_frame(MAX_BUFFERED_EVENTS + 1, telemetry=False))

    # Assert
    assert first_decision is BufferDecision.OVERLOADED
    assert second_decision is BufferDecision.CLOSED
    assert len(buffer.frames) == MAX_BUFFERED_EVENTS
    assert buffer.terminal_required is True


def test_buffer_drains_in_order_and_clears_frames() -> None:
    # Arrange
    buffer = ClientEventBuffer(max_events=2)
    first = _frame(1, telemetry=False)
    second = _frame(2, telemetry=False)
    buffer.push(first)
    buffer.push(second)

    # Act
    drained = buffer.drain()

    # Assert
    assert drained == (first, second)
    assert buffer.frames == ()


@pytest.mark.parametrize("capacity", [0, MAX_BUFFERED_EVENTS + 1, True])
def test_buffer_refuses_non_positive_over_ceiling_or_boolean_capacity(
    capacity: int,
) -> None:
    # Arrange
    expected = "SSE event capacity is outside its contract bound"

    # Act
    with pytest.raises(ValueError, match=expected) as captured:
        ClientEventBuffer(max_events=capacity)

    # Assert
    assert str(captured.value) == expected
