"""Long-lived Solace and PostgreSQL composition for the simulated fleet.

The private HTTP boundary owns this executor for one process epoch.  It opens one Solace
connection containing the two delivery-typed publishers and every per-drone Guaranteed
receiver, recovers the durable critical outboxes before readiness, and releases the broker
before the SQLAlchemy store during shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast, override

from aerial_rescue_broker.ingress import (
    IngressError,
    PayloadSchemaExecutor,
    validate_notification,
)
from aerial_rescue_broker.messaging import (
    AcknowledgingReceiver,
    BrokerEndpoint,
    BrokerLifecycle,
    BrokerLifecycleState,
    DirectPublisher,
    InboundMessage,
    MessagePublisher,
    MessageSettlement,
    MessagingError,
    MessagingRefusal,
    UnsettledMessageError,
    inbound_payload,
)
from aerial_rescue_broker.queues import drone_queue_name
from aerial_rescue_broker.routing import (
    DeliveryRouter,
    PublicationPorts,
    RoutingError,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.mission import is_terminal as mission_is_terminal
from aerial_rescue_domain.principals import Principal
from aerial_rescue_store.application_outbox import (
    StagedApplicationEvent,
)
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate

from aerial_rescue_fleet_simulator import FleetSimulatorError, run_event_source
from aerial_rescue_fleet_simulator.control_plane.wire import (
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from aerial_rescue_fleet_simulator.critical_outbox import (
    CriticalOutboxPort,
    PublicationOutcome,
    PublicationResult,
    drain_recovery,
)
from aerial_rescue_fleet_simulator.durable_processing import (
    CommandContext,
    CommandDelivery,
    EffectResult,
    FleetUnitOfWork,
    ProcessingError,
    handle_command,
    refusal_candidate,
)
from aerial_rescue_fleet_simulator.fleet import Reading, advance_tick, initial_fleet
from aerial_rescue_fleet_simulator.intake import (
    AssignSectorCommand,
    IncomingCommand,
)
from aerial_rescue_fleet_simulator.results import ResultStamp
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario, sectors
from aerial_rescue_fleet_simulator.service import IntakeBounds, Pacer
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp, telemetry_record


class FleetRuntimeRefusal(Enum):
    """Why the concrete Fleet runtime refuses an operation."""

    ALREADY_STARTED = "the Fleet runtime is already started"
    NOT_RECOVERED = "the Fleet runtime has not completed durable recovery"
    UNPROVISIONED_DRONE = "the accepted run names an unprovisioned drone"
    READINESS_LOST = "the Fleet runtime lost dependency readiness"
    SESSION_UNAVAILABLE = "the Fleet broker session is unavailable"
    ROUTER_UNAVAILABLE = "the typed Fleet delivery router is unavailable"


class FleetRuntimeError(FleetSimulatorError):
    """A runtime operation refused with a closed, secret-safe reason."""


class FleetRuntimeSession(Protocol):
    """The exact broker capabilities one fleet process owns."""

    @property
    def telemetry(self) -> DirectPublisher:
        """Return the Direct telemetry publisher."""

    @property
    def results(self) -> MessagePublisher:
        """Return the confirmed critical-event publisher."""

    @property
    def receivers(self) -> Mapping[str, AcknowledgingReceiver]:
        """Return every queue-bound per-drone receiver."""

    @property
    def readiness(self) -> BrokerLifecycle:
        """Return the shared transport and application lifecycle."""

    def close(self) -> None:
        """Release receivers, publishers, and the shared connection."""


class CriticalOutboxRuntime(CriticalOutboxPort, Protocol):
    """Fleet outbox reads, including ambiguity inspection before readiness."""

    async def reconciliation(self, drone_id: str) -> tuple[StagedApplicationEvent, ...]:
        """Return rows that require evidence rather than blind republication."""


class FleetRuntimeStore(Protocol):
    """Purpose-specific store composition owned by this process."""

    @property
    def outbox(self) -> CriticalOutboxRuntime:
        """Return the independently transactional critical outbox."""

    def commands(
        self,
        effect: Callable[[str, IncomingCommand], EffectResult],
    ) -> FleetUnitOfWork:
        """Return the durable command unit of work for one run effect."""

    async def close(self) -> None:
        """Dispose the SQLAlchemy engine within its configured bound."""


class FleetSessionOpener(Protocol):
    """Open one least-privilege Fleet connection without exposing vendor types."""

    def __call__(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        queues: Mapping[str, str],
        /,
    ) -> FleetRuntimeSession:
        """Return one connected mixed Fleet session."""


class RunStampSource(Protocol):
    """Set the correlation context used by a newly accepted run."""

    def begin_run(self, correlation_id: str) -> None:
        """Bind subsequent Fleet records to the run."""

    def next_stamp(self, drone_id: str) -> TelemetryStamp:
        """Mint the next producer-scoped telemetry stamp."""

    def next_result_stamp(
        self,
        drone_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> ResultStamp:
        """Mint the next command-result stamp."""

    def processed_at(self) -> str:
        """Return the canonical durable command-processing instant."""


@dataclass(frozen=True, repr=False)
class ExecutorDependencies:
    """Every long-lived or injected boundary used by :class:`FleetExecutor`."""

    endpoint: BrokerEndpoint
    credential: str
    configured_drone_ids: tuple[str, ...]
    open_broker: FleetSessionOpener
    store: FleetRuntimeStore
    schemas: PayloadSchemaExecutor
    stamps: RunStampSource
    pacer: Pacer
    send_budget: SendBudget
    intake: IntakeBounds
    confirmed_at: Callable[[], str]
    recovery_pause: Callable[[], Awaitable[None]]

    @override
    def __repr__(self) -> str:
        """Never render broker credentials in diagnostics."""
        return "ExecutorDependencies(credential=<redacted>)"


@dataclass(frozen=True, slots=True)
class _RunCheckpoint:
    """One immutable readiness-check position inside an active run."""

    request: FleetControlStartRequest
    cancelled: asyncio.Event
    ticks: int
    publications: int


class _RecoveryReadiness:
    """Adapt the shared broker lifecycle to the critical-outbox recovery port."""

    def __init__(self, lifecycle: BrokerLifecycle) -> None:
        self._lifecycle = lifecycle

    def is_connected(self) -> bool:
        """Permit recovery on initial or restored transport only."""
        return self._lifecycle.state in {
            BrokerLifecycleState.CONNECTED,
            BrokerLifecycleState.RECOVERY_PENDING,
        }

    def recovery_required(self) -> None:
        """Remove readiness before inspecting durable work."""
        self._lifecycle.recovery_required()

    def mark_ready(self) -> None:
        """Restore readiness only at an empty, unambiguous boundary."""
        self._lifecycle.mark_ready()


class _CriticalPublisher:
    """Translate typed broker outcomes into durable outbox transitions."""

    def __init__(self, router: DeliveryRouter, confirmed_at: Callable[[], str]) -> None:
        self._router = router
        self._confirmed_at = confirmed_at

    async def publish(self, event: StagedApplicationEvent) -> PublicationResult:
        """Publish exact staged bytes, distinguishing refusal from ambiguity."""
        properties = _message_properties(event.headers)
        if properties is None:
            return PublicationResult(PublicationOutcome.REFUSED, None)
        try:
            await asyncio.to_thread(
                self._router.publish,
                event.topic,
                event.payload,
                properties,
            )
        except RoutingError:
            return PublicationResult(PublicationOutcome.REFUSED, None)
        except MessagingError as error:
            outcome = (
                PublicationOutcome.REFUSED
                if error.refusal is MessagingRefusal.PUBLISH_REFUSED
                else PublicationOutcome.AMBIGUOUS
            )
            return PublicationResult(outcome, None)
        return PublicationResult(PublicationOutcome.CONFIRMED, self._confirmed_at())


class _AsyncSettlement:
    """Keep the synchronous SDK settlement outside the event-loop thread."""

    def __init__(self, receiver: AcknowledgingReceiver, message: InboundMessage) -> None:
        """Bind one exact one-shot broker settlement capability."""
        self._settlement = MessageSettlement(receiver, message)

    async def accept(self) -> None:
        """Accept only after the durable use case commits."""
        await asyncio.to_thread(self._settlement.accept)

    async def fail(self) -> None:
        """Return a transient refusal for broker redelivery."""
        await asyncio.to_thread(self._settlement.fail)

    async def reject(self) -> None:
        """Move a permanent refusal through the queue's dead-message policy."""
        await asyncio.to_thread(self._settlement.reject)


class FleetExecutor:
    """Run-scoped simulation over one process-scoped broker/store capability graph."""

    def __init__(self, dependencies: ExecutorDependencies) -> None:
        """Retain lazy dependencies without opening a socket or database connection."""
        self._dependencies = dependencies
        self._session: FleetRuntimeSession | None = None
        self._router: DeliveryRouter | None = None
        self._monitor: asyncio.Task[None] | None = None
        self._recovery_lock = asyncio.Lock()
        self._exhausted = asyncio.Event()
        self._store_closed = False

    @property
    def ready(self) -> bool:
        """Report only transport plus completed application recovery readiness."""
        return self._session is not None and self._session.readiness.is_ready()

    @property
    def exit_status(self) -> int:
        """Return nonzero only after active-session recovery has exhausted."""
        return int(self._exhausted.is_set())

    async def wait_for_exhaustion(self) -> None:
        """Wait until the broker declares active-session recovery terminal."""
        await self._exhausted.wait()

    async def startup(self) -> None:
        """Bind every configured queue on one session and drain durable critical work."""
        if self._session is not None:
            raise FleetRuntimeError(FleetRuntimeRefusal.ALREADY_STARTED, None)
        queue_names = {
            drone_id: drone_queue_name(drone_id)
            for drone_id in sorted(set(self._dependencies.configured_drone_ids))
        }
        try:
            session = await asyncio.to_thread(
                self._dependencies.open_broker,
                self._dependencies.endpoint,
                Principal.FLEET_SIMULATOR,
                self._dependencies.credential,
                queue_names,
            )
            self._session = session
            self._router = DeliveryRouter(
                Principal.FLEET_SIMULATOR,
                PublicationPorts(direct=session.telemetry, guaranteed=session.results),
            )
            await self._recover()
            self._monitor = asyncio.create_task(self._monitor_recovery())
        except BaseException:
            self._exhausted.set()
            await self.shutdown()
            raise

    async def execute(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
    ) -> FleetControlRunStatus:
        """Fold and publish one accepted simulation until cancellation or exhaustion."""
        scenario, requested, command_context = self._prepare_run(request)
        state = initial_fleet(scenario)
        publications = 0
        while not mission_is_terminal(state.mission):
            interrupted = await self._await_operable(
                request,
                cancelled,
                state.tick,
                publications,
            )
            if interrupted is not None:
                return interrupted
            started = self._dependencies.pacer.now_milliseconds()
            tick = advance_tick(scenario, state)
            state = tick.state
            checkpoint = _RunCheckpoint(request, cancelled, state.tick, publications)
            published, interrupted = await self._publish_readings(
                scenario,
                tick.readings,
                checkpoint,
            )
            publications += published
            if interrupted is not None:
                return interrupted
            interrupted = await self._drain_commands(
                tuple(sorted(requested)),
                command_context,
                _RunCheckpoint(request, cancelled, state.tick, publications),
            )
            if interrupted is not None:
                return interrupted
            await self._recover()
            await _pace(
                self._dependencies.pacer,
                scenario.tick_interval_milliseconds,
                started,
            )
        return _run_status(request, "EXHAUSTED", state.tick, publications)

    async def _await_operable(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
        ticks: int,
        publications: int,
    ) -> FleetControlRunStatus | None:
        """Pause an active run until complete broker application readiness returns."""
        while True:
            interrupted = self._interrupted(request, cancelled, ticks, publications)
            if interrupted is not None or self.ready:
                return interrupted
            await _wait_for_recovery_pause(self._dependencies.recovery_pause, cancelled)

    def _prepare_run(
        self,
        request: FleetControlStartRequest,
    ) -> tuple[FleetScenario, frozenset[str], CommandContext]:
        """Bind one accepted run to provisioned receivers and durable command work."""
        self._require_session()
        if not self.ready:
            raise FleetRuntimeError(FleetRuntimeRefusal.NOT_RECOVERED, None)
        scenario = _scenario(request)
        configured = frozenset(self._dependencies.configured_drone_ids)
        requested = frozenset(drone.drone_id for drone in scenario.drones)
        if not requested.issubset(configured):
            raise FleetRuntimeError(FleetRuntimeRefusal.UNPROVISIONED_DRONE, None)
        self._dependencies.stamps.begin_run(request.run_id)
        context = CommandContext(
            scenario.mission_id,
            requested,
            self._dependencies.send_budget,
            self._dependencies.stamps,
            self._dependencies.store.commands(_effect_for(scenario)),
        )
        return scenario, requested, context

    def _interrupted(
        self,
        request: FleetControlStartRequest,
        cancelled: asyncio.Event,
        ticks: int,
        publications: int,
    ) -> FleetControlRunStatus | None:
        """Return a terminal status or refuse an unexpected unready lifecycle."""
        if cancelled.is_set():
            return _run_status(request, "CANCELLED", ticks, publications)
        session = self._require_session()
        state = session.readiness.state
        if state is BrokerLifecycleState.EXHAUSTED:
            self._exhausted.set()
            return _run_status(request, "FAILED", ticks, publications)
        if not self.ready and state not in {
            BrokerLifecycleState.CONNECTED,
            BrokerLifecycleState.RECOVERING,
            BrokerLifecycleState.RECOVERY_PENDING,
        }:
            raise FleetRuntimeError(FleetRuntimeRefusal.READINESS_LOST, None)
        return None

    async def _publish_readings(
        self,
        scenario: FleetScenario,
        readings: tuple[Reading, ...],
        checkpoint: _RunCheckpoint,
    ) -> tuple[int, FleetControlRunStatus | None]:
        """Publish one tick's Direct telemetry, pausing between individual sends."""
        published = 0
        for reading in readings:
            interrupted = await self._await_operable(
                checkpoint.request,
                checkpoint.cancelled,
                checkpoint.ticks,
                checkpoint.publications + published,
            )
            if interrupted is not None:
                return published, interrupted
            topic, document = telemetry_record(
                scenario.mission_id,
                reading,
                self._dependencies.stamps.next_stamp(reading.drone_id),
                producer_source=run_event_source(reading.drone_id, scenario.mission_id),
            )
            try:
                await asyncio.to_thread(
                    self._require_router().publish,
                    topic,
                    canonical_bytes(document),
                    {},
                )
            except MessagingError, RoutingError:
                continue
            published += 1
        return published, None

    async def _drain_commands(
        self,
        drone_ids: tuple[str, ...],
        context: CommandContext,
        checkpoint: _RunCheckpoint,
    ) -> FleetControlRunStatus | None:
        """Process bounded commands, pausing before each receive while broker-unready."""
        session = self._require_session()
        for drone_id in drone_ids:
            receiver = session.receivers[drone_id]
            for _taken in range(self._dependencies.intake.commands_per_drone_per_tick):
                interrupted = await self._await_operable(
                    checkpoint.request,
                    checkpoint.cancelled,
                    checkpoint.ticks,
                    checkpoint.publications,
                )
                if interrupted is not None:
                    return interrupted
                try:
                    message = await asyncio.to_thread(receiver.receive, 0)
                except UnsettledMessageError as error:
                    await context.unit_of_work.refuse(
                        BrokerRefusalCandidate(
                            consumer="fleet-simulator",
                            source=error.metadata.source,
                            family=error.metadata.family,
                            channel=f"fleet-simulator-drone-command-{drone_id}",
                            refusal_code="native-trace-refused",
                            raw_digest=error.metadata.raw_digest,
                        )
                    )
                    error.settlement.reject()
                    continue
                if message is None:
                    break
                await self._process_command(receiver, message, context)
        return None

    async def _process_command(
        self,
        receiver: AcknowledgingReceiver,
        message: InboundMessage,
        context: CommandContext,
    ) -> None:
        """Execute runtime schema admission before durable command processing."""
        payload = inbound_payload(message) or b""
        topic = message.get_destination_name() or ""
        delivery = CommandDelivery(topic, payload, _AsyncSettlement(receiver, message))
        try:
            validate_notification(topic, payload, self._dependencies.schemas)
        except IngressError as error:
            await context.unit_of_work.refuse(
                refusal_candidate(
                    delivery,
                    f"runtime-{error.refusal.name.lower().replace('_', '-')}",
                )
            )
            await delivery.settlement.reject()
            return
        try:
            await handle_command(delivery, context)
        except ProcessingError:
            return

    async def shutdown(self) -> None:
        """Release broker before store, continuing through the first refused cleanup."""
        session = self._session
        monitor = self._monitor
        self._monitor = None
        self._session = None
        self._router = None
        failures = (
            await _cancel_monitor(monitor),
            await _close_broker(session),
            await self._close_store(),
        )
        for failure in failures:
            if failure is not None:
                raise failure

    async def _close_store(self) -> BaseException | None:
        """Close the SQLAlchemy resources once, returning the refusal after cleanup."""
        if self._store_closed:
            return None
        self._store_closed = True
        try:
            await self._dependencies.store.close()
        except BaseException as error:
            return error
        return None

    async def _recover(self) -> None:
        """Drain one connected epoch and refuse readiness with ambiguous evidence."""
        async with self._recovery_lock:
            await self._recover_locked()

    async def _recover_locked(self) -> None:
        """Perform one serialized bounded recovery cycle."""
        session = self._require_session()
        for drone_id in sorted(set(self._dependencies.configured_drone_ids)):
            if await self._dependencies.store.outbox.reconciliation(drone_id):
                session.readiness.recovery_required()
                return
        router = self._router
        if router is None:
            raise FleetRuntimeError(FleetRuntimeRefusal.ROUTER_UNAVAILABLE, None)
        await drain_recovery(
            tuple(sorted(set(self._dependencies.configured_drone_ids))),
            self._dependencies.store.outbox,
            _CriticalPublisher(router, self._dependencies.confirmed_at),
            _RecoveryReadiness(session.readiness),
        )

    async def _monitor_recovery(self) -> None:
        """Continuously reconcile restored broker epochs while the HTTP runtime is idle."""
        session = self._require_session()
        try:
            while self._session is session:
                await self._dependencies.recovery_pause()
                if self._session is not session:
                    return
                state = session.readiness.state
                if state is BrokerLifecycleState.EXHAUSTED:
                    self._exhausted.set()
                    return
                if state is BrokerLifecycleState.RECOVERY_PENDING or (
                    state is BrokerLifecycleState.CONNECTED and not session.readiness.is_ready()
                ):
                    await self._recover()
        except asyncio.CancelledError:
            raise
        except BaseException:
            session.readiness.exhausted()
            self._exhausted.set()

    def _require_session(self) -> FleetRuntimeSession:
        """Return the started broker session or fail before broker I/O."""
        if self._session is None:
            raise FleetRuntimeError(FleetRuntimeRefusal.SESSION_UNAVAILABLE, None)
        return self._session

    def _require_router(self) -> DeliveryRouter:
        """Return the typed router after successful startup."""
        if self._router is None:
            raise FleetRuntimeError(FleetRuntimeRefusal.ROUTER_UNAVAILABLE, None)
        return self._router


def _message_properties(headers: bytes) -> Mapping[str, object] | None:
    """Decode exact canonical outbox properties or refuse malformed stored bytes."""
    try:
        decoded = canonical.decode(headers)
    except TypeError, ValueError:
        return None
    return cast("Mapping[str, object]", decoded) if isinstance(decoded, dict) else None


async def _wait_for_recovery_pause(
    pause: Callable[[], Awaitable[None]],
    cancelled: asyncio.Event,
) -> None:
    """Wait for one bounded recovery inspection or cooperative run cancellation."""
    if cancelled.is_set():
        return
    pause_task: asyncio.Future[None] = asyncio.ensure_future(pause())
    cancellation_task: asyncio.Task[bool] = asyncio.create_task(cancelled.wait())
    tasks = (pause_task, cancellation_task)
    try:
        completed, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if cancellation_task not in completed:
            await pause_task
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _cancel_monitor(task: asyncio.Task[None] | None) -> BaseException | None:
    """Cancel the application monitor and return only an unexpected failure."""
    if task is None:
        return None
    task.cancel()
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
        return result
    return None


async def _close_broker(session: FleetRuntimeSession | None) -> BaseException | None:
    """Close every broker endpoint and return a refusal after it has continued cleanup."""
    if session is None:
        return None
    try:
        await asyncio.to_thread(session.close)
    except BaseException as error:
        return error
    return None


def _scenario(request: FleetControlStartRequest) -> FleetScenario:
    """Map the already validated private-control projection without coercion."""
    document = request.scenario
    absent: dict[str, set[int]] = {}
    for item in document.absent_heartbeats:
        absent.setdefault(item.drone_id, set()).add(item.tick_ordinal)
    thresholds = document.connectivity_thresholds
    return FleetScenario(
        mission_id=document.mission_id,
        drones=tuple(
            DroneStart(
                drone.drone_id,
                drone.sector_id,
                drone.latitude_microdegrees,
                drone.longitude_microdegrees,
                drone.altitude_metres,
                drone.heading_degrees,
                drone.ground_speed_centimetres_per_second,
                drone.battery_permille,
                drone.north_microdegrees_per_tick,
                drone.east_microdegrees_per_tick,
                drone.battery_drain_permille_per_tick,
            )
            for drone in document.drones
        ),
        tick_interval_milliseconds=document.tick_interval_milliseconds,
        thresholds=ConnectivityThresholds(
            thresholds.misses_to_degraded,
            thresholds.misses_to_offline,
            thresholds.heartbeats_to_recover,
        ),
        ticks_to_sweep=document.ticks_to_sweep,
        absent_heartbeats={key: frozenset(value) for key, value in absent.items()},
    )


def _effect_for(
    scenario: FleetScenario,
) -> Callable[[str, IncomingCommand], EffectResult]:
    """Return the deterministic simulated effect over this accepted run's sectors."""
    known_sectors = frozenset(sectors(scenario))

    def apply_effect(drone_id: str, command: IncomingCommand) -> EffectResult:
        succeeded = (
            command.sector_id in known_sectors if isinstance(command, AssignSectorCommand) else True
        )
        event = CommandEvent.SUCCEED if succeeded else CommandEvent.FAIL
        document: dict[str, object]
        if isinstance(command, AssignSectorCommand):
            document = {
                "commandId": command.command_id,
                "droneId": drone_id,
                "sectorId": command.sector_id,
                "state": "assigned" if succeeded else "refused",
            }
        else:
            document = {
                "approvalId": command.approval_id,
                "commandId": command.command_id,
                "droneId": drone_id,
                "evidenceDecisionDigest": command.evidence_decision_digest,
                "evidenceDecisionId": command.evidence_decision_id,
                "evidenceDecisionVersion": command.evidence_decision_version,
                "latitudeMicrodegrees": command.latitude_microdegrees,
                "longitudeMicrodegrees": command.longitude_microdegrees,
                "proposalDigest": command.proposal_digest,
                "proposalId": command.proposal_id,
                "proposalVersion": command.proposal_version,
                "state": "rescue-escalated",
            }
        payload = canonical_bytes(document)
        return EffectResult(event, command.sequence, payload)

    return apply_effect


async def _pace(pacer: Pacer, interval_milliseconds: int, started: int) -> None:
    """Wait only the remaining injected interval, without manufacturing catch-up."""
    remaining = interval_milliseconds - (pacer.now_milliseconds() - started)
    if remaining > 0:
        await asyncio.to_thread(pacer.wait, remaining)


def _run_status(
    request: FleetControlStartRequest,
    state: str,
    ticks: int,
    publications: int,
) -> FleetControlRunStatus:
    """Render exact coordinator-facing progress through the strict wire model."""
    return FleetControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "missionId": request.scenario.mission_id,
            "runId": request.run_id,
            "state": state,
            "completedTickCount": ticks,
            "telemetryPublicationCount": publications,
        }
    )
