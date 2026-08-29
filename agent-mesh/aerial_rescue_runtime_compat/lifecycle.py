"""Owned nonzero-on-failure lifecycle for Solace AI Connector."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, wait
from typing import Final, NoReturn, Protocol, cast

from aerial_rescue_runtime_compat.messaging import BrokerTerminalState

EXIT_SUCCESS: Final = 0
EXIT_RUNTIME_FAILURE: Final = 1
ASYNC_INITIALIZATION_TIMEOUT_SECONDS: Final = 60.0
ASYNC_INITIALIZATION_POLL_SECONDS: Final = 0.5
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


class _ComponentFlow(Protocol):
    """The pinned Connector flow surface used by the readiness barrier."""

    component_groups: Sequence[Sequence[object]]


class AsyncInitializationTimeoutError(TimeoutError):
    """At least one SAM component remained uninitialized past the startup bound."""

    def __init__(self) -> None:
        """Create a fixed diagnostic that cannot expose upstream configuration."""
        super().__init__("Agent Mesh async initialization exceeded its startup bound")


class AsyncInitializationContractError(RuntimeError):
    """The pinned SAM async-initialization seam has an unsupported shape."""

    def __init__(self) -> None:
        """Create a fixed diagnostic that cannot expose the incompatible value."""
        super().__init__("Agent Mesh async initialization future has unsupported type")


def _async_initialization_futures(flows: Sequence[object]) -> tuple[Future[object], ...]:
    """Collect each pinned SAM component future from the Connector's concrete flows."""
    futures: list[Future[object]] = []
    for candidate in flows:
        flow = cast(_ComponentFlow, candidate)
        for component_group in flow.component_groups:
            for component in component_group:
                future = cast(object, getattr(component, "_async_init_future", None))
                if future is None:
                    continue
                if not isinstance(future, Future):
                    raise AsyncInitializationContractError
                futures.append(cast("Future[object]", future))
    return tuple(futures)


def _raise_completed_initialization_failure(
    futures: Sequence[Future[object]],
) -> None:
    """Propagate completed component failures in deterministic flow order."""
    for future in futures:
        if future.done():
            future.result()


def _interrupt_if_stopped(
    stop_signal: threading.Event,
    futures: Sequence[Future[object]],
) -> None:
    """Leave through the interrupt branch unless completed startup already failed."""
    if stop_signal.is_set():
        _raise_completed_initialization_failure(futures)
        raise KeyboardInterrupt


def wait_for_async_initialization(
    flows: Sequence[object],
    *,
    stop_signal: threading.Event,
    timeout_seconds: float = ASYNC_INITIALIZATION_TIMEOUT_SECONDS,
    poll_seconds: float = ASYNC_INITIALIZATION_POLL_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Keep Connector readiness closed until every SAM component initializes."""
    deadline = monotonic() + timeout_seconds
    futures = _async_initialization_futures(flows)
    _raise_completed_initialization_failure(futures)
    _interrupt_if_stopped(stop_signal, futures)
    if not futures:
        return
    pending = {future for future in futures if not future.done()}
    while pending:
        _interrupt_if_stopped(stop_signal, futures)
        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            raise AsyncInitializationTimeoutError
        _completed, pending = wait(
            pending,
            timeout=min(poll_seconds, remaining_seconds),
            return_when=FIRST_EXCEPTION,
        )
        _raise_completed_initialization_failure(futures)
        _interrupt_if_stopped(stop_signal, futures)


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
