"""Concrete live Evidence Service composition over PubSub+ and PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Final, Protocol
from uuid import uuid4

from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.ingress import PayloadSchemaExecutor, load_runtime_schema_registry
from aerial_rescue_broker.messaging import (
    RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS,
    BrokerEndpoint,
    BrokerLifecycle,
    GuaranteedMessage,
    GuaranteedProcessingBindings,
    MessagePublisher,
    open_guaranteed_processing_session,
)
from aerial_rescue_broker.routing import DeliveryRouter, PublicationPorts
from aerial_rescue_contracts.instant import format_instant
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
from aerial_rescue_store.processing.evidence import (
    EvidenceApplicationOutbox,
    EvidenceProcessingTransactions,
    EvidenceSequenceReader,
)
from aerial_rescue_store.processing.source_ingress import SourceProcessingTransactions
from aerial_rescue_store.session import Disposable, close, create_session_factory
from aerial_rescue_store.settings import CONTAINER_HOST, DatabaseSettings, database_settings

from aerial_rescue_evidence_service.runtime import (
    BrokerOutboxPublisher,
    CountingStamps,
    DispatchPorts,
    ServeReport,
    ServicePorts,
    evidence_bindings,
    serve,
)
from aerial_rescue_evidence_service.store_adapter import (
    StoreEvidenceUnitOfWork,
    StoreSourceUnitOfWork,
)

BROKER_URL_SETTING: Final = "SOLACE_BROKER_URL"
BROKER_VPN_SETTING: Final = "SOLACE_BROKER_VPN"
TRUST_STORE_SETTING: Final = "TRUST_STORE"
DEPLOY_DIRECTORY_SETTING: Final = "AERIAL_RESCUE_DEPLOY_DIR"
SCHEMA_DIRECTORY_SETTING: Final = "AERIAL_RESCUE_SCHEMA_DIR"
DEFAULT_SCHEMA_DIRECTORY: Final = "schemas"


class SettingsRefusal(Enum):
    """Why the Evidence Service cannot resolve its effectful runtime."""

    MISSING_SETTING = "required evidence-service setting is absent or blank"


class SettingsError(ValueError):
    """A redacted startup refusal retaining only the setting name."""

    def __init__(self, refusal: SettingsRefusal, value: str) -> None:
        """Retain no setting value, credential, or path contents."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class EvidenceBrokerSession(Protocol):
    """The least-privilege mixed Guaranteed capability graph."""

    @property
    def publisher(self) -> MessagePublisher:
        """Return the confirmed Guaranteed publisher."""

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return stable durable receiver names."""

    @property
    def readiness(self) -> BrokerLifecycle:
        """Return shared transport and application readiness."""

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return one bounded delivery or idle result."""

    def rebind_complete(self) -> None:
        """Mark application recovery complete."""

    def close(self) -> None:
        """Close receivers, publisher, and the one owned connection."""


class SessionOpener(Protocol):
    """Open one long-lived least-privilege PubSub+ graph."""

    def __call__(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        bindings: GuaranteedProcessingBindings,
    ) -> EvidenceBrokerSession:
        """Connect with the Evidence Service role and derived durable queues."""


class DatabaseResolver(Protocol):
    """Resolve one bounded PostgreSQL target without opening a connection."""

    def __call__(
        self,
        environment: Mapping[str, str],
        deploy: Path,
        *,
        host: str,
    ) -> DatabaseSettings:
        """Read the generated database credential for the selected host."""


class StoreComposer(Protocol):
    """Construct lazy SQLAlchemy resources for live evidence processing."""

    def __call__(
        self,
        settings: DatabaseSettings,
        bounds: EngineBounds,
        observed_at: Callable[[], str],
    ) -> StoreResources:
        """Return the engine and purpose-specific transaction adapters."""


class SignalScope(Protocol):
    """Install and restore process cancellation handlers around owned resources."""

    def __call__(self, stop: Callable[[], None]) -> AbstractContextManager[None]:
        """Return the handler lifetime for SIGINT and SIGTERM."""


@dataclass(frozen=True, slots=True)
class StoreResources:
    """The complete SQLAlchemy graph owned by the live Evidence Service."""

    engine: Disposable
    proposal: StoreEvidenceUnitOfWork
    source: StoreSourceUnitOfWork
    outbox: EvidenceApplicationOutbox
    sequence: EvidenceSequenceReader


@dataclass(frozen=True, slots=True)
class Runtime:
    """Every external value and constructor used by the live console root."""

    environment: Mapping[str, str]
    deploy: Path
    schema_directory: Path
    database_host: str
    bounds: EngineBounds
    credential: Callable[[Path, Principal], str]
    load_schemas: Callable[[Path], PayloadSchemaExecutor]
    database: DatabaseResolver
    store: StoreComposer
    open_broker: SessionOpener
    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    observed_at: Callable[[], str]
    running: Callable[[], bool]
    signals: SignalScope
    pause: Callable[[], Awaitable[None] | None]


class _StopController:
    """A thread-safe process stop signal shared with synchronous receive polling."""

    def __init__(self) -> None:
        """Start in the running state."""
        self._stopped = Event()

    def stop(self) -> None:
        """Request cancellation without work in the signal callback."""
        self._stopped.set()

    def running(self) -> bool:
        """Return false after cancellation is requested."""
        return not self._stopped.is_set()


def broker_endpoint(environment: Mapping[str, str]) -> BrokerEndpoint:
    """Resolve the non-secret broker settings before any socket is opened."""
    values: list[str] = []
    for name in (BROKER_URL_SETTING, BROKER_VPN_SETTING, TRUST_STORE_SETTING):
        value = environment.get(name, "").strip()
        if not value:
            raise SettingsError(SettingsRefusal.MISSING_SETTING, name)
        values.append(value)
    return BrokerEndpoint(values[0], values[1], values[2])


def production_bounds() -> EngineBounds:
    """Construct every accepted SQLAlchemy wait from its canonical owner."""
    return EngineBounds(
        pool_size=POOL_SIZE,
        pool_overflow=POOL_OVERFLOW,
        checkout_timeout_seconds=CHECKOUT_TIMEOUT_SECONDS,
        connect_timeout_seconds=CONNECT_TIMEOUT_SECONDS,
        connect_retries=CONNECT_RETRIES,
        statement_timeout_milliseconds=STATEMENT_TIMEOUT_MILLISECONDS,
        lock_timeout_milliseconds=LOCK_TIMEOUT_MILLISECONDS,
        idle_in_transaction_timeout_milliseconds=(IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS),
        shutdown_grace_seconds=SHUTDOWN_GRACE_SECONDS,
    )


def compose_store(
    settings: DatabaseSettings,
    bounds: EngineBounds,
    observed_at: Callable[[], str],
) -> StoreResources:
    """Build the lazy engine and all evidence-specific SQLAlchemy adapters."""
    engine = create_engine(settings, bounds)
    sessions = create_session_factory(engine)
    refusals = BrokerRefusalRecorder(sessions, observed_at)
    return StoreResources(
        engine,
        StoreEvidenceUnitOfWork(EvidenceProcessingTransactions(sessions), refusals),
        StoreSourceUnitOfWork(SourceProcessingTransactions(sessions), refusals),
        EvidenceApplicationOutbox(sessions),
        EvidenceSequenceReader(sessions),
    )


@contextmanager
def process_signals(stop: Callable[[], None]) -> Iterator[None]:
    """Install small SIGINT/SIGTERM callbacks and restore prior handlers."""
    kinds = (signal.SIGINT, signal.SIGTERM)
    previous = tuple((kind, signal.getsignal(kind)) for kind in kinds)

    def request_stop(_number: int, _frame: object) -> None:
        stop()

    for kind in kinds:
        signal.signal(kind, request_stop)
    try:
        yield
    finally:
        for kind, handler in previous:
            signal.signal(kind, handler)


def _directory(environment: Mapping[str, str], name: str, default: str) -> Path:
    """Resolve one path and refuse an explicitly blank override."""
    if name not in environment:
        return Path(default)
    value = environment[name].strip()
    if not value:
        raise SettingsError(SettingsRefusal.MISSING_SETTING, name)
    return Path(value)


async def _recovery_pause() -> None:
    """Wait one broker-owned reconnect interval without adding another parameter."""
    await asyncio.sleep(RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS / 1_000)


def default_runtime() -> Runtime:
    """Return the real container runtime without opening files, sockets, or pools."""
    environment = os.environ
    return Runtime(
        environment=environment,
        deploy=_directory(environment, DEPLOY_DIRECTORY_SETTING, DEFAULT_DEPLOY_DIRECTORY),
        schema_directory=_directory(
            environment,
            SCHEMA_DIRECTORY_SETTING,
            DEFAULT_SCHEMA_DIRECTORY,
        ),
        database_host=CONTAINER_HOST,
        bounds=production_bounds(),
        credential=read_credential,
        load_schemas=load_runtime_schema_registry,
        database=database_settings,
        store=compose_store,
        open_broker=open_guaranteed_processing_session,
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        observed_at=lambda: format_instant(datetime.now(tz=UTC)),
        running=lambda: True,
        signals=process_signals,
        pause=_recovery_pause,
    )


async def _shutdown(
    session: EvidenceBrokerSession | None,
    store: StoreResources,
    grace_seconds: int,
) -> None:
    """Close broker before store, continuing disposal after a broker refusal."""
    first_failure: Exception | None = None
    if session is not None:
        try:
            session.close()
        except Exception as error:  # store disposal must still run
            first_failure = error
    try:
        await close(store.engine, grace_seconds)
    except Exception as error:  # preserve the first refusal after all cleanup
        if first_failure is None:
            first_failure = error
        else:
            raise first_failure from error
    if first_failure is not None:
        raise first_failure


async def run(runtime: Runtime) -> ServeReport:
    """Compose, recover, serve, and close one live Evidence Service graph."""
    endpoint = broker_endpoint(runtime.environment)
    schemas = runtime.load_schemas(runtime.schema_directory)
    database = runtime.database(
        runtime.environment,
        runtime.deploy,
        host=runtime.database_host,
    )
    role = Principal.EVIDENCE_SERVICE
    credential = runtime.credential(runtime.deploy, role)
    bindings = evidence_bindings()
    stop = _StopController()
    with runtime.signals(stop.stop):
        store = runtime.store(database, runtime.bounds, runtime.observed_at)
        session: EvidenceBrokerSession | None = None
        try:
            starting_sequence = await store.sequence.starting_sequence()
            stamps = CountingStamps(runtime.clock, runtime.identifiers, starting_sequence)
            session = runtime.open_broker(endpoint, role, credential, bindings)
            router = DeliveryRouter(role, PublicationPorts(guaranteed=session.publisher))
            publisher = BrokerOutboxPublisher(router, runtime.observed_at)
            return await serve(
                session,
                ServicePorts(
                    dispatch=DispatchPorts(
                        schemas,
                        stamps.next_stamp,
                        store.proposal,
                        store.source,
                    ),
                    outbox=store.outbox,
                    publisher=publisher,
                    running=lambda: stop.running() and runtime.running(),
                    pause=runtime.pause,
                ),
            )
        finally:
            await _shutdown(session, store, runtime.bounds.shutdown_grace_seconds)


def main(runtime: Runtime | None = None) -> int:
    """Run the concrete live Evidence Service and return its supervisor status."""
    selected = default_runtime() if runtime is None else runtime
    return asyncio.run(run(selected)).exit_status
