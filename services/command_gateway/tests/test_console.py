"""Concrete command-gateway broker/store composition and process lifecycle."""

from __future__ import annotations

import asyncio
import os
import signal
import tomllib
import unittest
from collections.abc import Callable, Mapping
from datetime import UTC
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aerial_rescue_broker.deployment import read_credential
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    BrokerLifecycle,
    CommandGatewayBindings,
    CommandGatewaySession,
    open_command_gateway_session,
)
from aerial_rescue_broker.routing import DeliveryRouter
from aerial_rescue_command_gateway.console import (
    DEPLOY_DIRECTORY_SETTING,
    ApplicationServer,
    BrokerOpener,
    CommandGatewayRuntime,
    DatabaseSettingsReader,
    EngineFactory,
    EnginePort,
    SessionFactoryBuilder,
    ShutdownRequest,
    SignalRegistrar,
    StoreComposer,
    _router,
    default_runtime,
    main,
    register_shutdown_signals,
    run,
)
from aerial_rescue_command_gateway.service import (
    ApplicationSessionPort,
    CountingStamps,
    GatewayApplication,
    ServiceExit,
    gateway_bindings,
    serve_application,
)
from aerial_rescue_command_gateway.store_adapter import ApplicationStore, compose_application_store
from aerial_rescue_domain.principals import Principal
from aerial_rescue_store.bounds import SHUTDOWN_GRACE_SECONDS, EngineBounds
from aerial_rescue_store.engine import create_engine
from aerial_rescue_store.session import StoreSessionFactory, create_session_factory
from aerial_rescue_store.settings import CONTAINER_HOST, DatabaseSettings


class _Engine:
    """A lazy fake pool which records its one disposal."""

    def __init__(self, order: list[str]) -> None:
        """Retain the shared lifecycle order."""
        self._order = order

    async def dispose(self) -> None:
        """Record bounded pool disposal."""
        self._order.append("engine-dispose")


class _Session:
    """A mixed broker session whose serving is replaced by an injected fake."""

    def __init__(self, order: list[str]) -> None:
        """Start connected and retain reverse-shutdown evidence."""
        self.readiness = BrokerLifecycle()
        self.readiness.connected()
        self.publisher = object()
        self.direct_publisher = object()
        self.receiver_names: tuple[str, ...] = ()
        self.opened: tuple[object, ...] | None = None
        self._order = order

    def close(self) -> None:
        """Record endpoint and service shutdown as one owned operation."""
        self._order.append("session-close")


class _SignalLoop:
    """Record installed callbacks without mutating process-global signal state."""

    def __init__(self) -> None:
        """Start with no callbacks or removals."""
        self.handlers: dict[signal.Signals, Callable[[], None]] = {}
        self.removed: list[signal.Signals] = []

    def add_signal_handler(
        self,
        event: signal.Signals,
        callback: Callable[[], None],
    ) -> None:
        """Retain one callback by signal."""
        self.handlers[event] = callback

    def remove_signal_handler(self, event: signal.Signals) -> bool:
        """Record deterministic reverse cleanup."""
        self.removed.append(event)
        return True


class _Boundaries:
    """A complete inert dependency graph with one shared lifecycle-order trace."""

    def __init__(
        self,
        order: list[str],
        outcome: ServiceExit,
        failure: BaseException | None,
    ) -> None:
        """Construct one fake engine and broker session without external effects."""
        self.order = order
        self.outcome = outcome
        self.failure = failure
        self.engine = _Engine(order)
        self.session = _Session(order)

    def register(self, _request: Callable[[], None]) -> Callable[[], None]:
        """Record registration and return the exact cleanup callback."""
        self.order.append("signals-register")
        return lambda: self.order.append("signals-remove")

    def database(
        self,
        _environment: Mapping[str, str],
        _deploy: Path,
        _host: str,
    ) -> DatabaseSettings:
        """Return one credential-separated fake database target."""
        self.order.append("database-settings")
        return DatabaseSettings("postgres", 5432, "app", "aerial", "not-a-secret")

    def engine_factory(
        self,
        _settings: DatabaseSettings,
        _bounds: EngineBounds,
    ) -> EnginePort:
        """Return the one fake lazy engine."""
        self.order.append("engine-create")
        return cast("EnginePort", self.engine)

    def sessions(self, _engine: EnginePort) -> StoreSessionFactory:
        """Return one inert store session-factory token."""
        self.order.append("sessions-create")
        return cast("StoreSessionFactory", object())

    def store(
        self,
        _sessions: StoreSessionFactory,
        _observed_at: Callable[[], str],
    ) -> ApplicationStore:
        """Return one inert composed store token."""
        self.order.append("store-compose")
        return cast("ApplicationStore", object())

    def broker(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        bindings: CommandGatewayBindings,
    ) -> object:
        """Return the one fake mixed broker session."""
        self.order.append("broker-open")
        self.session.opened = (endpoint, role, credential, bindings)
        return self.session

    async def serve(
        self,
        owned_session: ApplicationSessionPort,
        application: GatewayApplication,
        running: Callable[[], bool],
    ) -> ServiceExit:
        """Return the configured terminal result or injected cancellation."""
        self.order.append("serve")
        assert id(owned_session) == id(self.session)
        assert isinstance(application, GatewayApplication)
        assert running()
        if self.failure is not None:
            raise self.failure
        return self.outcome


def _runtime(
    order: list[str],
    *,
    outcome: ServiceExit = ServiceExit.STOPPED,
    failure: BaseException | None = None,
) -> CommandGatewayRuntime:
    """Return a complete dependency graph that opens no socket or database connection."""
    boundaries = _Boundaries(order, outcome, failure)
    defaults = default_runtime()

    return CommandGatewayRuntime(
        environment={
            "SOLACE_BROKER_URL": "tcps://broker:55443",
            "SOLACE_BROKER_VPN": "default",
            "TRUST_STORE": "/certs",
        },
        deploy=Path("deploy"),
        database_host="postgres",
        bounds=defaults.bounds,
        broker_credential=lambda _deploy, _role: "broker-credential",
        database_settings=cast("DatabaseSettingsReader", boundaries.database),
        create_engine=cast("EngineFactory", boundaries.engine_factory),
        create_sessions=cast("SessionFactoryBuilder", boundaries.sessions),
        compose_store=cast("StoreComposer", boundaries.store),
        open_broker=cast("BrokerOpener", boundaries.broker),
        bindings=gateway_bindings(),
        stamps=defaults.stamps,
        authority_clock=defaults.authority_clock,
        observed_at=lambda: "2026-08-25T12:00:00.000Z",
        register_signals=cast("SignalRegistrar", boundaries.register),
        serve=cast("ApplicationServer", boundaries.serve),
    )


class SignalTests(unittest.TestCase):
    def test_sigint_and_sigterm_request_stop_and_are_removed_in_reverse_order(self) -> None:
        # Arrange
        request = ShutdownRequest()
        loop = _SignalLoop()

        # Act
        cleanup = register_shutdown_signals(request.request, loop=loop)
        loop.handlers[signal.SIGTERM]()
        cleanup()

        # Assert
        self.assertEqual(
            (
                False,
                {signal.SIGINT, signal.SIGTERM},
                [signal.SIGTERM, signal.SIGINT],
            ),
            (request.running(), set(loop.handlers), loop.removed),
        )


class RouterTests(unittest.TestCase):
    def test_router_binds_each_owned_publisher_to_its_exact_capability(self) -> None:
        # Arrange
        session = _Session([])
        responder = object()
        ports = object()
        expected = object()
        response_adapter = patch(
            "aerial_rescue_command_gateway.console.GuaranteedReplyResponder",
            return_value=responder,
        )
        publication_ports = patch(
            "aerial_rescue_command_gateway.console.PublicationPorts",
            return_value=ports,
        )
        delivery_router = patch(
            "aerial_rescue_command_gateway.console.DeliveryRouter",
            return_value=expected,
        )

        # Act
        with (
            response_adapter as build_responder,
            publication_ports as build_ports,
            delivery_router as build_router,
        ):
            actual = _router(cast("CommandGatewaySession", session))

        # Assert
        self.assertEqual(expected, actual)
        build_responder.assert_called_once_with(session.publisher)
        build_ports.assert_called_once_with(
            direct=session.direct_publisher,
            guaranteed=session.publisher,
            responder=responder,
        )
        build_router.assert_called_once_with(Principal.COMMAND_GATEWAY, ports)


class CompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_forwards_every_exact_runtime_boundary_before_reverse_cleanup(self) -> None:
        # Arrange
        order: list[str] = []
        environment = {
            "SOLACE_BROKER_URL": "tcps://broker:55443",
            "SOLACE_BROKER_VPN": "default",
            "TRUST_STORE": "/certs",
        }
        deploy = Path("deploy")
        defaults = default_runtime({})
        endpoint = BrokerEndpoint("tcps://broker:55443", "default", "/certs")
        target = DatabaseSettings("postgres", 5432, "app", "aerial", "not-a-secret")
        engine = cast("EnginePort", _Engine(order))
        sessions = cast("StoreSessionFactory", object())
        store = cast("ApplicationStore", object())
        session = _Session(order)

        def observed_at() -> str:
            """Return the exact instant passed to every composed store port."""
            return "2026-08-25T12:00:00.000Z"

        register = Mock(return_value=Mock())
        credential = Mock(return_value="broker-credential")
        database = Mock(return_value=target)
        engine_factory = Mock(return_value=engine)
        session_factory = Mock(return_value=sessions)
        store_composer = Mock(return_value=store)
        broker = Mock(return_value=session)
        server = AsyncMock(return_value=ServiceExit.STOPPED)
        router = object()
        runtime = CommandGatewayRuntime(
            environment=environment,
            deploy=deploy,
            database_host="postgres",
            bounds=defaults.bounds,
            broker_credential=credential,
            database_settings=database,
            create_engine=cast("EngineFactory", engine_factory),
            create_sessions=cast("SessionFactoryBuilder", session_factory),
            compose_store=cast("StoreComposer", store_composer),
            open_broker=cast("BrokerOpener", broker),
            bindings=gateway_bindings(),
            stamps=defaults.stamps,
            authority_clock=defaults.authority_clock,
            observed_at=observed_at,
            register_signals=cast("SignalRegistrar", register),
            serve=cast("ApplicationServer", server),
        )
        endpoint_parser = patch(
            "aerial_rescue_command_gateway.console.broker_endpoint",
            return_value=endpoint,
        )
        router_builder = patch(
            "aerial_rescue_command_gateway.console._router",
            return_value=router,
        )
        closer = patch(
            "aerial_rescue_command_gateway.console.close",
            new_callable=AsyncMock,
        )

        # Act
        with (
            endpoint_parser as parse_endpoint,
            router_builder as build_router,
            closer as close_engine,
        ):
            outcome = await run(runtime)

        # Assert
        signal_callback = register.call_args_list[0].args[0]
        served_session, application, running = server.await_args_list[0].args
        self.assertEqual(
            (
                ServiceExit.STOPPED,
                (environment,),
                (deploy, Principal.COMMAND_GATEWAY),
                (environment, deploy, "postgres"),
                (target, defaults.bounds),
                (engine,),
                (sessions, observed_at),
                (
                    endpoint,
                    Principal.COMMAND_GATEWAY,
                    "broker-credential",
                    runtime.bindings,
                ),
                (session,),
                GatewayApplication(
                    store=store,
                    router=cast("DeliveryRouter", router),
                    stamps=defaults.stamps,
                    authority_clock=defaults.authority_clock,
                    observed_at=observed_at,
                ),
                True,
                True,
                (engine, defaults.bounds.shutdown_grace_seconds),
                1,
                ["session-close"],
            ),
            (
                outcome,
                parse_endpoint.call_args_list[0].args,
                credential.call_args_list[0].args,
                database.call_args_list[0].args,
                engine_factory.call_args_list[0].args,
                session_factory.call_args_list[0].args,
                store_composer.call_args_list[0].args,
                broker.call_args_list[0].args,
                build_router.call_args_list[0].args,
                application,
                served_session is session,
                callable(signal_callback) and running(),
                close_engine.await_args_list[0].args,
                register.return_value.call_count,
                order,
            ),
        )

    async def test_run_builds_one_store_and_broker_graph_then_closes_in_reverse_order(self) -> None:
        # Arrange
        order: list[str] = []
        runtime = _runtime(order)

        # Act
        outcome = await run(runtime)

        # Assert
        self.assertEqual(
            (
                ServiceExit.STOPPED,
                [
                    "signals-register",
                    "database-settings",
                    "engine-create",
                    "sessions-create",
                    "store-compose",
                    "broker-open",
                    "serve",
                    "session-close",
                    "engine-dispose",
                    "signals-remove",
                ],
            ),
            (outcome, order),
        )

    async def test_cancellation_propagates_after_broker_and_engine_cleanup(self) -> None:
        # Arrange
        order: list[str] = []
        runtime = _runtime(order, failure=asyncio.CancelledError())

        # Act
        with pytest.raises(asyncio.CancelledError):
            await run(runtime)

        # Assert
        self.assertEqual(
            ["session-close", "engine-dispose", "signals-remove"],
            order[-3:],
        )

    async def test_broker_exhaustion_is_returned_after_clean_shutdown(self) -> None:
        # Arrange
        order: list[str] = []
        runtime = _runtime(order, outcome=ServiceExit.BROKER_EXHAUSTED)

        # Act
        outcome = await run(runtime)

        # Assert
        self.assertEqual(
            (ServiceExit.BROKER_EXHAUSTED, "session-close", "engine-dispose"),
            (outcome, order[-3], order[-2]),
        )


class DefaultRuntimeTests(unittest.TestCase):
    def test_console_script_selects_the_concrete_broker_store_root(self) -> None:
        # Arrange
        manifest = Path(__file__).parents[1] / "pyproject.toml"

        # Act
        document = tomllib.loads(manifest.read_text(encoding="utf-8"))

        # Assert
        self.assertEqual(
            "aerial_rescue_command_gateway.console:main",
            document["project"]["scripts"]["aerial-rescue-command-gateway"],
        )

    def test_default_runtime_uses_the_owned_mixed_session_and_sqlalchemy_factories(self) -> None:
        # Arrange
        environment = os.environ

        # Act
        runtime = default_runtime()

        # Assert
        self.assertEqual(
            (
                environment,
                CONTAINER_HOST,
                read_credential,
                open_command_gateway_session,
                create_engine,
                create_session_factory,
                compose_application_store,
                register_shutdown_signals,
                serve_application,
                SHUTDOWN_GRACE_SECONDS,
            ),
            (
                runtime.environment,
                runtime.database_host,
                runtime.broker_credential,
                runtime.open_broker,
                runtime.create_engine,
                runtime.create_sessions,
                runtime.compose_store,
                runtime.register_signals,
                runtime.serve,
                runtime.bounds.shutdown_grace_seconds,
            ),
        )

    def test_default_deploy_directory_honours_the_explicit_runtime_input(self) -> None:
        # Arrange
        environment = {DEPLOY_DIRECTORY_SETTING: "/run/aerial-rescue"}

        # Act
        runtime = default_runtime(environment)

        # Assert
        self.assertEqual(Path("/run/aerial-rescue"), runtime.deploy)

    def test_default_database_reader_delegates_to_the_store_settings_boundary(self) -> None:
        # Arrange
        runtime = default_runtime({})
        expected = DatabaseSettings("postgres", 5432, "app", "aerial", "not-a-secret")

        # Act
        with patch(
            "aerial_rescue_command_gateway.console.database_settings",
            return_value=expected,
        ) as reader:
            target = runtime.database_settings({}, Path("deploy"), "database-host")

        # Assert
        self.assertEqual(
            (expected, ({}, Path("deploy")), "database-host"),
            (target, reader.call_args.args, reader.call_args.kwargs["host"]),
        )

    def test_default_authority_clock_uses_aware_wall_and_monotonic_readings(self) -> None:
        # Arrange
        runtime = default_runtime({})

        # Act
        clock = runtime.authority_clock()
        observed_at = runtime.observed_at()
        stamps = runtime.stamps

        # Assert
        assert isinstance(stamps, CountingStamps)
        self.assertEqual(
            (UTC, True, True, clock.runtime_epoch, True),
            (
                clock.reading.wall.tzinfo,
                clock.reading.monotonic.total_seconds() >= 0,
                bool(clock.runtime_epoch),
                stamps.producer_id,
                observed_at.endswith("Z"),
            ),
        )

    def test_console_returns_the_nonzero_exhaustion_status(self) -> None:
        # Arrange
        order: list[str] = []
        runtime = _runtime(order, outcome=ServiceExit.BROKER_EXHAUSTED)

        # Act
        status = main(runtime)

        # Assert
        self.assertEqual(1, status)

    def test_default_bindings_are_derived_from_the_total_role_table(self) -> None:
        # Arrange
        runtime = default_runtime()

        # Act
        bindings = runtime.bindings

        # Assert
        self.assertEqual(gateway_bindings(), bindings)


if __name__ == "__main__":
    unittest.main()
