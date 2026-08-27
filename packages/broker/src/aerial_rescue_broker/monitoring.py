"""Coalesced, paced, read-only queue health over the SEMP aggregate monitor view.

Routine monitoring is deliberately a smaller capability than provisioning. The adapter in
this module exposes only monitor reads, requires a dedicated management username, spaces
every SEMP request, and never offers ``send``. Queue depth comes from the parent collection's
aligned ``msgs.count`` aggregate. Active binds come from count-only transmit-flow collection
responses for observed desired queues. Neither path exposes message or flow rows.

The local pacer can bound this process, not unrelated SEMP clients. It reserves half of the
broker-wide ten-request-per-second ceiling for provisioning, Event Management Agent, and
operator diagnostics. Deployment acceptance must still account for those independent
clients together. The thirty-second successful-or-failed cache prevents a caller from
turning one routine probe into a retry storm.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_broker.provisioning import (
    MonitorRow,
    MonitorTransport,
    ProvisioningError,
    QueueDepthState,
    queue_depth_states,
    queue_tx_flow_monitor_path,
)
from aerial_rescue_broker.queues import DMQ_SUFFIX, MAX_BIND_COUNT, QUEUE_NAME_ROOT, QueueSpec
from aerial_rescue_broker.semp import HttpConnection, SempEndpoint, SempError, SempSession

MONITOR_USERNAME: Final = "aerialrescuemonitor"
"""Dedicated internal management username required by the routine read-only adapter."""

MONITOR_POLL_INTERVAL_SECONDS: Final = 30.0
"""Minimum interval between routine queue inventory attempts, successful or failed."""

ROUTINE_MONITOR_REQUESTS_PER_SECOND: Final = 5
"""This process's SEMP share, reserving half of the broker-wide ceiling for other clients."""

MONITOR_REQUEST_INTERVAL_SECONDS: Final = 1.0 / ROUTINE_MONITOR_REQUESTS_PER_SECOND
"""Minimum spacing between the individual SEMP pages a routine monitor requests."""

MAX_MONITORED_QUEUES: Final = 2_000
"""Maximum parent queue inventory aligned with the bounded SEMP page walk."""

MAX_MONITORED_BIND_COUNTS: Final = 89
"""Maximum desired queue bind fan-out for the fixed 23-drone reference fleet."""

_OWNED_PREFIX: Final = f"{QUEUE_NAME_ROOT}/"


class MonitorRefusal(Enum):
    """Why routine monitoring refused to report a queue-health snapshot."""

    IDENTITY = "routine monitoring requires its dedicated read-only identity"
    INVENTORY = "the desired queue inventory is ambiguous or exceeds the page bound"
    CLOCK = "the injected monotonic clock is invalid or moved backwards"
    READ = "the bounded aggregate SEMP read did not produce a complete snapshot"


class MonitorError(RuntimeError):
    """A typed, secret-independent routine-monitor refusal."""

    refusal: MonitorRefusal

    def __init__(self, refusal: MonitorRefusal) -> None:
        """Record only the safe refusal category; preserve detail solely as a typed cause."""
        super().__init__(refusal.value)
        self.refusal = refusal


class SempRequestPacer:
    """Space sequential monitor requests under this process's SEMP budget."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Inject monotonic time and the bounded sleeping seam."""
        self._clock = clock
        self._sleeper = sleeper
        self._last_request: float | None = None

    @staticmethod
    def _valid_instant(value: float) -> bool:
        """Return whether ``value`` can safely participate in interval arithmetic."""
        return not isinstance(value, bool) and math.isfinite(value)

    def pace(self) -> None:
        """Wait at most one request interval before the next monitor-plane page."""
        now = self._clock()
        if not self._valid_instant(now):
            raise MonitorError(MonitorRefusal.CLOCK)
        previous = self._last_request
        if previous is None:
            self._last_request = now
            return
        if now < previous:
            raise MonitorError(MonitorRefusal.CLOCK)
        wait = max(0.0, previous + MONITOR_REQUEST_INTERVAL_SECONDS - now)
        if wait:
            self._sleeper(wait)
        self._last_request = now + wait


class ReadOnlySempMonitor:
    """A paced SEMP session that exposes no configuration-plane operation."""

    def __init__(
        self,
        connection: HttpConnection,
        endpoint: SempEndpoint,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Bind only the dedicated identity to a page-paced underlying session."""
        if endpoint.username != MONITOR_USERNAME:
            raise MonitorError(MonitorRefusal.IDENTITY)
        self._session = SempSession(
            connection,
            endpoint,
            monitor_pacer=SempRequestPacer(clock=clock, sleeper=sleeper),
        )

    def read_monitor(self, path: str) -> tuple[dict[str, object], ...]:
        """Return a monitor collection without exposing a configuration writer."""
        rows = self._session.read_monitor(path)
        return tuple(dict(row) for row in rows)

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return aligned aggregate monitor rows through the paced session."""
        return self._session.read_monitor_rows(path)

    def read_monitor_count(self, path: str) -> int:
        """Return one paced child-collection total without exposing its rows."""
        return self._session.read_monitor_count(path)


@dataclass(frozen=True)
class QueueHealthSnapshot:
    """One complete desired/project-owned queue aggregate at a monotonic instant."""

    collected_at: float
    queues: tuple[QueueDepthState, ...]
    message_count: int
    primary_backlog: tuple[QueueDepthState, ...]
    nonempty_dead_messages: tuple[QueueDepthState, ...]
    unexpected_owned: tuple[QueueDepthState, ...]
    missing: tuple[str, ...]
    bind_mismatches: tuple[str, ...]
    healthy: bool


def _expected_inventory(queues: Sequence[QueueSpec]) -> dict[str, QueueSpec]:
    """Return an exact bounded desired inventory, refusing duplicate identities."""
    expected = {queue.name: queue for queue in queues}
    if not expected or len(expected) != len(queues) or len(expected) > MAX_MONITORED_BIND_COUNTS:
        raise MonitorError(MonitorRefusal.INVENTORY)
    return expected


def _is_owned(name: str, expected: dict[str, QueueSpec]) -> bool:
    """Return whether a row is desired or lies in the exact application queue namespace."""
    return name in expected or name.startswith(_OWNED_PREFIX)


def _bind_mismatches(
    observations: dict[str, QueueDepthState],
    bind_counts: dict[str, int],
    expected: dict[str, QueueSpec],
) -> tuple[str, ...]:
    """Return desired endpoints whose steady-state bind count disagrees with ownership."""
    mismatches = []
    for name, queue in expected.items():
        observation = observations.get(name)
        if observation is None:
            continue
        wanted = MAX_BIND_COUNT if queue.owner else 0
        if bind_counts.get(name) != wanted:
            mismatches.append(name)
    return tuple(sorted(mismatches))


def _snapshot(
    states: tuple[QueueDepthState, ...],
    bind_counts: dict[str, int],
    expected: dict[str, QueueSpec],
    collected_at: float,
) -> QueueHealthSnapshot:
    """Project one validated aggregate read into stable health and evidence groups."""
    observations = {state.name: state for state in states}
    owned = tuple(
        sorted(
            (state for state in states if _is_owned(state.name, expected)),
            key=lambda state: state.name,
        )
    )
    primary_backlog = tuple(
        state for state in owned if not state.name.endswith(DMQ_SUFFIX) and state.message_count
    )
    nonempty_dead_messages = tuple(
        state for state in owned if state.name.endswith(DMQ_SUFFIX) and state.message_count
    )
    unexpected_owned = tuple(state for state in owned if state.name not in expected)
    missing = tuple(sorted(set(expected) - observations.keys()))
    bind_mismatches = _bind_mismatches(observations, bind_counts, expected)
    healthy = not (missing or bind_mismatches or unexpected_owned or nonempty_dead_messages)
    return QueueHealthSnapshot(
        collected_at=collected_at,
        queues=owned,
        message_count=sum(state.message_count for state in owned),
        primary_backlog=primary_backlog,
        nonempty_dead_messages=nonempty_dead_messages,
        unexpected_owned=unexpected_owned,
        missing=missing,
        bind_mismatches=bind_mismatches,
        healthy=healthy,
    )


class QueueHealthMonitor:
    """Coalesce routine calls around one bounded parent read and desired bind fan-out."""

    def __init__(
        self,
        transport: MonitorTransport,
        vpn: str,
        expected: Sequence[QueueSpec],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the read-only transport, desired inventory, and monotonic cache clock."""
        self._transport = transport
        self._vpn = vpn
        self._expected = _expected_inventory(expected)
        self._clock = clock
        self._last_clock: float | None = None
        self._last_attempt: float | None = None
        self._snapshot: QueueHealthSnapshot | None = None
        self._failure: MonitorError | None = None

    @property
    def has_snapshot(self) -> bool:
        """Return whether at least one complete successful snapshot has been observed."""
        return self._snapshot is not None

    @property
    def last_snapshot(self) -> QueueHealthSnapshot | None:
        """Return the last complete aggregate, retained across a later failed refresh."""
        return self._snapshot

    def _now(self) -> float:
        """Read and validate monotonic time before cache arithmetic."""
        now = self._clock()
        if not SempRequestPacer._valid_instant(now):
            raise MonitorError(MonitorRefusal.CLOCK)
        if self._last_clock is not None and now < self._last_clock:
            raise MonitorError(MonitorRefusal.CLOCK)
        self._last_clock = now
        return now

    def _cached(self, now: float) -> QueueHealthSnapshot | None:
        """Return or re-raise the coalesced outcome while its attempt interval is live."""
        attempted = self._last_attempt
        if attempted is None or now - attempted >= MONITOR_POLL_INTERVAL_SECONDS:
            return None
        if self._failure is not None:
            raise self._failure
        return self._snapshot

    def poll(self) -> QueueHealthSnapshot:
        """Return one complete snapshot, reading no faster than the routine interval."""
        now = self._now()
        cached = self._cached(now)
        if cached is not None:
            return cached
        self._last_attempt = now
        try:
            states = queue_depth_states(
                self._transport,
                self._vpn,
                maximum_queues=MAX_MONITORED_QUEUES,
            )
            observed = {state.name for state in states}
            bind_counts = {
                name: self._transport.read_monitor_count(
                    queue_tx_flow_monitor_path(self._vpn, name)
                )
                for name in sorted(self._expected.keys() & observed)
            }
        except (ProvisioningError, SempError) as error:
            failure = MonitorError(MonitorRefusal.READ)
            self._failure = failure
            raise failure from error
        snapshot = _snapshot(states, bind_counts, self._expected, now)
        self._snapshot = snapshot
        self._failure = None
        return snapshot
