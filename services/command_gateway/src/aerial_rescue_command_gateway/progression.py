"""Persist the five-send lifecycle and guaranteed command-result ingress.

The pure command table counts sends.  This service supplies bounded schedule facts and wraps
result transitions in inbox commit-before-settlement transactions.  A result that cannot leave
the current state is recorded as stale and never reopens or overwrites durable progress.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import envelope_document
from aerial_rescue_domain.commands import (
    CommandError,
    CommandEvent,
    CommandState,
    SendBudget,
)
from aerial_rescue_store.command_progress import StoredCommandProgress, TransitionFacts
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity

from aerial_rescue_command_gateway.dispatch import MAX_COMMAND_SENDS, wait_milliseconds
from aerial_rescue_command_gateway.ingress import (
    CommandResultIngress,
    IngressError,
    accept_ingress,
)
from aerial_rescue_command_gateway.ports import (
    GuaranteedDelivery,
    ProgressRecorder,
    ResultUnitOfWork,
    SettlementPort,
)
from aerial_rescue_command_gateway.refusal import reject_after_refusal

CONSUMER: Final = "command-gateway"
REFUSAL_CHANNEL: Final = "command-gateway-command-result"
SEND_BUDGET: Final = SendBudget(MAX_COMMAND_SENDS)


class ProgressionRefusal(Enum):
    """Why progression cannot safely process a delivery."""

    INGRESS_KIND = "ingress is not a command result"
    DUPLICATE_RESULT = "an exact duplicate has no durable result"


class ProgressionError(ValueError):
    """A redacted progression failure which leaves broker input unsettled."""

    def __init__(self, refusal: ProgressionRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


class TimeoutOutcome(Enum):
    """Whether a timeout scheduled another send or exhausted the send budget."""

    RETRY = "retry"
    ABANDONED = "abandoned"


class CommandResultOutcome(Enum):
    """How one guaranteed result affected durable command progress."""

    UPDATED = "updated"
    STALE = "stale"
    MISMATCH = "identity-mismatch"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class SendClock:
    """The adapter-owned instants persisted for one confirmed send."""

    sent_at: str
    deadline_at: str
    updated_at: str


@dataclass(frozen=True)
class TimeoutResult:
    """Durable timeout progress and the optional wait before another send."""

    outcome: TimeoutOutcome
    progress: StoredCommandProgress
    wait_milliseconds: int | None


@dataclass(frozen=True)
class CommandResult:
    """The durable answer associated with one result event."""

    outcome: CommandResultOutcome
    state: CommandState
    result: bytes


async def record_send(
    current: StoredCommandProgress,
    clock: SendClock,
    recorder: ProgressRecorder,
) -> StoredCommandProgress:
    """Persist one confirmed send and its bounded acknowledgement deadline."""
    facts = TransitionFacts(
        last_sent_at=clock.sent_at,
        deadline_at=clock.deadline_at,
        result_id=current.result_id,
        updated_at=clock.updated_at,
    )
    return await recorder.transition(current, CommandEvent.SEND, SEND_BUDGET, facts)


async def record_timeout(
    current: StoredCommandProgress,
    updated_at: str,
    jitter_milliseconds: int,
    recorder: ProgressRecorder,
) -> TimeoutResult:
    """Persist a timeout, returning a bounded retry wait or terminal abandonment."""
    facts = TransitionFacts(
        last_sent_at=current.last_sent_at,
        deadline_at=None,
        result_id=current.result_id,
        updated_at=updated_at,
    )
    became = await recorder.transition(current, CommandEvent.TIME_OUT, SEND_BUDGET, facts)
    if became.progress.state is CommandState.ABANDONED:
        return TimeoutResult(TimeoutOutcome.ABANDONED, became, None)
    waiting = wait_milliseconds(current.progress.sends, jitter_milliseconds)
    return TimeoutResult(TimeoutOutcome.RETRY, became, waiting)


def _identity(ingress: CommandResultIngress) -> InboxIdentity:
    """Bind result identity to canonical envelope bytes and its authenticated source."""
    encoded = canonical.canonical_bytes(envelope_document(ingress.envelope))
    return InboxIdentity(
        consumer=CONSUMER,
        source=ingress.envelope.source,
        event_id=ingress.envelope.id,
        mission_id=ingress.payload.mission_id,
        canonical_digest=hashlib.sha256(encoded).hexdigest(),
    )


def _event(outcome: str) -> CommandEvent:
    """Map the closed wire discriminator onto the pure lifecycle table."""
    return {
        "acknowledged": CommandEvent.ACKNOWLEDGE,
        "succeeded": CommandEvent.SUCCEED,
        "failed": CommandEvent.FAIL,
    }[outcome]


def _result(
    ingress: CommandResultIngress,
    outcome: CommandResultOutcome,
    state: CommandState,
) -> bytes:
    """Return the exact durable response for inbox and duplicate processing."""
    return canonical.canonical_bytes(
        {
            "commandResult": outcome.value,
            "commandId": ingress.payload.command_id,
            "state": state.value,
        }
    )


def _matches(ingress: CommandResultIngress, current: StoredCommandProgress) -> bool:
    """Require every topic/body identity to match the durable command binding."""
    identity = current.identity
    return (
        ingress.payload.command_id == identity.command_id
        and ingress.payload.mission_id == identity.mission_id
        and ingress.payload.drone_id == identity.drone_id
    )


async def _apply_result(
    ingress: CommandResultIngress,
    current: StoredCommandProgress,
    transaction: ProgressRecorder,
) -> CommandResult:
    """Apply one valid state edge, or classify a non-edge as stale without mutation."""
    if not _matches(ingress, current):
        result = _result(ingress, CommandResultOutcome.MISMATCH, current.progress.state)
        return CommandResult(CommandResultOutcome.MISMATCH, current.progress.state, result)
    facts = TransitionFacts(
        last_sent_at=current.last_sent_at,
        deadline_at=None,
        result_id=ingress.envelope.id,
        updated_at=ingress.envelope.time,
    )
    try:
        became = await transaction.transition(
            current,
            _event(ingress.payload.outcome),
            SEND_BUDGET,
            facts,
        )
    except CommandError:
        result = _result(ingress, CommandResultOutcome.STALE, current.progress.state)
        return CommandResult(CommandResultOutcome.STALE, current.progress.state, result)
    result = _result(ingress, CommandResultOutcome.UPDATED, became.progress.state)
    return CommandResult(CommandResultOutcome.UPDATED, became.progress.state, result)


async def handle_command_result(
    delivery: GuaranteedDelivery,
    unit_of_work: ResultUnitOfWork,
    settlement: SettlementPort,
) -> CommandResult:
    """Commit result inbox and progress together, then settle the guaranteed message."""
    try:
        accepted = accept_ingress(delivery.payload, delivery.topic)
    except IngressError as error:
        await reject_after_refusal(
            delivery,
            REFUSAL_CHANNEL,
            error.refusal.name.lower().replace("_", "-"),
            unit_of_work,
            settlement,
        )
        raise
    if not isinstance(accepted, CommandResultIngress):
        await reject_after_refusal(
            delivery,
            REFUSAL_CHANNEL,
            "unexpected-family",
            unit_of_work,
            settlement,
        )
        raise ProgressionError(ProgressionRefusal.INGRESS_KIND)
    identity = _identity(accepted)
    result: CommandResult
    async with unit_of_work.begin() as transaction:
        claim = await transaction.claim(identity)
        if claim.decision is InboxDecision.DUPLICATE:
            if claim.result is None:
                raise ProgressionError(ProgressionRefusal.DUPLICATE_RESULT)
            current = await transaction.load_progress(accepted.payload.command_id)
            result = CommandResult(
                CommandResultOutcome.DUPLICATE,
                current.progress.state,
                claim.result,
            )
        else:
            current = await transaction.load_progress(accepted.payload.command_id)
            result = await _apply_result(accepted, current, transaction)
            await transaction.complete(identity, result.result, accepted.envelope.time)
    await settlement.accept(accepted.envelope.id)
    return result
