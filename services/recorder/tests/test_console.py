"""Concrete receiver-only recorder process composition and shutdown."""

from __future__ import annotations

import tomllib
import unittest
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from aerial_rescue_broker.deployment import read_credential
from aerial_rescue_broker.ingress import load_runtime_schema_registry
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    BrokerLifecycle,
    GuaranteedMessage,
    InboundMessage,
    ReceiverOnlyBindings,
    open_receiver_only_session,
)
from aerial_rescue_broker.queues import queues_for
from aerial_rescue_domain.principals import Principal
from aerial_rescue_recorder.console import (
    DEFAULT_SCHEMA_DIRECTORY,
    DEPLOY_DIRECTORY_SETTING,
    SCHEMA_DIRECTORY_SETTING,
    Runtime,
    SettingsError,
    SettingsRefusal,
    StoreResources,
    broker_endpoint,
    compose_store,
    default_runtime,
    main,
    process_signals,
    production_bounds,
    run,
)
from aerial_rescue_recorder.processing import ProcessDecision
from aerial_rescue_store.bounds import EngineBounds
from aerial_rescue_store.processing.broker_refusals import BrokerRefusalRecorder
from aerial_rescue_store.processing.recording import RecordingTransactions
from aerial_rescue_store.settings import DatabaseSettings, database_settings
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

if TYPE_CHECKING:
    from aerial_rescue_broker.ingress import PayloadSchemaExecutor
    from aerial_rescue_store.broker_refusals import (
        BrokerRefusalCandidate,
        BrokerRefusalOutcome,
    )
    from aerial_rescue_store.processing.recording import RecordingTransaction


ENVIRONMENT = {
    "SOLACE_BROKER_URL": "tcps://broker:55443",
    "SOLACE_BROKER_VPN": "default",
    "TRUST_STORE": "/etc/aerial-rescue/certs",
}
DATABASE = DatabaseSettings("postgres", 5432, "aerial", "missions", "database-credential")
BOUNDS = EngineBounds(5, 0, 2, 5, 0, 5_000, 2_000, 15_000, 15)


def _ticks(count: int) -> Callable[[], bool]:
    remaining = iter(range(count))
    return lambda: next(remaining, None) is not None


class _Schemas:
    def validate(self, schema_id: str, payload: Mapping[str, object], /) -> None:
        del schema_id, payload


@dataclass
class _Session:
    events: list[str]
    receiver_names: tuple[str, ...]
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)
    polls: int = 0
    ready_before_close: bool | None = None
    fail_close: bool = False

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        del timeout_milliseconds
        self.polls += 1
        return None

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        del receiver_name, timeout_milliseconds
        self.polls += 1
        return None

    def close(self) -> None:
        self.events.append("broker-close")
        self.ready_before_close = self.readiness.is_ready()
        self.readiness.closed()
        if self.fail_close:
            message = "broker shutdown failed"
            raise RuntimeError(message)


@dataclass
class _Engine:
    events: list[str]
    fail_close: bool = False

    async def dispose(self) -> None:
        self.events.append("store-close")
        if self.fail_close:
            message = "store shutdown failed"
            raise RuntimeError(message)


class _UnusedUnitOfWork:
    async def __aenter__(self) -> RecordingTransaction:
        message = "an idle receiver must not open a database transaction"
        raise AssertionError(message)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback


class _Transactions:
    def open(self) -> AbstractAsyncContextManager[RecordingTransaction]:
        return _UnusedUnitOfWork()


class _Refusals:
    async def record(self, fact: BrokerRefusalCandidate) -> BrokerRefusalOutcome:
        del fact
        message = "an idle receiver must not record a refusal"
        raise AssertionError(message)


@dataclass
class _Boundaries:
    events: list[str] = field(default_factory=list)
    schemas: list[Path] = field(default_factory=list)
    databases: list[tuple[Mapping[str, str], Path, str]] = field(default_factory=list)
    credentials: list[tuple[Path, Principal]] = field(default_factory=list)
    broker: list[tuple[BrokerEndpoint, Principal, str, ReceiverOnlyBindings]] = field(
        default_factory=list
    )
    session: _Session | None = None
    stop_on_enter: bool = False
    exhaust_on_open: bool = False
    fail_broker_close: bool = False
    fail_store_close: bool = False
    fail_open: bool = False
    fail_schema: bool = False

    def load_schemas(self, directory: Path) -> PayloadSchemaExecutor:
        self.schemas.append(directory)
        if self.fail_schema:
            message = "schema registry failed"
            raise RuntimeError(message)
        return _Schemas()

    def database(
        self,
        environment: Mapping[str, str],
        deploy: Path,
        *,
        host: str,
    ) -> DatabaseSettings:
        self.databases.append((environment, deploy, host))
        return DATABASE

    def credential(self, deploy: Path, role: Principal) -> str:
        self.credentials.append((deploy, role))
        return "recorder-credential"

    def store(
        self,
        settings: DatabaseSettings,
        bounds: EngineBounds,
        observed_at: Callable[[], str],
    ) -> StoreResources:
        del observed_at
        if (settings, bounds) != (DATABASE, BOUNDS):
            message = "the store received the wrong bounded target"
            raise AssertionError(message)
        return StoreResources(
            _Engine(self.events, self.fail_store_close),
            _Transactions(),
            _Refusals(),
        )

    def open_broker(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        bindings: ReceiverOnlyBindings,
    ) -> _Session:
        self.broker.append((endpoint, role, credential, bindings))
        if self.fail_open:
            message = "broker open failed"
            raise RuntimeError(message)
        session = _Session(
            self.events,
            tuple(sorted(bindings.queues)),
            fail_close=self.fail_broker_close,
        )
        session.readiness.connected()
        if self.exhaust_on_open:
            session.readiness.exhausted()
        self.session = session
        return session

    @contextmanager
    def signals(self, stop: Callable[[], None]) -> Iterator[None]:
        self.events.append("signals-enter")
        if self.stop_on_enter:
            stop()
        try:
            yield
        finally:
            self.events.append("signals-exit")


def _runtime(boundaries: _Boundaries, ticks: int) -> Runtime:
    return Runtime(
        environment=ENVIRONMENT,
        deploy=Path("/run"),
        schema_directory=Path("/app/schemas"),
        database_host="postgres",
        bounds=BOUNDS,
        credential=boundaries.credential,
        load_schemas=boundaries.load_schemas,
        database=boundaries.database,
        store=boundaries.store,
        open_broker=boundaries.open_broker,
        observed_at=lambda: "2026-08-25T12:00:00.000Z",
        running=_ticks(ticks),
        signals=boundaries.signals,
    )


class RecorderConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_composition_uses_exact_owned_inputs_and_closes_in_reverse_order(
        self,
    ) -> None:
        # Arrange
        boundaries = _Boundaries()
        deploy = Path("/run")
        schemas = Path("/app/schemas")
        runtime = _runtime(boundaries, 1)

        # Act
        report = await run(runtime)

        # Assert
        endpoint, role, credential, bindings = boundaries.broker[0]
        self.assertEqual(
            (
                0,
                [schemas],
                [(ENVIRONMENT, deploy, "postgres")],
                [(deploy, Principal.RECORDER)],
                BrokerEndpoint("tcps://broker:55443", "default", "/etc/aerial-rescue/certs"),
                Principal.RECORDER,
                "recorder-credential",
                len(queues_for(Principal.RECORDER, ())),
                ["signals-enter", "broker-close", "store-close", "signals-exit"],
            ),
            (
                report.exit_status,
                boundaries.schemas,
                boundaries.databases,
                boundaries.credentials,
                endpoint,
                role,
                credential,
                len(bindings.queues),
                boundaries.events,
            ),
        )

    async def test_a_complete_fair_cycle_restores_readiness_before_shutdown(self) -> None:
        # Arrange
        boundaries = _Boundaries()
        cycle = 11

        # Act
        report = await run(_runtime(boundaries, cycle))

        # Assert
        self.assertEqual(
            (cycle, True, {ProcessDecision.IDLE: cycle}, 0),
            (
                boundaries.session.polls if boundaries.session is not None else 0,
                boundaries.session.ready_before_close if boundaries.session is not None else None,
                report.outcomes,
                report.exit_status,
            ),
        )

    async def test_signal_cancellation_stops_intake_and_still_closes_every_resource(self) -> None:
        # Arrange
        boundaries = _Boundaries(stop_on_enter=True)

        # Act
        report = await run(_runtime(boundaries, 100))

        # Assert
        self.assertEqual(
            (0, {}, 0, ["signals-enter", "broker-close", "store-close", "signals-exit"]),
            (
                report.exit_status,
                report.outcomes,
                boundaries.session.polls if boundaries.session is not None else -1,
                boundaries.events,
            ),
        )

    async def test_reconnect_exhaustion_returns_nonzero_without_polling_or_claiming_ready(
        self,
    ) -> None:
        # Arrange
        boundaries = _Boundaries(exhaust_on_open=True)

        # Act
        report = await run(_runtime(boundaries, 100))

        # Assert
        self.assertEqual(
            (1, 0, False),
            (
                report.exit_status,
                boundaries.session.polls if boundaries.session is not None else -1,
                boundaries.session.ready_before_close if boundaries.session is not None else None,
            ),
        )

    async def test_schema_refusal_happens_before_credentials_store_or_broker_are_reached(
        self,
    ) -> None:
        # Arrange
        boundaries = _Boundaries(fail_schema=True)

        # Act
        with pytest.raises(RuntimeError):
            await run(_runtime(boundaries, 1))

        # Assert
        self.assertEqual(
            (1, [], [], [], []),
            (
                len(boundaries.schemas),
                boundaries.databases,
                boundaries.credentials,
                boundaries.broker,
                boundaries.events,
            ),
        )

    async def test_a_broker_open_refusal_still_disposes_the_lazy_store(self) -> None:
        # Arrange
        boundaries = _Boundaries(fail_open=True)

        # Act
        with pytest.raises(RuntimeError):
            await run(_runtime(boundaries, 1))

        # Assert
        self.assertEqual(
            ["signals-enter", "store-close", "signals-exit"],
            boundaries.events,
        )

    async def test_store_disposal_continues_after_receiver_shutdown_refuses(self) -> None:
        # Arrange
        boundaries = _Boundaries(fail_broker_close=True)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await run(_runtime(boundaries, 0))

        # Assert
        self.assertEqual(
            (
                "broker shutdown failed",
                ["signals-enter", "broker-close", "store-close", "signals-exit"],
            ),
            (str(captured.value), boundaries.events),
        )

    async def test_a_store_shutdown_refusal_is_reported_after_the_receiver_closes(self) -> None:
        # Arrange
        boundaries = _Boundaries(fail_store_close=True)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await run(_runtime(boundaries, 0))

        # Assert
        self.assertEqual(
            (
                "store shutdown failed",
                ["signals-enter", "broker-close", "store-close", "signals-exit"],
            ),
            (str(captured.value), boundaries.events),
        )

    async def test_the_first_shutdown_refusal_is_preserved_when_store_disposal_also_refuses(
        self,
    ) -> None:
        # Arrange
        boundaries = _Boundaries(fail_broker_close=True, fail_store_close=True)

        # Act
        with pytest.raises(RuntimeError) as captured:
            await run(_runtime(boundaries, 0))

        # Assert
        self.assertEqual(
            (
                "broker shutdown failed",
                "store shutdown failed",
                ["signals-enter", "broker-close", "store-close", "signals-exit"],
            ),
            (
                str(captured.value),
                str(captured.value.__cause__),
                boundaries.events,
            ),
        )


class RecorderSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_broker_settings_refuse_each_missing_or_blank_member_by_name(self) -> None:
        # Arrange
        cases = []
        for name in ENVIRONMENT:
            cases.append(({key: value for key, value in ENVIRONMENT.items() if key != name}, name))
        cases.append(({**ENVIRONMENT, "TRUST_STORE": "  "}, "TRUST_STORE"))

        # Act
        refusals = []
        for environment, _name in cases:
            with pytest.raises(SettingsError) as captured:
                broker_endpoint(environment)
            refusals.append((captured.value.refusal, captured.value.value))

        # Assert
        self.assertEqual(
            [(SettingsRefusal.MISSING_SETTING, name) for _environment, name in cases],
            refusals,
        )

    async def test_the_concrete_store_composes_lazy_sqlalchemy_and_owned_transactions(self) -> None:
        # Arrange
        settings = DATABASE

        # Act
        resources = compose_store(
            settings,
            BOUNDS,
            lambda: "2026-08-25T12:00:00.000Z",
        )

        # Assert
        engine = cast("AsyncEngine", resources.engine)
        try:
            self.assertEqual(
                (True, True, True, 0),
                (
                    isinstance(engine, AsyncEngine),
                    isinstance(resources.transactions, RecordingTransactions),
                    isinstance(resources.refusals, BrokerRefusalRecorder),
                    cast("QueuePool", engine.sync_engine.pool).checkedout(),
                ),
            )
        finally:
            await engine.dispose()


class DefaultRuntimeTests(unittest.TestCase):
    def test_member_manifest_exposes_the_real_recorder_console(self) -> None:
        # Arrange
        manifest = Path(__file__).parents[1] / "pyproject.toml"

        # Act
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))

        # Assert
        self.assertEqual(
            "aerial_rescue_recorder.console:main",
            document["project"]["scripts"]["aerial-rescue-recorder"],
        )

    def test_default_runtime_uses_container_paths_bounds_and_concrete_owned_adapters(self) -> None:
        # Arrange
        environment = {
            DEPLOY_DIRECTORY_SETTING: "/run",
            SCHEMA_DIRECTORY_SETTING: "/app/schemas",
        }

        # Act
        with patch.dict("os.environ", environment, clear=True):
            runtime = default_runtime()

        # Assert
        self.assertEqual(
            (
                Path("/run"),
                Path("/app/schemas"),
                "postgres",
                production_bounds(),
                True,
            ),
            (
                runtime.deploy,
                runtime.schema_directory,
                runtime.database_host,
                runtime.bounds,
                runtime.running(),
            ),
        )
        self.assertIs(runtime.credential, read_credential)
        self.assertIs(runtime.load_schemas, load_runtime_schema_registry)
        self.assertIs(runtime.database, database_settings)
        self.assertIs(runtime.store, compose_store)
        self.assertIs(runtime.open_broker, open_receiver_only_session)
        self.assertIs(runtime.signals, process_signals)

    def test_missing_directories_use_owned_defaults_and_blank_overrides_refuse(self) -> None:
        # Arrange
        environments: tuple[dict[str, str], ...] = (
            {},
            {DEPLOY_DIRECTORY_SETTING: " "},
            {SCHEMA_DIRECTORY_SETTING: " "},
        )

        # Act
        with patch.dict("os.environ", environments[0], clear=True):
            runtime = default_runtime()
        refusals = []
        for environment in environments[1:]:
            with (
                patch.dict("os.environ", environment, clear=True),
                pytest.raises(SettingsError) as captured,
            ):
                default_runtime()
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            (
                Path("deploy"),
                Path(DEFAULT_SCHEMA_DIRECTORY),
                [SettingsRefusal.MISSING_SETTING, SettingsRefusal.MISSING_SETTING],
            ),
            (runtime.deploy, runtime.schema_directory, refusals),
        )

    def test_process_signal_scope_installs_requests_stop_and_restores_prior_handlers(self) -> None:
        # Arrange
        stopped: list[bool] = []
        installed: list[tuple[object, object]] = []
        previous: dict[object, object] = {number: object() for number in (2, 15)}

        def get_handler(kind: object) -> object:
            return previous[kind]

        def set_handler(kind: object, handler: object) -> object:
            installed.append((kind, handler))
            return object()

        # Act
        with (
            patch("aerial_rescue_recorder.console.signal.getsignal", side_effect=get_handler),
            patch("aerial_rescue_recorder.console.signal.signal", side_effect=set_handler),
            process_signals(lambda: stopped.append(True)),
        ):
            callback = cast("Callable[[int, object], None]", installed[1][1])
            callback(15, None)

        # Assert
        self.assertEqual(
            (
                [True],
                previous[2],
                previous[15],
                4,
            ),
            (stopped, installed[2][1], installed[3][1], len(installed)),
        )

    def test_console_entrypoint_returns_the_live_supervisor_status(self) -> None:
        # Arrange
        boundaries = _Boundaries(exhaust_on_open=True)

        # Act
        status = main(_runtime(boundaries, 1))

        # Assert
        self.assertEqual((1, ["broker-close", "store-close"]), (status, boundaries.events[1:3]))


if __name__ == "__main__":
    unittest.main()
