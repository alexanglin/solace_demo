"""Live PubSub+ and PostgreSQL proof for the durable application data plane.

The deterministic Agent Response in this module is a model-boundary injection.  It does not
construct Agent Mesh, an application container, Ollama, or a provider client.  Every boundary
after that injection is the production contract, broker, SQLAlchemy, Alembic, inbox/outbox,
approval, evidence, fleet-command, and recorder implementation selected by ADR-0147.
"""

from __future__ import annotations

import asyncio
import errno
import os
import stat
import time
import unittest
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import ClassVar, Final, Never, cast, override
from uuid import uuid4

import pytest
from aerial_rescue_broker.ingress import PayloadSchemaExecutor, load_runtime_schema_registry
from aerial_rescue_broker.messaging import (
    BrokerLifecycle,
    BrokerLifecycleState,
    CommandGatewaySession,
    DashboardSession,
    FleetSession,
    GuaranteedMessage,
    GuaranteedProcessingSession,
    InboundMessage,
    MessageSettlement,
    SolaceDirectPublisher,
    open_command_gateway_session,
    open_dashboard_session,
    open_fleet_session,
    open_guaranteed_processing_session,
    open_receiver_only_session,
)
from aerial_rescue_broker.queues import drone_queue_name
from aerial_rescue_broker.routing import (
    DeliveryRouter,
    GuaranteedReplyResponder,
    PublicationPorts,
)
from aerial_rescue_command_gateway.authorization import AuthorizationClock
from aerial_rescue_command_gateway.operator_approval import ApprovalIngressOutcome
from aerial_rescue_command_gateway.progression import SendClock, record_send
from aerial_rescue_command_gateway.publication import (
    COMMAND_PUBLICATION_BATCH_SIZE,
    CommandPublication,
)
from aerial_rescue_command_gateway.service import (
    CountingStamps as GatewayStamps,
)
from aerial_rescue_command_gateway.service import (
    DirectDispatchOutcome,
    GuaranteedDispatchOutcome,
    dispatch_direct,
    gateway_bindings,
)
from aerial_rescue_command_gateway.service import (
    dispatch_guaranteed as dispatch_gateway_guaranteed,
)
from aerial_rescue_command_gateway.service import (
    recover_application as recover_gateway,
)
from aerial_rescue_command_gateway.store_adapter import (
    ApplicationStore,
    compose_application_store,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import source_event_digest
from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    decode_envelope,
    envelope_document,
)
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_contracts.integration import (
    AgentCandidate,
    AgentOutcome,
    AgentResponse,
    agent_response_document,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_dashboard_api.boundary.ingress import MutationIngressError, parse_mutation
from aerial_rescue_dashboard_api.boundary.mutation_boundary import AuthorizedMutation
from aerial_rescue_dashboard_api.console import dashboard_bindings
from aerial_rescue_dashboard_api.messaging.mutations import (
    DashboardMutationService,
    MutationStamp,
)
from aerial_rescue_dashboard_api.messaging.outbox import (
    DashboardOutboxPublisher,
)
from aerial_rescue_dashboard_api.messaging.outbox import (
    PublicationOutcome as DashboardPublicationOutcome,
)
from aerial_rescue_domain.approvals import ClockReading
from aerial_rescue_domain.commands import CommandEvent, SendBudget
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_domain.principals import Principal
from aerial_rescue_evidence_service.runtime import (
    BrokerOutboxPublisher as EvidenceOutboxPublisher,
)
from aerial_rescue_evidence_service.runtime import (
    CountingStamps as EvidenceStamps,
)
from aerial_rescue_evidence_service.runtime import (
    DispatchOutcome as EvidenceDispatchOutcome,
)
from aerial_rescue_evidence_service.runtime import (
    DispatchPorts as EvidenceDispatchPorts,
)
from aerial_rescue_evidence_service.runtime import (
    dispatch_guaranteed as dispatch_evidence_guaranteed,
)
from aerial_rescue_evidence_service.runtime import (
    evidence_bindings,
)
from aerial_rescue_evidence_service.runtime import (
    recover_application as recover_evidence,
)
from aerial_rescue_evidence_service.store_adapter import (
    StoreEvidenceUnitOfWork,
    StoreSourceUnitOfWork,
)
from aerial_rescue_fleet_simulator.critical_outbox import (
    PublicationOutcome as FleetPublicationOutcome,
)
from aerial_rescue_fleet_simulator.critical_outbox import (
    PublicationResult as FleetPublicationResult,
)
from aerial_rescue_fleet_simulator.critical_outbox import drain_recovery
from aerial_rescue_fleet_simulator.durable_processing import (
    CommandContext,
    CommandDelivery,
    CommandProcessingOutcome,
    EffectResult,
    handle_command,
)
from aerial_rescue_fleet_simulator.fleet import Reading
from aerial_rescue_fleet_simulator.intake import IncomingCommand
from aerial_rescue_fleet_simulator.results import ResultStamp
from aerial_rescue_fleet_simulator.store_adapter import (
    StoreCriticalOutbox,
    StoreFleetUnitOfWork,
)
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp, telemetry_record
from aerial_rescue_recorder.broker import RecorderBrokerReceiver
from aerial_rescue_recorder.capture import Recorder
from aerial_rescue_recorder.processing import (
    ProcessDecision,
    RecorderRuntime,
)
from aerial_rescue_recorder.service import recorder_bindings
from aerial_rescue_recorder.store import RecordingTransactionsAdapter
from aerial_rescue_store.application_outbox import (
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.bounds import (
    CHECKOUT_TIMEOUT_SECONDS,
    CONNECT_RETRIES,
    CONNECT_TIMEOUT_SECONDS,
    IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    LOCK_TIMEOUT_MILLISECONDS,
    POOL_OVERFLOW,
    POOL_SIZE,
    SHUTDOWN_GRACE_SECONDS,
    STATEMENT_TIMEOUT_MILLISECONDS,
    EngineBounds,
)
from aerial_rescue_store.command_progress import (
    CommandProgressSession,
)
from aerial_rescue_store.command_progress import (
    load as load_command_progress,
)
from aerial_rescue_store.database.schema import (
    APPLICATION_OUTBOX,
    APPROVAL,
    BROKER_INBOX,
    COMMAND_OUTBOX,
    COMMAND_PROGRESS,
    DRONE_COMMAND_EFFECT,
    DRONE_COMMAND_RECEIPT,
    EVIDENCE_DECISION,
    PROPOSAL,
)
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.migration import live_config
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.dashboard import (
    DashboardMutationTransactions,
    DashboardOutboxTransactions,
)
from aerial_rescue_store.processing.evidence import (
    EvidenceApplicationOutbox,
    EvidenceProcessingTransactions,
)
from aerial_rescue_store.processing.fleet import FleetTransactions
from aerial_rescue_store.processing.recording import RecordingTransactions
from aerial_rescue_store.processing.source_ingress import SourceProcessingTransactions
from aerial_rescue_store.session import StoreSessionFactory, create_session_factory, transaction
from aerial_rescue_store.settings import DatabaseSettings, database_settings
from alembic import command
from sqlalchemy import ColumnElement, Table, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.broker_live_support import DEPLOY_ROOT as DEPLOY
from tests.broker_live_support import LOCAL_BROKER_ENDPOINT as ENDPOINT
from tests.broker_live_support import REPOSITORY_ROOT, SHARED_PROBE_DRONES, role_credential
from tests.broker_live_support import connected_service as _service

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]

SCHEMAS: Final = REPOSITORY_ROOT / "schemas"
MAINTENANCE_DATABASE: Final = "postgres"
PROBE_DATABASE_PREFIX: Final = "aerial_rescue_app_probe_"
PROBE_DRONE: Final = SHARED_PROBE_DRONES[1]
AGENT_NAME: Final = "VisionAgent"
OPERATOR_ID: Final = "operator-live-probe"
APPROVAL_TTL_MILLISECONDS: Final = 60_000
RECEIVE_TIMEOUT_MILLISECONDS: Final = 10_000
IDLE_RECEIVE_MILLISECONDS: Final = 25
MAX_RECORDER_POLLS: Final = 500
RECOVERY_POLLS: Final = 600
GATEWAY_DRAIN_ATTEMPTS: Final = 3
RECOVERY_POLL_SECONDS: Final = 0.05
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203332-01"
RESTART_REQUEST_FIFO_SETTING: Final = "AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO"
RESTART_RESULT_FIFO_SETTING: Final = "AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO"
RESTART_AUTHORITY_SETTING: Final = "AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN"
RESTART_REQUEST_MARKER: Final = "AERIAL_RESCUE_BROKER_RESTART_ONCE_V1"
RESTART_SUCCEEDED_MARKER: Final = "AERIAL_RESCUE_BROKER_RESTART_SUCCEEDED_V1"
MAX_RESTART_MARKER_BYTES: Final = 128
CRITICAL_RESULT_COUNT: Final = 2

COMMAND_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/operator-command-request.schema.json"
)
DECISION_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-request.schema.json"
)

BOUNDS: Final = EngineBounds(
    pool_size=POOL_SIZE,
    pool_overflow=POOL_OVERFLOW,
    checkout_timeout_seconds=CHECKOUT_TIMEOUT_SECONDS,
    connect_timeout_seconds=CONNECT_TIMEOUT_SECONDS,
    connect_retries=CONNECT_RETRIES,
    statement_timeout_milliseconds=STATEMENT_TIMEOUT_MILLISECONDS,
    lock_timeout_milliseconds=LOCK_TIMEOUT_MILLISECONDS,
    idle_in_transaction_timeout_milliseconds=IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
    shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
)


@dataclass(frozen=True, slots=True)
class _RunIdentity:
    """Unique public identifiers for one repeat-safe live observation."""

    token: str
    mission_id: str
    source_event_id: str
    correlation_id: str
    runtime_id: str
    gateway_epoch: str


@dataclass(frozen=True, slots=True)
class _Authority:
    """Exact normalized proposal and evidence-decision authority."""

    label: str
    proposal_id: str
    proposal_digest: str
    decision_id: str
    decision_digest: str
    latitude_microdegrees: int
    longitude_microdegrees: int


@dataclass(frozen=True, slots=True)
class _DatabaseCounts:
    """Durable rows that make duplicate/no-op claims independently observable."""

    proposals: int
    decisions: int
    approvals: int
    consumed_approvals: int
    commands: int
    effects: int
    receipts: int
    gateway_inbox: int
    evidence_inbox: int
    recorder_inbox: int
    pending_application: int
    pending_commands: int
    progress_state: str


@dataclass(frozen=True, slots=True)
class _LiveReport:
    """Redacted outcomes retained after the unique probe database is dropped."""

    source_outcome: EvidenceDispatchOutcome
    telemetry_observed: bool
    normalized: int
    proposal_outcomes: tuple[EvidenceDispatchOutcome, ...]
    decision_scores: tuple[int, ...]
    invalid_approval_refused: bool
    negative_reasons: tuple[str, ...]
    approval_dispatches: tuple[GuaranteedDispatchOutcome, ...]
    command_dispatches: tuple[GuaranteedDispatchOutcome, ...]
    readiness_degraded: bool
    readiness_recovered: bool
    first_effect: CommandProcessingOutcome
    duplicate_effect: CommandProcessingOutcome
    result_outcomes: tuple[str, ...]
    critical_event_ids: frozenset[str]
    recorded_event_ids: frozenset[str]
    recorder_recorded: int
    recorder_duplicates: int
    counts: _DatabaseCounts


@dataclass(slots=True)
class _LiveGraph:
    """Concrete, closeable application capabilities for one isolated live run."""

    identity: _RunIdentity
    engine: AsyncEngine
    sessions: StoreSessionFactory
    schemas: PayloadSchemaExecutor
    gateway: CommandGatewaySession
    evidence: GuaranteedProcessingSession
    dashboard: DashboardSession
    fleet: FleetSession
    recorder: RecorderRuntime
    gateway_router: DeliveryRouter
    dashboard_router: DeliveryRouter
    fleet_router: DeliveryRouter
    gateway_store: ApplicationStore
    gateway_stamps: GatewayStamps
    evidence_ports: EvidenceDispatchPorts
    evidence_outbox: EvidenceApplicationOutbox
    evidence_publisher: EvidenceOutboxPublisher
    dashboard_transactions: DashboardMutationTransactions
    dashboard_outbox: DashboardOutboxTransactions
    dashboard_publisher: DashboardOutboxPublisher
    fleet_work: StoreFleetUnitOfWork
    fleet_outbox: StoreCriticalOutbox
    fleet_publisher: _FleetOutboxPublisher
    resources: ExitStack = field(repr=False)

    @property
    def lifecycles(self) -> tuple[BrokerLifecycle, ...]:
        """Return every application lifecycle whose reconnect is under test."""
        return (
            self.gateway.readiness,
            self.evidence.readiness,
            self.dashboard.readiness,
            self.fleet.readiness,
        )

    async def close(self) -> None:
        """Release broker capabilities before the database engine."""
        self.resources.close()
        await self.engine.dispose()


@dataclass(frozen=True, slots=True)
class _ApprovalExercise:
    """Negative approval observations plus the one authorized command publication."""

    invalid_refused: bool
    reasons: tuple[str, ...]
    approval_dispatches: tuple[GuaranteedDispatchOutcome, ...]
    command_dispatches: tuple[GuaranteedDispatchOutcome, ...]
    command: CommandPublication


@dataclass(frozen=True, slots=True)
class _FleetExercise:
    """Restart, effect, result, and duplicate observations for one command."""

    degraded: bool
    recovered: bool
    first: CommandProcessingOutcome
    duplicate: CommandProcessingOutcome
    result_outcomes: tuple[str, ...]
    critical_event_ids: frozenset[str]


def _fail(message: str) -> Never:
    """Raise one redacted live-probe failure without retaining broker bytes."""
    raise RuntimeError(message)


def _identity() -> _RunIdentity:
    """Mint identifiers only when the live class actually executes."""
    token = uuid4().hex[:16]
    return _RunIdentity(
        token=token,
        mission_id=f"mission-live-{token}",
        source_event_id=f"source-live-{token}",
        correlation_id=f"correlation-live-{token}",
        runtime_id=f"dashboard-live-{token}",
        gateway_epoch=f"gateway-live-{token}",
    )


def _now() -> datetime:
    """Return the live clock as one aware UTC reading."""
    return datetime.now(tz=UTC)


def _instant(clock: datetime | None = None) -> str:
    """Render a trusted live instant in the canonical millisecond profile."""
    return format_instant(clock or _now())


def _identifier() -> str:
    """Return one lowercase identifier also suitable as a trace identifier."""
    return uuid4().hex


class _DashboardStamps:
    """Issue dashboard stamps under a controllable wall instant and one sequence."""

    def __init__(self, occurred_at: str) -> None:
        self.occurred_at = occurred_at
        self.sequence = 10_000

    def __call__(self) -> MutationStamp:
        """Mint one mutation stamp without accepting any request-carried identity."""
        self.sequence += 1
        return MutationStamp(
            event_id=_identifier(),
            entity_id=_identifier(),
            occurred_at=self.occurred_at,
            monotonic_milliseconds=self.sequence,
            sequence=self.sequence,
            traceparent=TRACEPARENT,
        )


class _FleetStamps:
    """Mint exact result identities for the production fleet command boundary."""

    def __init__(self) -> None:
        self.sequence = 20_000
        self.started_at = _now()

    def next_result_stamp(
        self,
        drone_id: str,
        correlation_id: str,
        causation_id: str,
    ) -> ResultStamp:
        """Return the next drone-local command-result envelope stamp."""
        del drone_id
        self.sequence += 1
        return ResultStamp(
            event_id=_identifier(),
            occurred_at=_instant(self.started_at + timedelta(milliseconds=self.sequence - 20_000)),
            sequence=self.sequence,
            correlation_id=correlation_id,
            causation_id=causation_id,
            traceparent=TRACEPARENT,
        )

    def processed_at(self) -> str:
        """Return the durable receipt instant."""
        return _instant()


class _FleetSettlement:
    """Adapt one actual message-bound Solace settlement to the async fleet port."""

    def __init__(self, settlement: MessageSettlement) -> None:
        self._settlement = settlement

    async def accept(self) -> None:
        """Accept only when the production handler asks after commit."""
        self._settlement.accept()

    async def fail(self) -> None:
        """Return transient work for broker redelivery."""
        self._settlement.fail()

    async def reject(self) -> None:
        """Apply the queue's isolated dead-message policy."""
        self._settlement.reject()


class _FleetRecoveryReadiness:
    """Expose the public broker lifecycle to the production fleet recovery worker."""

    def __init__(self, lifecycle: BrokerLifecycle) -> None:
        self._lifecycle = lifecycle

    def is_connected(self) -> bool:
        """Permit recovery only in a connected SDK epoch."""
        return self._lifecycle.state in {
            BrokerLifecycleState.CONNECTED,
            BrokerLifecycleState.RECOVERY_PENDING,
        }

    def recovery_required(self) -> None:
        """Remove readiness before durable inspection."""
        self._lifecycle.recovery_required()

    def mark_ready(self) -> None:
        """Restore readiness only after the worker sees an empty boundary."""
        self._lifecycle.mark_ready()


class _FleetOutboxPublisher:
    """Map a confirmed typed-router send into the fleet outbox worker's result."""

    def __init__(self, router: DeliveryRouter) -> None:
        self._router = router

    async def publish(self, event: StagedApplicationEvent) -> FleetPublicationResult:
        """Publish exact staged bytes through the production least-privilege router."""
        properties = canonical.decode(event.headers)
        if not isinstance(properties, Mapping):
            return FleetPublicationResult(FleetPublicationOutcome.REFUSED, None)
        self._router.publish(event.topic, event.payload, cast("Mapping[str, object]", properties))
        return FleetPublicationResult(FleetPublicationOutcome.CONFIRMED, _instant())


def _effect(drone_id: str, command: IncomingCommand) -> EffectResult:
    """Return the one deterministic simulation-only rescue effect."""
    del drone_id
    return EffectResult(
        event=CommandEvent.SUCCEED,
        applied_sequence=command.sequence,
        effect_payload=canonical.canonical_bytes({"rescueEscalated": True}),
    )


def _probe_target(identity: _RunIdentity) -> DatabaseSettings:
    """Derive a unique database while refusing the configured persistent target."""
    configured = database_settings(os.environ, DEPLOY)
    database = f"{PROBE_DATABASE_PREFIX}{identity.token}"
    if database == configured.database:
        _fail("application probe database equals the configured database")
    return replace(configured, database=database)


async def _database_action(target: DatabaseSettings, action: str) -> None:
    """Create or drop only the dialect-quoted unique probe database."""
    if action not in {"CREATE", "DROP"}:
        _fail("unsupported probe database action")
    engine = create_engine(replace(target, database=MAINTENANCE_DATABASE), BOUNDS)
    try:
        quoted = engine.dialect.identifier_preparer.quote(target.database)
        autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
        async with autocommit.connect() as connection:
            await connection.execute(text(f"{action} DATABASE {quoted}"))
    finally:
        await engine.dispose()


async def _migrate(engine: AsyncEngine) -> None:
    """Apply the complete package-owned Alembic history through the probe connection."""
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: command.upgrade(live_config(sync), "head"))


def _router_for_gateway(session: CommandGatewaySession) -> DeliveryRouter:
    """Construct the gateway's exact Direct, Guaranteed, and response capabilities."""
    direct = session.direct_publisher
    guaranteed = session.publisher
    return DeliveryRouter(
        Principal.COMMAND_GATEWAY,
        PublicationPorts(
            direct=direct,
            guaranteed=guaranteed,
            responder=GuaranteedReplyResponder(guaranteed),
        ),
    )


def _source_event(identity: _RunIdentity) -> tuple[str, bytes, str]:
    """Build one deterministic fleet-principal salient-event injection."""
    topic = Topic(
        Family.DRONE_EVENT,
        identity.mission_id,
        {"droneId": PROBE_DRONE, "eventType": "salient"},
    )
    declared = event_type(topic)
    envelope = Envelope(
        id=identity.source_event_id,
        source=f"urn:aerial-rescue:drone:{PROBE_DRONE}",
        type=declared,
        subject=identity.mission_id,
        time=_instant(),
        dataschema=binding_for(declared).dataschema,
        sequence="000000000000101",
        correlation_id=identity.correlation_id,
        traceparent=TRACEPARENT,
        data={
            "missionId": identity.mission_id,
            "droneId": PROBE_DRONE,
            "observation": "thermal-contact",
            "latitudeMicrodegrees": 47_123_901,
            "longitudeMicrodegrees": -122_653_114,
            "detail": "Synthetic live probe observation.",
        },
    )
    payload = canonical.canonical_bytes(envelope_document(envelope))
    return format_topic(topic), payload, source_event_digest(envelope)


def _agent_response(
    identity: _RunIdentity,
    label: str,
    source_digest: str,
) -> tuple[str, bytes, Mapping[str, object]]:
    """Return a bounded deterministic model-boundary candidate and trusted context."""
    invocation_id = f"invocation-{label}-{identity.token}"
    correlation_id = f"correlation-{label}-{identity.token}"
    response = AgentResponse(
        mission_id=identity.mission_id,
        agent_name=AGENT_NAME,
        invocation_id=invocation_id,
        correlation_id=correlation_id,
        outcome=AgentOutcome.CANDIDATE,
        candidate=AgentCandidate(
            proposal_type="candidate-location",
            source_event_id=identity.source_event_id,
            source_event_digest=source_digest,
            drone_id=PROBE_DRONE,
            latitude_microdegrees=47_123_901,
            longitude_microdegrees=-122_653_114,
            command_type="escalate-rescue",
        ),
    )
    topic = format_topic(
        Topic(Family.AGENT_RESPONSE, identity.mission_id, {"agentName": AGENT_NAME})
    )
    properties: Mapping[str, object] = {
        "aerial-rescue-agent-response-invocation-id": invocation_id,
        "aerial-rescue-agent-response-correlation-id": correlation_id,
        "aerial-rescue-agent-response-mission-id": identity.mission_id,
        "aerial-rescue-agent-response-source-event-id": identity.source_event_id,
        "aerial-rescue-agent-response-source-event-digest": source_digest,
        "aerial-rescue-agent-response-agent-name": AGENT_NAME,
    }
    return topic, canonical.canonical_bytes(agent_response_document(response)), properties


def _object(value: object) -> dict[str, object]:
    """Require one decoded canonical object without coercing its members."""
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        _fail("live application probe expected a canonical object")
    return {cast("str", key): member for key, member in value.items()}


def _event_data(payload: bytes) -> dict[str, object]:
    """Return one validated event's object-valued data member."""
    return _object(decode_envelope(payload).data)


def _required_text(document: Mapping[str, object], name: str) -> str:
    """Return one required string from trusted, schema-validated probe output."""
    value = document.get(name)
    if not isinstance(value, str):
        _fail(f"live application probe output omitted {name}")
    return value


def _required_integer(document: Mapping[str, object], name: str) -> int:
    """Return one required non-Boolean integer from validated probe output."""
    value = document.get(name)
    if type(value) is not int:
        _fail(f"live application probe output omitted {name}")
    return value


def _rows_for_family(
    rows: Sequence[StagedApplicationEvent],
    family: Family,
) -> tuple[StagedApplicationEvent, ...]:
    """Select staged rows through their closed topic parser."""
    expected = family
    return tuple(row for row in rows if row.family == expected.outbox_family)


def _require_guaranteed(
    session: CommandGatewaySession | GuaranteedProcessingSession | DashboardSession,
    channel: str,
) -> GuaranteedMessage:
    """Receive one required queue-bound delivery inside the live timeout."""
    received = session.receive_guaranteed(channel, RECEIVE_TIMEOUT_MILLISECONDS)
    if received is None:
        _fail(f"live application probe received no {channel} delivery")
    return received


def _wait_for_direct(session: CommandGatewaySession) -> InboundMessage:
    """Receive one required Direct integration body without exposing it in errors."""
    received = session.receive_direct(RECEIVE_TIMEOUT_MILLISECONDS)
    if received is None:
        _fail("live application probe received no Agent Response")
    return received


def _wait_ready(lifecycles: Sequence[BrokerLifecycle]) -> None:
    """Wait a finite interval for all registered SDK flows to report active."""
    deadline = time.monotonic() + RECEIVE_TIMEOUT_MILLISECONDS / 1_000
    while time.monotonic() < deadline:
        if all(lifecycle.is_ready() for lifecycle in lifecycles):
            return
        time.sleep(RECOVERY_POLL_SECONDS)
    _fail("live application probe readiness did not become healthy")


def _private_fifo(setting: str) -> Path:
    """Resolve one runner-owned FIFO while refusing aliases or public authority."""
    raw = os.environ.get(setting, "").strip()
    if not raw:
        _fail(f"live broker restart authority omitted {setting}")
    path = Path(raw)
    if not path.is_absolute():
        _fail(f"live broker restart authority made {setting} relative")
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISFIFO(metadata.st_mode):
        _fail(f"live broker restart authority made {setting} unsafe")
    if metadata.st_mode & 0o077:
        _fail(f"live broker restart authority made {setting} public")
    parent = os.lstat(path.parent)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077:
        _fail(f"live broker restart authority made {setting} parent public")
    return path


def _open_request_fifo(path: Path, deadline: float) -> int:
    """Open the runner's already-authorized request reader without blocking forever."""
    while time.monotonic() < deadline:
        try:
            return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno != errno.ENXIO:
                raise
            time.sleep(RECOVERY_POLL_SECONDS)
    _fail("live broker restart request FIFO had no bounded reader")


def _degraded_indices(lifecycles: Sequence[BrokerLifecycle]) -> set[int]:
    """Return the sessions whose application readiness is currently absent."""
    return {index for index, lifecycle in enumerate(lifecycles) if not lifecycle.is_ready()}


def _send_restart_request(path: Path, deadline: float) -> None:
    """Write exactly one fixed marker to the runner-owned capability."""
    request_fd = _open_request_fifo(path, deadline)
    try:
        request = (RESTART_REQUEST_MARKER + "\n").encode("ascii")
        if os.write(request_fd, request) != len(request):
            _fail("live broker restart request was not one atomic token")
    finally:
        os.close(request_fd)


async def _receive_restart_result(
    result_fd: int,
    lifecycles: Sequence[BrokerLifecycle],
    deadline: float,
) -> set[int]:
    """Read one bounded result marker while observing degraded readiness."""
    degraded: set[int] = set()
    response = bytearray()
    while time.monotonic() < deadline:
        degraded.update(_degraded_indices(lifecycles))
        chunk = os.read(result_fd, MAX_RESTART_MARKER_BYTES - len(response))
        if chunk:
            response.extend(chunk)
            if b"\n" in response:
                break
        if len(response) >= MAX_RESTART_MARKER_BYTES:
            break
        await asyncio.sleep(RECOVERY_POLL_SECONDS)
    marker = bytes(response).strip().decode("ascii", errors="strict")
    if marker != RESTART_SUCCEEDED_MARKER:
        _fail("live broker restart runner did not report success")
    return degraded


async def _observe_reconnection(
    lifecycles: Sequence[BrokerLifecycle],
    degraded: set[int],
    deadline: float,
) -> bool:
    """Wait for every SDK session to reconnect without restoring app readiness."""
    while time.monotonic() < deadline:
        degraded.update(_degraded_indices(lifecycles))
        if all(
            lifecycle.state
            in {BrokerLifecycleState.CONNECTED, BrokerLifecycleState.RECOVERY_PENDING}
            for lifecycle in lifecycles
        ):
            return True
        await asyncio.sleep(RECOVERY_POLL_SECONDS)
    return False


async def _restart_broker_once(
    lifecycles: Sequence[BrokerLifecycle],
) -> tuple[bool, bool]:
    """Ask the owning runner for exactly one restart and observe SDK lifecycle truth."""
    if os.environ.get(RESTART_AUTHORITY_SETTING, "") != RESTART_REQUEST_MARKER:
        _fail("live broker restart authority token is absent or invalid")
    if not all(lifecycle.is_ready() for lifecycle in lifecycles):
        _fail("live broker restart began before production sessions were ready")
    deadline = time.monotonic() + RECOVERY_POLLS * RECOVERY_POLL_SECONDS
    result_fd = os.open(
        _private_fifo(RESTART_RESULT_FIFO_SETTING),
        os.O_RDONLY | os.O_NONBLOCK,
    )
    try:
        _send_restart_request(_private_fifo(RESTART_REQUEST_FIFO_SETTING), deadline)
        degraded = await _receive_restart_result(result_fd, lifecycles, deadline)
    finally:
        os.close(result_fd)
    reconnected = await _observe_reconnection(lifecycles, degraded, deadline)
    return len(degraded) == len(lifecycles), reconnected


def _router_for_evidence(session: GuaranteedProcessingSession) -> DeliveryRouter:
    """Construct the Evidence Service's Guaranteed-only publication surface."""
    return DeliveryRouter(
        Principal.EVIDENCE_SERVICE,
        PublicationPorts(guaranteed=session.publisher),
    )


def _router_for_dashboard(session: DashboardSession) -> DeliveryRouter:
    """Construct the dashboard's Guaranteed-only publication surface."""
    return DeliveryRouter(
        Principal.DASHBOARD_API,
        PublicationPorts(guaranteed=session.publisher),
    )


def _router_for_fleet(session: FleetSession) -> DeliveryRouter:
    """Construct separate Direct telemetry and Guaranteed critical capabilities."""
    return DeliveryRouter(
        Principal.FLEET_SIMULATOR,
        PublicationPorts(direct=session.telemetry, guaranteed=session.results),
    )


async def _open_live_graph(
    identity: _RunIdentity,
    engine: AsyncEngine,
    sessions: StoreSessionFactory,
    schemas: PayloadSchemaExecutor,
) -> _LiveGraph:
    """Open the production broker/store compositions with reverse-order ownership."""
    resources = ExitStack()
    try:
        gateway = open_command_gateway_session(
            ENDPOINT,
            Principal.COMMAND_GATEWAY,
            role_credential(Principal.COMMAND_GATEWAY),
            gateway_bindings(),
        )
        resources.callback(gateway.close)
        evidence = open_guaranteed_processing_session(
            ENDPOINT,
            Principal.EVIDENCE_SERVICE,
            role_credential(Principal.EVIDENCE_SERVICE),
            evidence_bindings(),
        )
        resources.callback(evidence.close)
        dashboard = open_dashboard_session(
            ENDPOINT,
            Principal.DASHBOARD_API,
            role_credential(Principal.DASHBOARD_API),
            dashboard_bindings(),
        )
        resources.callback(dashboard.close)
        fleet = open_fleet_session(
            ENDPOINT,
            Principal.FLEET_SIMULATOR,
            role_credential(Principal.FLEET_SIMULATOR),
            {PROBE_DRONE: drone_queue_name(PROBE_DRONE)},
        )
        resources.callback(fleet.close)
        recorder_session = open_receiver_only_session(
            ENDPOINT,
            Principal.RECORDER,
            role_credential(Principal.RECORDER),
            recorder_bindings(),
        )
        recorder = RecorderRuntime(
            RecorderBrokerReceiver(
                recorder_session,
                schemas,
                _instant,
                IDLE_RECEIVE_MILLISECONDS,
            ),
            Recorder(
                "recorder",
                RecordingTransactionsAdapter(RecordingTransactions(sessions)),
            ),
            BrokerRefusalRecorder(sessions, _instant),
        )
        resources.callback(recorder.close)
    except BaseException:
        resources.close()
        raise

    refusals = BrokerRefusalRecorder(sessions, _instant)
    evidence_outbox = EvidenceApplicationOutbox(sessions)
    dashboard_outbox = DashboardOutboxTransactions(sessions)
    fleet_outbox = StoreCriticalOutbox(sessions)
    gateway_router = _router_for_gateway(gateway)
    evidence_router = _router_for_evidence(evidence)
    dashboard_router = _router_for_dashboard(dashboard)
    fleet_router = _router_for_fleet(fleet)
    evidence_stamps = EvidenceStamps(_now, _identifier)
    return _LiveGraph(
        identity=identity,
        engine=engine,
        sessions=sessions,
        schemas=schemas,
        gateway=gateway,
        evidence=evidence,
        dashboard=dashboard,
        fleet=fleet,
        recorder=recorder,
        gateway_router=gateway_router,
        dashboard_router=dashboard_router,
        fleet_router=fleet_router,
        gateway_store=compose_application_store(sessions, _instant),
        gateway_stamps=GatewayStamps(_now, _identifier),
        evidence_ports=EvidenceDispatchPorts(
            schemas,
            evidence_stamps.next_stamp,
            StoreEvidenceUnitOfWork(EvidenceProcessingTransactions(sessions), refusals),
            StoreSourceUnitOfWork(SourceProcessingTransactions(sessions), refusals),
        ),
        evidence_outbox=evidence_outbox,
        evidence_publisher=EvidenceOutboxPublisher(evidence_router, _instant),
        dashboard_transactions=DashboardMutationTransactions(sessions),
        dashboard_outbox=dashboard_outbox,
        dashboard_publisher=DashboardOutboxPublisher(dashboard_router, _instant),
        fleet_work=StoreFleetUnitOfWork(FleetTransactions(sessions), _effect, refusals),
        fleet_outbox=fleet_outbox,
        fleet_publisher=_FleetOutboxPublisher(fleet_router),
        resources=resources,
    )


async def _recover_live_graph(graph: _LiveGraph) -> None:
    """Drain every committed producer outbox before declaring the graph ready."""
    gateway_ready = await recover_gateway(
        graph.gateway,
        graph.gateway_store,
        graph.gateway_router,
        _instant,
    )
    evidence_ready = await recover_evidence(
        graph.evidence,
        graph.evidence_outbox,
        graph.evidence_publisher,
    )
    fleet_report = await drain_recovery(
        (PROBE_DRONE,),
        graph.fleet_outbox,
        graph.fleet_publisher,
        _FleetRecoveryReadiness(graph.fleet.readiness),
    )
    graph.dashboard.rebind_complete()
    if not gateway_ready or not evidence_ready or not fleet_report.ready:
        _fail("live application graph did not complete durable recovery")
    _wait_ready(graph.lifecycles)


async def _publish_agent_responses(
    graph: _LiveGraph,
    source_digest: str,
    labels: Sequence[str],
) -> tuple[DirectDispatchOutcome, ...]:
    """Inject deterministic structured bodies through the Direct integration boundary."""
    service = _service(Principal.EVENT_MESH_GATEWAY)
    publisher = SolaceDirectPublisher(service)
    outcomes: list[DirectDispatchOutcome] = []
    try:
        for label in labels:
            topic, payload, properties = _agent_response(graph.identity, label, source_digest)
            publisher.publish_unacknowledged(topic, payload, properties)
            outcomes.append(
                await dispatch_direct(
                    _wait_for_direct(graph.gateway),
                    graph.gateway_router,
                    graph.gateway_stamps,
                    graph.gateway_store,
                )
            )
    finally:
        publisher.close()
        service.disconnect()
    return tuple(outcomes)


def _observe_telemetry(graph: _LiveGraph) -> bool:
    """Publish one Direct reading and observe it on the dashboard principal."""
    topic, document = telemetry_record(
        graph.identity.mission_id,
        Reading(
            drone_id=PROBE_DRONE,
            latitude_microdegrees=47_123_900,
            longitude_microdegrees=-122_653_100,
            altitude_metres=90,
            heading_degrees=180,
            ground_speed_centimetres_per_second=750,
            battery_percent=87,
        ),
        TelemetryStamp(
            event_id=_identifier(),
            occurred_at=_instant(),
            sequence=100,
            correlation_id=graph.identity.correlation_id,
            traceparent=TRACEPARENT,
        ),
    )
    graph.fleet_router.publish(topic, canonical.canonical_bytes(document), {})
    received = graph.dashboard.receive_direct(RECEIVE_TIMEOUT_MILLISECONDS)
    if received is None:
        return False
    payload = received.get_payload_as_bytes()
    destination = received.get_destination_name()
    if not isinstance(payload, bytes) or destination != topic:
        return False
    envelope = decode_envelope(payload)
    return (
        envelope.subject == graph.identity.mission_id
        and envelope.data.get("droneId") == PROBE_DRONE
    )


def _proposal_for_label(
    rows: Sequence[StagedApplicationEvent],
    identity: _RunIdentity,
    label: str,
) -> StagedApplicationEvent:
    """Select the proposal bound to one trusted structured-response invocation."""
    invocation = f"invocation-{label}-{identity.token}"
    matching = tuple(
        row
        for row in _rows_for_family(rows, Family.AGENT_PROPOSAL)
        if _event_data(row.payload).get("sourceInvocationId") == invocation
    )
    if len(matching) != 1:
        _fail(f"live application probe expected one normalized {label} proposal")
    return matching[0]


async def _drain_gateway(graph: _LiveGraph) -> bool:
    """Drain the gateway's bounded batches until one attempt finds nothing left.

    ``recover_application`` publishes one bounded batch per attempt and reports ready only
    when an attempt visits nothing, which is how the gateway's own scheduler drives it.
    """
    for _attempt in range(GATEWAY_DRAIN_ATTEMPTS):
        if await recover_gateway(
            graph.gateway,
            graph.gateway_store,
            graph.gateway_router,
            _instant,
        ):
            return True
    return False


async def _establish_authorities(
    graph: _LiveGraph,
) -> tuple[
    EvidenceDispatchOutcome,
    tuple[DirectDispatchOutcome, ...],
    tuple[EvidenceDispatchOutcome, ...],
    tuple[_Authority, ...],
    tuple[int, ...],
]:
    """Persist source evidence, normalize proposals, and publish scored decisions."""
    source_topic, source_payload, source_digest = _source_event(graph.identity)
    graph.fleet_router.publish(source_topic, source_payload, {})
    source_outcome = await dispatch_evidence_guaranteed(
        Family.DRONE_EVENT.literal_suffix,
        _require_guaranteed(graph.evidence, Family.DRONE_EVENT.literal_suffix),
        graph.evidence_ports,
    )
    labels = ("expired", "mismatch", "exact")
    normalized = await _publish_agent_responses(graph, source_digest, labels)
    gateway_rows = await graph.gateway_store.application_outbox.pending("command-gateway")
    proposals = tuple(_proposal_for_label(gateway_rows, graph.identity, label) for label in labels)
    if not await _drain_gateway(graph):
        _fail("live gateway did not publish normalized proposals")

    proposal_outcomes: list[EvidenceDispatchOutcome] = []
    for _proposal in proposals:
        proposal_outcomes.append(
            await dispatch_evidence_guaranteed(
                Family.AGENT_PROPOSAL.literal_suffix,
                _require_guaranteed(graph.evidence, Family.AGENT_PROPOSAL.literal_suffix),
                graph.evidence_ports,
            )
        )
    evidence_rows = await graph.evidence_outbox.pending("evidence-service")
    decisions = _rows_for_family(evidence_rows, Family.EVIDENCE_DECISION)
    authorities: list[_Authority] = []
    scores: list[int] = []
    for label, proposal in zip(labels, proposals, strict=True):
        proposal_id = _required_text(_event_data(proposal.payload), "proposalId")
        matching = tuple(
            row for row in decisions if _event_data(row.payload).get("proposalId") == proposal_id
        )
        if len(matching) != 1:
            _fail(f"live evidence service omitted the {label} decision")
        authority = _authority(label, proposal, matching[0])
        decision_data = _event_data(matching[0].payload)
        authorities.append(authority)
        scores.append(_required_integer(decision_data, "score"))
        if decision_data.get("band") != "corroborated" or decision_data.get("outcome") != (
            "contributing"
        ):
            _fail("live evidence decision was not corroborated and contributing")
    if not await recover_evidence(
        graph.evidence,
        graph.evidence_outbox,
        graph.evidence_publisher,
    ):
        _fail("live evidence service did not publish its decisions")
    return (
        source_outcome,
        normalized,
        tuple(proposal_outcomes),
        tuple(authorities),
        tuple(scores),
    )


def _authorized_mutation(
    schema_id: str,
    document: Mapping[str, object],
    path_bindings: Mapping[str, str],
) -> AuthorizedMutation:
    """Cross the exact canonical dashboard ingress and authenticated operator boundary."""
    ingress = parse_mutation(
        schema_id=schema_id,
        body=canonical.canonical_bytes(document),
        content_type="application/json",
        idempotency_key=str(uuid4()),
        path_bindings=path_bindings,
    )
    return AuthorizedMutation(ingress, OPERATOR_ID)


def _decision_document(authority: _Authority) -> dict[str, object]:
    """Build one exact public approval request from durable proposal authority."""
    return {
        "missionId": "",
        "proposalId": authority.proposal_id,
        "proposalDigest": authority.proposal_digest,
        "proposalVersion": 1,
        "evidenceDecisionId": authority.decision_id,
        "evidenceDecisionDigest": authority.decision_digest,
        "evidenceDecisionVersion": 1,
        "decision": "approve",
        "action": {
            "commandType": "escalate-rescue",
            "droneId": PROBE_DRONE,
            "latitudeMicrodegrees": authority.latitude_microdegrees,
            "longitudeMicrodegrees": authority.longitude_microdegrees,
        },
    }


def _command_document(mission_id: str, authority: _Authority) -> dict[str, object]:
    """Build one exact public escalation command from the selected authority."""
    return {
        "missionId": mission_id,
        "action": {
            "commandType": "escalate-rescue",
            "droneId": PROBE_DRONE,
            "proposalId": authority.proposal_id,
            "proposalDigest": authority.proposal_digest,
            "proposalVersion": 1,
            "evidenceDecisionId": authority.decision_id,
            "evidenceDecisionDigest": authority.decision_digest,
            "evidenceDecisionVersion": 1,
            "latitudeMicrodegrees": authority.latitude_microdegrees,
            "longitudeMicrodegrees": authority.longitude_microdegrees,
        },
    }


def _authority(
    label: str,
    proposal: StagedApplicationEvent,
    decision: StagedApplicationEvent,
) -> _Authority:
    """Bind one normalized proposal to its evidence service decision."""
    proposal_data = _event_data(proposal.payload)
    decision_data = _event_data(decision.payload)
    proposal_id = _required_text(proposal_data, "proposalId")
    if _required_text(decision_data, "proposalId") != proposal_id:
        _fail("live evidence decision did not bind its proposal")
    return _Authority(
        label=label,
        proposal_id=proposal_id,
        proposal_digest=_required_text(proposal_data, "proposalDigest"),
        decision_id=_required_text(decision_data, "evidenceDecisionId"),
        decision_digest=_required_text(decision_data, "evidenceDecisionDigest"),
        latitude_microdegrees=_required_integer(proposal_data, "latitudeMicrodegrees"),
        longitude_microdegrees=_required_integer(proposal_data, "longitudeMicrodegrees"),
    )


async def _publish_dashboard_row(
    store: DashboardOutboxTransactions,
    publisher: DashboardOutboxPublisher,
    row: StagedApplicationEvent,
) -> None:
    """Publish then compare-and-set one exact dashboard row after confirmation."""
    result = await publisher.publish(row)
    if result.outcome is not DashboardPublicationOutcome.CONFIRMED:
        _fail("live dashboard outbox publication was not confirmed")
    await store.record(
        ApplicationEventIdentity(row.producer, row.event_id),
        OutboxEvent.CONFIRM,
        result.confirmed_at,
    )


def _dashboard_service(
    graph: _LiveGraph,
    stamps: _DashboardStamps,
) -> DashboardMutationService:
    """Compose the authenticated dashboard mutation application over PostgreSQL."""
    return DashboardMutationService(
        transactions=graph.dashboard_transactions,
        runtime_id=graph.identity.runtime_id,
        stamps=stamps,
        schemas=graph.schemas,
        approval_time_to_live_milliseconds=APPROVAL_TTL_MILLISECONDS,
    )


def _decision_mutation(identity: _RunIdentity, authority: _Authority) -> AuthorizedMutation:
    """Bind one approval request to the selected mission and proposal route."""
    document = _decision_document(authority)
    document["missionId"] = identity.mission_id
    return _authorized_mutation(
        DECISION_SCHEMA,
        document,
        {"mission_id": identity.mission_id, "proposal_id": authority.proposal_id},
    )


def _command_mutation(identity: _RunIdentity, authority: _Authority) -> AuthorizedMutation:
    """Bind one operator command to its authenticated mission route."""
    return _authorized_mutation(
        COMMAND_SCHEMA,
        _command_document(identity.mission_id, authority),
        {"mission_id": identity.mission_id},
    )


async def _stage_decision(
    graph: _LiveGraph,
    service: DashboardMutationService,
    authority: _Authority,
) -> StagedApplicationEvent:
    """Commit one dashboard approval and return its still-staged broker row."""
    await service.decide(_decision_mutation(graph.identity, authority))
    return _only_row(
        await graph.dashboard_outbox.pending("dashboard-api"),
        Family.OPERATOR_APPROVAL,
    )


async def _stage_command(
    graph: _LiveGraph,
    service: DashboardMutationService,
    authority: _Authority,
) -> StagedApplicationEvent:
    """Commit one dashboard operator command and return its staged event."""
    await service.command(_command_mutation(graph.identity, authority))
    return _only_row(
        await graph.dashboard_outbox.pending("dashboard-api"),
        Family.OPERATOR_COMMAND,
    )


async def _dispatch_approval(
    graph: _LiveGraph,
    clock: Callable[[], AuthorizationClock],
) -> GuaranteedDispatchOutcome:
    """Receive one dashboard approval on the gateway's durable queue."""
    return await dispatch_gateway_guaranteed(
        Family.OPERATOR_APPROVAL.literal_suffix,
        _require_guaranteed(graph.gateway, Family.OPERATOR_APPROVAL.literal_suffix),
        graph.gateway_stamps,
        clock(),
        graph.gateway_store,
    )


async def _dispatch_command(
    graph: _LiveGraph,
    clock: Callable[[], AuthorizationClock],
) -> GuaranteedDispatchOutcome:
    """Receive one operator command through atomic authorization and settlement."""
    return await dispatch_gateway_guaranteed(
        Family.OPERATOR_COMMAND.literal_suffix,
        _require_guaranteed(graph.gateway, Family.OPERATOR_COMMAND.literal_suffix),
        graph.gateway_stamps,
        clock(),
        graph.gateway_store,
    )


async def _inbox_result(
    sessions: StoreSessionFactory,
    consumer: str,
    event_id: str,
) -> dict[str, object]:
    """Decode one exact durable broker-inbox result by its trusted identity."""
    async with sessions() as session:
        result = await session.scalar(
            select(BROKER_INBOX.c.result).where(
                (BROKER_INBOX.c.consumer == consumer) & (BROKER_INBOX.c.event_id == event_id)
            )
        )
    if not isinstance(result, bytes):
        _fail("live application probe broker inbox result was absent")
    return _object(canonical.decode(result))


def _invalid_approval_is_refused() -> bool:
    """Exercise the public mutation parser's closed approval schema refusal."""
    try:
        parse_mutation(
            schema_id=DECISION_SCHEMA,
            body=b"{}",
            content_type="application/json",
            idempotency_key=str(uuid4()),
            path_bindings={},
        )
    except MutationIngressError:
        return True
    return False


async def _authorization_reason(graph: _LiveGraph, row: StagedApplicationEvent) -> str:
    """Return the closed durable reason for one refused operator command."""
    result = await _inbox_result(graph.sessions, "command-gateway", row.event_id)
    return _required_text(result, "reason")


async def _exercise_expired_approval(
    graph: _LiveGraph,
    dashboard: DashboardMutationService,
    stamps: _DashboardStamps,
    clock: Callable[[], AuthorizationClock],
    authority: _Authority,
) -> tuple[GuaranteedDispatchOutcome, GuaranteedDispatchOutcome, tuple[str, str]]:
    """Persist and refuse one approval that expires before gateway binding."""
    stamps.occurred_at = _instant(_now() - timedelta(seconds=120))
    expired_approval = await _stage_decision(graph, dashboard, authority)
    await _publish_dashboard_row(
        graph.dashboard_outbox,
        graph.dashboard_publisher,
        expired_approval,
    )
    approval_dispatch = await _dispatch_approval(graph, clock)
    expired_result = await _inbox_result(
        graph.sessions,
        "command-gateway",
        expired_approval.event_id,
    )
    stamps.occurred_at = _instant()
    expired_command = await _stage_command(graph, dashboard, authority)
    await _publish_dashboard_row(
        graph.dashboard_outbox,
        graph.dashboard_publisher,
        expired_command,
    )
    command_dispatch = await _dispatch_command(graph, clock)
    reasons = (
        _required_text(expired_result, "approvalIngress"),
        await _authorization_reason(graph, expired_command),
    )
    if not await _drain_gateway(graph):
        _fail("live gateway did not publish the expired-command audit")
    return approval_dispatch, command_dispatch, reasons


async def _exercise_mismatched_approval(
    graph: _LiveGraph,
    dashboard: DashboardMutationService,
    clock: Callable[[], AuthorizationClock],
    authority: _Authority,
) -> tuple[
    tuple[GuaranteedDispatchOutcome, GuaranteedDispatchOutcome],
    GuaranteedDispatchOutcome,
    tuple[str, str],
]:
    """Refuse a conflicting event, then bind only the exact persisted approval."""
    mismatched_approval = await _stage_decision(graph, dashboard, authority)
    adversary = _mismatched_approval(mismatched_approval)
    mismatch_result = await graph.dashboard_publisher.publish(adversary)
    if mismatch_result.outcome is not DashboardPublicationOutcome.CONFIRMED:
        _fail("live mismatch adversary did not reach the broker")
    adversary_dispatch = await _dispatch_approval(graph, clock)
    mismatch_inbox = await _inbox_result(
        graph.sessions,
        "command-gateway",
        adversary.event_id,
    )
    mismatched_command = await _stage_command(graph, dashboard, authority)
    await _publish_dashboard_row(
        graph.dashboard_outbox,
        graph.dashboard_publisher,
        mismatched_command,
    )
    command_dispatch = await _dispatch_command(graph, clock)
    reasons = (
        _required_text(mismatch_inbox, "approvalIngress"),
        await _authorization_reason(graph, mismatched_command),
    )
    await _publish_dashboard_row(
        graph.dashboard_outbox,
        graph.dashboard_publisher,
        mismatched_approval,
    )
    exact_dispatch = await _dispatch_approval(graph, clock)
    if not await _drain_gateway(graph):
        _fail("live gateway did not publish the mismatch-command audit")
    return (adversary_dispatch, exact_dispatch), command_dispatch, reasons


async def _exercise_exact_approval(
    graph: _LiveGraph,
    dashboard: DashboardMutationService,
    clock: Callable[[], AuthorizationClock],
    authority: _Authority,
) -> tuple[
    tuple[GuaranteedDispatchOutcome, GuaranteedDispatchOutcome],
    tuple[GuaranteedDispatchOutcome, GuaranteedDispatchOutcome],
    CommandPublication,
]:
    """Bind one exact approval and prove repeated approval/command idempotency."""
    exact_approval = await _stage_decision(graph, dashboard, authority)
    await _publish_dashboard_row(
        graph.dashboard_outbox,
        graph.dashboard_publisher,
        exact_approval,
    )
    first_approval = await _dispatch_approval(graph, clock)
    graph.dashboard_router.publish(
        exact_approval.topic,
        exact_approval.payload,
        {},
    )
    duplicate_approval = await _dispatch_approval(graph, clock)
    exact_command = await _stage_command(graph, dashboard, authority)
    await _publish_dashboard_row(
        graph.dashboard_outbox,
        graph.dashboard_publisher,
        exact_command,
    )
    first_command = await _dispatch_command(graph, clock)
    pending = await graph.gateway_store.outbox.pending(COMMAND_PUBLICATION_BATCH_SIZE)
    if len(pending) != 1:
        _fail("live exact approval did not stage exactly one command")
    publication = pending[0]
    if publication.state is not OutboxState.STAGED:
        _fail("live exact command was not staged for confirmed publication")
    if not await _drain_gateway(graph):
        _fail("live gateway did not publish the exact authorized command")
    graph.dashboard_router.publish(
        exact_command.topic,
        exact_command.payload,
        {},
    )
    duplicate_command = await _dispatch_command(graph, clock)
    return (
        (first_approval, duplicate_approval),
        (first_command, duplicate_command),
        publication,
    )


def _authority_clock(graph: _LiveGraph) -> AuthorizationClock:
    """Read both live clocks for one dispatch, as the gateway's runtime does per message."""
    return AuthorizationClock(
        ClockReading(_now(), timedelta(seconds=time.monotonic())),
        graph.identity.gateway_epoch,
    )


async def _exercise_approvals(
    graph: _LiveGraph,
    authorities: Sequence[_Authority],
) -> _ApprovalExercise:
    """Prove invalid, expired, mismatched, repeated, and exact approval behavior."""
    by_label = {authority.label: authority for authority in authorities}
    stamps = _DashboardStamps(_instant())
    dashboard = _dashboard_service(graph, stamps)
    clock = partial(_authority_clock, graph)
    expired_approval, expired_command, expired_reasons = await _exercise_expired_approval(
        graph,
        dashboard,
        stamps,
        clock,
        by_label["expired"],
    )
    mismatch_approvals, mismatch_command, mismatch_reasons = await _exercise_mismatched_approval(
        graph,
        dashboard,
        clock,
        by_label["mismatch"],
    )
    exact_approvals, exact_commands, publication = await _exercise_exact_approval(
        graph,
        dashboard,
        clock,
        by_label["exact"],
    )
    return _ApprovalExercise(
        invalid_refused=_invalid_approval_is_refused(),
        reasons=(*expired_reasons, *mismatch_reasons),
        approval_dispatches=(expired_approval, *mismatch_approvals, *exact_approvals),
        command_dispatches=(expired_command, mismatch_command, *exact_commands),
        command=publication,
    )


def _command_topic(publication: CommandPublication) -> str:
    """Derive the exact rescue-command destination from durable identity."""
    command = publication.command
    return format_topic(
        Topic(
            Family.DRONE_COMMAND,
            command.mission_id,
            {"droneId": command.drone_id, "commandType": "escalate-rescue"},
        )
    )


async def _mark_command_sent(graph: _LiveGraph, command_id: str) -> None:
    """Apply the production scheduler's confirmed-send transition."""
    async with transaction(graph.sessions) as session:
        current = await load_command_progress(
            cast("CommandProgressSession", session),
            command_id,
        )
    sent_at = _instant()
    await record_send(
        current,
        SendClock(
            sent_at=sent_at,
            deadline_at=_instant(_now() + timedelta(seconds=10)),
            updated_at=sent_at,
        ),
        graph.gateway_store.progress,
    )


def _fleet_delivery(graph: _LiveGraph) -> CommandDelivery:
    """Receive one command from the probe drone's durable queue with bound settlement."""
    receiver = graph.fleet.receivers[PROBE_DRONE]
    message = receiver.receive(RECEIVE_TIMEOUT_MILLISECONDS)
    if message is None:
        _fail("live fleet received no authorized drone command")
    topic = message.get_destination_name()
    payload = message.get_payload_as_bytes()
    if not isinstance(topic, str) or not isinstance(payload, bytes):
        _fail("live fleet command omitted its typed broker members")
    return CommandDelivery(
        topic,
        payload,
        _FleetSettlement(MessageSettlement(receiver, message)),
    )


def _critical_facts(
    rows: Sequence[StagedApplicationEvent],
) -> tuple[tuple[str, ...], frozenset[str]]:
    """Return ordered command-result outcomes and their exact event identities."""
    outcomes: list[str] = []
    event_ids: set[str] = set()
    for row in rows:
        envelope = decode_envelope(row.payload)
        outcomes.append(_required_text(envelope.data, "outcome"))
        event_ids.add(envelope.id)
    return tuple(outcomes), frozenset(event_ids)


async def _exercise_fleet(
    graph: _LiveGraph,
    approval: _ApprovalExercise,
) -> _FleetExercise:
    """Restart the broker, recover, and prove one durable effect despite redelivery."""
    degraded, connected = await _restart_broker_once(graph.lifecycles)
    await _recover_live_graph(graph)
    recovered = connected and all(lifecycle.is_ready() for lifecycle in graph.lifecycles)
    command = approval.command.command
    await _mark_command_sent(graph, command.command_id)
    context = CommandContext(
        mission_id=graph.identity.mission_id,
        roster=frozenset({PROBE_DRONE}),
        budget=SendBudget(5),
        stamps=_FleetStamps(),
        unit_of_work=graph.fleet_work,
    )
    first = await handle_command(_fleet_delivery(graph), context)
    pending = await graph.fleet_outbox.pending(PROBE_DRONE)
    if len(pending) != CRITICAL_RESULT_COUNT:
        _fail("live fleet did not stage its acknowledgement and terminal result")
    result_outcomes, critical_ids = _critical_facts(pending)
    recovery = await drain_recovery(
        (PROBE_DRONE,),
        graph.fleet_outbox,
        graph.fleet_publisher,
        _FleetRecoveryReadiness(graph.fleet.readiness),
    )
    if not recovery.ready or recovery.confirmed != CRITICAL_RESULT_COUNT:
        _fail("live fleet did not confirm both durable command results")
    for _result in result_outcomes:
        outcome = await dispatch_gateway_guaranteed(
            Family.DRONE_COMMAND_RESULT.literal_suffix,
            _require_guaranteed(
                graph.gateway,
                Family.DRONE_COMMAND_RESULT.literal_suffix,
            ),
            graph.gateway_stamps,
            AuthorizationClock(
                ClockReading(_now(), timedelta(seconds=1_001)),
                graph.identity.gateway_epoch,
            ),
            graph.gateway_store,
        )
        if outcome is not GuaranteedDispatchOutcome.COMMITTED:
            _fail("live gateway refused a fleet command result")
    graph.gateway.publisher.publish(
        _command_topic(approval.command),
        command.payload,
        {},
    )
    duplicate = await handle_command(_fleet_delivery(graph), context)
    if await graph.fleet_outbox.pending(PROBE_DRONE):
        _fail("live duplicate command staged another critical result")
    return _FleetExercise(
        degraded=degraded,
        recovered=recovered,
        first=first.outcome,
        duplicate=duplicate.outcome,
        result_outcomes=result_outcomes,
        critical_event_ids=critical_ids,
    )


def _mismatched_approval(row: StagedApplicationEvent) -> StagedApplicationEvent:
    """Create one schema-valid broker adversary that conflicts with durable authority."""
    document = _object(canonical.decode(row.payload))
    data = _object(document.get("data"))
    event_id = _identifier()
    document["id"] = event_id
    data["evidenceDecisionDigest"] = "0" * 64
    document["data"] = data
    return replace(row, event_id=event_id, payload=canonical.canonical_bytes(document))


def _only_row(
    rows: Sequence[StagedApplicationEvent],
    family: Family,
) -> StagedApplicationEvent:
    """Require exactly one currently staged row for a family."""
    matching = _rows_for_family(rows, family)
    if len(matching) != 1:
        _fail(
            f"live application probe expected one {family.literal_suffix} row, got {len(matching)}"
        )
    return matching[0]


async def _drain_recorder(
    runtime: RecorderRuntime,
    receiver_names: Sequence[str],
) -> tuple[int, int]:
    """Drain exact probe collateral through the production receiver-only graph."""
    required_idle = (len(receiver_names) + 1) * 2
    idle = 0
    recorded = 0
    duplicates = 0
    for _ in range(MAX_RECORDER_POLLS):
        outcome = await runtime.process_next()
        if outcome.decision is ProcessDecision.IDLE:
            idle += 1
            if idle >= required_idle:
                return recorded, duplicates
            continue
        idle = 0
        if outcome.decision is ProcessDecision.RECORDED:
            recorded += 1
        elif outcome.decision is ProcessDecision.DUPLICATE:
            duplicates += 1
    _fail("live recorder did not reach one bounded empty cycle")


def _drain_dashboard_collateral(session: DashboardSession) -> None:
    """Accept every exact-test Guaranteed dashboard copy before database teardown."""
    for channel in session.receiver_names:
        timeout = IDLE_RECEIVE_MILLISECONDS
        while True:
            received = session.receive_guaranteed(channel, timeout)
            if received is None:
                break
            received.settlement.accept()
            timeout = IDLE_RECEIVE_MILLISECONDS
    timeout = IDLE_RECEIVE_MILLISECONDS
    while session.receive_direct(timeout) is not None:
        timeout = IDLE_RECEIVE_MILLISECONDS


async def _database_counts(
    sessions: StoreSessionFactory,
    mission_id: str,
    command_id: str,
) -> tuple[_DatabaseCounts, frozenset[str]]:
    """Read back every durable acceptance fact before dropping the probe database."""
    async with sessions() as session:

        async def count(table: Table, predicate: ColumnElement[bool]) -> int:
            statement = select(func.count()).select_from(table).where(predicate)
            scalar = await session.scalar(statement)
            if type(scalar) is not int:
                _fail("live application probe count was not integral")
            return scalar

        proposals = await count(PROPOSAL, PROPOSAL.c.mission_id == mission_id)
        decisions = await count(EVIDENCE_DECISION, EVIDENCE_DECISION.c.mission_id == mission_id)
        approvals = await count(APPROVAL, APPROVAL.c.mission_id == mission_id)
        consumed = await count(
            APPROVAL,
            (APPROVAL.c.mission_id == mission_id) & (APPROVAL.c.state == "executed"),
        )
        commands = await count(COMMAND_OUTBOX, COMMAND_OUTBOX.c.mission_id == mission_id)
        effects = await count(
            DRONE_COMMAND_EFFECT,
            DRONE_COMMAND_EFFECT.c.mission_id == mission_id,
        )
        receipts = await count(
            DRONE_COMMAND_RECEIPT,
            DRONE_COMMAND_RECEIPT.c.mission_id == mission_id,
        )
        gateway_inbox = await count(
            BROKER_INBOX,
            (BROKER_INBOX.c.mission_id == mission_id)
            & (BROKER_INBOX.c.consumer == "command-gateway"),
        )
        evidence_inbox = await count(
            BROKER_INBOX,
            (BROKER_INBOX.c.mission_id == mission_id)
            & (BROKER_INBOX.c.consumer == "evidence-service"),
        )
        recorder_inbox = await count(
            BROKER_INBOX,
            (BROKER_INBOX.c.mission_id == mission_id) & (BROKER_INBOX.c.consumer == "recorder"),
        )
        pending_application = await count(
            APPLICATION_OUTBOX,
            APPLICATION_OUTBOX.c.state != "confirmed",
        )
        pending_commands = await count(
            COMMAND_OUTBOX,
            COMMAND_OUTBOX.c.state != "confirmed",
        )
        progress = await session.scalar(
            select(COMMAND_PROGRESS.c.state).where(COMMAND_PROGRESS.c.command_id == command_id)
        )
        if not isinstance(progress, str):
            _fail("live application probe command progress was absent")
        recorded = await session.scalars(
            select(BROKER_INBOX.c.event_id).where(
                (BROKER_INBOX.c.mission_id == mission_id) & (BROKER_INBOX.c.consumer == "recorder")
            )
        )
        event_ids = frozenset(cast("Sequence[str]", recorded.all()))
    return (
        _DatabaseCounts(
            proposals,
            decisions,
            approvals,
            consumed,
            commands,
            effects,
            receipts,
            gateway_inbox,
            evidence_inbox,
            recorder_inbox,
            pending_application,
            pending_commands,
            progress,
        ),
        event_ids,
    )


async def _exercise_application_data_plane() -> _LiveReport:
    """Run one isolated, restart-inclusive production application data plane."""
    identity = _identity()
    target = _probe_target(identity)
    await _database_action(target, "CREATE")
    engine = create_engine(target, BOUNDS)
    graph: _LiveGraph | None = None
    try:
        await _migrate(engine)
        sessions = create_session_factory(engine)
        schemas = load_runtime_schema_registry(SCHEMAS)
        graph = await _open_live_graph(identity, engine, sessions, schemas)
        await _recover_live_graph(graph)
        telemetry_observed = _observe_telemetry(graph)
        (
            source_outcome,
            normalization_outcomes,
            proposal_outcomes,
            authorities,
            scores,
        ) = await _establish_authorities(graph)
        approval = await _exercise_approvals(graph, authorities)
        fleet = await _exercise_fleet(graph, approval)
        recorder_recorded, recorder_duplicates = await _drain_recorder(
            graph.recorder,
            tuple(recorder_bindings().queues),
        )
        _drain_dashboard_collateral(graph.dashboard)
        counts, recorded_ids = await _database_counts(
            graph.sessions,
            identity.mission_id,
            approval.command.command.command_id,
        )
        return _LiveReport(
            source_outcome=source_outcome,
            telemetry_observed=telemetry_observed,
            normalized=sum(
                outcome is DirectDispatchOutcome.RESPONSE_NORMALIZED
                for outcome in normalization_outcomes
            ),
            proposal_outcomes=proposal_outcomes,
            decision_scores=scores,
            invalid_approval_refused=approval.invalid_refused,
            negative_reasons=approval.reasons,
            approval_dispatches=approval.approval_dispatches,
            command_dispatches=approval.command_dispatches,
            readiness_degraded=fleet.degraded,
            readiness_recovered=fleet.recovered,
            first_effect=fleet.first,
            duplicate_effect=fleet.duplicate,
            result_outcomes=fleet.result_outcomes,
            critical_event_ids=fleet.critical_event_ids,
            recorded_event_ids=recorded_ids,
            recorder_recorded=recorder_recorded,
            recorder_duplicates=recorder_duplicates,
            counts=counts,
        )
    finally:
        if graph is not None:
            await graph.close()
        else:
            await engine.dispose()
        await _database_action(target, "DROP")


class ApplicationDataPlaneLiveTests(unittest.TestCase):
    """One serial, self-contained application-data-plane observation."""

    report: ClassVar[_LiveReport]

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Run the expensive authorized live observation exactly once."""
        cls.report = asyncio.run(_exercise_application_data_plane())

    def test_invalid_and_inexact_approvals_never_stage_a_command(self) -> None:
        # Arrange
        expected_reasons = (
            ApprovalIngressOutcome.EXPIRED.value,
            "approval-expired",
            ApprovalIngressOutcome.MISMATCH.value,
            "approval-expired",
        )
        expected_approval_dispatches = (GuaranteedDispatchOutcome.COMMITTED,) * 5
        expected_command_dispatches = (GuaranteedDispatchOutcome.COMMITTED,) * 4

        # Act
        report = self.report

        # Assert
        self.assertTrue(report.invalid_approval_refused)
        self.assertEqual(expected_reasons, report.negative_reasons)
        self.assertEqual(expected_approval_dispatches, report.approval_dispatches)
        self.assertEqual(expected_command_dispatches, report.command_dispatches)
        self.assertEqual(3, report.counts.approvals)
        self.assertEqual(1, report.counts.commands)

    def test_the_exact_approval_path_commits_one_command_effect(self) -> None:
        # Arrange
        expected_counts = (3, 3, 1, 1, 1, "succeeded")

        # Act
        report = self.report
        observed_counts = (
            report.counts.proposals,
            report.counts.decisions,
            report.counts.consumed_approvals,
            report.counts.effects,
            report.counts.receipts,
            report.counts.progress_state,
        )

        # Assert
        self.assertTrue(report.telemetry_observed)
        self.assertIs(report.source_outcome, EvidenceDispatchOutcome.PROCESSED)
        self.assertEqual(3, report.normalized)
        self.assertEqual((EvidenceDispatchOutcome.PROCESSED,) * 3, report.proposal_outcomes)
        self.assertEqual((75, 75, 75), report.decision_scores)
        self.assertIs(report.first_effect, CommandProcessingOutcome.APPLIED)
        self.assertEqual(("acknowledged", "succeeded"), report.result_outcomes)
        self.assertEqual(expected_counts, observed_counts)
        self.assertEqual(0, report.counts.pending_application)
        self.assertEqual(0, report.counts.pending_commands)

    def test_broker_restart_recovers_without_losing_or_reapplying_critical_work(self) -> None:
        # Arrange
        expected_critical_count = 2

        # Act
        report = self.report

        # Assert
        self.assertTrue(report.readiness_degraded)
        self.assertTrue(report.readiness_recovered)
        self.assertIs(report.duplicate_effect, CommandProcessingOutcome.DUPLICATE)
        self.assertEqual(expected_critical_count, len(report.critical_event_ids))
        self.assertLessEqual(report.critical_event_ids, report.recorded_event_ids)
        self.assertGreater(report.recorder_recorded, expected_critical_count)
        self.assertGreaterEqual(report.recorder_duplicates, 2)


if __name__ == "__main__":
    unittest.main()
