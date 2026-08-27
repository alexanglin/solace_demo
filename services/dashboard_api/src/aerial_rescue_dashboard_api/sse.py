"""Finite per-client SSE buffering with one reserved terminal overload slot."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum

from aerial_rescue_contracts import canonical

_TELEMETRY = "TELEMETRY"
_OVERLOAD = canonical.canonical_bytes(
    {
        "controlVersion": "dashboard-stream-overloaded/v1",
        "reason": "NON_DROPPABLE_BUFFER_FULL",
    }
)


class BufferDisposition(Enum):
    """How one offered data frame affected a finite client buffer."""

    RETAINED = "retained"
    DROPPED = "droppable frame discarded"
    OVERLOADED = "terminal overload reserved"
    CLOSED = "client buffer already closed"


@dataclass(frozen=True)
class BufferedEvent:
    """One serialized data frame or terminal control document."""

    event_class: str
    payload: bytes
    cursor: str | None = None
    terminal: bool = False


class ClientBuffer:
    """Retain bounded frames, shedding only oldest telemetry under pressure."""

    def __init__(self, *, capacity: int) -> None:
        """Create a data buffer plus an independent one-frame terminal slot."""
        if capacity < 1:
            message = "client buffer capacity must be positive"
            raise ValueError(message)
        self._capacity = capacity
        self._events: deque[BufferedEvent] = deque()
        self._terminal: BufferedEvent | None = None
        self._closed = False
        self._condition = asyncio.Condition()

    @property
    def closed(self) -> bool:
        """Report whether the buffer accepts no further data frames."""
        return self._closed

    async def push(self, event: BufferedEvent) -> BufferDisposition:
        """Offer one frame while preserving every non-droppable frame already retained."""
        async with self._condition:
            if self._closed:
                return BufferDisposition.CLOSED
            if len(self._events) < self._capacity:
                self._events.append(event)
                self._condition.notify()
                return BufferDisposition.RETAINED
            droppable_index = next(
                (
                    index
                    for index, retained in enumerate(self._events)
                    if retained.event_class == _TELEMETRY
                ),
                None,
            )
            if droppable_index is not None:
                del self._events[droppable_index]
                self._events.append(event)
                self._condition.notify()
                return BufferDisposition.RETAINED
            if event.event_class == _TELEMETRY:
                return BufferDisposition.DROPPED
            self._terminal = BufferedEvent("CONTROL", _OVERLOAD, terminal=True)
            self._closed = True
            self._condition.notify_all()
            return BufferDisposition.OVERLOADED

    async def pop(self) -> BufferedEvent | None:
        """Return one immediately available data or terminal frame without waiting."""
        async with self._condition:
            return self._pop_unlocked()

    async def wait_pop(self, timeout: float) -> BufferedEvent | None:
        """Wait a bounded interval for one frame, returning none for a keepalive opportunity."""
        async with self._condition:
            if not self._events and self._terminal is None and not self._closed:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout)
                except TimeoutError:
                    return None
            return self._pop_unlocked()

    async def close(self) -> None:
        """Release retained state and wake a waiting consumer."""
        async with self._condition:
            self._closed = True
            self._events.clear()
            self._terminal = None
            self._condition.notify_all()

    def _pop_unlocked(self) -> BufferedEvent | None:
        """Pop data before the reserved terminal frame while holding the condition."""
        if self._events:
            return self._events.popleft()
        if self._terminal is None:
            return None
        terminal = self._terminal
        self._terminal = None
        return terminal
