"""Concrete dashboard API composition and private Unix-socket console."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Final, cast
from uuid import uuid4

import uvicorn
from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.ingress import PayloadSchemaExecutor, load_runtime_schema_registry
from aerial_rescue_broker.messaging import (
    RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS,
    BrokerEndpoint,
    DashboardBindings,
    DashboardSession,
    open_dashboard_session,
)
from aerial_rescue_broker.queues import family_queue_name, guaranteed_grants
from aerial_rescue_broker.routing import DeliveryRouter, PublicationPorts
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_contracts.topics import Delivery, delivery_for
from aerial_rescue_contracts.view import (
    EMPTY_CHECKPOINT,
    MAX_BUFFERED_EVENTS,
)
from aerial_rescue_domain.principals import Access, Principal, grants
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
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.dashboard import (
    DashboardAuditReader,
    DashboardInboxTransactions,
    DashboardMutationTransactions,
    DashboardOutboxTransactions,
)
from aerial_rescue_store.session import Disposable, close, create_session_factory
from aerial_rescue_store.settings import CONTAINER_HOST, database_settings
from fastapi import FastAPI

from aerial_rescue_dashboard_api.application import ApplicationSettings, create_live_application
from aerial_rescue_dashboard_api.broker_runtime import DashboardDataPlane, DataPlanePorts
from aerial_rescue_dashboard_api.files import (
    DashboardFileSettings,
    FilesystemDashboardData,
    discover_asset_entrypoint,
)
from aerial_rescue_dashboard_api.lifecycle import RunMode, RuntimeReadiness
from aerial_rescue_dashboard_api.mutations import DashboardMutationService, MutationStamp
from aerial_rescue_dashboard_api.operations import LiveDashboardOperations, LiveOperationPorts
from aerial_rescue_dashboard_api.outbox import DashboardOutboxPublisher
from aerial_rescue_dashboard_api.projection import DashboardProjectionHub, ProjectionHubSettings
from aerial_rescue_dashboard_api.runtime_context import new_runtime_context
from aerial_rescue_dashboard_api.scenario_http import (
    ScenarioControlHttpClient,
    ScenarioControlHttpSettings,
)
from aerial_rescue_dashboard_api.supervisor import (
    DashboardBrokerSupervisor,
    OwnedDashboardSession,
    SupervisorPorts,
    SupervisorSettings,
)

_MAXIMUM_FILE_BYTES: Final = 262_144
_MAXIMUM_SECRET_BYTES: Final = 129
_MAXIMUM_SSE_CLIENTS: Final = 8
_SSE_KEEPALIVE_MILLISECONDS: Final = 15_000
_AUDIT_PAGE_SIZE: Final = 50
_APPROVAL_TIME_TO_LIVE_MILLISECONDS: Final = 60_000
_SOCKET_DEFAULT: Final = "/run/aerial-rescue/dashboard-api.sock"
_SCHEMA_DEFAULT: Final = "/app/schemas"
_SCENARIO_DEFAULT: Final = "/app/scenarios"
_ASSET_DEFAULT: Final = "/app/dashboard-assets"
_REPLAY_DEFAULT: Final = "/app/replays"


class SettingsRefusal(Enum):
    """Why the production dashboard graph cannot be configured safely."""

    MISSING = "dashboard runtime setting is missing"
    MATERIAL = "dashboard private material is unavailable"
    INVALID = "dashboard runtime setting is invalid"


class SettingsError(ValueError):
    """A redacted settings refusal that retains no value or path."""

    def __init__(self, refusal: SettingsRefusal) -> None:
        """Retain only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Validated live process settings with the private bearer structurally hidden."""

    environment: Mapping[str, str] = field(repr=False)
    deploy: Path
    schema_root: Path
    scenario_root: Path
    asset_root: Path
    replay_root: Path
    socket_path: Path
    allowed_hosts: tuple[str, ...]
    allowed_origin: str
    operator_id: str
    broker: BrokerEndpoint
    scenario: ScenarioControlHttpSettings


@dataclass(frozen=True, slots=True)
class ListenerOptions:
    """The complete private Uvicorn listener surface."""

    uds: Path
    access_log: bool
    proxy_headers: bool
    server_header: bool
    graceful_shutdown_seconds: int


@dataclass(slots=True)
class _StoreResources:
    """One lazy SQLAlchemy engine and its purpose-specific dashboard adapters."""

    engine: Disposable
    mutations: DashboardMutationTransactions
    inboxes: DashboardInboxTransactions
    outbox: DashboardOutboxTransactions
    audit: DashboardAuditReader
    refusals: BrokerRefusalRecorder
    closed: bool = False

    async def close(self) -> None:
        """Dispose the pool once inside the accepted store grace."""
        if self.closed:
            return
        self.closed = True
        await close(self.engine, SHUTDOWN_GRACE_SECONDS)


@dataclass(frozen=True, slots=True)
class DashboardProcess:
    """One already-composed application and its exhaustion signal."""

    application: FastAPI
    broker: DashboardBrokerSupervisor
    listener: ListenerOptions


class _StampSource:
    """Issue unique process-local event identities and increasing wire sequences."""

    def __init__(self) -> None:
        self._sequence = int(time.time() * 1_000) * 100

    def next(self) -> MutationStamp:
        """Return one trusted clock, identity, trace, and fifteen-digit sequence stamp."""
        self._sequence += 1
        trace_id = uuid4().hex
        span_id = uuid4().hex[:16]
        return MutationStamp(
            event_id=f"event-{uuid4().hex}",
            entity_id=f"operation-{uuid4().hex}",
            occurred_at=_observed_at(),
            monotonic_milliseconds=time.monotonic_ns() // 1_000_000,
            sequence=self._sequence,
            traceparent=f"00-{trace_id}-{span_id}-01",
        )


def settings_from_environment(environment: Mapping[str, str]) -> RuntimeSettings:
    """Resolve exact public, private-control, file, broker, and store inputs."""
    hosts = tuple(
        item.strip() for item in _required(environment, "DASHBOARD_ALLOWED_HOSTS").split(",")
    )
    if not hosts or any(not item for item in hosts) or len(set(hosts)) != len(hosts):
        raise SettingsError(SettingsRefusal.INVALID)
    deploy = _directory(environment, "AERIAL_RESCUE_DEPLOY_DIR", DEFAULT_DEPLOY_DIRECTORY)
    bearer = _read_secret(Path(_required(environment, "SCENARIO_CONTROL_BEARER_FILE")))
    try:
        scenario = ScenarioControlHttpSettings(
            _required(environment, "SCENARIO_CONTROL_URL"),
            _required(environment, "SCENARIO_CONTROL_HOST"),
            bearer,
        )
        broker = BrokerEndpoint(
            _required(environment, "SOLACE_BROKER_URL"),
            _required(environment, "SOLACE_BROKER_VPN"),
            _required(environment, "TRUST_STORE"),
        )
    except ValueError as error:
        raise SettingsError(SettingsRefusal.INVALID) from error
    return RuntimeSettings(
        environment,
        deploy,
        _directory(environment, "AERIAL_RESCUE_SCHEMA_DIR", _SCHEMA_DEFAULT),
        _directory(environment, "SCENARIO_CATALOG_ROOT", _SCENARIO_DEFAULT),
        _directory(environment, "DASHBOARD_ASSET_ROOT", _ASSET_DEFAULT),
        _directory(environment, "DASHBOARD_REPLAY_ROOT", _REPLAY_DEFAULT),
        _directory(environment, "DASHBOARD_SOCKET_PATH", _SOCKET_DEFAULT),
        hosts,
        _required(environment, "DASHBOARD_ALLOWED_ORIGIN"),
        _required(environment, "DASHBOARD_OPERATOR_ID"),
        broker,
        scenario,
    )


def listener_options(settings: RuntimeSettings) -> ListenerOptions:
    """Return a UDS-only listener with proxy and public diagnostics disabled."""
    return ListenerOptions(settings.socket_path, False, False, False, SHUTDOWN_GRACE_SECONDS)


def dashboard_bindings() -> DashboardBindings:
    """Derive every receiver from the dashboard role's closed subscribe grants."""
    role = Principal.DASHBOARD_API
    queues = {
        family.literal_suffix: family_queue_name(role, family) for family in guaranteed_grants(role)
    }
    direct = tuple(
        sorted(
            subscription_for(family)
            for family in grants(role, Access.SUBSCRIBE)
            if delivery_for(family) is Delivery.DIRECT
        )
    )
    return DashboardBindings(queues, direct, MAX_BUFFERED_EVENTS)


def production_bounds() -> EngineBounds:
    """Apply every accepted SQLAlchemy wait explicitly."""
    return EngineBounds(
        POOL_SIZE,
        POOL_OVERFLOW,
        CHECKOUT_TIMEOUT_SECONDS,
        CONNECT_TIMEOUT_SECONDS,
        CONNECT_RETRIES,
        STATEMENT_TIMEOUT_MILLISECONDS,
        LOCK_TIMEOUT_MILLISECONDS,
        IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS,
        SHUTDOWN_GRACE_SECONDS,
    )


def build_process(settings: RuntimeSettings) -> DashboardProcess:
    """Compose the lazy live graph; no socket, pool connection, or task opens here."""
    role = Principal.DASHBOARD_API
    readiness = RuntimeReadiness(RunMode.DEGRADED_LIVE)
    context = new_runtime_context(
        runtime_id=f"runtime-{uuid4().hex[:24]}",
        operator_id=settings.operator_id,
    )
    schemas = load_runtime_schema_registry(settings.schema_root)
    files = FilesystemDashboardData(
        DashboardFileSettings(
            settings.scenario_root,
            settings.asset_root,
            settings.replay_root,
            _MAXIMUM_FILE_BYTES,
        )
    )
    hub = DashboardProjectionHub(
        runtime_id=context.runtime_id,
        checkpoint=EMPTY_CHECKPOINT,
        current_run=None,
        cursor=_cursor_issuer(context.bearer),
        settings=ProjectionHubSettings(
            max_clients=_MAXIMUM_SSE_CLIENTS,
            keepalive_milliseconds=_SSE_KEEPALIVE_MILLISECONDS,
        ),
    )
    store = _compose_store(settings)
    credential = read_credential(settings.deploy, role)
    bindings = dashboard_bindings()
    supervisor = DashboardBrokerSupervisor(
        ports=SupervisorPorts(
            open_session=lambda: cast(
                "OwnedDashboardSession",
                open_dashboard_session(settings.broker, role, credential, bindings),
            ),
            plane=_plane_factory(store, hub, schemas),
            readiness=readiness,
            close_store=store.close,
            pause=_recovery_pause,
        ),
        settings=SupervisorSettings(RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS),
    )
    mutation_service = DashboardMutationService(
        transactions=store.mutations,
        runtime_id=context.runtime_id,
        stamps=_StampSource().next,
        schemas=schemas,
        approval_time_to_live_milliseconds=_APPROVAL_TIME_TO_LIVE_MILLISECONDS,
    )
    operations = LiveDashboardOperations(
        ports=LiveOperationPorts(
            files,
            ScenarioControlHttpClient(settings.scenario),
            supervisor,
            hub,
            mutation_service,
            store.mutations,
        ),
        readiness=readiness,
        claimed_at=_observed_at,
    )
    application = create_live_application(
        ApplicationSettings(
            settings.allowed_hosts,
            settings.allowed_origin,
            discover_asset_entrypoint(settings.asset_root, _MAXIMUM_FILE_BYTES),
        ),
        context,
        readiness,
        operations,
    )
    return DashboardProcess(application, supervisor, listener_options(settings))


async def run_process(settings: RuntimeSettings) -> int:
    """Serve the UDS and exit nonzero if active-session broker recovery exhausts."""
    process = build_process(settings)
    configuration = uvicorn.Config(
        process.application,
        uds=str(process.listener.uds),
        access_log=process.listener.access_log,
        proxy_headers=process.listener.proxy_headers,
        server_header=process.listener.server_header,
        timeout_graceful_shutdown=process.listener.graceful_shutdown_seconds,
    )
    server = uvicorn.Server(configuration)
    serving = asyncio.create_task(server.serve())
    exhaustion = asyncio.create_task(process.broker.wait_for_exhaustion())
    done, _pending = await asyncio.wait({serving, exhaustion}, return_when=asyncio.FIRST_COMPLETED)
    if exhaustion in done:
        await exhaustion
        server.should_exit = True
        await serving
    else:
        await serving
        exhaustion.cancel()
        await asyncio.gather(exhaustion, return_exceptions=True)
    return process.broker.exit_status


def main(environment: Mapping[str, str] | None = None) -> int:
    """Run the concrete live dashboard API and preserve terminal broker status."""
    selected = os.environ if environment is None else environment
    return asyncio.run(run_process(settings_from_environment(selected)))


def _plane_factory(
    store: _StoreResources,
    hub: DashboardProjectionHub,
    schemas: PayloadSchemaExecutor,
) -> Callable[[OwnedDashboardSession], DashboardDataPlane]:

    def build(session: OwnedDashboardSession) -> DashboardDataPlane:
        dashboard = cast("DashboardSession", session)
        router = DeliveryRouter(
            Principal.DASHBOARD_API,
            PublicationPorts(guaranteed=dashboard.publisher),
        )
        publisher = DashboardOutboxPublisher(router, _observed_at)
        return DashboardDataPlane(
            session=dashboard,
            ports=DataPlanePorts(
                hub,
                store.audit,
                store.inboxes,
                store.outbox,
                publisher,
                schemas,
                store.refusals,
                _observed_at,
            ),
            audit_page_size=_AUDIT_PAGE_SIZE,
        )

    return build


def _compose_store(settings: RuntimeSettings) -> _StoreResources:
    target = database_settings(settings.environment, settings.deploy, host=CONTAINER_HOST)
    engine = create_engine(target, production_bounds())
    sessions = create_session_factory(engine)
    return _StoreResources(
        engine,
        DashboardMutationTransactions(sessions),
        DashboardInboxTransactions(sessions),
        DashboardOutboxTransactions(sessions),
        DashboardAuditReader(sessions),
        BrokerRefusalRecorder(sessions, _observed_at),
    )


def _cursor_issuer(bearer: str) -> Callable[[int, str | None], str]:
    key = bearer.encode("ascii")

    def issue(ordinal: int, witness: str | None) -> str:
        material = f"{ordinal}:{witness or '-'}".encode("ascii")
        encoded = base64.urlsafe_b64encode(hmac.new(key, material, hashlib.sha256).digest())
        return encoded.rstrip(b"=").decode("ascii")

    return issue


async def _recovery_pause() -> None:
    await asyncio.sleep(RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS / 1_000)


def _observed_at() -> str:
    return format_instant(datetime.now(tz=UTC))


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise SettingsError(SettingsRefusal.MISSING)
    return value


def _directory(environment: Mapping[str, str], name: str, default: str) -> Path:
    value = environment.get(name, default).strip()
    if not value:
        raise SettingsError(SettingsRefusal.MISSING)
    return Path(value)


def _read_secret(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SettingsError(SettingsRefusal.MATERIAL)
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAXIMUM_SECRET_BYTES + 1)
        if len(raw) > _MAXIMUM_SECRET_BYTES:
            raise SettingsError(SettingsRefusal.MATERIAL)
        return raw.removesuffix(b"\n").decode("ascii")
    except (OSError, UnicodeError) as error:
        raise SettingsError(SettingsRefusal.MATERIAL) from error
