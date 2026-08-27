"""Bounded, redacted alerting for PubSub+ event-facility JSON Syslog.

The software broker's ``event`` facility contains SYSTEM, Message VPN, and client
events. The reference deployment sends that facility to its retained file and stdout in
the broker-native JSON message format. This module consumes either explicit stdin or the
fixed retained-log source one bounded line at a time. It emits only closed metadata: raw
messages, hostnames,
Message VPN names, clients, event arguments, and unknown event names never cross the
alert boundary.

The catalog is intentionally capability-specific. HA, DMR/clustering, bridging, LDAP,
replication, appliance hardware, and transaction events remain excluded until the
corresponding topology is activated. Any other unknown SYSTEM or VPN event degrades the
pipeline so an upstream catalog change cannot silently evade monitoring.
"""

from __future__ import annotations

import json
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Event
from typing import Final, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from aerial_rescue_broker.event_source import RetainedEventLogSource

MAX_JSON_EVENT_BYTES: Final = 8192
"""The maximum broker-native JSON Syslog message configured on the container."""

_READ_LIMIT: Final = MAX_JSON_EVENT_BYTES + 2
_INPUT_REFUSED: Final = "BROKER_EVENT_INPUT_REFUSED"
_CATALOG_GAP: Final = "BROKER_EVENT_CATALOG_GAP"
_SOURCE_CLOSED: Final = "BROKER_EVENT_SOURCE_CLOSED"
_SOURCE_FAILED: Final = "BROKER_EVENT_SOURCE_FAILED"
RETAINED_EVENT_LOG: Final = Path("/jail/logs/event.log")
"""The broker-native event facility in the read-only storage-volume log subpath."""


class BrokerSeverity(StrEnum):
    """Syslog severities emitted by the broker JSON event format."""

    CRIT = "crit"
    ERR = "err"
    WARNING = "warning"
    NOTICE = "notice"
    INFO = "info"


class EventScope(StrEnum):
    """Broker event scopes accepted at the JSON boundary."""

    SYSTEM = "SYSTEM"
    VPN = "VPN"
    CLIENT = "CLIENT"


class AlertScope(StrEnum):
    """Safe scopes the alert boundary can emit."""

    SYSTEM = "SYSTEM"
    VPN = "VPN"
    MONITOR = "MONITOR"


class AlertDisposition(StrEnum):
    """How an event changes or reports the monitored condition."""

    RAISED = "raised"
    CLEARED = "cleared"
    OBSERVED = "observed"
    PIPELINE_DEGRADED = "pipeline-degraded"


class EventMonitorRefusal(StrEnum):
    """Secret-independent reasons the continuous monitor cannot proceed."""

    CLOCK = "broker event monitor clock must be timezone-aware"
    SOURCE_READ = "broker event source failed"
    ALERT_DELIVERY = "broker event alert delivery failed"


class EventMonitorError(RuntimeError):
    """A typed event-monitor refusal that never includes input or sink prose."""

    refusal: EventMonitorRefusal

    def __init__(self, refusal: EventMonitorRefusal) -> None:
        """Retain only the safe refusal category."""
        super().__init__(refusal.value)
        self.refusal = refusal


class _BrokerEventWire(BaseModel):
    """Extract the fixed metadata from vendor-defined JSON event arguments."""

    model_config = ConfigDict(extra="ignore", strict=True)

    time: AwareDatetime
    sol_facility: Literal["event"] = Field(alias="solFacility")
    severity: BrokerSeverity
    host: str = Field(min_length=1, max_length=255, exclude=True)
    tag: str = Field(max_length=32, exclude=True)
    scope: EventScope
    event: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]+$")
    msg: str = Field(max_length=MAX_JSON_EVENT_BYTES, exclude=True)


@dataclass(frozen=True)
class _EventRule:
    """One exact catalog row and its default broker severity."""

    scope: AlertScope
    severity: BrokerSeverity
    disposition: AlertDisposition
    condition: str


@dataclass(frozen=True)
class BrokerAlert:
    """A tenant-neutral alert containing no raw broker prose or dynamic arguments."""

    observed_at: datetime
    scope: AlertScope
    severity: BrokerSeverity
    event: str
    condition: str
    disposition: AlertDisposition


@dataclass(frozen=True)
class StreamSummary:
    """Bounded counters for one source lifetime."""

    lines: int
    alerts: int
    ignored: int
    refused: int


class BoundedLineSource(Protocol):
    """The only source capability used by the streaming monitor."""

    def readline(self, limit: int = -1) -> bytes:
        """Return no more than ``limit`` bytes from one line."""
        ...


_PairRow = tuple[str, str, BrokerSeverity, BrokerSeverity]
_SingleRow = tuple[str, BrokerSeverity]

_SYSTEM_PAIRS: Final[tuple[_PairRow, ...]] = (
    (
        "SYSTEM_AD_DELIVERED_UNACKED_MSGS_HIGH",
        "SYSTEM_AD_DELIVERED_UNACKED_MSGS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_DISK_USAGE_HIGH",
        "SYSTEM_AD_DISK_USAGE_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_EGRESS_FLOWS_HIGH",
        "SYSTEM_AD_EGRESS_FLOWS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_ENDPOINTS_HIGH",
        "SYSTEM_AD_ENDPOINTS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_INGRESS_FLOWS_HIGH",
        "SYSTEM_AD_INGRESS_FLOWS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_MSG_SPOOL_HIGH",
        "SYSTEM_AD_MSG_SPOOL_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_MSG_COUNT_UTILIZATION_HIGH",
        "SYSTEM_AD_MSG_COUNT_UTILIZATION_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_AD_SPOOL_FILES_HIGH",
        "SYSTEM_AD_SPOOL_FILES_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_CLIENT_CONNECTIONS_HIGH",
        "SYSTEM_CLIENT_CONNECTIONS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_CLIENT_EG_MSG_RATE_HIGH",
        "SYSTEM_CLIENT_EG_MSG_RATE_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_CLIENT_ING_MSG_RATE_HIGH",
        "SYSTEM_CLIENT_ING_MSG_RATE_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_CLIENT_SUBSCRIPTIONS_HIGH",
        "SYSTEM_CLIENT_SUBSCRIPTIONS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_CLIENT_SUBSCRIPTIONS_MEMORY_HIGH",
        "SYSTEM_CLIENT_SUBSCRIPTIONS_MEMORY_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "SYSTEM_SERVICE_LISTEN_PORT_DISABLE",
        "SYSTEM_SERVICE_LISTEN_PORT_ENABLE",
        BrokerSeverity.NOTICE,
        BrokerSeverity.NOTICE,
    ),
    (
        "SYSTEM_SSL_CONNECTIONS_HIGH",
        "SYSTEM_SSL_CONNECTIONS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
)

_SYSTEM_RAISED: Final[tuple[_SingleRow, ...]] = (
    ("SYSTEM_AD_DELIVERED_UNACKED_MSGS_EXCEED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_DISK_USAGE_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_MAX_EGRESS_FLOWS_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_MAX_ENDPOINTS_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_MAX_INGRESS_FLOWS_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_MSG_SPOOL_QUOTA_EXCEED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_MSG_COUNT_UTILIZATION_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_AD_FLASH_FAILED", BrokerSeverity.ERR),
    ("SYSTEM_AD_RESTORE_FAILED", BrokerSeverity.ERR),
    ("SYSTEM_AD_SPOOL_FILES_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_AUTHENTICATION_SESSION_DENIED", BrokerSeverity.NOTICE),
    ("SYSTEM_AUTHENTICATION_SHELL_ACCESS_DENIED", BrokerSeverity.NOTICE),
    ("SYSTEM_AUTHENTICATION_TLS_START_FAIL", BrokerSeverity.WARNING),
    ("SYSTEM_CLIENT_CONNECTIONS_EXCEEDED", BrokerSeverity.WARNING),
    ("SYSTEM_LOGGING_LOST_EVENTS", BrokerSeverity.WARNING),
    ("SYSTEM_SSL_CONNECTIONS_EXCEEDED", BrokerSeverity.WARNING),
)

_SYSTEM_OBSERVED: Final[tuple[_SingleRow, ...]] = (
    ("SYSTEM_AD_MSG_SPOOL_CHG", BrokerSeverity.NOTICE),
    ("SYSTEM_SYSTEM_STARTUP_COMPLETE", BrokerSeverity.WARNING),
)

_VPN_PAIRS: Final[tuple[_PairRow, ...]] = (
    (
        "VPN_AD_BIND_COUNT_HIGH",
        "VPN_AD_BIND_COUNT_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_AD_CLIENT_USERNAME_ENDPOINTS_HIGH",
        "VPN_AD_CLIENT_USERNAME_ENDPOINTS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_AD_EGRESS_FLOWS_HIGH",
        "VPN_AD_EGRESS_FLOWS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_AD_ENDPOINTS_HIGH",
        "VPN_AD_ENDPOINTS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_AD_INGRESS_FLOWS_HIGH",
        "VPN_AD_INGRESS_FLOWS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_AD_MSG_SPOOL_HIGH",
        "VPN_AD_MSG_SPOOL_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_AD_MSG_SPOOL_REJECT_LOW_PRIORITY_MSG_LIMIT_HIGH",
        "VPN_AD_MSG_SPOOL_REJECT_LOW_PRIORITY_MSG_LIMIT_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_CLIENT_USERNAME_CONNECTIONS_HIGH",
        "VPN_CLIENT_USERNAME_CONNECTIONS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_VPN_CONNECTIONS_HIGH",
        "VPN_VPN_CONNECTIONS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_VPN_EG_MSG_RATE_HIGH",
        "VPN_VPN_EG_MSG_RATE_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_VPN_ING_MSG_RATE_HIGH",
        "VPN_VPN_ING_MSG_RATE_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
    (
        "VPN_VPN_SUBSCRIPTIONS_HIGH",
        "VPN_VPN_SUBSCRIPTIONS_HIGH_CLEAR",
        BrokerSeverity.WARNING,
        BrokerSeverity.INFO,
    ),
)

_VPN_RAISED: Final[tuple[_SingleRow, ...]] = (
    ("VPN_AD_CLIENT_USERNAME_MAX_ENDPOINTS_EXCEEDED", BrokerSeverity.WARNING),
    ("VPN_AD_MAX_EGRESS_FLOWS_EXCEEDED", BrokerSeverity.WARNING),
    ("VPN_AD_MAX_ENDPOINTS_EXCEEDED", BrokerSeverity.WARNING),
    ("VPN_AD_MAX_INGRESS_FLOWS_EXCEEDED", BrokerSeverity.WARNING),
    ("VPN_AD_MSG_SPOOL_QUOTA_EXCEED", BrokerSeverity.WARNING),
    ("VPN_AD_MSG_SPOOL_REJECT_LOW_PRIORITY_MSG_LIMIT_EXCEED", BrokerSeverity.WARNING),
    ("VPN_CLIENT_USERNAME_CONNECTIONS_EXCEEDED", BrokerSeverity.WARNING),
    ("VPN_VPN_MAX_CONNECTIONS_EXCEEDED", BrokerSeverity.WARNING),
    ("VPN_VPN_MAX_SUBSCRIPTIONS_EXCEEDED", BrokerSeverity.WARNING),
)

_VPN_OBSERVED: Final[tuple[_SingleRow, ...]] = (
    ("VPN_SERVICE_LISTEN_PORT_STATE_CHANGE", BrokerSeverity.WARNING),
    ("VPN_SERVICE_SMF_STATE_CHANGE", BrokerSeverity.WARNING),
    ("VPN_VPN_STATE_CHANGE", BrokerSeverity.WARNING),
)


def _pair_rules(scope: AlertScope, rows: tuple[_PairRow, ...]) -> dict[str, _EventRule]:
    """Build paired raise/clear rules that share one safe condition identity."""
    rules: dict[str, _EventRule] = {}
    for raised, cleared, raised_severity, cleared_severity in rows:
        rules[raised] = _EventRule(scope, raised_severity, AlertDisposition.RAISED, raised)
        rules[cleared] = _EventRule(scope, cleared_severity, AlertDisposition.CLEARED, raised)
    return rules


def _single_rules(
    scope: AlertScope,
    disposition: AlertDisposition,
    rows: tuple[_SingleRow, ...],
) -> dict[str, _EventRule]:
    """Build unpaired catalog rules whose event is also their safe condition identity."""
    return {event: _EventRule(scope, severity, disposition, event) for event, severity in rows}


_EVENT_CATALOG: Final[dict[str, _EventRule]] = {
    **_pair_rules(AlertScope.SYSTEM, _SYSTEM_PAIRS),
    **_single_rules(AlertScope.SYSTEM, AlertDisposition.RAISED, _SYSTEM_RAISED),
    **_single_rules(AlertScope.SYSTEM, AlertDisposition.OBSERVED, _SYSTEM_OBSERVED),
    **_pair_rules(AlertScope.VPN, _VPN_PAIRS),
    **_single_rules(AlertScope.VPN, AlertDisposition.RAISED, _VPN_RAISED),
    **_single_rules(AlertScope.VPN, AlertDisposition.OBSERVED, _VPN_OBSERVED),
}

APPLICABLE_SYSTEM_EVENTS: Final = frozenset(
    event for event, rule in _EVENT_CATALOG.items() if rule.scope is AlertScope.SYSTEM
)
APPLICABLE_VPN_EVENTS: Final = frozenset(
    event for event, rule in _EVENT_CATALOG.items() if rule.scope is AlertScope.VPN
)

_EXCLUDED_PREFIXES: Final = (
    "SYSTEM_ADB_",
    "SYSTEM_CHASSIS_",
    "SYSTEM_CLUSTERING_",
    "SYSTEM_HA_",
    "SYSTEM_LINK_",
    "SYSTEM_NAB_",
    "SYSTEM_ROUTING_",
    "SYSTEM_CFGSYNC_",
    "SYSTEM_AD_TRANSACTION",
    "SYSTEM_AD_TRANSACTED",
    "VPN_BRIDGING_",
    "VPN_CLUSTERING_",
    "VPN_REPLICATION_",
    "VPN_AD_TRANSACTION",
    "VPN_AD_TRANSACTED",
)

_EXCLUDED_EVENTS: Final = frozenset(
    {
        "SYSTEM_AUTHENTICATION_ADMIN_BIND_FAIL",
        "SYSTEM_AUTHENTICATION_ADMIN_CONN_DOWN",
        "SYSTEM_AUTHENTICATION_ADMIN_CONN_UP",
        "SYSTEM_AUTHENTICATION_BIND_CONN_DOWN",
        "SYSTEM_AUTHENTICATION_BIND_CONN_UP",
        "SYSTEM_AUTHENTICATION_CRL_DOWNLOAD_FAILED",
        "SYSTEM_AUTHENTICATION_CRL_DOWNLOAD_SUCCESS",
        "SYSTEM_DNS_NAME_SERVER_DOWN",
        "SYSTEM_DNS_NAME_SERVER_UP",
        "SYSTEM_NTP_SERVER_DOWN",
        "SYSTEM_NTP_SERVER_UP",
    }
)


def _is_capability_excluded(event: str) -> bool:
    """Return whether ``event`` belongs to a feature absent from this topology."""
    return event in _EXCLUDED_EVENTS or event.startswith(_EXCLUDED_PREFIXES)


def _aware_utc(value: datetime) -> datetime:
    """Normalize an aware clock value or stop before emitting a false timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventMonitorError(EventMonitorRefusal.CLOCK)
    return value.astimezone(UTC)


def _pipeline_alert(event: str, now: datetime) -> BrokerAlert:
    """Build one safe monitor-health alert without including rejected input."""
    return BrokerAlert(
        observed_at=_aware_utc(now),
        scope=AlertScope.MONITOR,
        severity=BrokerSeverity.ERR,
        event=event,
        condition=event,
        disposition=AlertDisposition.PIPELINE_DEGRADED,
    )


class BrokerEventProcessor:
    """Validate and classify one already-bounded broker JSON event line."""

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Inject the clock used only for pipeline-health alerts."""
        self._clock = clock

    def refusal(self) -> BrokerAlert:
        """Return a redacted alert for malformed or over-bound input."""
        return _pipeline_alert(_INPUT_REFUSED, self._clock())

    def source_closed(self) -> BrokerAlert:
        """Return the alert that prevents an ended source from looking healthy."""
        return _pipeline_alert(_SOURCE_CLOSED, self._clock())

    def source_failed(self) -> BrokerAlert:
        """Return the alert used when the bounded source itself refuses a read."""
        return _pipeline_alert(_SOURCE_FAILED, self._clock())

    def process(self, line: bytes) -> BrokerAlert | None:
        """Return a safe catalog alert, a safe pipeline alert, or an intentional ignore."""
        if len(line) > MAX_JSON_EVENT_BYTES:
            return self.refusal()
        try:
            wire = _BrokerEventWire.model_validate_json(line)
        except ValidationError:
            return self.refusal()
        if wire.scope is EventScope.CLIENT or _is_capability_excluded(wire.event):
            return None
        rule = _EVENT_CATALOG.get(wire.event)
        if rule is None:
            return _pipeline_alert(_CATALOG_GAP, self._clock())
        if rule.scope.value != wire.scope.value or rule.severity is not wire.severity:
            return self.refusal()
        return BrokerAlert(
            observed_at=_aware_utc(wire.time),
            scope=rule.scope,
            severity=wire.severity,
            event=wire.event,
            condition=rule.condition,
            disposition=rule.disposition,
        )


def render_alert(alert: BrokerAlert) -> str:
    """Render one closed alert as stable single-line JSON."""
    document = {
        "condition": alert.condition,
        "disposition": alert.disposition.value,
        "event": alert.event,
        "observedAt": alert.observed_at.isoformat().replace("+00:00", "Z"),
        "scope": alert.scope.value,
        "severity": alert.severity.value,
        "source": "solace-pubsubplus",
    }
    return json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _line_payload(chunk: bytes) -> bytes:
    """Remove one line ending without changing any other untrusted bytes."""
    if chunk.endswith(b"\n"):
        chunk = chunk[:-1]
    if chunk.endswith(b"\r"):
        chunk = chunk[:-1]
    return chunk


def _read_bounded(source: BoundedLineSource) -> tuple[bytes | None, bool]:
    """Read and, when necessary, drain exactly one line with constant memory."""
    chunk = source.readline(_READ_LIMIT)
    if not chunk:
        return None, False
    payload = _line_payload(chunk)
    oversized = len(payload) > MAX_JSON_EVENT_BYTES
    while chunk and not chunk.endswith(b"\n") and len(chunk) == _READ_LIMIT:
        chunk = source.readline(_READ_LIMIT)
        oversized = True
    return payload if not oversized else b"", oversized


def _deliver(emit: Callable[[BrokerAlert], None], alert: BrokerAlert) -> None:
    """Convert only the output boundary's expected failure into a typed refusal."""
    try:
        emit(alert)
    except OSError as error:
        raise EventMonitorError(EventMonitorRefusal.ALERT_DELIVERY) from error


def monitor_stream(
    source: BoundedLineSource,
    emit: Callable[[BrokerAlert], None],
    *,
    processor: BrokerEventProcessor | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
) -> StreamSummary:
    """Process synchronously; distinguish requested shutdown from source loss."""
    selected = processor or BrokerEventProcessor()
    lines = alerts = ignored = refused = 0
    while True:
        try:
            payload, oversized = _read_bounded(source)
        except OSError as error:
            _deliver(emit, selected.source_failed())
            raise EventMonitorError(EventMonitorRefusal.SOURCE_READ) from error
        if payload is None:
            if stop_requested():
                return StreamSummary(lines, alerts, ignored, refused)
            _deliver(emit, selected.source_closed())
            return StreamSummary(lines, alerts + 1, ignored, refused)
        lines += 1
        alert = selected.refusal() if oversized else selected.process(payload)
        if alert is None:
            ignored += 1
            continue
        refused += alert.event == _INPUT_REFUSED
        _deliver(emit, alert)
        alerts += 1


def _write_alert(alert: BrokerAlert) -> None:
    """Write and flush one safe alert to the supervising log collector."""
    sys.stdout.write(f"{render_alert(alert)}\n")
    sys.stdout.flush()


def main() -> int:
    """Run continuously; an ended source or failed boundary is never a zero exit."""
    try:
        monitor_stream(sys.stdin.buffer, _write_alert)
    except EventMonitorError:
        return 3
    return 2


@contextmanager
def _shutdown_signals(request: Callable[[], None]) -> Iterator[None]:
    """Install callback-only process stop handlers and restore previous handlers."""
    kinds = (signal.SIGINT, signal.SIGTERM)
    previous = tuple((kind, signal.getsignal(kind)) for kind in kinds)

    def stop(_number: int, _frame: object) -> None:
        request()

    for kind in kinds:
        signal.signal(kind, stop)
    try:
        yield
    finally:
        for kind, handler in previous:
            signal.signal(kind, handler)


def retained_log_main() -> int:
    """Continuously monitor the exact retained event file until graceful shutdown."""
    stopped = Event()
    source = RetainedEventLogSource(
        RETAINED_EVENT_LOG,
        running=lambda: not stopped.is_set(),
    )
    try:
        with _shutdown_signals(stopped.set):
            monitor_stream(
                source,
                _write_alert,
                stop_requested=stopped.is_set,
            )
    except EventMonitorError:
        return 3
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
