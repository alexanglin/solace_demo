"""Concrete Evidence Service process composition and bounded shutdown."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    BrokerLifecycle,
    GuaranteedMessage,
    GuaranteedProcessingBindings,
)
from aerial_rescue_domain.principals import Principal
from aerial_rescue_evidence_service.console import (
    DEPLOY_DIRECTORY_SETTING,
    SCHEMA_DIRECTORY_SETTING,
    Runtime,
    SettingsError,
    SettingsRefusal,
    SignalScope,
    StoreResources,
    _directory,
    _recovery_pause,
    _shutdown,
    broker_endpoint,
    compose_store,
    default_runtime,
    main,
    process_signals,
    production_bounds,
    run,
)
from aerial_rescue_evidence_service.runtime import ServeReport
from aerial_rescue_evidence_service.store_adapter import (
    StoreEvidenceUnitOfWork,
    StoreSourceUnitOfWork,
)
from aerial_rescue_store.bounds import EngineBounds
from aerial_rescue_store.processing.evidence import (
    EvidenceApplicationOutbox,
    EvidenceSequenceReader,
)
from aerial_rescue_store.settings import DatabaseSettings

ENVIRONMENT = {
    "SOLACE_BROKER_URL": "tcps://broker:55443",
    "SOLACE_BROKER_VPN": "default",
    "TRUST_STORE": "/etc/aerial-rescue/certs",
}
DATABASE = DatabaseSettings("postgres", 5432, "aerial", "missions", "database-credential")
BOUNDS = EngineBounds(5, 0, 2, 5, 0, 5_000, 2_000, 15_000, 15)


def _ticks(count: int) -> Callable[[], bool]:
    """Return a bounded process-running predicate."""
    remaining = iter(range(count))
    return lambda: next(remaining, None) is not None


class _Schemas:
    """Accept local runtime schema execution."""

    def validate(self, _schema_id: str, _payload: Mapping[str, object], /) -> None:
        """Accept one payload without external I/O."""


class _Publisher:
    """One confirmed Guaranteed publication capability."""

    def publish(
        self,
        _topic: str,
        _payload: bytes,
        _properties: Mapping[str, object],
        /,
    ) -> None:
        """Confirm without retaining bytes."""


@dataclass
class _Session:
    """One idle mixed Guaranteed broker graph."""

    events: list[str]
    receiver_names: tuple[str, ...]
    publisher: _Publisher = field(default_factory=_Publisher)
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def receive_guaranteed(
        self,
        _receiver_name: str,
        _timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return one idle receive window."""
        return None

    def rebind_complete(self) -> None:
        """Mark application recovery complete."""
        self.readiness.mark_ready()

    def close(self) -> None:
        """Record broker cleanup."""
        self.events.append("broker-close")
        self.readiness.closed()


@dataclass
class _Engine:
    """Record store pool disposal."""

    events: list[str]

    async def dispose(self) -> None:
        """Record store cleanup."""
        self.events.append("store-close")


class _Outbox:
    """Expose an empty recovered application outbox."""

    async def pending(self, _producer: str) -> tuple[object, ...]:
        """Return no staged row."""
        return ()

    async def reconciliation(self, _producer: str) -> tuple[object, ...]:
        """Return no ambiguous row."""
        return ()

    async def record(self, _identity: object, _event: object, _instant: str | None) -> None:
        """No empty batch has an outcome."""


@dataclass
class _Sequence:
    """Recover one durable producer starting sequence."""

    calls: int = 0

    async def starting_sequence(self) -> int:
        """Return after the last committed decision/audit pair."""
        self.calls += 1
        return 20


@dataclass
class _Boundaries:
    """Record every concrete composition boundary."""

    events: list[str] = field(default_factory=list)
    schemas: list[Path] = field(default_factory=list)
    databases: list[tuple[Mapping[str, str], Path, str]] = field(default_factory=list)
    credentials: list[tuple[Path, Principal]] = field(default_factory=list)
    broker: list[tuple[BrokerEndpoint, Principal, str, GuaranteedProcessingBindings]] = field(
        default_factory=list
    )
    sequence: _Sequence = field(default_factory=_Sequence)

    def load_schemas(self, directory: Path) -> PayloadSchemaExecutor:
        """Record the offline schema directory."""
        self.schemas.append(directory)
        return _Schemas()

    def database(
        self,
        environment: Mapping[str, str],
        deploy: Path,
        *,
        host: str,
    ) -> DatabaseSettings:
        """Record the secret-resolving database target."""
        self.databases.append((environment, deploy, host))
        return DATABASE

    def credential(self, deploy: Path, role: Principal) -> str:
        """Record the one least-privilege broker credential read."""
        self.credentials.append((deploy, role))
        return "evidence-credential"

    def store(
        self,
        settings: DatabaseSettings,
        bounds: EngineBounds,
        observed_at: Callable[[], str],
    ) -> StoreResources:
        """Return typed fakes over one owned engine."""
        del observed_at
        assert (settings, bounds) == (DATABASE, BOUNDS)
        return StoreResources(
            _Engine(self.events),
            cast("StoreEvidenceUnitOfWork", object()),
            cast("StoreSourceUnitOfWork", object()),
            cast("EvidenceApplicationOutbox", _Outbox()),
            cast("EvidenceSequenceReader", self.sequence),
        )

    def open_broker(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        bindings: GuaranteedProcessingBindings,
    ) -> _Session:
        """Record one broker connection and return an idle session."""
        self.broker.append((endpoint, role, credential, bindings))
        session = _Session(self.events, tuple(sorted(bindings.queues)))
        session.readiness.connected()
        return session

    @contextmanager
    def signals(self, _stop: Callable[[], None]) -> Iterator[None]:
        """Record signal-handler lifetime around owned resources."""
        self.events.append("signals-enter")
        try:
            yield
        finally:
            self.events.append("signals-exit")


async def test_live_composition_recovers_sequence_and_closes_broker_before_store() -> None:
    # Arrange
    boundaries = _Boundaries()
    deploy = Path("/run")
    schemas = Path("/app/schemas")
    runtime = Runtime(
        environment=ENVIRONMENT,
        deploy=deploy,
        schema_directory=schemas,
        database_host="postgres",
        bounds=BOUNDS,
        credential=boundaries.credential,
        load_schemas=boundaries.load_schemas,
        database=boundaries.database,
        store=boundaries.store,
        open_broker=boundaries.open_broker,
        clock=lambda: datetime(2026, 8, 25, 12, tzinfo=UTC),
        identifiers=lambda: "1" * 32,
        observed_at=lambda: "2026-08-25T12:00:00.000Z",
        running=_ticks(1),
        signals=cast("SignalScope", boundaries.signals),
        pause=lambda: None,
    )

    # Act
    report = await run(runtime)

    # Assert
    endpoint, role, credential, bindings = boundaries.broker[0]
    assert (
        report.exit_status,
        boundaries.schemas,
        boundaries.databases,
        boundaries.credentials,
        endpoint,
        role,
        credential,
        len(bindings.queues),
        boundaries.sequence.calls,
        boundaries.events,
    ) == (
        0,
        [schemas],
        [(ENVIRONMENT, deploy, "postgres")],
        [(deploy, Principal.EVIDENCE_SERVICE)],
        BrokerEndpoint("tcps://broker:55443", "default", "/etc/aerial-rescue/certs"),
        Principal.EVIDENCE_SERVICE,
        "evidence-credential",
        2,
        1,
        ["signals-enter", "broker-close", "store-close", "signals-exit"],
    )


def test_settings_bounds_and_default_runtime_resolve_without_external_io() -> None:
    # Arrange
    missing = {"SOLACE_BROKER_URL": "tcps://broker:55443"}
    environment = {
        DEPLOY_DIRECTORY_SETTING: "/run",
        SCHEMA_DIRECTORY_SETTING: "/app/schemas",
    }

    # Act
    endpoint = broker_endpoint(ENVIRONMENT)
    bounds = production_bounds()
    with pytest.raises(SettingsError) as captured:
        broker_endpoint(missing)
    with patch.dict("aerial_rescue_evidence_service.console.os.environ", environment, clear=True):
        runtime = default_runtime()

    # Assert
    assert (
        endpoint,
        bounds,
        captured.value.refusal,
        captured.value.value,
        runtime.deploy,
        runtime.schema_directory,
        runtime.database_host,
    ) == (
        BrokerEndpoint("tcps://broker:55443", "default", "/etc/aerial-rescue/certs"),
        BOUNDS,
        SettingsRefusal.MISSING_SETTING,
        "SOLACE_BROKER_VPN",
        Path("/run"),
        Path("/app/schemas"),
        "postgres",
    )


def test_directory_uses_default_only_when_absent_and_refuses_a_blank_override() -> None:
    # Arrange
    name = "TEST_DIRECTORY"

    # Act
    defaulted = _directory({}, name, "/default")
    with pytest.raises(SettingsError) as captured:
        _directory({name: "  "}, name, "/default")

    # Assert
    assert (defaulted, captured.value.refusal, captured.value.value) == (
        Path("/default"),
        SettingsRefusal.MISSING_SETTING,
        name,
    )


def test_store_composition_is_lazy_and_contains_only_sqlalchemy_adapters() -> None:
    # Arrange
    engine = _Engine([])
    sessions = object()

    # Act
    with (
        patch("aerial_rescue_evidence_service.console.create_engine", return_value=engine),
        patch(
            "aerial_rescue_evidence_service.console.create_session_factory",
            return_value=sessions,
        ),
    ):
        store = compose_store(DATABASE, BOUNDS, lambda: "2026-08-25T12:00:00.000Z")

    # Assert
    assert (
        store.engine,
        type(store.proposal).__name__,
        type(store.source).__name__,
        type(store.outbox).__name__,
        type(store.sequence).__name__,
    ) == (
        engine,
        "StoreEvidenceUnitOfWork",
        "StoreSourceUnitOfWork",
        "EvidenceApplicationOutbox",
        "EvidenceSequenceReader",
    )


def test_signal_scope_requests_stop_and_restores_both_prior_handlers() -> None:
    # Arrange
    stopped: list[str] = []
    installed: list[tuple[object, object]] = []

    def remember(kind: object, handler: object) -> None:
        installed.append((kind, handler))

    # Act
    with (
        patch("aerial_rescue_evidence_service.console.signal.getsignal", return_value="prior"),
        patch("aerial_rescue_evidence_service.console.signal.signal", side_effect=remember),
        process_signals(lambda: stopped.append("stop")),
    ):
        callback = cast("Callable[[int, object], None]", installed[0][1])
        callback(15, None)

    # Assert
    assert (
        stopped,
        len(installed),
        tuple(item[1] for item in installed[-2:]),
    ) == (["stop"], 4, ("prior", "prior"))


async def test_shutdown_continues_store_disposal_and_preserves_first_broker_failure() -> None:
    # Arrange
    events: list[str] = []
    failure = RuntimeError("injected broker close failure")
    session = _Session(events, ())

    def fail_close() -> None:
        events.append("broker-close")
        raise failure

    store = StoreResources(
        _Engine(events),
        cast("StoreEvidenceUnitOfWork", object()),
        cast("StoreSourceUnitOfWork", object()),
        cast("EvidenceApplicationOutbox", _Outbox()),
        cast("EvidenceSequenceReader", _Sequence()),
    )

    # Act
    with (
        patch.object(session, "close", side_effect=fail_close),
        pytest.raises(RuntimeError) as captured,
    ):
        await _shutdown(session, store, 15)

    # Assert
    assert (captured.value, events) == (failure, ["broker-close", "store-close"])


async def test_shutdown_without_a_session_preserves_a_store_disposal_failure() -> None:
    # Arrange
    failure = RuntimeError("injected store close failure")
    engine = _Engine([])
    store = StoreResources(
        engine,
        cast("StoreEvidenceUnitOfWork", object()),
        cast("StoreSourceUnitOfWork", object()),
        cast("EvidenceApplicationOutbox", _Outbox()),
        cast("EvidenceSequenceReader", _Sequence()),
    )

    # Act
    with (
        patch.object(engine, "dispose", AsyncMock(side_effect=failure)),
        pytest.raises(RuntimeError) as captured,
    ):
        await _shutdown(None, store, 15)

    # Assert
    assert captured.value is failure


async def test_production_pause_uses_the_broker_owned_reconnect_interval() -> None:
    # Arrange
    sleeper = AsyncMock()

    # Act
    with patch("aerial_rescue_evidence_service.console.asyncio.sleep", sleeper):
        await _recovery_pause()
    arguments = sleeper.await_args

    # Assert
    assert arguments is not None
    assert arguments.args == (1.0,)


def test_main_returns_the_async_run_supervisor_status() -> None:
    # Arrange
    runtime = cast("Runtime", object())
    report = ServeReport({}, 7)
    awaitable = cast("Coroutine[object, object, ServeReport]", object())

    # Act
    with (
        patch("aerial_rescue_evidence_service.console.run", new=lambda _runtime: awaitable),
        patch("aerial_rescue_evidence_service.console.asyncio.run", return_value=report) as runner,
    ):
        status = main(runtime)

    # Assert
    assert (status, runner.call_count) == (7, 1)
