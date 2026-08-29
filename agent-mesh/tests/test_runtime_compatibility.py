"""Owned TLS, recovery, and process lifecycle around the pinned SAC runtime."""

from __future__ import annotations

import importlib.metadata
import inspect
import signal
import threading
import unittest
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future
from pathlib import Path
from typing import Final, Protocol, Self
from unittest.mock import patch

import pytest
import sam_event_mesh_tool.tools as upstream_event_mesh_tool
import solace_agent_mesh.agent.adk.setup as upstream_agent_setup
import solace_agent_mesh.agent.sac.component as upstream_agent_component
import solace_agent_mesh.common.sac.sam_component_base as upstream_component_base
import solace_ai_connector.common.messaging.solace_messaging as upstream_messaging
import solace_ai_connector.flow.flow as upstream_flow
import solace_ai_connector.main as connector_main
import solace_ai_connector.solace_ai_connector as upstream_connector
from solace.messaging.config.retry_strategy import RetryStrategy

import aerial_rescue_runtime_compat as runtime_compat
import aerial_rescue_runtime_compat.__main__ as runtime_main
from aerial_rescue_runtime_compat import lifecycle as runtime_lifecycle
from aerial_rescue_runtime_compat.lifecycle import (
    EXIT_RUNTIME_FAILURE,
    EXIT_SUCCESS,
    THREAD_SETTLE_SECONDS,
    run_connector,
    terminate_process,
)
from aerial_rescue_runtime_compat.messaging import (
    ACTIVE_RECONNECTION_ATTEMPTS,
    ACTIVE_RECONNECTION_WAIT_MILLISECONDS,
    CONNECTION_ATTEMPT_TIMEOUT_MILLISECONDS,
    INITIAL_CONNECTION_RETRIES,
    KEEP_ALIVE_INTERVAL_MILLISECONDS,
    KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT,
    PER_HOST_CONNECTION_RETRIES,
    BrokerTerminalState,
    MissingTrustStoreError,
    NonTcpsBrokerError,
    harden_builder,
    require_supported_runtime,
)

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]

EXPECTED_SDK_VERSION: Final = "1.11.0"
EXPECTED_SAC_VERSION: Final = "3.3.12"
EXPECTED_SAM_VERSION: Final = "1.28.7"
VENDOR_SDK_PIN: Final = "solace-pubsubplus==1.9.0"
SENSITIVE_FAILURE: Final = "tenant-secret-must-not-escape"
EXPECTED_SETTLE_SECONDS: Final = 15.0
TEST_SETTLE_SECONDS: Final = 1.0
SURVIVING_THREADS: Final = 3


class _InterruptionListener(Protocol):
    def on_service_interrupted(self, event: object) -> None:
        """Receive one terminal service interruption."""


class _FakeService:
    """Record the terminal listener attached to a built messaging service."""

    def __init__(self) -> None:
        self.listeners: list[_InterruptionListener] = []

    def add_service_interruption_listener(self, listener: _InterruptionListener) -> None:
        """Record one interruption listener."""
        self.listeners.append(listener)


class _FakeBuilder:
    """The exact builder seam SAC uses, without broker I/O."""

    def __init__(self, service: _FakeService) -> None:
        self.service = service
        self.properties: dict[str, object] = {}
        self.connection_strategy: object | None = None
        self.reconnection_strategy: object | None = None

    def from_properties(self, properties: dict[str, object]) -> Self:
        """Record the effective service properties."""
        self.properties = dict(properties)
        return self

    def with_connection_retry_strategy(self, strategy: object) -> Self:
        """Record the initial connection strategy."""
        self.connection_strategy = strategy
        return self

    def with_reconnection_retry_strategy(self, strategy: object) -> Self:
        """Record the active-session recovery strategy."""
        self.reconnection_strategy = strategy
        return self

    def build(self, application_id: str | None = None) -> _FakeService:
        """Return the one inert service."""
        del application_id
        return self.service


class _FakeConnector:
    """Record owned run, stop, and cleanup lifecycle ordering."""

    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        startup: Callable[[], None] | None = None,
    ) -> None:
        self.failure = failure
        self.startup = startup
        self.stop_signal = threading.Event()
        self.calls: list[str] = []

    def run(self) -> None:
        """Start or raise one injected runtime failure."""
        self.calls.append("run")
        if self.startup is not None:
            self.startup()
        if self.failure is not None:
            raise self.failure

    def wait_for_flows(self) -> None:
        """Return when the controlled test says the process ended."""
        self.calls.append("wait")

    def stop(self) -> None:
        """Record graceful stop before cleanup."""
        self.calls.append("stop")
        self.stop_signal.set()

    def cleanup(self) -> None:
        """Record final resource cleanup."""
        self.calls.append("cleanup")


class _FakeAsyncComponent:
    """Expose the pinned SAM component's async-initialization seam."""

    def __init__(self, future: Future[object] | None) -> None:
        self._async_init_future = future


class _InvalidAsyncComponent:
    """Expose an incompatible non-future value at the pinned private seam."""

    def __init__(self) -> None:
        self._async_init_future = object()


class _FakeFlow:
    """Expose the pinned Connector flow's nested component groups."""

    def __init__(self, component_groups: Sequence[Sequence[object]]) -> None:
        self.component_groups = component_groups


class _FakeInterpreter:
    """Script the surviving nondaemon thread counts an owned termination step observes."""

    def __init__(self, counts: Sequence[int]) -> None:
        self.counts = list(counts)
        self.waits: list[float] = []
        self.forced: list[int] = []

    def surviving(self) -> int:
        """Return the next scripted count and repeat the last one forever."""
        if len(self.counts) > 1:
            return self.counts.pop(0)
        return self.counts[0]

    def sleep(self, seconds: float) -> None:
        """Record one bounded settle wait instead of blocking the suite."""
        self.waits.append(seconds)

    def force(self, status: int) -> None:
        """Record the status a hard interpreter exit would have carried."""
        self.forced.append(status)


def _retry_values(strategy: object) -> tuple[int, int]:
    """Read the two values exposed by the pinned SDK retry strategy."""
    attributes = vars(strategy)
    return (attributes["_retries"], attributes["_retry_interval"])


class MessagingCompatibilityTests(unittest.TestCase):
    def test_every_sdk_property_and_retry_builder_is_forced_to_the_closed_policy(self) -> None:
        # Arrange
        service = _FakeService()
        stopped: list[bool] = []
        terminal = BrokerTerminalState(on_exhausted=lambda: stopped.append(True))
        source = _FakeBuilder(service)
        builder = harden_builder(source, terminal)
        hostile = {
            "solace.messaging.transport.host": "tcps://broker:55443",
            "solace.messaging.tls.trust-store-path": "/etc/aerial-rescue/certs",
            "solace.messaging.tls.minimum-protocol": "TLSv1.2",
            "solace.messaging.tls.cert-validated": False,
            "solace.messaging.tls.cert-reject-expired": False,
            "solace.messaging.tls.cert-validate-servername": False,
            "solace.messaging.transport.connection-retries": 99,
            "solace.messaging.transport.reconnection-attempts": -1,
        }

        # Act
        built = (
            builder.from_properties(hostile)
            .with_reconnection_retry_strategy(RetryStrategy.forever_retry(9_000))
            .with_connection_retry_strategy(RetryStrategy.forever_retry(9_000))
            .build()
        )
        listener = service.listeners[0]
        listener.on_service_interrupted(object())

        # Assert
        self.assertIs(service, built)
        self.assertEqual(
            "tcps://broker:55443", source.properties["solace.messaging.transport.host"]
        )
        self.assertEqual("TLSv1.3", source.properties["solace.messaging.tls.minimum-protocol"])
        self.assertIs(True, source.properties["solace.messaging.tls.cert-validated"])
        self.assertIs(True, source.properties["solace.messaging.tls.cert-reject-expired"])
        self.assertIs(True, source.properties["solace.messaging.tls.cert-validate-servername"])
        self.assertEqual(
            CONNECTION_ATTEMPT_TIMEOUT_MILLISECONDS,
            source.properties["solace.messaging.transport.connection-attempts-timeout"],
        )
        self.assertEqual(
            INITIAL_CONNECTION_RETRIES,
            source.properties["solace.messaging.transport.connection-retries"],
        )
        self.assertEqual(
            PER_HOST_CONNECTION_RETRIES,
            source.properties["solace.messaging.transport.connection.retries-per-host"],
        )
        self.assertEqual(
            ACTIVE_RECONNECTION_ATTEMPTS,
            source.properties["solace.messaging.transport.reconnection-attempts"],
        )
        self.assertEqual(
            ACTIVE_RECONNECTION_WAIT_MILLISECONDS,
            source.properties["solace.messaging.transport.reconnection-attempts-wait-interval"],
        )
        self.assertEqual(
            KEEP_ALIVE_INTERVAL_MILLISECONDS,
            source.properties["solace.messaging.transport.keep-alive-interval"],
        )
        self.assertEqual(
            KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT,
            source.properties["solace.messaging.transport.keep-alive-without-response-limit"],
        )
        self.assertEqual(
            (INITIAL_CONNECTION_RETRIES, 1_000), _retry_values(source.connection_strategy)
        )
        self.assertEqual(
            (ACTIVE_RECONNECTION_ATTEMPTS, ACTIVE_RECONNECTION_WAIT_MILLISECONDS),
            _retry_values(source.reconnection_strategy),
        )
        self.assertTrue(terminal.exhausted)
        self.assertEqual([True], stopped)

    def test_non_tcps_hosts_and_blank_trust_stores_are_refused_before_the_sdk(self) -> None:
        # Arrange
        refused: tuple[tuple[dict[str, object], type[ValueError]], ...] = (
            (
                {
                    "solace.messaging.transport.host": "tcp://broker:55555",
                    "solace.messaging.tls.trust-store-path": "/trust",
                },
                NonTcpsBrokerError,
            ),
            (
                {
                    "solace.messaging.transport.host": "ws://broker:80",
                    "solace.messaging.tls.trust-store-path": "/trust",
                },
                NonTcpsBrokerError,
            ),
            (
                {
                    "solace.messaging.transport.host": "wss://broker:443",
                    "solace.messaging.tls.trust-store-path": "/trust",
                },
                NonTcpsBrokerError,
            ),
            (
                {
                    "solace.messaging.transport.host": "tcps://broker:55443",
                    "solace.messaging.tls.trust-store-path": "  ",
                },
                MissingTrustStoreError,
            ),
        )

        # Act
        outcomes: list[tuple[type[Exception], dict[str, object]]] = []
        for properties, expected_error in refused:
            service = _FakeService()
            source = _FakeBuilder(service)
            builder = harden_builder(
                source,
                BrokerTerminalState(on_exhausted=lambda: None),
            )
            with pytest.raises(expected_error) as raised:
                builder.from_properties(properties)
            outcomes.append((type(raised.value), source.properties))

        # Assert
        expected: list[tuple[type[Exception], dict[str, object]]] = [
            (error_type, {}) for _, error_type in refused
        ]
        self.assertEqual(expected, outcomes)

    def test_the_leaf_override_and_runtime_guard_name_the_exact_supported_combination(self) -> None:
        # Arrange
        expected = (EXPECTED_SAM_VERSION, EXPECTED_SAC_VERSION, EXPECTED_SDK_VERSION)

        # Act
        actual = require_supported_runtime(importlib.metadata.version)

        # Assert
        self.assertEqual(expected, actual)
        self.assertEqual(EXPECTED_SDK_VERSION, importlib.metadata.version("solace-pubsubplus"))
        self.assertIn(
            VENDOR_SDK_PIN,
            importlib.metadata.requires("solace-ai-connector") or [],
        )

    def test_the_upstream_builder_and_zero_exit_shapes_force_compatibility_review_on_drift(
        self,
    ) -> None:
        # Arrange
        upstream = (upstream_messaging, connector_main.main)

        # Act
        messaging_source = inspect.getsource(upstream[0])
        lifecycle_source = inspect.getsource(upstream[1])

        # Assert
        self.assertEqual(2, messaging_source.count("MessagingService.builder()"))
        self.assertEqual(2, messaging_source.count("RetryStrategy.forever_retry"))
        self.assertEqual(2, messaging_source.count("with_connection_retry_strategy"))
        self.assertEqual(2, messaging_source.count("with_reconnection_retry_strategy"))
        self.assertIn("os._exit(0)", lifecycle_source)
        self.assertIn("def shutdown", lifecycle_source)

    def test_the_upstream_startup_callback_precedes_readiness_and_covers_tool_initialization(
        self,
    ) -> None:
        # Arrange
        sources = (
            upstream_connector.SolaceAiConnector.run,
            upstream_flow.Flow.__init__,
            upstream_flow.Flow.create_component_group,
            upstream_component_base.SamComponentBase.run,
            upstream_agent_component.SamAgentComponent.__init__,
            upstream_agent_component.SamAgentComponent._perform_async_init,
            upstream_agent_setup._create_python_tool_lifecycle_hooks,
            upstream_event_mesh_tool.EventMeshTool.init,
        )

        # Act
        connector, flow, flow_group, component, agent_init, agent, hooks, event_tool = map(
            inspect.getsource, sources
        )

        # Assert
        self.assertLess(
            connector.index("self.create_apps()"),
            connector.index("on_flow_creation(self.flows)"),
        )
        self.assertLess(
            connector.index("on_flow_creation(self.flows)"),
            connector.index("self.health_checker.mark_ready()"),
        )
        self.assertLess(
            connector.index("except KeyboardInterrupt:"),
            connector.index("except Exception:"),
        )
        self.assertIn("raise KeyboardInterrupt", connector)
        self.assertIn("self.component_groups: List[List[ComponentBase]] = []", flow)
        self.assertIn("self.create_components()", flow)
        self.assertIn("self.component_groups.append(component_group)", flow_group)
        self.assertIn("self._async_init_future.add_done_callback", component)
        self.assertIn(
            "self._async_init_future = concurrent.futures.Future()",
            agent_init,
        )
        self.assertLess(
            agent.index("await load_adk_tools(self)"),
            agent.index("self._signal_async_init_future(success=True)"),
        )
        self.assertIn("await tool_instance.init(component, tool_config_model)", hooks)
        self.assertIn("component.create_request_response_session(", event_tool)


class RuntimeLifecycleTests(unittest.TestCase):
    def test_requested_shutdown_converts_an_upstream_keyboard_interrupt_to_success(self) -> None:
        # Arrange
        connector = _FakeConnector(failure=KeyboardInterrupt())
        requested = threading.Event()
        requested.set()
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(["run", "stop", "cleanup"], connector.calls)

    def test_requested_stop_interrupts_initialization_and_remains_successful(self) -> None:
        # Arrange
        future: Future[object] = Future()
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]
        connector = _FakeConnector()
        connector.startup = lambda: runtime_lifecycle.wait_for_async_initialization(
            flows,
            stop_signal=connector.stop_signal,
        )
        requested = threading.Event()
        requested.set()
        connector.stop_signal.set()
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)

        # Assert
        self.assertEqual(EXIT_SUCCESS, status)
        self.assertEqual(["run", "stop", "cleanup"], connector.calls)

    def test_initialization_failure_outweighs_concurrent_requested_shutdown(self) -> None:
        # Arrange
        future: Future[object] = Future()
        failure = RuntimeError(SENSITIVE_FAILURE)
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]
        connector = _FakeConnector()
        connector.startup = lambda: runtime_lifecycle.wait_for_async_initialization(
            flows,
            stop_signal=connector.stop_signal,
        )
        requested = threading.Event()
        requested.set()
        future.add_done_callback(lambda _completed: connector.stop_signal.set())
        future.set_exception(failure)
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)
        logger_patch = patch("aerial_rescue_runtime_compat.lifecycle._LOGGER")
        logger = logger_patch.start()
        self.addCleanup(logger_patch.stop)

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)

        # Assert
        self.assertEqual(EXIT_RUNTIME_FAILURE, status)
        self.assertEqual(["run", "stop", "cleanup"], connector.calls)
        self.assertIn("RuntimeError", str(logger.error.call_args_list))

    def test_terminal_stop_interrupts_initialization_and_remains_a_failure(self) -> None:
        # Arrange
        future: Future[object] = Future()
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]
        connector = _FakeConnector()
        connector.startup = lambda: runtime_lifecycle.wait_for_async_initialization(
            flows,
            stop_signal=connector.stop_signal,
        )
        requested = threading.Event()
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)
        terminal.mark_exhausted()
        logger_patch = patch("aerial_rescue_runtime_compat.lifecycle._LOGGER")
        logger = logger_patch.start()
        self.addCleanup(logger_patch.stop)

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)
        logger_patch.stop()

        # Assert
        self.assertEqual(EXIT_RUNTIME_FAILURE, status)
        self.assertEqual(["run", "stop", "cleanup"], connector.calls)
        self.assertIn("KeyboardInterrupt", str(logger.error.call_args_list))

    def test_requested_shutdown_stops_then_cleans_up_and_returns_success(self) -> None:
        # Arrange
        connector = _FakeConnector()
        requested = threading.Event()
        requested.set()
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(["run", "wait", "stop", "cleanup"], connector.calls)

    def test_retry_exhaustion_removes_readiness_and_returns_nonzero_after_cleanup(self) -> None:
        # Arrange
        connector = _FakeConnector()
        requested = threading.Event()
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)
        terminal.mark_exhausted()

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)

        # Assert
        self.assertNotEqual(0, status)
        self.assertTrue(connector.stop_signal.is_set())
        self.assertEqual(["run", "wait", "stop", "cleanup"], connector.calls)

    def test_unexpected_failures_are_redacted_and_return_nonzero_after_cleanup(self) -> None:
        # Arrange
        connector = _FakeConnector(failure=RuntimeError(SENSITIVE_FAILURE))
        requested = threading.Event()
        terminal = BrokerTerminalState(on_exhausted=connector.stop_signal.set)
        logger_patch = patch("aerial_rescue_runtime_compat.lifecycle._LOGGER")
        logger = logger_patch.start()

        # Act
        status = run_connector(connector, requested=requested, terminal=terminal)
        logger_patch.stop()

        # Assert
        self.assertNotEqual(0, status)
        self.assertEqual(["run", "stop", "cleanup"], connector.calls)
        output = str(logger.error.call_args_list)
        self.assertNotIn(SENSITIVE_FAILURE, output)
        self.assertIn("RuntimeError", output)


class AsyncInitializationReadinessTests(unittest.TestCase):
    def test_completed_component_initialization_allows_connector_readiness(self) -> None:
        # Arrange
        first: Future[object] = Future()
        second: Future[object] = Future()
        first.set_result(None)
        second.set_result(None)
        flows = [_FakeFlow([[_FakeAsyncComponent(first), _FakeAsyncComponent(second)]])]
        stop_signal = threading.Event()

        # Act
        runtime_lifecycle.wait_for_async_initialization(flows, stop_signal=stop_signal)

        # Assert
        self.assertEqual((None, None), (first.exception(), second.exception()))

    def test_component_initialization_failure_refuses_connector_readiness(self) -> None:
        # Arrange
        future: Future[object] = Future()
        failure = RuntimeError(SENSITIVE_FAILURE)
        future.set_exception(failure)
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]
        stop_signal = threading.Event()

        # Act
        with pytest.raises(RuntimeError) as raised:
            runtime_lifecycle.wait_for_async_initialization(flows, stop_signal=stop_signal)

        # Assert
        self.assertIs(failure, raised.value)

    def test_completed_failure_outweighs_its_stop_callback(self) -> None:
        # Arrange
        future: Future[object] = Future()
        failure = RuntimeError(SENSITIVE_FAILURE)
        stop_signal = threading.Event()
        future.add_done_callback(lambda _completed: stop_signal.set())
        future.set_exception(failure)
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]

        # Act
        with pytest.raises(RuntimeError) as raised:
            runtime_lifecycle.wait_for_async_initialization(
                flows,
                stop_signal=stop_signal,
            )

        # Assert
        self.assertIs(failure, raised.value)
        self.assertTrue(stop_signal.is_set())

    def test_failure_completed_during_poll_outweighs_its_stop_callback(self) -> None:
        # Arrange
        future: Future[object] = Future()
        failure = RuntimeError(SENSITIVE_FAILURE)
        stop_signal = threading.Event()
        future.add_done_callback(lambda _completed: stop_signal.set())
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]

        def fail_during_poll(
            pending: set[Future[object]],
            *,
            timeout: float,
            return_when: str,
        ) -> tuple[set[Future[object]], set[Future[object]]]:
            del timeout, return_when
            future.set_exception(failure)
            return ({future}, pending - {future})

        wait_patch = patch(
            "aerial_rescue_runtime_compat.lifecycle.wait",
            side_effect=fail_during_poll,
        )
        waiter = wait_patch.start()
        self.addCleanup(wait_patch.stop)

        # Act
        with pytest.raises(RuntimeError) as raised:
            runtime_lifecycle.wait_for_async_initialization(
                flows,
                stop_signal=stop_signal,
            )
        wait_patch.stop()

        # Assert
        self.assertIs(failure, raised.value)
        self.assertTrue(stop_signal.is_set())
        self.assertEqual(1, waiter.call_count)

    def test_pending_components_share_one_global_initialization_timeout(self) -> None:
        # Arrange
        first: Future[object] = Future()
        second: Future[object] = Future()
        futures = (first, second)
        flows = [_FakeFlow([[_FakeAsyncComponent(first)], [_FakeAsyncComponent(second)]])]
        stop_signal = threading.Event()
        clock_values = iter((10.0, 10.0, 69.75, 70.0))
        wait_patch = patch(
            "aerial_rescue_runtime_compat.lifecycle.wait",
            return_value=(set(), set(futures)),
        )
        waiter = wait_patch.start()
        self.addCleanup(wait_patch.stop)

        # Act
        with pytest.raises(runtime_lifecycle.AsyncInitializationTimeoutError) as raised:
            runtime_lifecycle.wait_for_async_initialization(
                flows,
                stop_signal=stop_signal,
                monotonic=lambda: next(clock_values),
            )
        wait_patch.stop()

        # Assert
        self.assertEqual(
            "Agent Mesh async initialization exceeded its startup bound",
            str(raised.value),
        )
        self.assertEqual(60.0, runtime_lifecycle.ASYNC_INITIALIZATION_TIMEOUT_SECONDS)
        self.assertEqual(0.5, runtime_lifecycle.ASYNC_INITIALIZATION_POLL_SECONDS)
        self.assertEqual(
            [0.5, 0.25],
            [pending_wait.kwargs["timeout"] for pending_wait in waiter.call_args_list],
        )
        self.assertTrue(
            all(
                pending_wait.kwargs["return_when"] == FIRST_EXCEPTION
                for pending_wait in waiter.call_args_list
            )
        )

    def test_stop_signal_interrupts_a_pending_initialization_after_one_poll(self) -> None:
        # Arrange
        future: Future[object] = Future()
        flows = [_FakeFlow([[_FakeAsyncComponent(future)]])]
        stop_signal = threading.Event()

        def stop_during_poll(
            pending: set[Future[object]],
            *,
            timeout: float,
            return_when: str,
        ) -> tuple[set[Future[object]], set[Future[object]]]:
            del timeout, return_when
            stop_signal.set()
            return (set(), pending)

        wait_patch = patch(
            "aerial_rescue_runtime_compat.lifecycle.wait",
            side_effect=stop_during_poll,
        )
        waiter = wait_patch.start()
        self.addCleanup(wait_patch.stop)

        # Act
        with pytest.raises(KeyboardInterrupt):
            runtime_lifecycle.wait_for_async_initialization(
                flows,
                stop_signal=stop_signal,
            )
        wait_patch.stop()

        # Assert
        self.assertEqual(1, waiter.call_count)

    def test_components_without_an_async_initialization_future_are_ignored(self) -> None:
        # Arrange
        flows = [_FakeFlow([[object(), _FakeAsyncComponent(None)]])]
        stop_signal = threading.Event()
        wait_patch = patch("aerial_rescue_runtime_compat.lifecycle.wait")
        waiter = wait_patch.start()
        self.addCleanup(wait_patch.stop)

        # Act
        runtime_lifecycle.wait_for_async_initialization(flows, stop_signal=stop_signal)
        wait_patch.stop()

        # Assert
        waiter.assert_not_called()

    def test_an_incompatible_async_initialization_seam_is_refused(self) -> None:
        # Arrange
        flows = [_FakeFlow([[_InvalidAsyncComponent()]])]
        stop_signal = threading.Event()

        # Act
        with pytest.raises(runtime_lifecycle.AsyncInitializationContractError) as raised:
            runtime_lifecycle.wait_for_async_initialization(flows, stop_signal=stop_signal)

        # Assert
        self.assertEqual(
            "Agent Mesh async initialization future has unsupported type",
            str(raised.value),
        )

    def test_owned_entrypoint_registers_the_initialization_barrier(self) -> None:
        # Arrange
        connector = _FakeConnector()
        constructor_patch = patch.object(
            runtime_main,
            "SolaceAiConnector",
            return_value=connector,
        )
        run_patch = patch.object(runtime_main, "run_connector", return_value=EXIT_SUCCESS)
        messaging_patch = patch.object(
            runtime_main,
            "install_hardened_messaging",
            return_value=lambda: None,
        )
        signals_patch = patch.object(runtime_main, "_install_signal_handlers", return_value={})
        initialization_patch = patch.object(runtime_main, "wait_for_async_initialization")
        constructor = constructor_patch.start()
        self.addCleanup(constructor_patch.stop)
        run_patch.start()
        self.addCleanup(run_patch.stop)
        messaging_patch.start()
        self.addCleanup(messaging_patch.stop)
        signals_patch.start()
        self.addCleanup(signals_patch.stop)
        initialization_waiter = initialization_patch.start()
        self.addCleanup(initialization_patch.stop)

        # Act
        status = runtime_main._run_configuration({}, ["mission-coordinator.yaml"])
        handler = constructor.call_args.kwargs["event_handlers"]["on_flow_creation"]
        handler([])
        initialization_patch.stop()
        signals_patch.stop()
        messaging_patch.stop()
        run_patch.stop()
        constructor_patch.stop()

        # Assert
        self.assertEqual(EXIT_SUCCESS, status)
        initialization_waiter.assert_called_once_with(
            [],
            stop_signal=connector.stop_signal,
        )

    def test_shutdown_requested_during_connector_construction_cancels_its_startup(self) -> None:
        # Arrange
        connector = _FakeConnector()

        def request_during_install(
            control: runtime_main._StopControl,
        ) -> dict[signal.Signals, signal.Handlers]:
            control.request_shutdown(signal.SIGTERM, None)
            return {}

        constructor_patch = patch.object(
            runtime_main,
            "SolaceAiConnector",
            return_value=connector,
        )
        run_patch = patch.object(runtime_main, "run_connector", return_value=EXIT_SUCCESS)
        messaging_patch = patch.object(
            runtime_main,
            "install_hardened_messaging",
            return_value=lambda: None,
        )
        signals_patch = patch.object(
            runtime_main,
            "_install_signal_handlers",
            side_effect=request_during_install,
        )
        constructor_patch.start()
        self.addCleanup(constructor_patch.stop)
        run_patch.start()
        self.addCleanup(run_patch.stop)
        messaging_patch.start()
        self.addCleanup(messaging_patch.stop)
        signals_patch.start()
        self.addCleanup(signals_patch.stop)

        # Act
        runtime_main._run_configuration({}, ["mission-coordinator.yaml"])
        signals_patch.stop()
        messaging_patch.stop()
        run_patch.stop()
        constructor_patch.stop()

        # Assert
        self.assertTrue(connector.stop_signal.is_set())


class ProcessTerminationTests(unittest.TestCase):
    def test_a_settled_interpreter_returns_the_status_without_forcing_the_exit(self) -> None:
        # Arrange
        interpreter = _FakeInterpreter([0])

        # Act
        status = terminate_process(
            EXIT_RUNTIME_FAILURE,
            surviving=interpreter.surviving,
            sleep=interpreter.sleep,
            force=interpreter.force,
            settle_seconds=TEST_SETTLE_SECONDS,
        )

        # Assert
        self.assertEqual(EXIT_RUNTIME_FAILURE, status)
        self.assertEqual([], interpreter.forced)
        self.assertEqual([], interpreter.waits)

    def test_a_thread_that_ends_within_the_bound_is_awaited_rather_than_forced(self) -> None:
        # Arrange
        interpreter = _FakeInterpreter([SURVIVING_THREADS, 1, 0])

        # Act
        status = terminate_process(
            EXIT_SUCCESS,
            surviving=interpreter.surviving,
            sleep=interpreter.sleep,
            force=interpreter.force,
            settle_seconds=TEST_SETTLE_SECONDS,
        )

        # Assert
        self.assertEqual(EXIT_SUCCESS, status)
        self.assertEqual([], interpreter.forced)
        self.assertNotEqual([], interpreter.waits)

    def test_a_thread_that_outlives_the_bound_forces_termination_with_the_status(self) -> None:
        # Arrange
        interpreter = _FakeInterpreter([SURVIVING_THREADS])

        # Act
        status = terminate_process(
            EXIT_RUNTIME_FAILURE,
            surviving=interpreter.surviving,
            sleep=interpreter.sleep,
            force=interpreter.force,
            settle_seconds=TEST_SETTLE_SECONDS,
        )

        # Assert
        self.assertEqual(EXIT_RUNTIME_FAILURE, status)
        self.assertEqual([EXIT_RUNTIME_FAILURE], interpreter.forced)
        self.assertLessEqual(sum(interpreter.waits), TEST_SETTLE_SECONDS)
        self.assertEqual(EXPECTED_SETTLE_SECONDS, THREAD_SETTLE_SECONDS)

    def test_a_forced_termination_logs_the_surviving_count_and_nothing_else(self) -> None:
        # Arrange
        interpreter = _FakeInterpreter([SURVIVING_THREADS])
        logger_patch = patch("aerial_rescue_runtime_compat.lifecycle._LOGGER")
        logger = logger_patch.start()

        # Act
        terminate_process(
            EXIT_RUNTIME_FAILURE,
            surviving=interpreter.surviving,
            sleep=interpreter.sleep,
            force=interpreter.force,
            settle_seconds=TEST_SETTLE_SECONDS,
        )
        logger_patch.stop()

        # Assert
        self.assertEqual(1, len(logger.error.call_args_list))
        self.assertEqual((SURVIVING_THREADS,), logger.error.call_args_list[0].args[1:])

    def test_the_default_forced_exit_flushes_the_logs_before_stopping_the_interpreter(
        self,
    ) -> None:
        # Arrange
        interpreter = _FakeInterpreter([SURVIVING_THREADS])
        order: list[str] = []
        flush_patch = patch(
            "aerial_rescue_runtime_compat.lifecycle.logging.shutdown",
            side_effect=lambda: order.append("flush"),
        )
        exit_patch = patch(
            "aerial_rescue_runtime_compat.lifecycle.os._exit",
            side_effect=lambda code: order.append(f"exit:{code}"),
        )
        flush_patch.start()
        exit_patch.start()

        # Act
        terminate_process(
            EXIT_RUNTIME_FAILURE,
            surviving=interpreter.surviving,
            sleep=interpreter.sleep,
            settle_seconds=TEST_SETTLE_SECONDS,
        )
        flush_patch.stop()
        exit_patch.stop()

        # Assert
        self.assertEqual(["flush", f"exit:{EXIT_RUNTIME_FAILURE}"], order)

    def test_the_owned_entrypoint_terminates_the_process_after_the_lifecycle(self) -> None:
        # Arrange
        entrypoint = Path(inspect.getfile(runtime_compat)).parent / "__main__.py"

        # Act
        normalized = " ".join(entrypoint.read_text(encoding="utf-8").split())

        # Assert
        self.assertIn("raise SystemExit(terminate_process(main()))", normalized)


class DeploymentCompatibilityTests(unittest.TestCase):
    def test_the_image_installs_the_hashed_sdk_overlay_and_uses_the_owned_entrypoint(self) -> None:
        # Arrange
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "deploy" / "agent-mesh" / "Dockerfile").read_text(encoding="utf-8")
        requirements = (root / "deploy" / "agent-mesh" / "plugin-requirements.txt").read_text(
            encoding="utf-8"
        )

        # Act
        normalized = " ".join(dockerfile.split())

        # Assert
        self.assertIn(f"solace-pubsubplus=={EXPECTED_SDK_VERSION}", requirements)
        self.assertIn("--hash=sha256:114768fe", requirements)
        self.assertIn("ENV PYTHONPATH=/opt/aerial-rescue-runtime", dockerfile)
        self.assertIn("aerial_rescue_runtime_compat", dockerfile)
        self.assertIn(
            'ENTRYPOINT ["/opt/venv/bin/python", "-m", "aerial_rescue_runtime_compat"]',
            normalized,
        )


if __name__ == "__main__":
    unittest.main()
