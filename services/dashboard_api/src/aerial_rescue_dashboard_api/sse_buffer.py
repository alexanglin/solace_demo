"""Finite per-client buffering for normalized dashboard SSE frames."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aerial_rescue_contracts.view import MAX_BUFFERED_EVENTS


class BufferDecision(Enum):
    """The observable outcome of one frame offer."""

    RETAINED = "frame retained"
    EVICTED_TELEMETRY = "oldest telemetry evicted"
    DROPPED_TELEMETRY = "incoming telemetry dropped"
    OVERLOADED = "non-droppable capacity exhausted"
    CLOSED = "buffer already closed"


@dataclass(frozen=True)
class BufferedFrame:
    """One already validated serialized data frame and its loss classification."""

    payload: bytes
    telemetry: bool


class ClientEventBuffer:
    """Retain finite frames while reserving one terminal overload signal."""

    def __init__(self, max_events: int = MAX_BUFFERED_EVENTS) -> None:
        """Create a buffer no larger than the contract-owned production ceiling."""
        if type(max_events) is not int or not 0 < max_events <= MAX_BUFFERED_EVENTS:
            message = "SSE event capacity is outside its contract bound"
            raise ValueError(message)
        self._max_events = max_events
        self._frames: list[BufferedFrame] = []
        self._closed = False
        self._terminal_required = False

    @property
    def frames(self) -> tuple[BufferedFrame, ...]:
        """Return an immutable diagnostic snapshot of retained frames."""
        return tuple(self._frames)

    @property
    def terminal_required(self) -> bool:
        """Whether the reserved overload control must be emitted exactly once."""
        return self._terminal_required

    def push(self, frame: BufferedFrame) -> BufferDecision:
        """Retain one frame under the telemetry-only loss policy."""
        if self._closed:
            return BufferDecision.CLOSED
        if len(self._frames) < self._max_events:
            self._frames.append(frame)
            return BufferDecision.RETAINED
        for index, retained in enumerate(self._frames):
            if retained.telemetry:
                del self._frames[index]
                self._frames.append(frame)
                return BufferDecision.EVICTED_TELEMETRY
        if frame.telemetry:
            return BufferDecision.DROPPED_TELEMETRY
        self._terminal_required = True
        self._closed = True
        return BufferDecision.OVERLOADED

    def drain(self) -> tuple[BufferedFrame, ...]:
        """Remove and return retained data frames in their admission order."""
        frames = tuple(self._frames)
        self._frames.clear()
        return frames
