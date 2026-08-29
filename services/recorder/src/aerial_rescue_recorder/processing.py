"""Receiver-only recorder composition and one-message processing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_contracts.topics import Family
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalOutcome,
)

from aerial_rescue_recorder.capture import (
    AcceptedSettlement,
    CaptureDecision,
    CaptureOutcome,
    ReceivedNotification,
    RecordingPolicy,
    recording_policy,
)


class ProcessDecision(Enum):
    """The observable result of one bounded receive operation."""

    IDLE = "receive window produced no message"
    EXCLUDED = "transport-only or integration input was intentionally excluded"
    RECORDED = "application notification was durably recorded"
    DUPLICATE = "exact application notification duplicate reused its durable result"
    REJECTED = "malformed guaranteed input was durably refused and rejected"
    DROPPED = "malformed direct input was durably refused and dropped"


class ProcessRefusal(Enum):
    """Why receiver input cannot be processed by the recorder graph."""

    INVALID_EXCLUSION = "a recordable family was presented as excluded transport"


class ProcessError(ValueError):
    """A receiver input refused before durable capture."""

    def __init__(self, refusal: ProcessRefusal, value: object) -> None:
        """Retain the family name rather than a raw ingress body."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class ExcludedIngress:
    """A classified input whose raw body was discarded at the broker boundary."""

    family: Family


@dataclass(frozen=True)
class NotificationIngress:
    """One typed notification and its exact delivery-specific settlement capability."""

    notification: ReceivedNotification
    settlement: AcceptedSettlement | None


class RejectedSettlement(Protocol):
    """The exact malformed Guaranteed message's permanent settlement capability."""

    def reject(self) -> None:
        """Move the message through its isolated dead-message policy."""


@dataclass(frozen=True)
class RefusedIngress:
    """One body-free refusal candidate and the message-bound settlement it protects."""

    fact: BrokerRefusalCandidate
    settlement: RejectedSettlement | None


type RecorderIngress = ExcludedIngress | NotificationIngress | RefusedIngress


class ReceiverOnly(Protocol):
    """The complete transport capability held by the recorder process."""

    async def receive(self) -> RecorderIngress | None:
        """Return one classified input or an idle receive result."""

    def close(self) -> None:
        """Close the owned receiver without a publisher shutdown path."""


class CapturePort(Protocol):
    """The durable capture behavior used after receiver classification."""

    async def capture(
        self,
        notification: ReceivedNotification,
        settlement: AcceptedSettlement | None,
        /,
    ) -> CaptureOutcome:
        """Commit one notification and settle it according to its derived delivery."""


class RefusalPort(Protocol):
    """The store-owned independent transaction used for poison-message evidence."""

    async def record(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Return only after a new or exact prior refusal fact commits."""


@dataclass(frozen=True)
class ProcessOutcome:
    """One processing result and its authoritative ordinal when one exists."""

    decision: ProcessDecision
    audit_ordinal: int | None = None


@dataclass(frozen=True)
class RecorderRuntime:
    """A live recorder graph with no publication capability by construction."""

    receiver: ReceiverOnly
    capture: CapturePort
    refusals: RefusalPort

    async def process_next(self) -> ProcessOutcome:
        """Receive and process one classified input without retaining raw excluded bytes."""
        ingress = await self.receiver.receive()
        if ingress is None:
            return ProcessOutcome(ProcessDecision.IDLE)
        if isinstance(ingress, RefusedIngress):
            await self.refusals.record(ingress.fact)
            if ingress.settlement is None:
                return ProcessOutcome(ProcessDecision.DROPPED)
            ingress.settlement.reject()
            return ProcessOutcome(ProcessDecision.REJECTED)
        if isinstance(ingress, ExcludedIngress):
            if recording_policy(ingress.family) is not RecordingPolicy.EXCLUDED:
                raise ProcessError(ProcessRefusal.INVALID_EXCLUSION, ingress.family.name)
            return ProcessOutcome(ProcessDecision.EXCLUDED)
        captured = await self.capture.capture(ingress.notification, ingress.settlement)
        if captured.decision is CaptureDecision.REFUSED:
            return ProcessOutcome(ProcessDecision.REJECTED)
        decision = (
            ProcessDecision.RECORDED
            if captured.decision is CaptureDecision.RECORDED
            else ProcessDecision.DUPLICATE
        )
        return ProcessOutcome(decision, captured.audit_ordinal)

    def close(self) -> None:
        """Close the only transport object this graph can own."""
        self.receiver.close()
