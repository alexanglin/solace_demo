"""Concrete long-running command-gateway broker and PostgreSQL composition root."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Callable, Coroutine, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    CommandGatewayBindings,
    CommandGatewaySession,
    open_command_gateway_session,
)
from aerial_rescue_broker.routing import (
    DeliveryRouter,
    GuaranteedReplyResponder,
    PublicationPorts,
)
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_domain.approvals import ClockReading
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
from aerial_rescue_store.session import StoreSessionFactory, close, create_session_factory
from aerial_rescue_store.settings import (
    CONTAINER_HOST,
    DatabaseSettings,
    database_settings,
)

from aerial_rescue_command_gateway.authorization import AuthorizationClock
from aerial_rescue_command_gateway.service import (
    ApplicationSessionPort,
    ApplicationStampSource,
    CountingStamps,
    GatewayApplication,
    ServiceExit,
    broker_endpoint,
    gateway_bindings,
    serve_application,
)
from aerial_rescue_command_gateway.store_adapter import (
    ApplicationStore,
    compose_application_store,
)

DEPLOY_DIRECTORY_SETTING = "AERIAL_RESCUE_DEPLOY_DIR"


class SignalLoop(Protocol):
    """The event-loop signal surface used without process-global test mutation."""

    def add_signal_handler(
        self,
        event: signal.Signals,
        callback: Callable[[], None],
    ) -> None:
        """Arrange for one signal to request cooperative shutdown."""

    def remove_signal_handler(self, event: signal.Signals) -> bool:
        """Remove one previously installed handler."""


class DatabaseSettingsReader(Protocol):
    """Resolve a credential-separated durable target."""

    def __call__(
        self,
        environment: Mapping[str, str],
        deploy: Path,
        host: str,
        /,
    ) -> DatabaseSettings:
        """Return one validated database target."""


class BrokerOpener(Protocol):
    """Open exactly one command-gateway mixed broker graph."""

    def __call__(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        bindings: CommandGatewayBindings,
        /,
    ) -> CommandGatewaySession:
        """Return one connected owned session."""


class ApplicationServer(Protocol):
    """Continuously serve one already-composed application graph."""

    def __call__(
        self,
        session: ApplicationSessionPort,
        application: GatewayApplication,
        running: Callable[[], bool],
        /,
    ) -> Coroutine[object, object, ServiceExit]:
        """Return only after shutdown or terminal broker exhaustion."""


class EnginePort(Protocol):
    """The only owned SQLAlchemy engine operation used during shutdown."""

    async def dispose(self) -> None:
        """Release every pooled connection."""


EngineFactory = Callable[[DatabaseSettings, EngineBounds], EnginePort]
SessionFactoryBuilder = Callable[[EnginePort], StoreSessionFactory]
StoreComposer = Callable[[StoreSessionFactory, Callable[[], str]], ApplicationStore]
CredentialReader = Callable[[Path, Principal], str]
SignalRegistrar = Callable[[Callable[[], None]], Callable[[], None]]


@dataclass
class ShutdownRequest:
    """A callback-safe cooperative cancellation signal."""

    requested: bool = False

    def request(self) -> None:
        """Stop the next scheduler turn without performing I/O in the callback."""
        self.requested = True

    def running(self) -> bool:
        """Report whether the serving loop may start another bounded turn."""
        return not self.requested


@dataclass(frozen=True)
class CommandGatewayRuntime:
    """Every external operation and setting owned by the composition root."""

    environment: Mapping[str, str]
    deploy: Path
    database_host: str
    bounds: EngineBounds
    broker_credential: CredentialReader
    database_settings: DatabaseSettingsReader
    create_engine: EngineFactory
    create_sessions: SessionFactoryBuilder
    compose_store: StoreComposer
    open_broker: BrokerOpener
    bindings: CommandGatewayBindings
    stamps: ApplicationStampSource
    authority_clock: Callable[[], AuthorizationClock]
    observed_at: Callable[[], str]
    register_signals: SignalRegistrar
    serve: ApplicationServer


def register_shutdown_signals(
    request: Callable[[], None],
    *,
    loop: SignalLoop | None = None,
) -> Callable[[], None]:
    """Install callback-only SIGINT/SIGTERM handling and return reverse cleanup."""
    selected = asyncio.get_running_loop() if loop is None else loop
    installed = (signal.SIGINT, signal.SIGTERM)
    for event in installed:
        selected.add_signal_handler(event, request)

    def cleanup() -> None:
        for event in reversed(installed):
            selected.remove_signal_handler(event)

    return cleanup


def _production_bounds() -> EngineBounds:
    """Apply every accepted store bound explicitly rather than inheriting driver defaults."""
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


def _database_settings(
    environment: Mapping[str, str],
    deploy: Path,
    host: str,
    /,
) -> DatabaseSettings:
    """Adapt the store's keyword-only host without reconstructing its trust boundary."""
    return database_settings(environment, deploy, host=host)


def _wall_clock() -> datetime:
    """Read one aware UTC wall instant at the explicit runtime boundary."""
    return datetime.now(tz=UTC)


def _elapsed_clock() -> timedelta:
    """Read one monotonic duration at the explicit runtime boundary."""
    return timedelta(seconds=time.monotonic())


def _identifier() -> str:
    """Mint one opaque application identity at the explicit runtime boundary."""
    return uuid4().hex


def _router(session: CommandGatewaySession) -> DeliveryRouter:
    """Expose only role-authorized publications over the one owned session."""
    return DeliveryRouter(
        Principal.COMMAND_GATEWAY,
        PublicationPorts(
            direct=session.direct_publisher,
            guaranteed=session.publisher,
            responder=GuaranteedReplyResponder(session.publisher),
        ),
    )


async def run(runtime: CommandGatewayRuntime) -> ServiceExit:
    """Build once, serve continuously, and release every resource in reverse order."""
    role = Principal.COMMAND_GATEWAY
    shutdown = ShutdownRequest()
    async with AsyncExitStack() as resources:
        remove_signals = runtime.register_signals(shutdown.request)
        resources.callback(remove_signals)
        endpoint = broker_endpoint(runtime.environment)
        broker_secret = runtime.broker_credential(runtime.deploy, role)
        target = runtime.database_settings(
            runtime.environment,
            runtime.deploy,
            runtime.database_host,
        )
        engine = runtime.create_engine(target, runtime.bounds)
        resources.push_async_callback(close, engine, runtime.bounds.shutdown_grace_seconds)
        sessions = runtime.create_sessions(engine)
        store = runtime.compose_store(sessions, runtime.observed_at)
        session = runtime.open_broker(
            endpoint,
            role,
            broker_secret,
            runtime.bindings,
        )
        resources.callback(session.close)
        application = GatewayApplication(
            store=store,
            router=_router(session),
            stamps=runtime.stamps,
            authority_clock=runtime.authority_clock,
            observed_at=runtime.observed_at,
        )
        return await runtime.serve(session, application, shutdown.running)


def default_runtime(
    environment: Mapping[str, str] | None = None,
) -> CommandGatewayRuntime:
    """Return the concrete container runtime without opening a connection or reading a secret."""
    selected = os.environ if environment is None else environment
    deploy = Path(selected.get(DEPLOY_DIRECTORY_SETTING, DEFAULT_DEPLOY_DIRECTORY))
    runtime_epoch = _identifier()
    stamps = CountingStamps(
        clock=_wall_clock,
        identifiers=_identifier,
        producer_id=runtime_epoch,
    )
    return CommandGatewayRuntime(
        environment=selected,
        deploy=deploy,
        database_host=CONTAINER_HOST,
        bounds=_production_bounds(),
        broker_credential=read_credential,
        database_settings=_database_settings,
        create_engine=cast("EngineFactory", create_engine),
        create_sessions=cast("SessionFactoryBuilder", create_session_factory),
        compose_store=compose_application_store,
        open_broker=open_command_gateway_session,
        bindings=gateway_bindings(),
        stamps=stamps,
        authority_clock=lambda: AuthorizationClock(
            ClockReading(_wall_clock(), _elapsed_clock()),
            runtime_epoch,
        ),
        observed_at=lambda: format_instant(_wall_clock()),
        register_signals=register_shutdown_signals,
        serve=serve_application,
    )


def main(runtime: CommandGatewayRuntime | None = None) -> int:
    """Run the concrete service and return nonzero after broker recovery exhaustion."""
    resolved = default_runtime() if runtime is None else runtime
    return int(asyncio.run(run(resolved)))
