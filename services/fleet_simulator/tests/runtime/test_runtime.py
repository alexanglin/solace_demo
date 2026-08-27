"""Concrete Fleet PubSub+/PostgreSQL runtime composition and lifecycle."""

from __future__ import annotations

import asyncio
import threading
import unittest
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from types import TracebackType
from typing import cast, override

import pytest
from aerial_rescue_broker.messaging import (
    AcknowledgingReceiver,
    BrokerEndpoint,
    BrokerLifecycle,
    DirectPublisher,
    MessagePublisher,
    MessageSettlement,
    MessagingError,
    MessagingRefusal,
    Outcome,
    UnsettledMessageError,
    UnsettledMessageMetadata,
)
from aerial_rescue_broker.routing import DeliveryRouter, RoutingError, RoutingRefusal
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import Envelope, envelope_document
from aerial_rescue_domain.commands import CommandEvent, CommandState, SendBudget
from aerial_rescue_domain.idempotency import SequenceVerdict
from aerial_rescue_domain.outbox import OutboxEvent
from aerial_rescue_domain.principals import Principal
from aerial_rescue_fleet_simulator.control_plane.wire import (
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from aerial_rescue_fleet_simulator.critical_outbox import PublicationOutcome
from aerial_rescue_fleet_simulator.durable_processing import EffectResult
from aerial_rescue_fleet_simulator.intake import IncomingCommand
from aerial_rescue_fleet_simulator.results import ResultStamp, result_record
from aerial_rescue_fleet_simulator.runtime import (
    ExecutorDependencies,
    FleetExecutor,
    FleetRuntimeError,
    FleetRuntimeRefusal,
    FleetSessionOpener,
    _CriticalPublisher,
    _pace,
    _scenario,
)
from aerial_rescue_fleet_simulator.service import IntakeBounds
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalCandidate,
    BrokerRefusalDecision,
    BrokerRefusalOutcome,
    StoredBrokerRefusal,
)
from aerial_rescue_store.receipts import (
    CommandReceiptIdentity,
    ReceiptDecision,
    ReceiptOutcome,
)

pytestmark = [pytest.mark.unit]


class FakeDirectPublisher:
    """A direct port that records exact sends."""

    def __init__(self) -> None:
        """Begin without a publication."""
        self.sent: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish_unacknowledged(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Record one direct publication."""
        self.sent.append((topic, payload, properties))


class InterruptingDirectPublisher(FakeDirectPublisher):
    """Remove readiness after one accepted Direct publication."""

    def __init__(self, interrupt: Callable[[], None]) -> None:
        """Bind one exact lifecycle interruption."""
        super().__init__()
        self._interrupt = interrupt

    @override
    def publish_unacknowledged(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Record the publication and interrupt only the first time."""
        super().publish_unacknowledged(topic, payload, properties)
        if len(self.sent) == 1:
            self._interrupt()


class FakeGuaranteedPublisher:
    """A Guaranteed port that records exact sends."""

    def __init__(self, order: list[str] | None = None) -> None:
        """Begin without a publication."""
        self.sent: list[tuple[str, bytes, Mapping[str, object]]] = []
        self.order = order if order is not None else []

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        /,
    ) -> None:
        """Record one confirmed publication."""
        self.sent.append((topic, payload, properties))
        self.order.append("publish-confirmed")


class ScriptedRouter:
    """A typed-router test seam with one selected publication outcome."""

    def __init__(self, failure: Exception | None = None) -> None:
        """Begin with the selected failure and no broker calls."""
        self.failure = failure
        self.calls: list[tuple[str, bytes, Mapping[str, object]]] = []

    def publish(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
    ) -> None:
        """Record exact bytes or raise the scripted typed refusal."""
        self.calls.append((topic, payload, properties))
        if self.failure is not None:
            raise self.failure


class FakeMessage:
    """One exact Guaranteed command delivery."""

    def __init__(self, topic: str, payload: bytes) -> None:
        """Retain only the broker-visible command members."""
        self.topic = topic
        self.payload = payload

    def get_payload_as_bytes(self) -> bytes:
        """Return the exact command bytes."""
        return self.payload

    def get_destination_name(self) -> str:
        """Return the exact concrete command topic."""
        return self.topic

    def get_properties(self) -> Mapping[str, object]:
        """Return no user properties."""
        return {}


class FakeReceiver:
    """A bounded queue receiver with observable settlement."""

    def __init__(self, messages: list[FakeMessage], order: list[str]) -> None:
        """Retain a finite delivery script."""
        self.messages = messages
        self.order = order
        self.receive_calls = 0
        self.outcomes: list[Outcome] = []

    def receive(self, timeout_milliseconds: int, /) -> FakeMessage | None:
        """Return one scripted message without blocking."""
        if timeout_milliseconds != 0:
            raise AssertionError
        self.receive_calls += 1
        return self.messages.pop(0) if self.messages else None

    def settle(self, message: object, outcome: Outcome, /) -> None:
        """Record the exact post-commit settlement."""
        del message
        self.outcomes.append(outcome)
        self.order.append(f"settle-{outcome.name}")


class NativeTracePoisonReceiver(FakeReceiver):
    """Raise one message-bound native trace refusal, then become idle."""

    def __init__(self, order: list[str]) -> None:
        """Begin without the error that will be bound back to this receiver."""
        super().__init__([], order)
        self.error: UnsettledMessageError | None = None

    @override
    def receive(self, timeout_milliseconds: int, /) -> FakeMessage | None:
        """Raise the configured refusal once and return an idle result afterwards."""
        if timeout_milliseconds != 0:
            raise AssertionError
        self.receive_calls += 1
        if self.error is None:
            return None
        error = self.error
        self.error = None
        raise error


class FakeSession:
    """One connected Fleet capability graph."""

    def __init__(self, lifecycle: list[str], order: list[str] | None = None) -> None:
        """Start connected but application-unready."""
        self.telemetry: DirectPublisher = FakeDirectPublisher()
        self.results: MessagePublisher = FakeGuaranteedPublisher(order)
        self.receivers: Mapping[str, AcknowledgingReceiver] = {}
        self.readiness = BrokerLifecycle()
        self.readiness.connected()
        self._lifecycle = lifecycle

    def close(self) -> None:
        """Record broker shutdown."""
        self._lifecycle.append("broker-close")
        self.readiness.closed()


class FailingCloseSession(FakeSession):
    """Refuse broker close after recording the cleanup attempt."""

    @override
    def close(self) -> None:
        """Expose one bounded broker shutdown failure."""
        self._lifecycle.append("broker-close-failed")
        message = "broker-close-failed"
        raise RuntimeError(message)


class FakeOutbox:
    """An empty durable edge outbox."""

    def __init__(self, order: list[str] | None = None) -> None:
        """Begin with no staged or ambiguous rows."""
        self.rows: dict[str, list[StagedApplicationEvent]] = {}
        self.ambiguous: dict[str, list[StagedApplicationEvent]] = {}
        self.order = order

    async def pending(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Return no staged work for the selected drone."""
        return tuple(self.rows.get(drone_id, ()))

    async def reconciliation(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Return no ambiguous work for the selected drone."""
        return tuple(self.ambiguous.get(drone_id, ()))

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Apply one confirmed or ambiguous publication transition."""
        for drone_id, rows in self.rows.items():
            matched = [row for row in rows if row.event_id == identity.event_id]
            if not matched:
                continue
            rows[:] = [row for row in rows if row.event_id != identity.event_id]
            if event is OutboxEvent.AMBIGUOUS:
                self.ambiguous.setdefault(drone_id, []).extend(matched)
            if self.order is not None:
                self.order.append(event.name.lower())
            return
        raise AssertionError((identity, event, confirmed_at))


class FakeStore:
    """The lazy durable resources owned by the Fleet composition."""

    def __init__(
        self,
        lifecycle: list[str],
        order: list[str] | None = None,
        *,
        refusal_failure: bool = False,
    ) -> None:
        """Bind an empty outbox, shutdown recorder, and optional refusal fault."""
        self.outbox = FakeOutbox(order)
        self._lifecycle = lifecycle
        self.order = order if order is not None else []
        self.unit_of_works: list[FakeUnitOfWork] = []
        self.refusal_failure = refusal_failure

    def commands(
        self,
        effect: Callable[[str, IncomingCommand], EffectResult],
    ) -> FakeUnitOfWork:
        """Return one transaction graph over the shared outbox."""
        work = FakeUnitOfWork(
            self.outbox,
            effect,
            self.order,
            refusal_failure=self.refusal_failure,
        )
        self.unit_of_works.append(work)
        return work

    async def close(self) -> None:
        """Record store shutdown after broker shutdown."""
        self._lifecycle.append("store-close")


class FailingCloseStore(FakeStore):
    """Refuse the first store close so cleanup error ordering is observable."""

    @override
    async def close(self) -> None:
        """Record and raise one store shutdown failure."""
        self._lifecycle.append("store-close-failed")
        message = "store-close-failed"
        raise RuntimeError(message)


class FakeStamps:
    """A stamp source unused by lifecycle-only tests."""

    def __init__(self) -> None:
        """Start one deterministic sequence and no run binding."""
        self.correlation_id: str | None = None
        self.sequence = 0

    def begin_run(self, correlation_id: str) -> None:
        """Accept one run correlation identifier."""
        self.correlation_id = correlation_id

    def next_stamp(self, drone_id: str) -> TelemetryStamp:
        """Mint a deterministic telemetry stamp."""
        del drone_id
        self.sequence += 1
        return TelemetryStamp(
            f"telemetry-{self.sequence}",
            "2026-08-26T00:00:00.000Z",
            self.sequence,
            self.correlation_id or "missing-correlation",
            "00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
        )

    def next_result_stamp(
        self,
        drone_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> ResultStamp:
        """Remain available to command-processing tests."""
        del drone_id
        self.sequence += 1
        return ResultStamp(
            f"result-{self.sequence}",
            "2026-08-26T00:00:00.000Z",
            self.sequence,
            correlation_id,
            causation_id,
            "00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
        )

    def processed_at(self) -> str:
        """Return one canonical durable-processing instant."""
        return "2026-08-26T00:00:00.000Z"


class FakeSchemas:
    """A no-op schema executor for runs with no inbound command."""

    def __init__(self) -> None:
        """Begin without a validation."""
        self.validated: list[str] = []

    def validate(self, schema_id: str, payload: Mapping[str, object], /) -> None:
        """Accept a payload after recording neither value."""
        del payload
        self.validated.append(schema_id)


class FakeCommandTransaction:
    """One atomic command receipt/effect/outbox transaction."""

    def __init__(
        self,
        outbox: FakeOutbox,
        effect: Callable[[str, IncomingCommand], EffectResult],
        order: list[str],
    ) -> None:
        """Retain staged work until the context commits."""
        self.outbox = outbox
        self.effect = effect
        self.order = order
        self.drone_id = ""
        self.staged: list[StagedApplicationEvent] = []
        self.effects: list[EffectResult] = []

    async def admit_sequence(self, drone_id: str, sequence: int) -> SequenceVerdict:
        """Admit the first command sequence."""
        del sequence
        self.drone_id = drone_id
        return SequenceVerdict.ADVANCES

    async def claim_receipt(self, identity: CommandReceiptIdentity) -> ReceiptOutcome:
        """Claim one new durable receipt."""
        self.drone_id = identity.drone_id
        return ReceiptOutcome(ReceiptDecision.CLAIMED, None, None, None)

    async def apply_effect(self, command: IncomingCommand) -> EffectResult:
        """Apply the injected deterministic effect once."""
        effect = self.effect(self.drone_id, command)
        self.effects.append(effect)
        return effect

    async def stage_critical(self, drone_id: str, event: StagedApplicationEvent) -> None:
        """Retain exact critical bytes until commit."""
        if drone_id != self.drone_id:
            raise AssertionError
        self.staged.append(event)

    async def complete_receipt(
        self,
        identity: CommandReceiptIdentity,
        result: bytes,
        applied_sequence: int,
        processed_at: str,
    ) -> None:
        """Record completion while the transaction remains open."""
        del identity, result, applied_sequence, processed_at
        self.order.append("receipt-complete")


class FakeCommandContext(AbstractAsyncContextManager[FakeCommandTransaction]):
    """Commit staged rows only on a successful transaction exit."""

    def __init__(self, transaction: FakeCommandTransaction) -> None:
        """Bind one fake transaction."""
        self.transaction = transaction

    @override
    async def __aenter__(self) -> FakeCommandTransaction:
        """Open the fake transaction."""
        return self.transaction

    @override
    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit exact staged rows only on success."""
        del exception, traceback
        if exception_type is not None:
            self.transaction.order.append("rollback")
            return
        self.transaction.outbox.rows.setdefault(self.transaction.drone_id, []).extend(
            self.transaction.staged
        )
        self.transaction.order.append("commit")


class FakeUnitOfWork:
    """Construct fresh fake command transactions."""

    def __init__(
        self,
        outbox: FakeOutbox,
        effect: Callable[[str, IncomingCommand], EffectResult],
        order: list[str],
        *,
        refusal_failure: bool = False,
    ) -> None:
        """Retain transaction dependencies."""
        self.outbox = outbox
        self.effect = effect
        self.order = order
        self.transactions: list[FakeCommandTransaction] = []
        self.refusals: list[BrokerRefusalCandidate] = []
        self.refusal_failure = refusal_failure

    def begin(self) -> FakeCommandContext:
        """Return one fresh atomic command boundary."""
        transaction = FakeCommandTransaction(self.outbox, self.effect, self.order)
        self.transactions.append(transaction)
        return FakeCommandContext(transaction)

    async def refuse(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        """Persist a body-free refusal if a later test supplies one."""
        if self.refusal_failure:
            message = "refusal-store-unavailable"
            raise RuntimeError(message)
        self.refusals.append(fact)
        stored = StoredBrokerRefusal(
            "fleet-simulator",
            None,
            None,
            "test-channel",
            "test-refusal",
            fact.raw_digest,
            "2026-08-26T00:00:00.000Z",
        )
        return BrokerRefusalOutcome(BrokerRefusalDecision.STORED, stored)


@dataclass
class FakePacer:
    """A deterministic pacer unused by lifecycle-only tests."""

    milliseconds: int = 0

    def now_milliseconds(self) -> int:
        """Return the controlled reading."""
        return self.milliseconds

    def wait(self, milliseconds: int) -> None:
        """Advance without sleeping."""
        self.milliseconds += milliseconds


class StepPause:
    """A deterministic scheduler turn controlled by the test."""

    def __init__(self) -> None:
        """Begin without a pending pause."""
        self.waits: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    async def __call__(self) -> None:
        """Expose and await one release token."""
        released = asyncio.Event()
        await self.waits.put(released)
        await released.wait()

    async def next_wait(self) -> asyncio.Event:
        """Return the next monitor pause after its prior work is complete."""
        return await self.waits.get()


class InterruptingPacer(FakePacer):
    """Pause the first completed tick after applying one lifecycle interruption."""

    def __init__(self, interrupt: Callable[[], None]) -> None:
        """Bind one exact interruption and a bounded test-controlled release."""
        super().__init__()
        self._interrupt = interrupt
        self._loop = asyncio.get_running_loop()
        self._wait_calls = 0
        self.interrupted = asyncio.Event()
        self.release = threading.Event()

    @override
    def wait(self, milliseconds: int) -> None:
        """Interrupt after the first tick and hold its pacing call until released."""
        super().wait(milliseconds)
        self._wait_calls += 1
        if self._wait_calls != 1:
            return
        self._interrupt()
        self._loop.call_soon_threadsafe(self.interrupted.set)
        if not self.release.wait(timeout=1):
            message = "test did not release the interrupted tick"
            raise AssertionError(message)


def _start_request(*, ticks_to_sweep: int = 1) -> FleetControlStartRequest:
    """Return one accepted, bounded, twenty-drone Fleet run."""
    return FleetControlStartRequest.model_validate(
        {
            "controlVersion": 1,
            "runId": "run-2026-0001",
            "scenario": {
                "missionId": "m-2026-0001",
                "drones": [
                    {
                        "droneId": f"drone-{index:02d}",
                        "sectorId": f"sector-{index:02d}",
                        "latitudeMicrodegrees": 47_000_000 + index,
                        "longitudeMicrodegrees": -122_000_000,
                        "altitudeMetres": 400,
                        "headingDegrees": 0,
                        "groundSpeedCentimetresPerSecond": 850,
                        "batteryPermille": 1_000,
                        "northMicrodegreesPerTick": 10,
                        "eastMicrodegreesPerTick": 0,
                        "batteryDrainPermillePerTick": 5,
                    }
                    for index in range(20)
                ],
                "tickIntervalMilliseconds": 1_000,
                "connectivityThresholds": {
                    "missesToDegraded": 3,
                    "missesToOffline": 6,
                    "heartbeatsToRecover": 2,
                },
                "ticksToSweep": ticks_to_sweep,
                "absentHeartbeats": [],
            },
        }
    )


def _command() -> FakeMessage:
    """Return one valid assign-sector command for the first simulated drone."""
    topic = "aerial-rescue/v1/m-2026-0001/drone/drone-00/command/assign-sector"
    document = envelope_document(
        Envelope(
            id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6e",
            source="urn:aerial-rescue:service:command-gateway",
            type="aerial-rescue.v1.drone.command.assign-sector",
            subject="m-2026-0001",
            time="2026-08-26T00:00:00.000Z",
            dataschema=(
                "https://aerial-rescue.invalid/schemas/v1/payload/"
                "drone-command-assign-sector.schema.json"
            ),
            sequence="000000000000001",
            correlation_id="corr-2026-0001",
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
            data={
                "missionId": "m-2026-0001",
                "droneId": "drone-00",
                "commandId": "cmd-2026-0001",
                "sectorId": "sector-00",
            },
        )
    )
    return FakeMessage(topic, canonical_bytes(document))


def _rescue_command() -> FakeMessage:
    """Return one fully bound rescue escalation for the first simulated drone."""
    topic = "aerial-rescue/v1/m-2026-0001/drone/drone-00/command/escalate-rescue"
    document = envelope_document(
        Envelope(
            id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a70",
            source="urn:aerial-rescue:command-gateway:gateway-synthetic-01",
            type="aerial-rescue.v1.drone.command.escalate-rescue",
            subject="m-2026-0001",
            time="2026-08-26T00:00:00.000Z",
            dataschema=(
                "https://aerial-rescue.invalid/schemas/v1/payload/"
                "drone-command-escalate-rescue.schema.json"
            ),
            sequence="000000000000002",
            correlation_id="corr-2026-0002",
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
            data={
                "missionId": "m-2026-0001",
                "droneId": "drone-00",
                "commandId": "cmd-rescue-0001",
                "approvalId": "approval-0001",
                "proposalId": "proposal-0001",
                "proposalDigest": "1" * 64,
                "proposalVersion": 1,
                "evidenceDecisionId": "decision-0001",
                "evidenceDecisionDigest": "2" * 64,
                "evidenceDecisionVersion": 1,
                "latitudeMicrodegrees": 45_123_456,
                "longitudeMicrodegrees": -75_123_456,
            },
        )
    )
    return FakeMessage(topic, canonical_bytes(document))


def _staged_result() -> StagedApplicationEvent:
    """Return one valid exact command-result row awaiting reconnect recovery."""
    stamp = ResultStamp(
        "result-reconnect-1",
        "2026-08-26T00:00:00.000Z",
        9,
        "corr-2026-0001",
        "command-event-1",
        "00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01",
    )
    topic, document = result_record(
        "m-2026-0001",
        "drone-00",
        "cmd-2026-0001",
        CommandState.SUCCEEDED,
        stamp,
    )
    return StagedApplicationEvent(
        producer="urn:aerial-rescue:drone:drone-00",
        event_id=stamp.event_id,
        family="drone-command-result",
        topic=topic,
        headers=b"{}",
        payload=canonical_bytes(document),
        traceparent=stamp.traceparent,
        tracestate=None,
        correlation_id=stamp.correlation_id,
        causation_id=stamp.causation_id,
        staged_at=stamp.occurred_at,
    )


def _executor_for(
    session: FakeSession,
    store: FakeStore,
    configured_drone_ids: tuple[str, ...],
    *,
    recovery_pause: StepPause | None = None,
    pacer: FakePacer | None = None,
) -> FleetExecutor:
    """Return one runtime with deterministic dependencies for lifecycle edge tests."""
    return FleetExecutor(
        ExecutorDependencies(
            endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
            credential="secret",
            configured_drone_ids=configured_drone_ids,
            open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
            store=store,
            schemas=FakeSchemas(),
            stamps=FakeStamps(),
            pacer=pacer or FakePacer(),
            send_budget=SendBudget(max_sends=5),
            intake=IntakeBounds(commands_per_drone_per_tick=3),
            confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
            recovery_pause=recovery_pause or StepPause(),
        )
    )


def _session_for_run(
    drone_ids: tuple[str, ...],
    lifecycle: list[str],
    order: list[str],
    messages: list[FakeMessage] | None = None,
) -> tuple[FakeSession, FakeReceiver]:
    """Return one fully receiver-bound fake Fleet session."""
    session = FakeSession(lifecycle, order)
    receiver = FakeReceiver(messages or [], order)
    session.receivers = {
        drone_id: receiver if drone_id == "drone-00" else FakeReceiver([], order)
        for drone_id in drone_ids
    }
    return session, receiver


async def _start_run(
    executor: FleetExecutor,
    request: FleetControlStartRequest,
    pause: StepPause,
    cancelled: asyncio.Event | None = None,
) -> tuple[asyncio.Event, asyncio.Task[FleetControlRunStatus]]:
    """Start the executor and retain its initial monitor pause beside the run task."""
    await executor.startup()
    monitor_wait = await pause.next_wait()
    run_task = asyncio.create_task(executor.execute(request, cancelled or asyncio.Event()))
    return monitor_wait, run_task


async def _stop_run(
    executor: FleetExecutor,
    monitor_wait: asyncio.Event,
    run_task: asyncio.Task[FleetControlRunStatus],
    *,
    pacer: InterruptingPacer | None = None,
    run_wait: asyncio.Event | None = None,
) -> None:
    """Release test controls, cancel unfinished work, and close the executor."""
    if pacer is not None:
        pacer.release.set()
    monitor_wait.set()
    if run_wait is not None:
        run_wait.set()
    if not run_task.done():
        run_task.cancel()
    await asyncio.gather(run_task, return_exceptions=True)
    await executor.shutdown()


async def _release_and_take_two(
    pause: StepPause,
    releases: tuple[asyncio.Event, asyncio.Event],
) -> tuple[asyncio.Event, asyncio.Event]:
    """Release both recovery-loop callers and capture their next bounded waits."""
    for release in releases:
        release.set()
    return (
        await asyncio.wait_for(pause.next_wait(), timeout=0.25),
        await asyncio.wait_for(pause.next_wait(), timeout=0.25),
    )


def _recovery_snapshot(
    publisher: FakeDirectPublisher,
    receiver: FakeReceiver,
    store: FakeStore,
    pacer: FakePacer,
) -> tuple[int, int, int, int]:
    """Return every side-effect count that must remain fixed during recovery."""
    return (
        len(publisher.sent),
        receiver.receive_calls,
        len(store.unit_of_works[0].transactions),
        pacer.milliseconds,
    )


def _effects(store: FakeStore) -> list[EffectResult]:
    """Flatten the command effects committed by one fake runtime store."""
    return [
        effect
        for unit_of_work in store.unit_of_works
        for transaction in unit_of_work.transactions
        for effect in transaction.effects
    ]


def _drone_zero_telemetry(
    publisher: FakeDirectPublisher,
) -> tuple[list[int], list[object]]:
    """Return sequence and latitude evidence for drone zero's exact resumed ticks."""
    documents = [
        cast("Mapping[str, object]", canonical.decode(payload))
        for topic, payload, _properties in publisher.sent
        if "/drone/drone-00/telemetry" in topic
    ]
    sequences = [int(cast("str", document["sequence"])) for document in documents]
    latitudes = [
        cast("Mapping[str, object]", document["data"])["latitudeMicrodegrees"]
        for document in documents
    ]
    return sequences, latitudes


@dataclass(frozen=True)
class RecoveryExercise:
    """The collaborators needed to drive one multi-state active-run recovery."""

    pause: StepPause
    pacer: InterruptingPacer
    session: FakeSession
    receiver: FakeReceiver
    store: FakeStore
    monitor_wait: asyncio.Event
    run_task: asyncio.Task[FleetControlRunStatus]


@dataclass(frozen=True)
class RecoveryEvidence:
    """Side-effect and ordering evidence captured across one recovery exercise."""

    paused: tuple[int, int, int, int]
    repeated: tuple[int, int, int, int]
    pending: tuple[int, int, int, int]
    status: FleetControlRunStatus
    telemetry_sequences: list[int]
    latitudes: list[object]
    effects: list[EffectResult]


async def _exercise_transient_recovery(exercise: RecoveryExercise) -> RecoveryEvidence:
    """Drive RECOVERING and RECOVERY_PENDING before exact run resumption."""
    await exercise.pacer.interrupted.wait()
    exercise.receiver.messages.append(_command())
    exercise.pacer.release.set()
    run_wait = await asyncio.wait_for(exercise.pause.next_wait(), timeout=0.25)
    publisher = cast("FakeDirectPublisher", exercise.session.telemetry)
    paused = _recovery_snapshot(publisher, exercise.receiver, exercise.store, exercise.pacer)
    repeated_waits = await _release_and_take_two(
        exercise.pause,
        (exercise.monitor_wait, run_wait),
    )
    repeated = _recovery_snapshot(publisher, exercise.receiver, exercise.store, exercise.pacer)
    exercise.session.readiness.reconnected()
    exercise.store.outbox.ambiguous["drone-00"] = [_staged_result()]
    pending_waits = await _release_and_take_two(exercise.pause, repeated_waits)
    pending = _recovery_snapshot(publisher, exercise.receiver, exercise.store, exercise.pacer)
    exercise.store.outbox.ambiguous.clear()
    exercise.session.readiness.mark_ready()
    for wait in pending_waits:
        wait.set()
    status = await asyncio.wait_for(exercise.run_task, timeout=0.5)
    telemetry_sequences, latitudes = _drone_zero_telemetry(publisher)
    return RecoveryEvidence(
        paused,
        repeated,
        pending,
        status,
        telemetry_sequences,
        latitudes,
        _effects(exercise.store),
    )


class FleetRuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_critical_publisher_classifies_malformed_refused_ambiguous_and_confirmed_rows(
        self,
    ) -> None:
        # Arrange
        staged = _staged_result()
        malformed_router = ScriptedRouter()
        refused_router = ScriptedRouter(RoutingError(RoutingRefusal.INVALID_TOPIC, staged.topic))
        definite_router = ScriptedRouter(
            MessagingError(MessagingRefusal.PUBLISH_REFUSED, "refused")
        )
        ambiguous_router = ScriptedRouter(
            MessagingError(MessagingRefusal.PUBLISH_AMBIGUOUS, "confirmation-timeout")
        )
        confirmed_router = ScriptedRouter()
        publishers = (
            _CriticalPublisher(
                cast("DeliveryRouter", malformed_router),
                lambda: "2026-08-26T00:00:01.000Z",
            ),
            _CriticalPublisher(
                cast("DeliveryRouter", refused_router),
                lambda: "2026-08-26T00:00:01.000Z",
            ),
            _CriticalPublisher(
                cast("DeliveryRouter", definite_router),
                lambda: "2026-08-26T00:00:01.000Z",
            ),
            _CriticalPublisher(
                cast("DeliveryRouter", ambiguous_router),
                lambda: "2026-08-26T00:00:01.000Z",
            ),
            _CriticalPublisher(
                cast("DeliveryRouter", confirmed_router),
                lambda: "2026-08-26T00:00:01.000Z",
            ),
        )
        rows = (replace(staged, headers=b"[]"), staged, staged, staged, staged)

        # Act
        results = [
            await publisher.publish(row) for publisher, row in zip(publishers, rows, strict=True)
        ]

        # Assert
        self.assertEqual(
            [result.outcome for result in results],
            [
                PublicationOutcome.REFUSED,
                PublicationOutcome.REFUSED,
                PublicationOutcome.REFUSED,
                PublicationOutcome.AMBIGUOUS,
                PublicationOutcome.CONFIRMED,
            ],
        )
        self.assertEqual(results[-1].confirmed_at, "2026-08-26T00:00:01.000Z")
        self.assertEqual(malformed_router.calls, [])

    async def test_runtime_refuses_missing_duplicate_and_unprovisioned_sessions(self) -> None:
        # Arrange
        request = _start_request()
        requested = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        missing_session = _executor_for(FakeSession(lifecycle), FakeStore(lifecycle), requested)
        started_session = FakeSession(lifecycle)
        started = _executor_for(started_session, FakeStore(lifecycle), ("drone-00",))

        # Act
        with pytest.raises(FleetRuntimeError) as unavailable:
            await missing_session.execute(request, asyncio.Event())
        await started.startup()
        with pytest.raises(FleetRuntimeError) as duplicate:
            await started.startup()
        with pytest.raises(FleetRuntimeError) as unprovisioned:
            await started.execute(request, asyncio.Event())
        await started.shutdown()
        await missing_session.shutdown()

        # Assert
        self.assertEqual(unavailable.value.refusal, FleetRuntimeRefusal.SESSION_UNAVAILABLE)
        self.assertEqual(duplicate.value.refusal, FleetRuntimeRefusal.ALREADY_STARTED)
        self.assertEqual(unprovisioned.value.refusal, FleetRuntimeRefusal.UNPROVISIONED_DRONE)

    async def test_cancellation_and_exhaustion_return_distinct_terminal_outcomes(self) -> None:
        # Arrange
        request = _start_request()
        requested = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        session = FakeSession(lifecycle)
        executor = _executor_for(session, FakeStore(lifecycle), requested)
        await executor.startup()
        cancelled = asyncio.Event()
        cancelled.set()

        # Act
        cancelled_status = await executor.execute(request, cancelled)
        session.readiness.exhausted()
        failed_status = executor._interrupted(request, asyncio.Event(), 3, 40)
        exit_status = executor.exit_status
        await executor.shutdown()

        # Assert
        self.assertEqual(cancelled_status.state, "CANCELLED")
        self.assertIsNotNone(failed_status)
        self.assertEqual(failed_status.state if failed_status is not None else None, "FAILED")
        self.assertEqual(exit_status, 1)

    async def test_transient_disconnect_pauses_then_resumes_each_tick_and_effect_once(
        self,
    ) -> None:
        # Arrange
        request = _start_request(ticks_to_sweep=3)
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        order: list[str] = []
        session, receiver = _session_for_run(drone_ids, lifecycle, order)
        store = FakeStore(lifecycle, order)
        pause = StepPause()
        pacer = InterruptingPacer(session.readiness.reconnecting)
        executor = _executor_for(
            session,
            store,
            drone_ids,
            recovery_pause=pause,
            pacer=pacer,
        )
        cancelled = asyncio.Event()
        monitor_wait, run_task = await _start_run(executor, request, pause, cancelled)
        exercise = RecoveryExercise(
            pause,
            pacer,
            session,
            receiver,
            store,
            monitor_wait,
            run_task,
        )

        # Act
        try:
            evidence = await _exercise_transient_recovery(exercise)
        finally:
            await _stop_run(executor, monitor_wait, run_task, pacer=pacer)

        # Assert
        self.assertEqual(evidence.paused, (20, 1, 0, 1_000))
        self.assertEqual(evidence.repeated, evidence.paused)
        self.assertEqual(evidence.pending, evidence.paused)
        self.assertEqual(
            (
                evidence.status.state,
                evidence.status.completed_tick_count,
                evidence.status.telemetry_publication_count,
            ),
            ("EXHAUSTED", 3, 60),
        )
        self.assertEqual(evidence.telemetry_sequences, [1, 21, 43])
        self.assertEqual(evidence.latitudes, [47_000_010, 47_000_020, 47_000_030])
        self.assertEqual(len(evidence.effects), 1)
        self.assertEqual(receiver.outcomes, [Outcome.ACCEPTED])
        self.assertEqual(pacer.milliseconds, 3_000)

    async def test_connected_application_recovery_wait_is_promptly_cancellable(self) -> None:
        # Arrange
        request = _start_request(ticks_to_sweep=2)
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        session, _receiver = _session_for_run(drone_ids, lifecycle, [])
        pause = StepPause()
        pacer = InterruptingPacer(session.readiness.recovery_required)
        executor = _executor_for(
            session,
            FakeStore(lifecycle),
            drone_ids,
            recovery_pause=pause,
            pacer=pacer,
        )
        cancelled = asyncio.Event()
        monitor_wait, run_task = await _start_run(executor, request, pause, cancelled)
        run_wait: asyncio.Event | None = None

        # Act
        try:
            await pacer.interrupted.wait()
            pacer.release.set()
            run_wait = await asyncio.wait_for(pause.next_wait(), timeout=0.25)
            ready_while_waiting = executor.ready
            cancelled.set()
            status = await asyncio.wait_for(asyncio.shield(run_task), timeout=0.25)
        finally:
            await _stop_run(
                executor,
                monitor_wait,
                run_task,
                pacer=pacer,
                run_wait=run_wait,
            )

        # Assert
        publisher = cast("FakeDirectPublisher", session.telemetry)
        self.assertFalse(ready_while_waiting)
        self.assertEqual(
            (status.state, status.completed_tick_count, status.telemetry_publication_count),
            ("CANCELLED", 1, 20),
        )
        self.assertEqual(len(publisher.sent), 20)

    async def test_disconnect_mid_tick_pauses_remaining_publications_and_command_drain(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        order: list[str] = []
        session, receiver = _session_for_run(drone_ids, lifecycle, order, [_command()])
        session.telemetry = InterruptingDirectPublisher(session.readiness.reconnecting)
        store = FakeStore(lifecycle, order)
        pause = StepPause()
        executor = _executor_for(session, store, drone_ids, recovery_pause=pause)
        monitor_wait, run_task = await _start_run(executor, request, pause)

        # Act
        try:
            run_wait = await asyncio.wait_for(pause.next_wait(), timeout=0.25)
            publisher = session.telemetry
            paused_snapshot = (
                len(publisher.sent),
                receiver.receive_calls,
                len(store.unit_of_works[0].transactions),
            )
            session.readiness.reconnected()
            session.readiness.mark_ready()
            run_wait.set()
            status = await asyncio.wait_for(run_task, timeout=0.5)
        finally:
            await _stop_run(executor, monitor_wait, run_task)

        # Assert
        self.assertEqual(paused_snapshot, (1, 0, 0))
        self.assertEqual(
            (status.state, status.completed_tick_count, status.telemetry_publication_count),
            ("EXHAUSTED", 1, 20),
        )
        self.assertEqual(len(publisher.sent), 20)
        self.assertEqual(receiver.outcomes, [Outcome.ACCEPTED])
        self.assertEqual(len(store.unit_of_works[0].transactions), 1)

    async def test_recovery_exhaustion_fails_the_paused_run_and_requests_nonzero_exit(
        self,
    ) -> None:
        # Arrange
        request = _start_request(ticks_to_sweep=2)
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        session, _receiver = _session_for_run(drone_ids, lifecycle, [])
        pause = StepPause()
        pacer = InterruptingPacer(session.readiness.reconnecting)
        executor = _executor_for(
            session,
            FakeStore(lifecycle),
            drone_ids,
            recovery_pause=pause,
            pacer=pacer,
        )
        monitor_wait, run_task = await _start_run(executor, request, pause)

        # Act
        try:
            await pacer.interrupted.wait()
            pacer.release.set()
            run_wait = await asyncio.wait_for(pause.next_wait(), timeout=0.25)
            session.readiness.exhausted()
            run_wait.set()
            status = await asyncio.wait_for(run_task, timeout=0.25)
            exit_status = executor.exit_status
        finally:
            await _stop_run(executor, monitor_wait, run_task, pacer=pacer)

        # Assert
        publisher = cast("FakeDirectPublisher", session.telemetry)
        self.assertEqual(
            (status.state, status.completed_tick_count, status.telemetry_publication_count),
            ("FAILED", 1, 20),
        )
        self.assertEqual(exit_status, 1)
        self.assertEqual(len(publisher.sent), 20)

    async def test_ambiguous_outbox_evidence_prevents_startup_readiness(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        session = FakeSession(lifecycle)
        store = FakeStore(lifecycle)
        store.outbox.ambiguous["drone-00"] = [_staged_result()]
        executor = _executor_for(session, store, ("drone-00",))

        # Act
        await executor.startup()
        ready = executor.ready
        await executor.shutdown()

        # Assert
        self.assertFalse(ready)
        self.assertEqual(store.outbox.rows, {})

    async def test_connected_monitor_cycle_does_not_repeat_completed_recovery(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        pause = StepPause()
        session = FakeSession(lifecycle)
        executor = _executor_for(
            session,
            FakeStore(lifecycle),
            ("drone-00",),
            recovery_pause=pause,
        )
        await executor.startup()
        first_pause = await pause.next_wait()

        # Act
        first_pause.set()
        second_pause = await pause.next_wait()
        still_ready = executor.ready
        await executor.shutdown()

        # Assert
        self.assertTrue(still_ready)
        self.assertFalse(second_pause.is_set())
        self.assertEqual(executor.exit_status, 0)

    async def test_shutdown_continues_after_broker_failure_and_store_close_is_one_shot(
        self,
    ) -> None:
        # Arrange
        broker_lifecycle: list[str] = []
        broker_executor = _executor_for(
            FailingCloseSession(broker_lifecycle),
            FakeStore(broker_lifecycle),
            ("drone-00",),
        )
        await broker_executor.startup()
        store_lifecycle: list[str] = []
        store_executor = _executor_for(
            FakeSession(store_lifecycle),
            FailingCloseStore(store_lifecycle),
            ("drone-00",),
        )

        # Act
        with pytest.raises(RuntimeError, match="broker-close-failed") as broker_error:
            await broker_executor.shutdown()
        with pytest.raises(RuntimeError, match="store-close-failed") as store_error:
            await store_executor.shutdown()
        await store_executor.shutdown()

        # Assert
        self.assertEqual(str(broker_error.value), "broker-close-failed")
        self.assertEqual(str(store_error.value), "store-close-failed")
        self.assertEqual(broker_lifecycle, ["broker-close-failed", "store-close"])
        self.assertEqual(store_lifecycle, ["store-close-failed"])

    async def test_router_guard_absent_heartbeat_mapping_and_elapsed_pacing_are_bounded(
        self,
    ) -> None:
        # Arrange
        request_document = _start_request().model_dump(mode="json", by_alias=True)
        scenario_document = cast("dict[str, object]", request_document["scenario"])
        scenario_document["absentHeartbeats"] = [
            {"droneId": "drone-00", "tickOrdinal": 2},
            {"droneId": "drone-00", "tickOrdinal": 3},
        ]
        request = FleetControlStartRequest.model_validate(request_document)
        lifecycle: list[str] = []
        executor = _executor_for(FakeSession(lifecycle), FakeStore(lifecycle), ("drone-00",))
        pacer = FakePacer(milliseconds=2_000)

        # Act
        with pytest.raises(FleetRuntimeError) as router_error:
            executor._require_router()
        scenario = _scenario(request)
        await _pace(pacer, 1_000, 0)
        await executor.shutdown()

        # Assert
        self.assertEqual(router_error.value.refusal, FleetRuntimeRefusal.ROUTER_UNAVAILABLE)
        self.assertEqual(scenario.absent_heartbeats["drone-00"], frozenset({2, 3}))
        self.assertEqual(pacer.milliseconds, 2_000)

    async def test_native_trace_refusal_commits_body_free_evidence_before_dmq_rejection(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        order: list[str] = []
        session = FakeSession(lifecycle, order)
        receiver = NativeTracePoisonReceiver(order)
        message = _command()
        receiver.error = UnsettledMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            MessageSettlement(cast("AcknowledgingReceiver", receiver), message),
            UnsettledMessageMetadata(
                source="urn:aerial-rescue:service:command-gateway",
                family="drone.command",
                raw_digest="4" * 64,
            ),
        )
        session.receivers = {
            drone_id: receiver if drone_id == "drone-00" else FakeReceiver([], order)
            for drone_id in drone_ids
        }
        store = FakeStore(lifecycle, order)
        executor = FleetExecutor(
            ExecutorDependencies(
                endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
                credential="secret",
                configured_drone_ids=drone_ids,
                open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
                store=store,
                schemas=FakeSchemas(),
                stamps=FakeStamps(),
                pacer=FakePacer(),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
                recovery_pause=StepPause(),
            )
        )
        await executor.startup()

        # Act
        status = await executor.execute(request, asyncio.Event())
        await executor.shutdown()

        # Assert
        self.assertEqual(status.state, "EXHAUSTED")
        self.assertEqual(receiver.outcomes, [Outcome.REJECTED])
        self.assertEqual(
            store.unit_of_works[0].refusals,
            [
                BrokerRefusalCandidate(
                    consumer="fleet-simulator",
                    source="urn:aerial-rescue:service:command-gateway",
                    family="drone.command",
                    channel="fleet-simulator-drone-command-drone-00",
                    refusal_code="native-trace-refused",
                    raw_digest="4" * 64,
                )
            ],
        )

    async def test_native_trace_refusal_stays_unsettled_when_evidence_cannot_commit(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        order: list[str] = []
        session = FakeSession(lifecycle, order)
        receiver = NativeTracePoisonReceiver(order)
        message = _command()
        receiver.error = UnsettledMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            MessageSettlement(cast("AcknowledgingReceiver", receiver), message),
            UnsettledMessageMetadata(None, None, "4" * 64),
        )
        session.receivers = {
            drone_id: receiver if drone_id == "drone-00" else FakeReceiver([], order)
            for drone_id in drone_ids
        }
        store = FakeStore(lifecycle, order, refusal_failure=True)
        executor = FleetExecutor(
            ExecutorDependencies(
                endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
                credential="secret",
                configured_drone_ids=drone_ids,
                open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
                store=store,
                schemas=FakeSchemas(),
                stamps=FakeStamps(),
                pacer=FakePacer(),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
                recovery_pause=StepPause(),
            )
        )
        await executor.startup()

        # Act
        with pytest.raises(RuntimeError, match="refusal-store-unavailable"):
            await executor.execute(request, asyncio.Event())
        await executor.shutdown()

        # Assert
        self.assertEqual([], receiver.outcomes)

    async def test_startup_opens_one_shared_session_recovers_then_shutdown_reverses_resources(
        self,
    ) -> None:
        # Arrange
        lifecycle: list[str] = []
        session = FakeSession(lifecycle)
        store = FakeStore(lifecycle)
        opened: list[tuple[BrokerEndpoint, Principal, str, Mapping[str, str]]] = []
        endpoint = BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem")

        def open_broker(
            target: BrokerEndpoint,
            role: Principal,
            credential: str,
            queues: Mapping[str, str],
        ) -> FakeSession:
            opened.append((target, role, credential, queues))
            return session

        dependencies = ExecutorDependencies(
            endpoint=endpoint,
            credential="secret",
            configured_drone_ids=("drone-sim-02", "drone-sim-01"),
            open_broker=cast("FleetSessionOpener", open_broker),
            store=store,
            schemas=FakeSchemas(),
            stamps=FakeStamps(),
            pacer=FakePacer(),
            send_budget=SendBudget(max_sends=5),
            intake=IntakeBounds(commands_per_drone_per_tick=3),
            confirmed_at=lambda: "2026-08-26T00:00:00.000Z",
            recovery_pause=StepPause(),
        )
        executor = FleetExecutor(dependencies)

        # Act
        await executor.startup()
        ready_after_recovery = executor.ready
        await executor.shutdown()

        # Assert
        self.assertTrue(ready_after_recovery)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0][:3], (endpoint, Principal.FLEET_SIMULATOR, "secret"))
        self.assertEqual(
            opened[0][3],
            {
                "drone-sim-01": "aerial-rescue/v1/fleet-simulator/drone.command/drone-sim-01",
                "drone-sim-02": "aerial-rescue/v1/fleet-simulator/drone.command/drone-sim-02",
            },
        )
        self.assertEqual(lifecycle, ["broker-close", "store-close"])
        self.assertFalse(executor.ready)

    async def test_execute_folds_twenty_drones_and_publishes_direct_until_exhausted(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        session = FakeSession(lifecycle)
        session.receivers = {drone_id: FakeReceiver([], []) for drone_id in drone_ids}
        store = FakeStore(lifecycle)
        stamps = FakeStamps()
        pacer = FakePacer()
        dependencies = ExecutorDependencies(
            endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
            credential="secret",
            configured_drone_ids=drone_ids,
            open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
            store=store,
            schemas=FakeSchemas(),
            stamps=stamps,
            pacer=pacer,
            send_budget=SendBudget(max_sends=5),
            intake=IntakeBounds(commands_per_drone_per_tick=3),
            confirmed_at=lambda: "2026-08-26T00:00:00.000Z",
            recovery_pause=StepPause(),
        )
        executor = FleetExecutor(dependencies)
        await executor.startup()

        # Act
        result = await executor.execute(request, asyncio.Event())
        await executor.shutdown()

        # Assert
        publisher = cast("FakeDirectPublisher", session.telemetry)
        self.assertIsInstance(publisher, FakeDirectPublisher)
        self.assertEqual(
            (result.state, result.completed_tick_count, result.telemetry_publication_count),
            ("EXHAUSTED", 1, 20),
        )
        self.assertEqual(len(publisher.sent), 20)
        self.assertEqual(stamps.correlation_id, request.run_id)
        self.assertEqual(pacer.milliseconds, 1_000)

    async def test_command_commits_then_settles_then_publishes_both_durable_results(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        order: list[str] = []
        session = FakeSession(lifecycle, order)
        receiver = FakeReceiver([_command()], order)
        session.receivers = {
            drone_id: receiver if drone_id == "drone-00" else FakeReceiver([], order)
            for drone_id in drone_ids
        }
        store = FakeStore(lifecycle, order)
        schemas = FakeSchemas()
        executor = FleetExecutor(
            ExecutorDependencies(
                endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
                credential="secret",
                configured_drone_ids=drone_ids,
                open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
                store=store,
                schemas=schemas,
                stamps=FakeStamps(),
                pacer=FakePacer(),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
                recovery_pause=StepPause(),
            )
        )
        await executor.startup()

        # Act
        status = await executor.execute(request, asyncio.Event())
        await executor.shutdown()

        # Assert
        publisher = cast("FakeGuaranteedPublisher", session.results)
        self.assertIsInstance(publisher, FakeGuaranteedPublisher)
        self.assertEqual(status.state, "EXHAUSTED")
        self.assertEqual(receiver.outcomes, [Outcome.ACCEPTED])
        self.assertEqual(len(publisher.sent), 2)
        self.assertEqual(len(schemas.validated), 1)
        self.assertLess(order.index("commit"), order.index("settle-ACCEPTED"))
        self.assertLess(order.index("settle-ACCEPTED"), order.index("publish-confirmed"))
        self.assertEqual(order.count("confirm"), 2)

    async def test_rescue_escalation_commits_the_complete_bound_effect_before_settlement(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        lifecycle: list[str] = []
        order: list[str] = []
        session = FakeSession(lifecycle, order)
        receiver = FakeReceiver([_rescue_command()], order)
        session.receivers = {
            drone_id: receiver if drone_id == "drone-00" else FakeReceiver([], order)
            for drone_id in drone_ids
        }
        store = FakeStore(lifecycle, order)
        executor = FleetExecutor(
            ExecutorDependencies(
                endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
                credential="secret",
                configured_drone_ids=drone_ids,
                open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
                store=store,
                schemas=FakeSchemas(),
                stamps=FakeStamps(),
                pacer=FakePacer(),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
                recovery_pause=StepPause(),
            )
        )
        await executor.startup()

        # Act
        status = await executor.execute(request, asyncio.Event())
        effect = store.unit_of_works[0].transactions[0].effects[0]
        await executor.shutdown()

        # Assert
        self.assertEqual(status.state, "EXHAUSTED")
        self.assertEqual(receiver.outcomes, [Outcome.ACCEPTED])
        self.assertEqual(effect.event, CommandEvent.SUCCEED)
        self.assertEqual(
            canonical.decode(effect.effect_payload),
            {
                "approvalId": "approval-0001",
                "commandId": "cmd-rescue-0001",
                "droneId": "drone-00",
                "evidenceDecisionDigest": "2" * 64,
                "evidenceDecisionId": "decision-0001",
                "evidenceDecisionVersion": 1,
                "latitudeMicrodegrees": 45_123_456,
                "longitudeMicrodegrees": -75_123_456,
                "proposalDigest": "1" * 64,
                "proposalId": "proposal-0001",
                "proposalVersion": 1,
                "state": "rescue-escalated",
            },
        )
        self.assertLess(order.index("commit"), order.index("settle-ACCEPTED"))
        self.assertLess(order.index("settle-ACCEPTED"), order.index("publish-confirmed"))

    async def test_reconnect_drains_before_readiness_and_exhaustion_requests_nonzero_exit(
        self,
    ) -> None:
        # Arrange
        lifecycle: list[str] = []
        order: list[str] = []
        session = FakeSession(lifecycle, order)
        session.receivers = {"drone-00": FakeReceiver([], order)}
        store = FakeStore(lifecycle, order)
        pause = StepPause()
        executor = FleetExecutor(
            ExecutorDependencies(
                endpoint=BrokerEndpoint("tcps://broker:55443", "default", "/run/ca.pem"),
                credential="secret",
                configured_drone_ids=("drone-00",),
                open_broker=cast("FleetSessionOpener", lambda *_arguments: session),
                store=store,
                schemas=FakeSchemas(),
                stamps=FakeStamps(),
                pacer=FakePacer(),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                confirmed_at=lambda: "2026-08-26T00:00:01.000Z",
                recovery_pause=pause,
            )
        )

        # Act
        await executor.startup()
        first_pause = await pause.next_wait()
        session.readiness.reconnecting()
        ready_during_disconnect = executor.ready
        store.outbox.rows["drone-00"] = [_staged_result()]
        session.readiness.reconnected()
        first_pause.set()
        second_pause = await pause.next_wait()
        ready_after_drain = executor.ready
        session.readiness.exhausted()
        second_pause.set()
        await executor.wait_for_exhaustion()
        exit_status = executor.exit_status
        await executor.shutdown()

        # Assert
        publisher = cast("FakeGuaranteedPublisher", session.results)
        self.assertIsInstance(publisher, FakeGuaranteedPublisher)
        self.assertFalse(ready_during_disconnect)
        self.assertTrue(ready_after_drain)
        self.assertEqual((len(publisher.sent), order.count("confirm")), (1, 1))
        self.assertEqual(exit_status, 1)


if __name__ == "__main__":
    unittest.main()
