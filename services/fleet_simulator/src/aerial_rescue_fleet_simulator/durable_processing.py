"""Commit one simulated command effect, receipt, and critical results before settlement.

This service owns the use-case order and no storage or transport implementation.  The
transaction and message-bound settlement capabilities are injected, so SQLAlchemy remains
inside ``packages/store`` and the Solace SDK remains inside ``packages/broker``.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Protocol

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import parse_envelope
from aerial_rescue_contracts.topics import DRONE_PARAMETER, Family, parse_topic
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.idempotency import SequenceVerdict
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate, BrokerRefusalOutcome
from aerial_rescue_store.receipts import CommandReceiptIdentity, ReceiptDecision, ReceiptOutcome

from aerial_rescue_fleet_simulator import FleetSimulatorError, event_source
from aerial_rescue_fleet_simulator.intake import IncomingCommand, IntakeError, accept
from aerial_rescue_fleet_simulator.protocol import apply, received
from aerial_rescue_fleet_simulator.results import ResultStamp, result_record

_EMPTY_HEADERS = b"{}"


class ProcessingRefusal(Enum):
    """Why a command cannot complete its durable processing contract."""

    INVALID_COMMAND = "the delivery is not a command this fleet can process"
    UNKNOWN_DRONE = "the command names no drone in the accepted run"
    INVALID_EFFECT = "the durable simulator effect returned no reportable command outcome"
    CRITICAL_OUTBOX_CAPACITY = "the drone critical outbox has reached an accepted bound"
    DUPLICATE_RESULT = "the completed duplicate has no exact durable result"


class ProcessingError(FleetSimulatorError):
    """A durable command operation refused with a closed, secret-safe reason."""

    def __init__(self, refusal: ProcessingRefusal, value: object = None) -> None:
        """Record a refusal while allowing capacity to have no unsafe offending value."""
        super().__init__(refusal, value)


class CommandProcessingOutcome(Enum):
    """The durable result of one Guaranteed command delivery."""

    APPLIED = "effect and result committed"
    DUPLICATE = "exact prior durable result reused"
    SUPERSEDED = "producer sequence did not advance"
    OUTBOX_FULL = "critical result could not be admitted"


@dataclass(frozen=True)
class CommandProcessingResult:
    """One processing decision and its exact final result when one exists."""

    outcome: CommandProcessingOutcome
    result: bytes | None = None


class SettlementPort(Protocol):
    """The one-shot settlement capability bound to this delivery."""

    async def accept(self) -> None:
        """Remove the delivery after its transaction commits."""

    async def fail(self) -> None:
        """Return transient work for redelivery."""

    async def reject(self) -> None:
        """Reject a permanently invalid delivery."""


@dataclass(frozen=True)
class CommandDelivery:
    """Canonical command bytes, their destination, and exact settlement capability."""

    topic: str
    payload: bytes
    settlement: SettlementPort


@dataclass(frozen=True)
class EffectResult:
    """The simulated effect outcome and its durable drone-local sequence."""

    event: CommandEvent
    applied_sequence: int
    effect_payload: bytes


class _SupersededCommandError(Exception):
    """Force rollback of a newly claimed receipt whose sequence cannot advance."""


class ResultStamps(Protocol):
    """Mint result envelope members and a durable processing instant."""

    def next_result_stamp(
        self, drone_id: str, correlation_id: str, causation_id: str
    ) -> ResultStamp:
        """Return the next producer-scoped command-result stamp."""

    def processed_at(self) -> str:
        """Return the canonical instant recorded on the durable receipt."""


class CommandTransaction(Protocol):
    """Every effect that commits atomically before command settlement."""

    async def admit_sequence(self, drone_id: str, sequence: int) -> SequenceVerdict:
        """Persist and return the producer-scoped sequence decision."""

    async def claim_receipt(self, identity: CommandReceiptIdentity) -> ReceiptOutcome:
        """Claim a new effect or return its exact completed duplicate."""

    async def apply_effect(self, command: IncomingCommand) -> EffectResult:
        """Apply the simulated effect once inside this transaction."""

    async def stage_critical(self, drone_id: str, event: StagedApplicationEvent) -> None:
        """Stage one exact result under that drone's record and byte bounds."""

    async def complete_receipt(
        self,
        identity: CommandReceiptIdentity,
        result: bytes,
        applied_sequence: int,
        processed_at: str,
    ) -> None:
        """Complete the claimed receipt with the exact final result."""


class TransactionContext(Protocol):
    """An async transaction that commits only on successful exit."""

    async def __aenter__(self) -> CommandTransaction:
        """Return the transaction operations."""

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success or roll back on any exception."""


class FleetUnitOfWork(Protocol):
    """Construct one command-processing transaction."""

    def begin(self) -> TransactionContext:
        """Return a fresh transaction context."""

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Commit malformed-command evidence in a separate transaction."""


@dataclass(frozen=True)
class CommandContext:
    """Run-scoped dependencies shared by every command delivery."""

    mission_id: str
    roster: frozenset[str]
    budget: SendBudget
    stamps: ResultStamps
    unit_of_work: FleetUnitOfWork


@dataclass(frozen=True)
class _ResultContext:
    """Inputs shared by the two result records for one command."""

    mission_id: str
    drone_id: str
    command: IncomingCommand
    budget: SendBudget
    stamps: ResultStamps


def _drone(delivery: CommandDelivery) -> str:
    """Return the addressed drone after closed topic validation."""
    try:
        topic = parse_topic(delivery.topic)
    except ValueError as error:
        raise ProcessingError(ProcessingRefusal.INVALID_COMMAND, "topic") from error
    if topic.family is not Family.DRONE_COMMAND:
        raise ProcessingError(ProcessingRefusal.INVALID_COMMAND, topic.family.name)
    return topic.parameters[DRONE_PARAMETER]


def _identity(
    drone_id: str,
    mission_id: str,
    command: IncomingCommand,
    payload: bytes,
) -> CommandReceiptIdentity:
    """Bind a drone-scoped command identifier to the exact canonical bytes."""
    return CommandReceiptIdentity(
        drone_id=drone_id,
        command_id=command.command_id,
        mission_id=mission_id,
        command_digest=hashlib.sha256(payload).hexdigest(),
    )


def _staged(topic: str, payload: bytes, drone_id: str) -> StagedApplicationEvent:
    """Map one validated command result to its exact critical-outbox row."""
    envelope = parse_envelope(canonical.decode(payload))
    return StagedApplicationEvent(
        producer=event_source(drone_id),
        event_id=envelope.id,
        family="drone-command-result",
        topic=topic,
        headers=_EMPTY_HEADERS,
        payload=payload,
        traceparent=envelope.traceparent,
        tracestate=envelope.tracestate,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        staged_at=envelope.time,
    )


def _result(context: _ResultContext, event: CommandEvent) -> tuple[bytes, str]:
    """Build one schema-bound result and retain its protocol progress."""
    progress = received(context.budget)
    acknowledged = apply(progress, CommandEvent.ACKNOWLEDGE, context.budget)
    selected = (
        acknowledged
        if event is CommandEvent.ACKNOWLEDGE
        else apply(acknowledged, event, context.budget)
    )
    stamp = context.stamps.next_result_stamp(
        context.drone_id,
        context.command.correlation_id,
        context.command.event_id,
    )
    topic, document = result_record(
        context.mission_id,
        context.drone_id,
        context.command.command_id,
        selected.state,
        stamp,
    )
    return canonical.canonical_bytes(document), topic


async def _apply_new(
    transaction: CommandTransaction,
    identity: CommandReceiptIdentity,
    command: IncomingCommand,
    budget: SendBudget,
    stamps: ResultStamps,
) -> bytes:
    """Apply one effect, stage acknowledgement and final result, and complete its receipt."""
    effect = await transaction.apply_effect(command)
    if effect.event not in {CommandEvent.SUCCEED, CommandEvent.FAIL}:
        raise ProcessingError(ProcessingRefusal.INVALID_EFFECT, effect.event.name)
    final_payload = b""
    context = _ResultContext(
        identity.mission_id,
        identity.drone_id,
        command,
        budget,
        stamps,
    )
    for event in (CommandEvent.ACKNOWLEDGE, effect.event):
        payload, topic = _result(context, event)
        await transaction.stage_critical(
            identity.drone_id,
            _staged(topic, payload, identity.drone_id),
        )
        final_payload = payload
    await transaction.complete_receipt(
        identity,
        final_payload,
        effect.applied_sequence,
        stamps.processed_at(),
    )
    return final_payload


async def _inside_transaction(
    transaction: CommandTransaction,
    identity: CommandReceiptIdentity,
    command: IncomingCommand,
    budget: SendBudget,
    stamps: ResultStamps,
) -> CommandProcessingResult:
    """Resolve sequence and receipt identity before applying a new effect."""
    receipt = await transaction.claim_receipt(identity)
    if receipt.decision is ReceiptDecision.DUPLICATE:
        if receipt.result is None:
            raise ProcessingError(ProcessingRefusal.DUPLICATE_RESULT, identity.command_id)
        return CommandProcessingResult(CommandProcessingOutcome.DUPLICATE, receipt.result)
    verdict = await transaction.admit_sequence(identity.drone_id, command.sequence)
    if verdict is not SequenceVerdict.ADVANCES:
        raise _SupersededCommandError
    result = await _apply_new(transaction, identity, command, budget, stamps)
    return CommandProcessingResult(CommandProcessingOutcome.APPLIED, result)


def _known_drone(delivery: CommandDelivery, roster: frozenset[str]) -> str:
    """Return the addressed roster drone or refuse before durable work."""
    drone_id = _drone(delivery)
    if drone_id not in roster:
        raise ProcessingError(ProcessingRefusal.UNKNOWN_DRONE, drone_id)
    return drone_id


async def handle_command(
    delivery: CommandDelivery,
    context: CommandContext,
) -> CommandProcessingResult:
    """Commit durable command work and only then settle its Guaranteed delivery."""
    try:
        drone_id = _known_drone(delivery, context.roster)
        command = accept(delivery.payload, delivery.topic, drone_id, context.mission_id)
    except (IntakeError, ProcessingError) as error:
        refusal = (
            error.refusal.name.lower().replace("_", "-")
            if isinstance(error, ProcessingError)
            else ProcessingRefusal.INVALID_COMMAND.name.lower().replace("_", "-")
        )
        await context.unit_of_work.refuse(refusal_candidate(delivery, refusal))
        await delivery.settlement.reject()
        if isinstance(error, ProcessingError):
            raise
        raise ProcessingError(ProcessingRefusal.INVALID_COMMAND, "payload") from error
    identity = _identity(drone_id, context.mission_id, command, delivery.payload)
    try:
        async with context.unit_of_work.begin() as transaction:
            result = await _inside_transaction(
                transaction,
                identity,
                command,
                context.budget,
                context.stamps,
            )
    except _SupersededCommandError:
        result = CommandProcessingResult(CommandProcessingOutcome.SUPERSEDED)
    except ProcessingError as error:
        if error.refusal is not ProcessingRefusal.CRITICAL_OUTBOX_CAPACITY:
            raise
        await delivery.settlement.fail()
        return CommandProcessingResult(CommandProcessingOutcome.OUTBOX_FULL)
    await delivery.settlement.accept()
    return result


def refusal_candidate(
    delivery: CommandDelivery,
    refusal_code: str,
) -> BrokerRefusalCandidate:
    """Derive bounded command context and the exact raw-body SHA-256.

    Runtime schema admission happens before command decoding and uses this same public
    projection so every permanent refusal has one body-free durable representation.
    """
    family: str | None = None
    source: str | None = None
    channel = "fleet-simulator-drone-command-unrouted"
    try:
        topic = parse_topic(delivery.topic)
        family = topic.family.literal_suffix
        drone_id = topic.parameters.get(DRONE_PARAMETER)
        if drone_id is not None:
            channel = f"fleet-simulator-drone-command-{drone_id}"
    except ValueError:
        pass
    with suppress(ValueError):
        source = parse_envelope(canonical.decode(delivery.payload)).source
    return BrokerRefusalCandidate(
        consumer="fleet-simulator",
        source=source,
        family=family,
        channel=channel,
        refusal_code=refusal_code,
        raw_digest=hashlib.sha256(delivery.payload).hexdigest(),
    )
