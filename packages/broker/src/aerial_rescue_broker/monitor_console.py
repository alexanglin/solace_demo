"""Continuous, fail-closed composition for the VPN-scoped read-only SEMP monitor.

The runtime deliberately owns no provisioning transport. Its session factory returns only
typed monitor reads and ``close``; there is no configuration ``send`` capability to leak
into the polling loop. The broker identity itself is an external operator prerequisite
because the pinned SEMP v2 configuration surface cannot express its global-none and
single-VPN read-only grants (ADR-0157 and ADR-0181).
"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from http.client import HTTPSConnection
from pathlib import Path
from threading import Event
from typing import Final, Protocol, TextIO

from aerial_rescue_contracts.topics import TopicError

from aerial_rescue_broker.monitoring import (
    MONITOR_POLL_INTERVAL_SECONDS,
    MONITOR_USERNAME,
    MonitorError,
    QueueHealthMonitor,
    QueueHealthSnapshot,
    ReadOnlySempMonitor,
)
from aerial_rescue_broker.provisioning import MonitorRow, MonitorTransport
from aerial_rescue_broker.queues import QueueSpec, desired_queues
from aerial_rescue_broker.semp import SempEndpoint, connect

MONITOR_CREDENTIAL: Final = "secrets/semp-monitor-password"
"""Generated password path relative to ``AERIAL_RESCUE_DEPLOY_DIR``."""

CERTIFICATE_AUTHORITY: Final = "ca.pem"
DEFAULT_SEMP_HOST: Final = "broker"
DEFAULT_SEMP_PORT: Final = 1943
MAXIMUM_NETWORK_PORT: Final = 65535
MAXIMUM_SECRET_FILE_BYTES: Final = 129

_DEPLOY_DIRECTORY_SETTING: Final = "AERIAL_RESCUE_DEPLOY_DIR"
_TRUST_STORE_SETTING: Final = "TRUST_STORE"
_VPN_SETTING: Final = "SOLACE_BROKER_VPN"
_HOST_SETTING: Final = "SEMP_MONITOR_HOST"
_PORT_SETTING: Final = "SEMP_MONITOR_PORT"
_DRONE_IDS_SETTING: Final = "FLEET_DRONE_IDS"


class MonitorConsoleRefusal(StrEnum):
    """Closed, credential-independent composition refusal codes."""

    MISSING_SETTING = "MISSING_SETTING"
    INVALID_SETTING = "INVALID_SETTING"
    MATERIAL_UNAVAILABLE = "MATERIAL_UNAVAILABLE"


class MonitorConsoleError(RuntimeError):
    """A startup refusal that retains no setting value, path, or credential."""

    def __init__(self, refusal: MonitorConsoleRefusal) -> None:
        """Retain only the closed refusal code."""
        super().__init__(refusal.value)
        self.refusal = refusal


class ClosableMonitorTransport(MonitorTransport, Protocol):
    """The complete capability surface owned by the continuous monitor process."""

    def close(self) -> None:
        """Close the one bounded HTTPS connection."""
        ...


class PollingMonitor(Protocol):
    """The one QueueHealthMonitor operation needed by the lifecycle loop."""

    def poll(self) -> QueueHealthSnapshot:
        """Return one complete, paced queue-health snapshot."""
        ...


Wait = Callable[[float], bool]
SessionFactory = Callable[[SempEndpoint], ClosableMonitorTransport]


@dataclass(frozen=True, slots=True)
class MonitorSettings:
    """Validated connection target and exact desired queue inventory."""

    endpoint: SempEndpoint
    vpn: str
    expected: tuple[QueueSpec, ...]


@dataclass(slots=True)
class OwnedReadOnlySession:
    """A read-only adapter paired with the HTTPS connection it must close."""

    connection: HTTPSConnection
    adapter: ReadOnlySempMonitor

    def read_monitor(self, path: str) -> tuple[dict[str, object], ...]:
        """Delegate the generic monitor-plane read without adding a write surface."""
        return self.adapter.read_monitor(path)

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Delegate the aligned aggregate monitor-plane read."""
        return self.adapter.read_monitor_rows(path)

    def read_monitor_count(self, path: str) -> int:
        """Delegate one aggregate child-collection count read."""
        return self.adapter.read_monitor_count(path)

    def close(self) -> None:
        """Close the owned HTTPS connection exactly once."""
        self.connection.close()


def open_read_only_session(endpoint: SempEndpoint) -> ClosableMonitorTransport:
    """Open one TLS-validating session that exposes no SEMP configuration writer."""
    connection = connect(endpoint)
    return OwnedReadOnlySession(connection, ReadOnlySempMonitor(connection, endpoint))


def _required(environment: Mapping[str, str], name: str) -> str:
    """Return one nonblank setting without retaining its value in a refusal."""
    value = environment.get(name)
    if value is None or not value.strip():
        raise MonitorConsoleError(MonitorConsoleRefusal.MISSING_SETTING)
    return value.strip()


def _port(environment: Mapping[str, str]) -> int:
    """Return the bounded SEMP TLS port."""
    raw = environment.get(_PORT_SETTING, str(DEFAULT_SEMP_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as error:
        raise MonitorConsoleError(MonitorConsoleRefusal.INVALID_SETTING) from error
    if not 1 <= port <= MAXIMUM_NETWORK_PORT:
        raise MonitorConsoleError(MonitorConsoleRefusal.INVALID_SETTING)
    return port


def _roster(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Return a nonempty, duplicate-free, comma-separated queue owner roster."""
    raw = _required(environment, _DRONE_IDS_SETTING)
    drones = tuple(raw.split(","))
    if any(not drone or drone != drone.strip() for drone in drones) or len(set(drones)) != len(
        drones
    ):
        raise MonitorConsoleError(MonitorConsoleRefusal.INVALID_SETTING)
    return drones


def _credential(path: Path) -> str:
    """Read one bounded private file while never retaining its bytes in a refusal."""
    try:
        raw = path.read_bytes()
        if len(raw) > MAXIMUM_SECRET_FILE_BYTES:
            raise MonitorConsoleError(MonitorConsoleRefusal.MATERIAL_UNAVAILABLE)
        value = raw.decode("utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise MonitorConsoleError(MonitorConsoleRefusal.MATERIAL_UNAVAILABLE) from error
    if not value:
        raise MonitorConsoleError(MonitorConsoleRefusal.MATERIAL_UNAVAILABLE)
    return value


def settings_from_environment(environment: Mapping[str, str]) -> MonitorSettings:
    """Resolve and validate the complete monitor configuration before any broker I/O."""
    deploy = Path(_required(environment, _DEPLOY_DIRECTORY_SETTING))
    authority = Path(_required(environment, _TRUST_STORE_SETTING)) / CERTIFICATE_AUTHORITY
    if not authority.is_file():
        raise MonitorConsoleError(MonitorConsoleRefusal.MATERIAL_UNAVAILABLE)
    host = environment.get(_HOST_SETTING, DEFAULT_SEMP_HOST).strip()
    if not host or any(character.isspace() for character in host):
        raise MonitorConsoleError(MonitorConsoleRefusal.INVALID_SETTING)
    vpn = _required(environment, _VPN_SETTING)
    password = _credential(deploy / MONITOR_CREDENTIAL)
    try:
        expected = desired_queues(_roster(environment))
    except TopicError as error:
        raise MonitorConsoleError(MonitorConsoleRefusal.INVALID_SETTING) from error
    return MonitorSettings(
        endpoint=SempEndpoint(
            host,
            _port(environment),
            MONITOR_USERNAME,
            password,
            str(authority),
        ),
        vpn=vpn,
        expected=expected,
    )


def _report(snapshot: QueueHealthSnapshot) -> str:
    """Render only bounded aggregate counts; no queue name, tenant value, or credential."""
    document = {
        "bindMismatchCount": len(snapshot.bind_mismatches),
        "deadMessageQueueCount": len(snapshot.nonempty_dead_messages),
        "healthy": snapshot.healthy,
        "messageCount": snapshot.message_count,
        "missingQueueCount": len(snapshot.missing),
        "primaryBacklogCount": len(snapshot.primary_backlog),
        "queueCount": len(snapshot.queues),
        "unexpectedQueueCount": len(snapshot.unexpected_owned),
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def serve(
    monitor: PollingMonitor,
    *,
    wait: Wait,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Poll immediately, continue at the accepted interval, and stop without a final read."""
    while True:
        try:
            snapshot = monitor.poll()
        except MonitorError as failure:
            error.write(f"FAILED: routine SEMP monitor refused ({failure.refusal.name})\n")
            return 1
        out.write(_report(snapshot) + "\n")
        out.flush()
        if wait(MONITOR_POLL_INTERVAL_SECONDS):
            return 0


def _signal_wait() -> Wait:
    """Return a wait function released by either ordinary process termination signal."""
    stopped = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return stopped.wait


def main(
    *,
    environment: Mapping[str, str] = os.environ,
    session: SessionFactory = open_read_only_session,
    wait: Wait | None = None,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Run the dedicated continuous monitor, closing its only connection on every exit."""
    resources: ClosableMonitorTransport | None = None
    try:
        settings = settings_from_environment(environment)
        resources = session(settings.endpoint)
        monitor = QueueHealthMonitor(resources, settings.vpn, settings.expected)
        return serve(monitor, wait=_signal_wait() if wait is None else wait, out=out, error=error)
    except MonitorConsoleError as failure:
        error.write(f"FAILED: {failure.refusal.value}\n")
        return 1
    except OSError:
        error.write(f"FAILED: {MonitorConsoleRefusal.MATERIAL_UNAVAILABLE.value}\n")
        return 1
    finally:
        if resources is not None:
            resources.close()


if __name__ == "__main__":
    raise SystemExit(main())
