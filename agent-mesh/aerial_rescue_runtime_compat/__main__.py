"""Credential-safe system-environment entrypoint for the pinned Agent Mesh image."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Sequence
from types import FrameType
from typing import cast

from solace_agent_mesh.cli.utils import discover_config_files
from solace_agent_mesh.common.utils.initializer import initialize
from solace_ai_connector.common.logging_config import configure_from_file
from solace_ai_connector.main import load_config, merge_config
from solace_ai_connector.solace_ai_connector import SolaceAiConnector

from aerial_rescue_runtime_compat.lifecycle import (
    ConnectorRuntime,
    run_connector,
    terminate_process,
)
from aerial_rescue_runtime_compat.messaging import (
    BrokerTerminalState,
    install_hardened_messaging,
    require_supported_runtime,
)

_LOGGER = logging.getLogger(__name__)


class EmptyConfigurationError(RuntimeError):
    """Configuration discovery returned no runnable YAML files."""

    def __init__(self) -> None:
        """Create the fixed credential-free failure."""
        super().__init__("Agent Mesh configuration discovery returned no files")


class _StopControl:
    """Share requested and terminal stop state with signal and SDK callbacks."""

    def __init__(self) -> None:
        self.requested = threading.Event()
        self.connector: ConnectorRuntime | None = None

    def request_terminal_stop(self) -> None:
        """Wake the Connector without marking an operator-requested shutdown."""
        self._wake_connector()

    def request_shutdown(self, signum: int, frame: FrameType | None) -> None:
        """Mark an OS-requested graceful stop and wake the Connector."""
        del signum, frame
        self.requested.set()
        self._wake_connector()

    def _wake_connector(self) -> None:
        connector = self.connector
        if connector is not None:
            connector.stop_signal.set()


def _log_failure(stage: str, error: Exception) -> None:
    """Log only the exception class because upstream text may contain configuration."""
    _LOGGER.error("Agent Mesh %s failed: %s", stage, type(error).__name__)


def _configuration_files(paths: Sequence[str]) -> list[str]:
    """Resolve the same closed YAML file set as ``sam run --system-env``."""
    return cast("list[str]", discover_config_files(tuple(paths)))


def _merged_configuration(files: Sequence[str]) -> dict[str, object]:
    """Load and merge the Connector configuration using its pinned implementation."""
    merged: dict[str, object] = {}
    for filename in files:
        loaded = cast("dict[str, object] | None", load_config(filename))
        merged = cast("dict[str, object]", merge_config(merged, loaded))
    return merged


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the owned Aerial Rescue Agent Mesh runtime boundary.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=("configs",),
        help="Configuration YAML files or directories; system environment only.",
    )
    return parser


def _startup(arguments: Sequence[str] | None) -> tuple[dict[str, object], list[str]]:
    """Validate the runtime and produce the merged Connector configuration."""
    require_supported_runtime()
    configure_from_file()
    initialize()
    files = _configuration_files(_parser().parse_args(arguments).paths)
    if not files:
        raise EmptyConfigurationError
    return (_merged_configuration(files), files)


def _install_signal_handlers(control: _StopControl) -> dict[signal.Signals, signal.Handlers]:
    """Install the two Linux container shutdown handlers and return their prior values."""
    previous: dict[signal.Signals, signal.Handlers] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        installed = signal.signal(signum, control.request_shutdown)
        previous[signum] = cast(signal.Handlers, installed)
    return previous


def _restore_signal_handlers(previous: dict[signal.Signals, signal.Handlers]) -> None:
    """Restore process-global signal state after the owned lifecycle ends."""
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _run_configuration(configuration: dict[str, object], files: list[str]) -> int:
    """Construct and run one Connector under owned signals and builder policy."""
    control = _StopControl()
    terminal = BrokerTerminalState(on_exhausted=control.request_terminal_stop)
    restore_builder = install_hardened_messaging(terminal)
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}
    try:
        previous_handlers = _install_signal_handlers(control)
        connector = cast(
            ConnectorRuntime,
            SolaceAiConnector(configuration, config_filenames=files),
        )
        control.connector = connector
        if terminal.exhausted:
            connector.stop_signal.set()
        return run_connector(connector, requested=control.requested, terminal=terminal)
    except Exception as error:
        _log_failure("construction", error)
        return 1
    finally:
        _restore_signal_handlers(previous_handlers)
        restore_builder()


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the attested Connector without the upstream unconditional zero exit."""
    try:
        configuration, files = _startup(arguments)
    except Exception as error:
        _log_failure("startup", error)
        return 1
    return _run_configuration(configuration, files)


if __name__ == "__main__":
    raise SystemExit(terminate_process(main()))
