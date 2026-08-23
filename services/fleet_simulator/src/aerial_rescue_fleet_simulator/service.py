"""The fleet simulator's composition root: wire the ports, fold, publish, and shut down.

Everything this would otherwise reach out and take for itself -- the broker, the credential,
the clock, the identifier source, the scenario, and the decision to keep running -- arrives
as a :class:`Runtime`. There is no environment read and no filesystem read anywhere in this
member: ``docs/adr/0077`` puts the scenario at the composition boundary, and the endpoint
and the credential are there for the same reason.

The member declares no console script and ``deploy/compose.yaml`` keeps its import-and-exit
command, because a process entry point would need a scenario and producing one is the
scenario service's job. This root is exercised by tests and by the live run until that
service exists; that obligation is recorded in ``TECH_DEBT.md``.

Telemetry is published direct, because ``docs/CONTRACTS.md`` puts routine telemetry on
direct delivery, and a refused publication is counted rather than fatal: telemetry is
contractually droppable, and a simulator that stopped on one dropped event would model the
wrong failure (``docs/adr/0078``). A command result is published guaranteed, on a separate
port, so the two can never be confused.

Each tick is followed by a bounded drain of every drone's own command queue. The drain runs
after the fold, so a command received now affects the next observation rather than
retroactively rewriting one already published, and inside the loop's own guard, so a run
that has stopped ticking stops intaking. It polls rather than waiting: a blocking receive
window would make command traffic the pacer of a loop that has none, so the tick rate would
run fast under load and slow when idle.

Settlement has one rule. A condition that could differ on the next delivery is ``FAILED``,
which returns the message for the redelivery the queue bounds; a condition that cannot is
``REJECTED``, which reaches the dead-message queue at once rather than after four arrivals.
Nothing is settled on receipt, and nothing is settled before its results are on the wire and
acknowledged by the broker.

**What this does not claim.** The receipts are process-local. A restart between publishing a
result and settling its command yields a redelivery this process no longer recognises, so it
re-answers. The claim is at-least-once with duplicates possible across a restart -- never
exactly-once, zero loss, backlog recovery, or reconnect reconciliation, each of which waits
on ``packages/store``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Final, Protocol, override

from aerial_rescue_broker.messaging import (
    AcknowledgingReceiver,
    BrokerEndpoint,
    DirectPublisher,
    InboundMessage,
    MessagePublisher,
    MessagingError,
    Outcome,
)
from aerial_rescue_broker.queues import drone_queue_name
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_domain.commands import CommandEvent, CommandProgress, SendBudget
from aerial_rescue_domain.idempotency import (
    IdempotencyDecision,
    IdempotencyKind,
    SequenceVerdict,
    Stream,
    idempotency_decision,
)
from aerial_rescue_domain.idempotency import receive as receive_sequence
from aerial_rescue_domain.mission import is_terminal as mission_is_terminal
from aerial_rescue_domain.principals import Principal

from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.fleet import FleetState, Reading, advance_tick, initial_fleet
from aerial_rescue_fleet_simulator.intake import IncomingCommand, IntakeError, accept
from aerial_rescue_fleet_simulator.protocol import ProtocolError, apply, received
from aerial_rescue_fleet_simulator.results import ResultError, ResultStamp, result_record
from aerial_rescue_fleet_simulator.scenario import FleetScenario, ordered_drones, sectors
from aerial_rescue_fleet_simulator.telemetry import (
    TelemetryError,
    TelemetryStamp,
    telemetry_record,
)

TRACE_SAMPLED: Final = "01"
TRACE_VERSION: Final = "00"
TRACE_PARENT_DIGITS: Final = 16

_NO_PROPERTIES: Final[dict[str, object]] = {}
_FIRST_SEQUENCE: Final = 0
_MINIMUM_PER_TICK: Final = 1
_POLL_MILLISECONDS: Final = 0
"""A non-blocking poll, so intake never becomes the tick loop's pacer."""


class PublishOutcome(Enum):
    """What became of one reading."""

    PUBLISHED = "the reading was sent"
    UNRECORDABLE = "the reading could not become a record, so nothing was sent"
    REFUSED = "the transport did not accept the publication"


class IntakeOutcome(Enum):
    """What became of one message taken off a drone's command queue."""

    HANDLED = "the command was acknowledged, resolved, and settled"
    REPLAYED = "known command identifier; the prior result stands"
    SUPERSEDED = "duplicate or stale within this queue's view of the producer's stream"
    UNREADABLE = "refused before any result, and rejected to the dead-message queue"
    UNANSWERABLE = "the result could not be built, so the command was rejected"
    RESULT_REFUSED = "the transport did not accept a result; returned for redelivery"
    SETTLEMENT_REFUSED = "the transport did not accept the settlement; nothing was settled"


class IntakeBoundRefusal(Enum):
    """Why a drain bound is not usable."""

    PER_TICK = "a drain that takes fewer than one command per drone could never drain"


@dataclass(frozen=True)
class IntakeBounds:
    """How much command work one tick may do, injected with no default.

    The per-drone cap is a derived row in ``docs/operating-parameters.md``: it is what
    decides whether the declared backlog-recovery envelope is reachable at all, so it does
    not belong in this member as a constant.
    """

    commands_per_drone_per_tick: int

    def __post_init__(self) -> None:
        """Refuse a bound under which a queue could never be drained."""
        if self.commands_per_drone_per_tick < _MINIMUM_PER_TICK:
            raise FleetSimulatorError(IntakeBoundRefusal.PER_TICK, self.commands_per_drone_per_tick)


@dataclass
class DroneInbox:
    """One drone's view of its own command queue, carried between ticks.

    Process-local and authority for nothing. The stream is keyed per queue rather than per
    producer because one gateway counts globally, so a drone's exclusive queue sees a
    monotonic subsequence of that counter and a shared stream would call one drone's
    sequence stale because another drone's had gone further.
    """

    stream: Stream = field(default_factory=Stream)
    answered: set[str] = field(default_factory=set)


class StampSource(Protocol):
    """Where an event's identifier, instant, sequence, and trace parent come from."""

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return the stamp for the next event ``producer`` publishes."""

    def next_result_stamp(
        self, producer: str, correlation_id: str, causation_id: str
    ) -> ResultStamp:
        """Return the stamp for the next command result ``producer`` publishes."""


class FleetSessionPort(Protocol):
    """The part of a fleet broker session this root uses.

    All three are read-only properties rather than attributes, so a session yielding
    narrower types than the protocol names still satisfies it. The two publishers stay
    distinct so a command result cannot be downgraded to droppable delivery.
    """

    @property
    def telemetry(self) -> DirectPublisher:
        """Return the direct publisher routine telemetry uses."""

    @property
    def results(self) -> MessagePublisher:
        """Return the guaranteed publisher command results use."""

    @property
    def receivers(self) -> Mapping[str, AcknowledgingReceiver]:
        """Return each drone's own queue-bound receiver, keyed by drone identifier."""

    def close(self) -> None:
        """Terminate every receiver and publisher and disconnect."""


SessionOpener = Callable[[BrokerEndpoint, Principal, str, Mapping[str, str]], FleetSessionPort]


@dataclass(frozen=True)
class ServeReport:
    """The state one run reached, what became of each reading, and of each command."""

    state: FleetState
    outcomes: Mapping[PublishOutcome, int]
    intake: Mapping[IntakeOutcome, int]


@dataclass
class CountingStamps:
    """Producer-scoped stamps over an injected clock and identifier source.

    The sequence is keyed by drone, because ``docs/CONTRACTS.md`` scopes it to its producer
    and each simulated drone is its own producer. The correlation identifier is the run's,
    supplied once, so every event of one run is correlatable without a request to bind to.
    """

    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    correlation_id: str
    sequences: dict[str, int] = field(default_factory=dict)

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return the next stamp for one producer and advance only that producer's stream."""
        sequence = self.sequences.get(producer, _FIRST_SEQUENCE)
        self.sequences[producer] = sequence + 1
        return TelemetryStamp(
            event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            sequence=sequence,
            correlation_id=self.correlation_id,
            traceparent=self._traceparent(),
        )

    def next_result_stamp(
        self, producer: str, correlation_id: str, causation_id: str
    ) -> ResultStamp:
        """Return the next result stamp, on the same stream the drone's telemetry uses.

        The correlation is the arriving command's rather than this run's, so the gateway's
        own trail holds across the answer, and the causation is the command's event, which
        is the only member that links a result to the send it answers.
        """
        sequence = self.sequences.get(producer, _FIRST_SEQUENCE)
        self.sequences[producer] = sequence + 1
        return ResultStamp(
            event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            sequence=sequence,
            correlation_id=correlation_id,
            causation_id=causation_id,
            traceparent=self._traceparent(),
        )

    def _traceparent(self) -> str:
        """Return a freshly minted trace parent, never one copied from untrusted context."""
        return "-".join(
            (
                TRACE_VERSION,
                self.identifiers(),
                self.identifiers()[:TRACE_PARENT_DIGITS],
                TRACE_SAMPLED,
            )
        )


@dataclass(frozen=True, repr=False)
class Runtime:
    """Every boundary the root would otherwise cross on its own.

    The generated representation is suppressed. ``credential`` is a secret, and a frozen
    dataclass's default ``repr`` would render it into any traceback, log line, or failing
    assertion that touched the runtime -- the same hazard ``packages/broker/AGENTS.md``
    records for `SempEndpoint`.
    """

    endpoint: BrokerEndpoint
    credential: str
    open_broker: SessionOpener
    scenario: FleetScenario
    stamps: StampSource
    running: Callable[[], bool]
    send_budget: SendBudget
    intake: IntakeBounds

    @override
    def __repr__(self) -> str:
        """Render the runtime without the credential it holds."""
        return f"Runtime(mission={self.scenario.mission_id!r}, credential=<redacted>)"


def _publish(
    publisher: DirectPublisher, mission_id: str, reading: Reading, stamps: StampSource
) -> PublishOutcome:
    """Send one reading, converting both expected failures into a counted outcome."""
    try:
        topic, document = telemetry_record(mission_id, reading, stamps.next_stamp(reading.drone_id))
    except TelemetryError:
        return PublishOutcome.UNRECORDABLE
    try:
        publisher.publish_unacknowledged(topic, canonical_bytes(document), _NO_PROPERTIES)
    except MessagingError:
        return PublishOutcome.REFUSED
    return PublishOutcome.PUBLISHED


def _resolution(scenario: FleetScenario, command: IncomingCommand) -> CommandEvent:
    """Return whether this drone can carry the command out.

    A sector this run holds succeeds; anything else fails. The simulator changes no sector
    state either way: reassigning a sector mid-run is a mission-coordination decision no
    record has made, and this member does not get to invent one.
    """
    if command.sector_id in sectors(scenario):
        return CommandEvent.SUCCEED
    return CommandEvent.FAIL


def _report(
    session: FleetSessionPort,
    runtime: Runtime,
    drone_id: str,
    command: IncomingCommand,
    progress: CommandProgress,
) -> None:
    """Publish one command result, guaranteed, and wait for the broker to take it."""
    stamp = runtime.stamps.next_result_stamp(drone_id, command.correlation_id, command.event_id)
    topic, document = result_record(
        runtime.scenario.mission_id, drone_id, command.command_id, progress.state, stamp
    )
    session.results.publish(topic, canonical_bytes(document), _NO_PROPERTIES)


def _answer(
    session: FleetSessionPort,
    runtime: Runtime,
    drone_id: str,
    command: IncomingCommand,
) -> None:
    """Acknowledge the command, resolve it, and report both, in that order.

    Two reports rather than one, because ADR-0074 gives no edge from ``IN_FLIGHT`` to
    ``FAILED``: a drone that refuses a command has to acknowledge it first, and that
    constraint is only observable on the wire if both reports are published.
    """
    budget = runtime.send_budget
    progress = apply(received(budget), CommandEvent.ACKNOWLEDGE, budget)
    _report(session, runtime, drone_id, command, progress)
    progress = apply(progress, _resolution(runtime.scenario, command), budget)
    _report(session, runtime, drone_id, command, progress)


def _admit(inbox: DroneInbox, command: IncomingCommand) -> IntakeOutcome | None:
    """Return the outcome a command needs no work for, or ``None`` to execute it.

    Two mechanisms, and they catch different things. A broker redelivery is the same event
    and its sequence has not advanced; a retry is a new event with a higher sequence and the
    original command identifier. Either way the result is already spooled on every consuming
    queue, so settling the message is what returns it.
    """
    reception = receive_sequence(inbox.stream, command.sequence)
    if reception.verdict is not SequenceVerdict.ADVANCES:
        return IntakeOutcome.SUPERSEDED
    inbox.stream = reception.stream
    known = command.command_id in inbox.answered
    if idempotency_decision(IdempotencyKind.COMMAND, known=known) is not (
        IdempotencyDecision.EXECUTE
    ):
        return IntakeOutcome.REPLAYED
    return None


def _handle(
    session: FleetSessionPort,
    runtime: Runtime,
    drone_id: str,
    inbox: DroneInbox,
    message: InboundMessage,
) -> tuple[IntakeOutcome, Outcome | None]:
    """Return what became of one message, and how it is to be settled.

    ``FAILED`` is for a condition that could differ on the next delivery; ``REJECTED`` is
    for one that cannot, so an unreadable command reaches the dead-message queue at once
    rather than after the queue's four arrivals.
    """
    try:
        command = accept(
            message.get_payload_as_bytes(),
            message.get_destination_name() or "",
            drone_id,
            runtime.scenario.mission_id,
        )
    except IntakeError:
        return (IntakeOutcome.UNREADABLE, Outcome.REJECTED)
    settled = _admit(inbox, command)
    if settled is not None:
        return (settled, Outcome.ACCEPTED)
    try:
        _answer(session, runtime, drone_id, command)
    except MessagingError:
        return (IntakeOutcome.RESULT_REFUSED, Outcome.FAILED)
    except ResultError, ProtocolError:
        return (IntakeOutcome.UNANSWERABLE, Outcome.REJECTED)
    inbox.answered.add(command.command_id)
    return (IntakeOutcome.HANDLED, Outcome.ACCEPTED)


def _settle(receiver: AcknowledgingReceiver, message: InboundMessage, outcome: Outcome) -> bool:
    """Settle one message, reporting whether the transport accepted the settlement.

    A refused settlement is not the work being undone: the message stays on the queue and is
    redelivered, so the run counts it and carries on rather than treating it as done. The
    port raises exactly one refusal here, so the refusal is not discriminated.
    """
    try:
        receiver.settle(message, outcome)
    except MessagingError:
        return False
    return True


def _drain_drone(
    session: FleetSessionPort,
    runtime: Runtime,
    drone_id: str,
    inbox: DroneInbox,
    counted: dict[IntakeOutcome, int],
) -> None:
    """Take at most the bound's worth of commands off one drone's queue and answer them."""
    receiver = session.receivers[drone_id]
    for _taken in range(runtime.intake.commands_per_drone_per_tick):
        message = receiver.receive(_POLL_MILLISECONDS)
        if message is None:
            return
        outcome, settlement = _handle(session, runtime, drone_id, inbox, message)
        if settlement is not None and not _settle(receiver, message, settlement):
            outcome = IntakeOutcome.SETTLEMENT_REFUSED
        counted[outcome] = counted.get(outcome, 0) + 1


def serve(
    session: FleetSessionPort,
    runtime: Runtime,
) -> ServeReport:
    """Fold ticks, publish their readings, and drain commands, until the run ends.

    The loop asks the runtime first and the mission second, so a run that has reached an
    ending never asks the fold for the tick ``docs/adr/0078`` refuses. The drain follows the
    fold, so a command received now affects the next observation rather than one already
    published, and it runs inside the same guard, so a run that stops ticking stops intaking.

    Drones are drained in ascending identifier order, the order ``docs/adr/0078`` already
    fixes for the fold, so broker arrival order never decides which drone acts first.
    """
    scenario = runtime.scenario
    state = initial_fleet(scenario)
    counted: dict[PublishOutcome, int] = {}
    taken: dict[IntakeOutcome, int] = {}
    inboxes = {drone.drone_id: DroneInbox() for drone in ordered_drones(scenario)}
    while runtime.running() and not mission_is_terminal(state.mission):
        tick = advance_tick(scenario, state)
        state = tick.state
        for reading in tick.readings:
            outcome = _publish(session.telemetry, scenario.mission_id, reading, runtime.stamps)
            counted[outcome] = counted.get(outcome, 0) + 1
        for drone_id, inbox in inboxes.items():
            _drain_drone(session, runtime, drone_id, inbox, taken)
    return ServeReport(state, counted, taken)


def run(runtime: Runtime) -> ServeReport:
    """Connect on the fleet-simulator role, serve the scenario, and shut the session down.

    Every declared drone's command queue is bound at startup. A queue the broker will not
    give is fatal here rather than counted: a drone whose queue was never provisioned loses
    its commands silently, which ``docs/adr/0080`` records as its sharpest negative and
    nothing else detects.

    Args:
        runtime: Every boundary the root uses, supplied by the caller.

    Returns:
        The state the run reached, what became of each reading, and of each command.
    """
    queues = {
        drone.drone_id: drone_queue_name(drone.drone_id)
        for drone in ordered_drones(runtime.scenario)
    }
    session = runtime.open_broker(
        runtime.endpoint, Principal.FLEET_SIMULATOR, runtime.credential, queues
    )
    try:
        return serve(session, runtime)
    finally:
        session.close()
