"""Long-running receiver-only recorder bindings and readiness coordination."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_broker.messaging import (
    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    BrokerLifecycle,
    BrokerLifecycleState,
    ReceiverOnlyBindings,
)
from aerial_rescue_broker.queues import family_queue_name
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.topics import Delivery, Family, delivery_for
from aerial_rescue_domain.principals import Access, Principal, grants

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
    queues = {
        family.name: family_queue_name(role, family)
        for family in recordable
        if delivery_for(family) is Delivery.GUARANTEED
    }
    direct = tuple(
        subscription_for(family) for family in recordable if delivery_for(family) is Delivery.DIRECT
    )
    return ReceiverOnlyBindings(queues, direct, DIRECT_INTEGRATION_RECEIVER_CAPACITY)


async def serve(
    recorder: RecorderProcess,
    lifecycle: BrokerLifecycle,
    running: Callable[[], bool],
    recovery_cycle_polls: int,
) -> ServeReport:
    """Process bounded polls and restore readiness only after one complete healthy cycle."""
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
    exit_status = int(lifecycle.state is BrokerLifecycleState.EXHAUSTED)
    return ServeReport(counted, exit_status)
