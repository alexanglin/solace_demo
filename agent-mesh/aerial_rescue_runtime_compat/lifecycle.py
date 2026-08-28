"""Owned nonzero-on-failure lifecycle for Solace AI Connector."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Final, NoReturn, Protocol

from aerial_rescue_runtime_compat.messaging import BrokerTerminalState

EXIT_SUCCESS: Final = 0
EXIT_RUNTIME_FAILURE: Final = 1
THREAD_SETTLE_SECONDS: Final = 15.0
SETTLE_POLL_SECONDS: Final = 0.5
_LOGGER = logging.getLogger(__name__)


def _log_failure(stage: str, error: BaseException) -> None:
    """Log only the exception class because upstream text may contain configuration."""
    _LOGGER.error("Agent Mesh %s failed: %s", stage, type(error).__name__)


class ConnectorRuntime(Protocol):
    """The lifecycle surface provided by the pinned Connector."""

    stop_signal: threading.Event

    def run(self) -> None:
        """Start configured flows."""

    def wait_for_flows(self) -> None:
        """Wait for all configured flows."""

    def stop(self) -> None:
        """Request graceful flow termination."""

    def cleanup(self) -> None:
        """Release Connector resources."""


def run_connector(
    connector: ConnectorRuntime,
    *,
    requested: threading.Event,
    terminal: BrokerTerminalState,
) -> int:
    """Run, stop, and clean one Connector; only an operator-requested stop succeeds."""
    status = EXIT_RUNTIME_FAILURE
    try:
        connector.run()
        connector.wait_for_flows()
        if requested.is_set() and not terminal.exhausted:
            status = EXIT_SUCCESS
    except (KeyboardInterrupt, SystemExit) as error:
        if requested.is_set() and not terminal.exhausted:
            status = EXIT_SUCCESS
        else:
            _log_failure("runtime", error)
    except Exception as error:
        _log_failure("runtime", error)
    finally:
        try:
            connector.stop()
        except Exception as error:
            status = EXIT_RUNTIME_FAILURE
            _log_failure("stop", error)
        try:
            connector.cleanup()
        except Exception as error:
            status = EXIT_RUNTIME_FAILURE
            _log_failure("cleanup", error)
    if terminal.exhausted:
        return EXIT_RUNTIME_FAILURE
    return status


def _surviving_thread_count() -> int:
    """Count the nondaemon threads that would hold interpreter shutdown open."""
    current = threading.current_thread()
    return sum(1 for thread in threading.enumerate() if thread is not current and not thread.daemon)


def _force_exit(status: int) -> NoReturn:
    """Flush the owned diagnostics, then stop an interpreter that cannot stop itself."""
    logging.shutdown()
    os._exit(status)


def terminate_process(
    status: int,
    *,
    surviving: Callable[[], int] = _surviving_thread_count,
    sleep: Callable[[float], None] = time.sleep,
    force: Callable[[int], None] = _force_exit,
    settle_seconds: float = THREAD_SETTLE_SECONDS,
) -> int:
    """Return the status once the interpreter can exit, forcing it when it cannot.

    The pinned Solace SDK builds its executors from the main thread, so their workers are
    nondaemon and `concurrent.futures` joins them without a bound at interpreter shutdown.
    A worker parked in a native call would otherwise keep the process alive after the owned
    lifecycle has already stopped and cleaned up, hiding a failed run from the supervisor
    (docs/adr/0199-terminate-the-owned-agent-mesh-entrypoint.md).
    """
    waited = 0.0
    count = surviving()
    while count and waited < settle_seconds:
        sleep(SETTLE_POLL_SECONDS)
        waited += SETTLE_POLL_SECONDS
        count = surviving()
    if count:
        _LOGGER.error("Agent Mesh termination forced past %d surviving threads", count)
        force(status)
    return status
