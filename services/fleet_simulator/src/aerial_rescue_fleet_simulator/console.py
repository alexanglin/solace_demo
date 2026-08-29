"""Concrete long-running Fleet control, PubSub+, and PostgreSQL composition root."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import IPv4Address
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import uvicorn
from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.ingress import (
    PayloadSchemaExecutor,
    load_runtime_schema_registry,
)
from aerial_rescue_broker.messaging import (
    RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS,
    BrokerEndpoint,
    open_fleet_session,
)
from aerial_rescue_broker.queues import drone_queue_name
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.principals import Principal
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
from aerial_rescue_store.processing.fleet import FleetTransactions
from aerial_rescue_store.session import (
    Disposable,
    StoreSessionFactory,
    close,
    create_session_factory,
)
from aerial_rescue_store.settings import (
    CONTAINER_HOST,
    DatabaseSettings,
    database_settings,
)
from fastapi import FastAPI

from aerial_rescue_fleet_simulator.control_plane.control import FleetCoordinator
from aerial_rescue_fleet_simulator.control_plane.runtime import (
    FleetControl,
    ServerSettings,
    create_application,
)
from aerial_rescue_fleet_simulator.durable_processing import EffectResult
from aerial_rescue_fleet_simulator.intake import IncomingCommand
from aerial_rescue_fleet_simulator.runtime import (
    ExecutorDependencies,
    FleetExecutor,
    FleetSessionOpener,
)
from aerial_rescue_fleet_simulator.service import CountingStamps, IntakeBounds, MonotonicPacer
from aerial_rescue_fleet_simulator.store_adapter import (
    EffectCallback,
    StoreCriticalOutbox,
    StoreFleetUnitOfWork,
)

_CONTROL_PORT = 8082
_LISTENER_HOST = str(IPv4Address(0))
_STARTUP_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_TIMEOUT_SECONDS = 15.0
_MAXIMUM_SECRET_FILE_BYTES = 129
_DRONE_IDS_SETTING = "FLEET_DRONE_IDS"
_BROKER_URL_SETTING = "SOLACE_BROKER_URL"
_BROKER_VPN_SETTING = "SOLACE_BROKER_VPN"
_TRUST_STORE_SETTING = "TRUST_STORE"
_DEPLOY_DIRECTORY_SETTING = "AERIAL_RESCUE_DEPLOY_DIR"
_SCHEMA_DIRECTORY_SETTING = "AERIAL_RESCUE_SCHEMA_DIR"
_DEFAULT_SCHEMA_DIRECTORY = "schemas"

_CANCELLATION_TIMEOUT_SECONDS = 15.0
_ACTIVE_RUN_CAPACITY = 1
_MAX_COMMAND_SENDS = 5
_COMMANDS_PER_DRONE_PER_TICK = 3
"""Composition values whose canonical rows live in ``docs/operating-parameters.md``."""


class SettingsRefusal(StrEnum):
    """Closed, secret-safe environment refusal vocabulary."""

    MISSING = "MISSING"
    MATERIAL_UNAVAILABLE = "MATERIAL_UNAVAILABLE"
    INVALID = "INVALID"


class SettingsError(RuntimeError):
    """A settings refusal that never retains secret material or file paths."""

    def __init__(self, refusal: SettingsRefusal) -> None:
        """Retain only the closed refusal code."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Validated settings needed by the private HTTP adapter."""

    server: ServerSettings


@dataclass(frozen=True, slots=True)
class ListenerOptions:
    """Uvicorn options that keep the listener private and diagnostics bounded."""

    host: str
    port: int
    access_log: bool
    server_header: bool
    proxy_headers: bool


class ServerRunner(Protocol):
    """Injectable synchronous ASGI listener boundary."""

    def __call__(self, application: FastAPI, options: ListenerOptions) -> None:
        """Serve the application until shutdown."""


class ExhaustionSignal(Protocol):
    """The terminal broker-recovery signal observed by process supervision."""

    @property
    def exit_status(self) -> int:
        """Return zero for ordinary shutdown and nonzero for terminal recovery."""

    async def wait_for_exhaustion(self) -> None:
        """Wait until active-session recovery is terminal."""


class AsyncServer(Protocol):
    """The Uvicorn operations needed for graceful terminal supervision."""

    should_exit: bool

    async def serve(self) -> None:
        """Serve until an external signal or ``should_exit`` requests shutdown."""


class AsyncServerFactory(Protocol):
    """Build one private ASGI server without starting it."""

    def __call__(self, application: FastAPI, options: ListenerOptions) -> AsyncServer:
        """Return a server retaining the supplied private listener options."""


class DatabaseResolver(Protocol):
    """Resolve one credential-separated durable target without opening a connection."""

    def __call__(
        self,
        environment: Mapping[str, str],
        deploy: Path,
        *,
        host: str,
    ) -> DatabaseSettings:
        """Return the validated PostgreSQL target for the selected host."""


class StoreComposer(Protocol):
    """Build the lazy Fleet SQLAlchemy graph."""

    def __call__(
        self,
        settings: DatabaseSettings,
        bounds: EngineBounds,
        observed_at: Callable[[], str],
    ) -> FleetStoreResources:
        """Return the engine and purpose-specific Fleet adapters."""


@dataclass(slots=True)
class FleetStoreResources:
    """All SQLAlchemy resources owned by one Fleet process epoch."""

    engine: Disposable
    bounds: EngineBounds
    sessions: StoreSessionFactory
    outbox: StoreCriticalOutbox
    refusals: BrokerRefusalRecorder
    closed: bool = False

    def commands(
        self,
        effect: Callable[[str, IncomingCommand], EffectResult],
    ) -> StoreFleetUnitOfWork:
        """Return the durable command unit of work over the shared session factory."""
        return StoreFleetUnitOfWork(
            FleetTransactions(self.sessions),
            cast("EffectCallback", effect),
            self.refusals,
        )

    async def close(self) -> None:
        """Dispose the pool once within the accepted shutdown grace."""
        if self.closed:
            return
        self.closed = True
        await close(self.engine, self.bounds.shutdown_grace_seconds)


@dataclass(frozen=True, slots=True)
class FleetProcessRuntime:
    """Every external value and constructor used by the concrete Fleet process."""

    environment: Mapping[str, str]
    deploy: Path
    schema_directory: Path
    database_host: str
    bounds: EngineBounds
    credential: Callable[[Path, Principal], str]
    load_schemas: Callable[[Path], PayloadSchemaExecutor]
    database: DatabaseResolver
    store: StoreComposer
    open_broker: FleetSessionOpener
    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    observed_at: Callable[[], str]
    recovery_pause: Callable[[], Awaitable[None]]
    server_factory: AsyncServerFactory | None = None


def _required(environment: Mapping[str, str], name: str) -> str:
    """Read one non-empty setting without retaining it in an exception."""
    value = environment.get(name)
    if value is None or not value:
        raise SettingsError(SettingsRefusal.MISSING)
    return value


def _read_bearer(filename: str) -> str:
    """Read one small regular non-symlink bearer file without exposing its value."""
    path = Path(filename)
    if path.is_symlink() or not path.is_file():
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE)
    try:
        with path.open("rb") as stream:
            encoded = stream.read(_MAXIMUM_SECRET_FILE_BYTES + 1)
    except OSError as error:
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE) from error
    if len(encoded) > _MAXIMUM_SECRET_FILE_BYTES:
        raise SettingsError(SettingsRefusal.MATERIAL_UNAVAILABLE)
    try:
        return encoded.removesuffix(b"\n").decode("ascii")
    except UnicodeDecodeError as error:
        raise SettingsError(SettingsRefusal.INVALID) from error


def settings_from_environment(environment: Mapping[str, str]) -> RuntimeSettings:
    """Validate private-control identity and lifecycle settings at startup."""
    host = _required(environment, "FLEET_CONTROL_HOST")
    bearer_file = _required(environment, "FLEET_CONTROL_BEARER_FILE")
    bearer = _read_bearer(bearer_file)
    try:
        server = ServerSettings(
            host=host,
            bearer=bearer,
            startup_timeout_seconds=_STARTUP_TIMEOUT_SECONDS,
            shutdown_timeout_seconds=_SHUTDOWN_TIMEOUT_SECONDS,
        )
    except ValueError as error:
        raise SettingsError(SettingsRefusal.INVALID) from error
    return RuntimeSettings(server)


def fleet_drone_ids(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Return the exact unique provisioned queue roster without retaining bad values."""
    rendered = _required(environment, _DRONE_IDS_SETTING)
    members = tuple(member.strip() for member in rendered.split(","))
    if not members or any(not member for member in members) or len(set(members)) != len(members):
        raise SettingsError(SettingsRefusal.INVALID)
    try:
        for member in members:
            drone_queue_name(member)
    except ValueError as error:
        raise SettingsError(SettingsRefusal.INVALID) from error
    return tuple(sorted(members))


def broker_endpoint(environment: Mapping[str, str]) -> BrokerEndpoint:
    """Resolve required non-secret broker settings before opening any socket."""
    values = tuple(
        _required(environment, name).strip()
        for name in (_BROKER_URL_SETTING, _BROKER_VPN_SETTING, _TRUST_STORE_SETTING)
    )
    if any(not value for value in values):
        raise SettingsError(SettingsRefusal.MISSING)
    return BrokerEndpoint(values[0], values[1], values[2])


def production_bounds() -> EngineBounds:
    """Apply every accepted SQLAlchemy wait explicitly rather than inheriting one."""
    return EngineBounds(
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


def compose_store(
    settings: DatabaseSettings,
    bounds: EngineBounds,
    observed_at: Callable[[], str],
) -> FleetStoreResources:
    """Build the lazy engine and typed Fleet transaction/outbox adapters."""
    engine = create_engine(settings, bounds)
    sessions = create_session_factory(engine)
    return FleetStoreResources(
        engine,
        bounds,
        sessions,
        StoreCriticalOutbox(sessions),
        BrokerRefusalRecorder(sessions, observed_at),
    )


def _directory(environment: Mapping[str, str], name: str, default: str) -> Path:
    """Resolve one startup path while refusing an explicitly blank override."""
    if name not in environment:
        return Path(default)
    value = environment[name].strip()
    if not value:
        raise SettingsError(SettingsRefusal.MISSING)
    return Path(value)


async def _recovery_pause() -> None:
    """Wait one broker-owned reconnect interval between lifecycle inspections."""
    await asyncio.sleep(RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS / 1_000)


def default_process_runtime(
    environment: Mapping[str, str] | None = None,
) -> FleetProcessRuntime:
    """Return the real container runtime without reading a file or opening a resource."""
    selected = os.environ if environment is None else environment
    return FleetProcessRuntime(
        environment=selected,
        deploy=_directory(selected, _DEPLOY_DIRECTORY_SETTING, DEFAULT_DEPLOY_DIRECTORY),
        schema_directory=_directory(
            selected,
            _SCHEMA_DIRECTORY_SETTING,
            _DEFAULT_SCHEMA_DIRECTORY,
        ),
        database_host=CONTAINER_HOST,
        bounds=production_bounds(),
        credential=read_credential,
        load_schemas=load_runtime_schema_registry,
        database=database_settings,
        store=compose_store,
        open_broker=cast("FleetSessionOpener", open_fleet_session),
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        observed_at=lambda: format_instant(datetime.now(tz=UTC)),
        recovery_pause=_recovery_pause,
    )


async def run_process(runtime: FleetProcessRuntime) -> int:
    """Resolve, compose, serve, and release one concrete Fleet process graph."""
    control_settings = settings_from_environment(runtime.environment)
    endpoint = broker_endpoint(runtime.environment)
    drone_ids = fleet_drone_ids(runtime.environment)
    schemas = runtime.load_schemas(runtime.schema_directory)
    database = runtime.database(
        runtime.environment,
        runtime.deploy,
        host=runtime.database_host,
    )
    role = Principal.FLEET_SIMULATOR
    credential = runtime.credential(runtime.deploy, role)
    store = runtime.store(database, runtime.bounds, runtime.observed_at)
    stamps = CountingStamps(runtime.clock, runtime.identifiers, "unbound-runtime")
    executor = FleetExecutor(
        ExecutorDependencies(
            endpoint=endpoint,
            credential=credential,
            configured_drone_ids=drone_ids,
            open_broker=runtime.open_broker,
            store=store,
            schemas=schemas,
            stamps=stamps,
            pacer=MonotonicPacer(),
            send_budget=SendBudget(_MAX_COMMAND_SENDS),
            intake=IntakeBounds(_COMMANDS_PER_DRONE_PER_TICK),
            confirmed_at=runtime.observed_at,
            recovery_pause=runtime.recovery_pause,
        )
    )
    control = FleetCoordinator(
        executor,
        cancellation_timeout_seconds=_CANCELLATION_TIMEOUT_SECONDS,
        capacity=_ACTIVE_RUN_CAPACITY,
    )
    try:
        return await serve_control(
            control,
            control_settings.server,
            executor,
            server_factory=runtime.server_factory,
        )
    finally:
        await executor.shutdown()


def _run_uvicorn(application: FastAPI, options: ListenerOptions) -> None:
    """Serve one private listener with public diagnostics disabled."""
    uvicorn.run(
        application,
        host=options.host,
        port=options.port,
        access_log=options.access_log,
        server_header=options.server_header,
        proxy_headers=options.proxy_headers,
    )


def _uvicorn_server(application: FastAPI, options: ListenerOptions) -> AsyncServer:
    """Build the real signal-aware Uvicorn server without starting its loop."""
    configuration = uvicorn.Config(
        application,
        host=options.host,
        port=options.port,
        access_log=options.access_log,
        server_header=options.server_header,
        proxy_headers=options.proxy_headers,
    )
    return uvicorn.Server(configuration)


async def serve_control(
    control: FleetControl,
    settings: ServerSettings,
    exhaustion: ExhaustionSignal,
    *,
    server_factory: AsyncServerFactory | None = None,
) -> int:
    """Serve private control and stop gracefully when broker recovery exhausts."""
    application = create_application(settings, control)
    options = ListenerOptions(_LISTENER_HOST, _CONTROL_PORT, False, False, False)
    server = (server_factory or _uvicorn_server)(application, options)
    serving = asyncio.create_task(server.serve())
    terminal = asyncio.create_task(exhaustion.wait_for_exhaustion())
    done, _pending = await asyncio.wait(
        {serving, terminal},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if terminal in done:
        await terminal
        server.should_exit = True
        await serving
    else:
        await serving
        terminal.cancel()
        await asyncio.gather(terminal, return_exceptions=True)
    return exhaustion.exit_status


def run_console(
    control: FleetControl,
    environment: Mapping[str, str],
    *,
    runner: ServerRunner | None = None,
) -> None:
    """Compose and serve the private HTTP boundary over an injected fleet runtime."""
    settings = settings_from_environment(environment)
    application = create_application(settings.server, control)
    options = ListenerOptions(
        _LISTENER_HOST,
        _CONTROL_PORT,
        False,
        False,
        False,
    )
    (runner or _run_uvicorn)(application, options)


def main(runtime: FleetProcessRuntime | None = None) -> int:
    """Run the concrete service and preserve nonzero broker-exhaustion status."""
    selected = default_process_runtime() if runtime is None else runtime
    return asyncio.run(run_process(selected))
