"""Owned TLS, recovery, and process lifecycle around the pinned SAC runtime."""

from __future__ import annotations

import importlib.metadata
import inspect
import threading
import unittest
from pathlib import Path
from typing import Final, Protocol, Self
from unittest.mock import patch

import pytest
import solace_ai_connector.common.messaging.solace_messaging as upstream_messaging
import solace_ai_connector.main as connector_main
from solace.messaging.config.retry_strategy import RetryStrategy

from aerial_rescue_runtime_compat.lifecycle import run_connector
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

    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.stop_signal = threading.Event()
        self.calls: list[str] = []

    def run(self) -> None:
        """Start or raise one injected runtime failure."""
        self.calls.append("run")
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
