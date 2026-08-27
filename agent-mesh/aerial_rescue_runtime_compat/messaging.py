"""Force the pinned Connector's Solace SDK builders through the closed policy.

Solace AI Connector 3.3.12 constructs every messaging service through the SDK's
``MessagingService.builder`` seam, but supplies neither the project's TLS policy nor its
bounded initial/reconnection strategies. This compatibility layer wraps that single
source-shape-attested seam before the Connector constructs any app.
"""

from __future__ import annotations

import importlib.metadata
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Final, Protocol, Self, cast, override
from urllib.parse import urlsplit

from solace.messaging.config.retry_strategy import RetryStrategy
from solace.messaging.messaging_service import (
    MessagingService,
    ServiceEvent,
    ServiceInterruptionListener,
)

SAM_DISTRIBUTION: Final = "solace-agent-mesh"
SAC_DISTRIBUTION: Final = "solace-ai-connector"
SDK_DISTRIBUTION: Final = "solace-pubsubplus"
SUPPORTED_SAM_VERSION: Final = "1.28.7"
SUPPORTED_SAC_VERSION: Final = "3.3.12"
SUPPORTED_SDK_VERSION: Final = "1.11.0"

CONNECTION_ATTEMPT_TIMEOUT_MILLISECONDS: Final = 1_000
INITIAL_CONNECTION_RETRIES: Final = 2
PER_HOST_CONNECTION_RETRIES: Final = 0
ACTIVE_RECONNECTION_ATTEMPTS: Final = 30
ACTIVE_RECONNECTION_WAIT_MILLISECONDS: Final = 1_000
KEEP_ALIVE_INTERVAL_MILLISECONDS: Final = 3_000
KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT: Final = 3

_HOST: Final = "solace.messaging.transport.host"
_TLS_MINIMUM_PROTOCOL: Final = "solace.messaging.tls.minimum-protocol"
_TLS_CERT_VALIDATED: Final = "solace.messaging.tls.cert-validated"
_TLS_CERT_REJECT_EXPIRED: Final = "solace.messaging.tls.cert-reject-expired"
_TLS_CERT_VALIDATE_SERVERNAME: Final = "solace.messaging.tls.cert-validate-servername"
_TLS_TRUST_STORE_PATH: Final = "solace.messaging.tls.trust-store-path"
_CONNECTION_ATTEMPTS_TIMEOUT: Final = "solace.messaging.transport.connection-attempts-timeout"
_CONNECTION_RETRIES: Final = "solace.messaging.transport.connection-retries"
_CONNECTION_RETRIES_PER_HOST: Final = "solace.messaging.transport.connection.retries-per-host"
_RECONNECTION_ATTEMPTS: Final = "solace.messaging.transport.reconnection-attempts"
_RECONNECTION_WAIT: Final = "solace.messaging.transport.reconnection-attempts-wait-interval"
_KEEP_ALIVE_INTERVAL: Final = "solace.messaging.transport.keep-alive-interval"
_KEEP_ALIVE_WITHOUT_RESPONSE: Final = "solace.messaging.transport.keep-alive-without-response-limit"

_CLOSED_PROPERTIES: Final[Mapping[str, object]] = {
    _TLS_MINIMUM_PROTOCOL: "TLSv1.3",
    _TLS_CERT_VALIDATED: True,
    _TLS_CERT_REJECT_EXPIRED: True,
    _TLS_CERT_VALIDATE_SERVERNAME: True,
    _CONNECTION_ATTEMPTS_TIMEOUT: CONNECTION_ATTEMPT_TIMEOUT_MILLISECONDS,
    _CONNECTION_RETRIES: INITIAL_CONNECTION_RETRIES,
    _CONNECTION_RETRIES_PER_HOST: PER_HOST_CONNECTION_RETRIES,
    _RECONNECTION_ATTEMPTS: ACTIVE_RECONNECTION_ATTEMPTS,
    _RECONNECTION_WAIT: ACTIVE_RECONNECTION_WAIT_MILLISECONDS,
    _KEEP_ALIVE_INTERVAL: KEEP_ALIVE_INTERVAL_MILLISECONDS,
    _KEEP_ALIVE_WITHOUT_RESPONSE: KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT,
}

_LOGGER = logging.getLogger(__name__)


class UnsupportedRuntimeError(RuntimeError):
    """The installed SAM/SAC/SDK tuple is outside the attested combination."""

    def __init__(self) -> None:
        """Create the fixed credential-free failure."""
        super().__init__("unsupported Agent Mesh runtime combination")


class NonTcpsBrokerError(ValueError):
    """The Connector tried to construct a non-TLS SMF transport."""

    def __init__(self) -> None:
        """Create the fixed credential-free refusal."""
        super().__init__("Agent Mesh broker host must use tcps")


class MissingTrustStoreError(ValueError):
    """The Connector tried to construct a TLS service without a CA source."""

    def __init__(self) -> None:
        """Create the fixed credential-free refusal."""
        super().__init__("Agent Mesh broker trust store must be nonempty")


class _VersionLookup(Protocol):
    def __call__(self, distribution_name: str) -> str:
        """Return one installed distribution version."""


class _SdkBuilder(Protocol):
    def from_properties(self, properties: dict[str, object]) -> object:
        """Apply SDK service properties."""

    def with_connection_retry_strategy(self, strategy: object) -> object:
        """Apply the initial-connection retry strategy."""

    def with_reconnection_retry_strategy(self, strategy: object) -> object:
        """Apply the active-session retry strategy."""

    def build(self, application_id: str | None = None) -> object:
        """Build one messaging service."""


class _InterruptionService(Protocol):
    def add_service_interruption_listener(self, listener: ServiceInterruptionListener) -> None:
        """Attach a terminal interruption listener."""


class BrokerTerminalState:
    """Record one non-recoverable broker interruption and request process stop once."""

    def __init__(self, *, on_exhausted: Callable[[], None]) -> None:
        """Create a healthy state with one injected terminal-stop callback."""
        self._on_exhausted = on_exhausted
        self._lock = threading.Lock()
        self._exhausted = False

    @property
    def exhausted(self) -> bool:
        """Whether any Connector-owned service exhausted active reconnection."""
        with self._lock:
            return self._exhausted

    def mark_exhausted(self) -> None:
        """Make readiness terminal and wake the owned lifecycle without duplicate callbacks."""
        with self._lock:
            if self._exhausted:
                return
            self._exhausted = True
        try:
            self._on_exhausted()
        except Exception as error:
            _log_failure("terminal broker callback", error)


class _TerminalInterruptionListener(
    ServiceInterruptionListener,  # type: ignore[misc]
):
    """Translate SDK recovery exhaustion into the owned process lifecycle.

    The exact ignore is required because the SDK exposes no ``py.typed`` marker; ADR-0177
    admits this one subclass boundary and the runtime guard/source sentinel bind its shape.
    """

    def __init__(self, terminal: BrokerTerminalState) -> None:
        self._terminal = terminal

    @override
    def on_service_interrupted(self, event: ServiceEvent) -> None:
        """Record the terminal state without exposing the SDK event diagnostic."""
        del event
        self._terminal.mark_exhausted()


class HardenedBuilder[BuilderType]:
    """A chaining proxy that closes all Connector-controlled builder choices."""

    def __init__(self, source: BuilderType, terminal: BrokerTerminalState) -> None:
        """Wrap one SDK builder without calling it."""
        self.source = source
        self._sdk = cast(_SdkBuilder, source)
        self._terminal = terminal

    def from_properties(self, properties: Mapping[str, object]) -> Self:
        """Validate the transport and overlay every closed SDK property."""
        self._sdk.from_properties(_effective_properties(properties))
        return self

    def with_connection_retry_strategy(self, strategy: object) -> Self:
        """Replace any Connector strategy with the bounded initial strategy."""
        del strategy
        self._sdk.with_connection_retry_strategy(
            RetryStrategy.parametrized_retry(
                INITIAL_CONNECTION_RETRIES,
                CONNECTION_ATTEMPT_TIMEOUT_MILLISECONDS,
            )
        )
        return self

    def with_reconnection_retry_strategy(self, strategy: object) -> Self:
        """Replace any Connector strategy with bounded active-session recovery."""
        del strategy
        self._sdk.with_reconnection_retry_strategy(
            RetryStrategy.parametrized_retry(
                ACTIVE_RECONNECTION_ATTEMPTS,
                ACTIVE_RECONNECTION_WAIT_MILLISECONDS,
            )
        )
        return self

    def build(self, application_id: str | None = None) -> object:
        """Build and attach the terminal interruption listener before returning."""
        service = self._sdk.build(application_id)
        interruption_service = cast(_InterruptionService, service)
        interruption_service.add_service_interruption_listener(
            _TerminalInterruptionListener(self._terminal)
        )
        return service


def _log_failure(stage: str, error: Exception) -> None:
    """Log only the failure class; upstream exception text may carry configuration."""
    _LOGGER.error("Agent Mesh %s failed: %s", stage, type(error).__name__)


def _effective_properties(properties: Mapping[str, object]) -> dict[str, object]:
    """Validate the no-I/O inputs and return the forced SDK property map."""
    host = properties.get(_HOST)
    if not isinstance(host, str):
        raise NonTcpsBrokerError
    parsed_host = urlsplit(host)
    if parsed_host.scheme != "tcps" or parsed_host.hostname is None:
        raise NonTcpsBrokerError
    trust_store = properties.get(_TLS_TRUST_STORE_PATH)
    if not isinstance(trust_store, str) or not trust_store.strip():
        raise MissingTrustStoreError
    effective = dict(properties)
    effective.update(_CLOSED_PROPERTIES)
    return effective


def require_supported_runtime(
    version_lookup: _VersionLookup = importlib.metadata.version,
) -> tuple[str, str, str]:
    """Refuse runtime drift outside the single source-shape-attested combination."""
    actual = (
        version_lookup(SAM_DISTRIBUTION),
        version_lookup(SAC_DISTRIBUTION),
        version_lookup(SDK_DISTRIBUTION),
    )
    expected = (
        SUPPORTED_SAM_VERSION,
        SUPPORTED_SAC_VERSION,
        SUPPORTED_SDK_VERSION,
    )
    if actual != expected:
        raise UnsupportedRuntimeError
    return actual


def harden_builder[BuilderType](
    builder: BuilderType,
    terminal: BrokerTerminalState,
) -> HardenedBuilder[BuilderType]:
    """Wrap one SDK builder with the closed security and recovery policy."""
    return HardenedBuilder(builder, terminal)


def install_hardened_messaging(terminal: BrokerTerminalState) -> Callable[[], None]:
    """Wrap every Connector-created SDK builder and return an idempotent restoration."""
    original_builder = MessagingService.builder
    restored = False
    restoration_lock = threading.Lock()

    def hardened_builder() -> HardenedBuilder[object]:
        return harden_builder(original_builder(), terminal)

    type.__setattr__(MessagingService, "builder", staticmethod(hardened_builder))

    def restore() -> None:
        nonlocal restored
        with restoration_lock:
            if restored:
                return
            type.__setattr__(MessagingService, "builder", staticmethod(original_builder))
            restored = True

    return restore
