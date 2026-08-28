"""Long-running receiver-only recorder bindings and readiness coordination."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_broker.messaging import (
    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    AcknowledgingReceiver,
    BrokerLifecycle,
    BrokerLifecycleState,
    InboundMessage,
    MessageReceiver,
    ReceiverOnlyBindings,
)
from aerial_rescue_broker.queues import queues_for
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.topics import Delivery, Family, delivery_for
from aerial_rescue_domain.mission import (
    MissionError,
    MissionEvent,
    MissionRefusal,
    MissionState,
    transition,
)
from aerial_rescue_domain.principals import Access, Principal, grants
from aerial_rescue_store.audit import AuditRecord
from aerial_rescue_store.dashboard.events import (
    BrokerEvent,
    BrokerEventReceipt,
    EventSession,
    append_broker_event,
)
from aerial_rescue_store.dashboard.runs import mission_lifecycle_for_update, transition_mission

from aerial_rescue_recorder.capture import RecordingPolicy, recording_policy
from aerial_rescue_recorder.processing import ProcessDecision, ProcessOutcome


class ServiceRefusal(Enum):
    """Why the recorder process cannot safely enter its receive loop."""

    INVALID_RECOVERY_CYCLE = "the recovery cycle must contain one positive poll per receiver"


class ServiceError(ValueError):
    """A redacted recorder process refusal."""

    def __init__(self, refusal: ServiceRefusal) -> None:
        """Expose only the closed refusal reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


class RecorderProcess(Protocol):
    """The one-message operation exposed by the receiver-only recorder graph."""

    async def process_next(self) -> ProcessOutcome:
        """Process one bounded channel poll."""


@dataclass(frozen=True)
class ServeReport:
    """Bounded processing counts and the supervisor-facing exit status."""

    outcomes: Mapping[ProcessDecision, int]
    exit_status: int


def recorder_bindings() -> ReceiverOnlyBindings:
    """Derive every recorder endpoint from grants, delivery, and recording policy."""
    role = Principal.RECORDER
    subscribed = grants(role, Access.SUBSCRIBE)
    recordable = tuple(
        family
        for family in Family
        if family in subscribed and recording_policy(family) is RecordingPolicy.RECORD
    )
    queues = {queue.name: queue.name for queue in queues_for(role, ())}
    direct = tuple(
        subscription_for(family) for family in recordable if delivery_for(family) is Delivery.DIRECT
    )
    return ReceiverOnlyBindings(queues, direct, DIRECT_INTEGRATION_RECEIVER_CAPACITY)


def ignore_readiness(ready: bool) -> None:
    """Accept a readiness observation for a composition that publishes none."""
    del ready


async def serve(
    recorder: RecorderProcess,
    lifecycle: BrokerLifecycle,
    running: Callable[[], bool],
    recovery_cycle_polls: int,
    observe_readiness: Callable[[bool], None] = ignore_readiness,
) -> ServeReport:
    """Process bounded polls and restore readiness only after one complete healthy cycle.

    ``observe_readiness`` receives the lifecycle's readiness after every poll so the composing
    process can publish it (the container healthcheck reads a freshness lease) at poll cadence.
    """
    if type(recovery_cycle_polls) is not int or recovery_cycle_polls <= 0:
        raise ServiceError(ServiceRefusal.INVALID_RECOVERY_CYCLE)
    counted: dict[ProcessDecision, int] = {}
    successful_polls = 0
    observed_state = lifecycle.state
    while running():
        state = lifecycle.state
        if state in {BrokerLifecycleState.EXHAUSTED, BrokerLifecycleState.CLOSED}:
            break
        if state is not observed_state:
            successful_polls = 0
            observed_state = state
        outcome = await recorder.process_next()
        counted[outcome.decision] = counted.get(outcome.decision, 0) + 1
        current_state = lifecycle.state
        if current_state is not observed_state:
            successful_polls = 0
            observed_state = current_state
        if current_state in {
            BrokerLifecycleState.CONNECTED,
            BrokerLifecycleState.RECOVERY_PENDING,
        }:
            successful_polls += 1
            if successful_polls >= recovery_cycle_polls:
                lifecycle.mark_ready()
        else:
            successful_polls = 0
        observe_readiness(lifecycle.is_ready())
    exit_status = int(lifecycle.state is BrokerLifecycleState.EXHAUSTED)
    return ServeReport(counted, exit_status)


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
            message = await self._receive_from_source(source_index, timeout)
            if message is None:
                empty_sources += 1
            else:
                processed += 1
                empty_sources = 0
            source_index = (source_index + 1) % source_count

    async def _receive_from_source(
        self,
        source_index: int,
        timeout_milliseconds: int,
    ) -> InboundMessage | None:
        """Receive and process one message from the selected delivery source."""
        if source_index == 0:
            message = self.direct.receive(timeout_milliseconds)
            if message is not None:
                await self.processor.process_best_effort(message)
            return message
        receiver = self.guaranteed[source_index - 1]
        message = receiver.receive(timeout_milliseconds)
        if message is not None:
            await self.processor.process_guaranteed(receiver, message)
        return message


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
