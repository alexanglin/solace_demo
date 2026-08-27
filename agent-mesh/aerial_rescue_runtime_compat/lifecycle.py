"""Owned nonzero-on-failure lifecycle for Solace AI Connector."""

from __future__ import annotations

import logging
import threading
from typing import Final, Protocol

from aerial_rescue_runtime_compat.messaging import BrokerTerminalState

EXIT_SUCCESS: Final = 0
EXIT_RUNTIME_FAILURE: Final = 1
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
