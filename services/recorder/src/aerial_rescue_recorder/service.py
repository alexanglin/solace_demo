"""Bounded capture polling and the durable recorder transaction adapter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

from aerial_rescue_broker.messaging import AcknowledgingReceiver, InboundMessage, MessageReceiver
from aerial_rescue_contracts import canonical
from aerial_rescue_domain.mission import (
    MissionError,
    MissionEvent,
    MissionRefusal,
    MissionState,
    transition,
)
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard_events import (
    BrokerEvent,
    BrokerEventReceipt,
    EventSession,
    append_broker_event,
)
from aerial_rescue_store.dashboard_runs import mission_lifecycle_for_update, transition_mission


class CaptureProcessorPort(Protocol):
    """Validate and persist the two delivery classes without conflating them."""

    async def process_best_effort(self, message: InboundMessage) -> None:
        """Process one direct message without an acknowledgement claim."""

    async def process_guaranteed(
        self,
        receiver: AcknowledgingReceiver,
        message: InboundMessage,
    ) -> None:
        """Process and settle one guaranteed message after its durable outcome."""


@dataclass(frozen=True)
class CaptureLoop:
    """Drain a bounded round-robin batch with at most one blocking receive per cycle."""

    direct: MessageReceiver
    guaranteed: Sequence[AcknowledgingReceiver]
    processor: CaptureProcessorPort
    receive_timeout_milliseconds: int
    maximum_batch_messages: int = 64

    def __post_init__(self) -> None:
        """Refuse an unbounded or negative receive wait."""
        if self.receive_timeout_milliseconds < 0 or self.maximum_batch_messages < 1:
            message = "capture loop bounds are invalid"
            raise ValueError(message)

    async def poll_once(self) -> None:
        """Process one bounded fair batch after at most one shared receive wait."""
        processed = 0
        source_count = 1 + len(self.guaranteed)
        empty_sources = 0
        source_index = 0
        may_wait = True
        while processed < self.maximum_batch_messages and empty_sources < source_count:
            timeout = self.receive_timeout_milliseconds if may_wait else 0
            may_wait = False
            if source_index == 0:
                message = self.direct.receive(timeout)
                if message is not None:
                    await self.processor.process_best_effort(message)
            else:
                receiver = self.guaranteed[source_index - 1]
                message = receiver.receive(timeout)
                if message is not None:
                    await self.processor.process_guaranteed(receiver, message)
            if message is None:
                empty_sources += 1
            else:
                processed += 1
                empty_sources = 0
            source_index = (source_index + 1) % source_count


type TransactionFactory = Callable[[], AbstractAsyncContextManager[EventSession]]
type EventPersist = Callable[
    [EventSession, BrokerEvent, AuditRecord],
    Awaitable[BrokerEventReceipt],
]
type LifecycleRead = Callable[[EventSession, str], Awaitable[str]]
type LifecycleWrite = Callable[[EventSession, str, str, str], Awaitable[None]]

_LIFECYCLE_EVENTS = {
    MissionState.SEARCHING: MissionEvent.START,
    MissionState.EXHAUSTED: MissionEvent.EXHAUST,
    MissionState.ABORTED: MissionEvent.ABORT,
}


async def _read_lifecycle(session: EventSession, mission_id: str) -> str:
    """Adapt the transaction's structural session to the dashboard-run repository."""
    return await mission_lifecycle_for_update(session, mission_id)


async def _write_lifecycle(
    session: EventSession,
    mission_id: str,
    expected: str,
    target: str,
) -> None:
    """Persist the domain-approved transition in the caller's recorder transaction."""
    await transition_mission(session, mission_id, expected, target)


@dataclass(frozen=True)
class DashboardAppender:
    """Own one transaction per broker event and return only after its commit."""

    transactions: TransactionFactory
    persist: EventPersist = append_broker_event
    lifecycle: LifecycleRead = _read_lifecycle
    transition_lifecycle: LifecycleWrite = _write_lifecycle

    async def append(self, event: BrokerEvent, record: AuditRecord) -> None:
        """Persist broker identity and audit row in the same committed transaction."""
        async with self.transactions() as session:
            await self._apply_mission_lifecycle(session, record)
            await self.persist(session, event, record)

    async def _apply_mission_lifecycle(
        self,
        session: EventSession,
        record: AuditRecord,
    ) -> None:
        """Apply a normalized mission event using the domain transition table before audit."""
        target = _mission_lifecycle(record)
        if target is None:
            return
        current_name = await self.lifecycle(session, record.mission_id)
        try:
            current = MissionState[current_name]
        except KeyError as invalid:
            raise MissionError(MissionRefusal.TRANSITION, current_name) from invalid
        if current is target:
            return
        event = _LIFECYCLE_EVENTS.get(target)
        if event is None or transition(current, event) is not target:
            raise MissionError(MissionRefusal.TRANSITION, (current, target))
        await self.transition_lifecycle(session, record.mission_id, current.name, target.name)


def _mission_lifecycle(record: AuditRecord) -> MissionState | None:
    """Read the already-canonical normalized lifecycle payload without another wire model."""
    if record.kind != "missionLifecycle":
        return None
    document = canonical.decode(record.payload)
    if not isinstance(document, Mapping):
        raise MissionError(MissionRefusal.TRANSITION, record.kind)
    data = document.get("data")
    if not isinstance(data, Mapping):
        raise MissionError(MissionRefusal.TRANSITION, record.kind)
    lifecycle = data.get("lifecycle")
    if not isinstance(lifecycle, str):
        raise MissionError(MissionRefusal.TRANSITION, lifecycle)
    try:
        return MissionState[lifecycle]
    except KeyError as invalid:
        raise MissionError(MissionRefusal.TRANSITION, lifecycle) from invalid
