"""Concrete live recorder composition with no publication capability.

The runtime validates its local schemas and settings before opening either external
dependency.  It then owns one long-lived receiver-only PubSub+ session and one lazy,
bounded SQLAlchemy engine.  Guaranteed input crosses the store transaction before its
message-bound settlement can be accepted or rejected; Direct input retains its declared
loss boundary.  Replay is deliberately absent from this graph and continues to use the
separate zero-network composition in :mod:`aerial_rescue_recorder.replay`.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Final, Protocol

from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.ingress import PayloadSchemaExecutor, load_runtime_schema_registry
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    BrokerLifecycle,
    GuaranteedMessage,
    InboundMessage,
    ReceiverOnlyBindings,
    open_receiver_only_session,
)
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_domain.principals import Principal
from aerial_rescue_store.bounds import EngineBounds
from aerial_rescue_store.bounds import production_bounds as _production_bounds
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.recording import RecordingTransactions
from aerial_rescue_store.session import Disposable, close, create_session_factory
from aerial_rescue_store.settings import (
    CONTAINER_HOST,
    DatabaseResolver,
    DatabaseSettings,
    database_settings,
)

from aerial_rescue_recorder.broker import RecorderBrokerReceiver
from aerial_rescue_recorder.capture import Recorder
from aerial_rescue_recorder.processing import RecorderRuntime, RefusalPort
from aerial_rescue_recorder.service import ServeReport, recorder_bindings, serve
from aerial_rescue_recorder.store import (
    RecordingTransactionsAdapter,
    StoreRecordingTransactions,
)

BROKER_URL_SETTING: Final = "SOLACE_BROKER_URL"
BROKER_VPN_SETTING: Final = "SOLACE_BROKER_VPN"
TRUST_STORE_SETTING: Final = "TRUST_STORE"
DEPLOY_DIRECTORY_SETTING: Final = "AERIAL_RESCUE_DEPLOY_DIR"
SCHEMA_DIRECTORY_SETTING: Final = "AERIAL_RESCUE_SCHEMA_DIR"
DEFAULT_SCHEMA_DIRECTORY: Final = "schemas"
production_bounds: Final = _production_bounds

RECEIVE_WINDOW_MILLISECONDS: Final = 1_000
"""Bound one fair channel poll so cancellation is observed within one receive window."""


class SettingsRefusal(Enum):
    """Why the recorder cannot resolve its effectful runtime."""

    MISSING_SETTING = "required recorder setting is absent or blank"


class SettingsError(ValueError):
    """A redacted startup refusal retaining only the missing setting name."""

    def __init__(self, refusal: SettingsRefusal, value: str) -> None:
        """Retain no setting value, credential, or path contents."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class ReceiverSession(Protocol):
    """The receiver-only capability returned by the shared broker boundary."""

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return the stable durable receiver names."""

    @property
    def readiness(self) -> BrokerLifecycle:
        """Return the shared transport and application readiness signal."""

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return one bounded Direct delivery or an idle result."""

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return one durable message and its exact settlement capability."""

    def close(self) -> None:
        """Close every receiver and the owned messaging service."""


class SessionOpener(Protocol):
    """Open one long-lived receiver-only PubSub+ graph."""

    def __call__(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        bindings: ReceiverOnlyBindings,
    ) -> ReceiverSession:
        """Connect with the exact recorder role and pre-derived endpoints."""


class StoreComposer(Protocol):
    """Construct the lazy SQLAlchemy resources used by live recording."""

    def __call__(
        self,
        settings: DatabaseSettings,
        bounds: EngineBounds,
        observed_at: Callable[[], str],
    ) -> StoreResources:
        """Return the engine and purpose-specific transaction factories."""


class SignalScope(Protocol):
    """Install and restore process cancellation handlers around owned resources."""

    def __call__(self, stop: Callable[[], None]) -> AbstractContextManager[None]:
        """Return the handler lifetime for SIGINT and SIGTERM."""


@dataclass(frozen=True, slots=True)
class StoreResources:
    """The exact live store capabilities, none of which appear in replay."""

    engine: Disposable
    transactions: StoreRecordingTransactions
    refusals: RefusalPort


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
    observed_at: Callable[[], str]
    running: Callable[[], bool]
    signals: SignalScope


class _StopController:
    """A thread-safe process stop signal shared with synchronous receive polling."""

    def __init__(self) -> None:
        """Start in the running state."""
        self._stopped = Event()

    def stop(self) -> None:
        """Request cancellation without performing work in the signal handler."""
        self._stopped.set()

    def running(self) -> bool:
        """Return false after the first cancellation request."""
        return not self._stopped.is_set()


def broker_endpoint(environment: Mapping[str, str]) -> BrokerEndpoint:
    """Resolve the three non-secret broker settings or fail before broker I/O."""
    values: list[str] = []
    for name in (BROKER_URL_SETTING, BROKER_VPN_SETTING, TRUST_STORE_SETTING):
        value = environment.get(name, "").strip()
        if not value:
            raise SettingsError(SettingsRefusal.MISSING_SETTING, name)
        values.append(value)
    return BrokerEndpoint(values[0], values[1], values[2])


def compose_store(
    settings: DatabaseSettings,
    bounds: EngineBounds,
    observed_at: Callable[[], str],
) -> StoreResources:
    """Build the lazy engine, typed session factory, and purpose-specific store adapters."""
    engine = create_engine(settings, bounds)
    sessions = create_session_factory(engine)
    return StoreResources(
        engine,
        RecordingTransactions(sessions),
        BrokerRefusalRecorder(sessions, observed_at),
    )


@contextmanager
def process_signals(stop: Callable[[], None]) -> Iterator[None]:
    """Install small SIGINT/SIGTERM callbacks and restore the prior process handlers."""
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
    """Resolve one startup path while refusing an explicitly blank override."""
    if name not in environment:
        return Path(default)
    value = environment[name].strip()
    if not value:
        raise SettingsError(SettingsRefusal.MISSING_SETTING, name)
    return Path(value)


def default_runtime() -> Runtime:
    """Return the real container runtime without opening a file, socket, or connection."""
    environment = os.environ
    return Runtime(
        environment=environment,
        deploy=_directory(
            environment,
            DEPLOY_DIRECTORY_SETTING,
            DEFAULT_DEPLOY_DIRECTORY,
        ),
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
        open_broker=open_receiver_only_session,
        observed_at=lambda: format_instant(datetime.now(tz=UTC)),
        running=lambda: True,
        signals=process_signals,
    )


async def _shutdown(
    graph: RecorderRuntime | None,
    session: ReceiverSession | None,
    store: StoreResources,
    grace_seconds: int,
) -> None:
    """Close broker before store, continuing store disposal after a broker refusal."""
    first_failure: Exception | None = None
    receiver = graph if graph is not None else session
    if receiver is not None:
        try:
            receiver.close()
        except Exception as error:  # store disposal must still run
            first_failure = error
    try:
        await close(store.engine, grace_seconds)
    except Exception as error:  # report the first refusal after all owned cleanup
        if first_failure is None:
            first_failure = error
        else:
            raise first_failure from error
    if first_failure is not None:
        raise first_failure


async def run(runtime: Runtime) -> ServeReport:
    """Compose, serve, recover readiness, and close the live receiver-only graph."""
    endpoint = broker_endpoint(runtime.environment)
    schemas = runtime.load_schemas(runtime.schema_directory)
    database = runtime.database(
        runtime.environment,
        runtime.deploy,
        host=runtime.database_host,
    )
    role = Principal.RECORDER
    credential = runtime.credential(runtime.deploy, role)
    bindings = recorder_bindings()
    stop = _StopController()
    with runtime.signals(stop.stop):
        store = runtime.store(database, runtime.bounds, runtime.observed_at)
        session: ReceiverSession | None = None
        graph: RecorderRuntime | None = None
        try:
            session = runtime.open_broker(endpoint, role, credential, bindings)
            receiver = RecorderBrokerReceiver(
                session,
                schemas,
                runtime.observed_at,
                RECEIVE_WINDOW_MILLISECONDS,
            )
            graph = RecorderRuntime(
                receiver,
                Recorder(
                    role.value,
                    RecordingTransactionsAdapter(store.transactions),
                ),
                store.refusals,
            )
            recovery_cycle = len(session.receiver_names) + 1
            return await serve(
                graph,
                session.readiness,
                lambda: stop.running() and runtime.running(),
                recovery_cycle,
            )
        finally:
            await _shutdown(
                graph,
                session,
                store,
                runtime.bounds.shutdown_grace_seconds,
            )


def main(runtime: Runtime | None = None) -> int:
    """Run the concrete live recorder and return the supervisor-facing exit status."""
    selected = default_runtime() if runtime is None else runtime
    return asyncio.run(run(selected)).exit_status
