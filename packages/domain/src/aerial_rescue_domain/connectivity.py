"""The drone connectivity state machine, counted in consecutive heartbeat intervals.

The states and rules are the decision in
``docs/adr/0039-drone-connectivity-states-and-recovery.md``; the provisional counts live in
``docs/operating-parameters.md`` and are injected, never defaulted. This module is pure: it
reads no clock. The adapter decides once per interval whether a heartbeat was observed and
applies exactly one transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_domain import DomainError


class ConnectivityState(Enum):
    """Where a drone's link stands, from healthy to lost."""

    CONNECTED = "connected"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class ConnectivityRefusal(Enum):
    """Why a threshold record cannot be built."""

    MISS_THRESHOLDS = "misses to degraded must be at least one and below misses to offline"
    RECOVERY_COUNT = "heartbeats to recover must be at least one"


class ConnectivityError(DomainError):
    """A threshold record the machine refuses, carrying the refusal as structured data."""


_MINIMUM_COUNT: Final = 1


@dataclass(frozen=True)
class ConnectivityThresholds:
    """The injected counts; each is provisional and has its home in operating-parameters.md."""

    misses_to_degraded: int
    misses_to_offline: int
    heartbeats_to_recover: int

    def __post_init__(self) -> None:
        """Refuse a configuration that could never degrade or never recover."""
        _check_thresholds(self)


@dataclass(frozen=True)
class ConnectivityStatus:
    """One drone's link state together with the streak that produced it."""

    state: ConnectivityState
    consecutive_misses: int
    consecutive_heartbeats: int


INITIAL_STATUS: Final = ConnectivityStatus(ConnectivityState.CONNECTED, 0, 0)
"""Every drone starts connected with no history."""


def _check_thresholds(thresholds: ConnectivityThresholds) -> None:
    """Raise unless the degraded count is at least one and below the offline count."""
    if not _MINIMUM_COUNT <= thresholds.misses_to_degraded < thresholds.misses_to_offline:
        raise ConnectivityError(
            ConnectivityRefusal.MISS_THRESHOLDS,
            (thresholds.misses_to_degraded, thresholds.misses_to_offline),
        )
    if thresholds.heartbeats_to_recover < _MINIMUM_COUNT:
        raise ConnectivityError(
            ConnectivityRefusal.RECOVERY_COUNT, thresholds.heartbeats_to_recover
        )


def heartbeat_missed(
    status: ConnectivityStatus, thresholds: ConnectivityThresholds
) -> ConnectivityStatus:
    """Return the status after an interval without a heartbeat; a miss never improves the state.

    Args:
        status: The status before the interval.
        thresholds: The injected counts.

    Returns:
        The status after the interval, with the heartbeat streak reset.
    """
    misses = status.consecutive_misses + 1
    if status.state is ConnectivityState.OFFLINE or misses >= thresholds.misses_to_offline:
        state = ConnectivityState.OFFLINE
    elif status.state is ConnectivityState.DEGRADED or misses >= thresholds.misses_to_degraded:
        state = ConnectivityState.DEGRADED
    else:
        state = ConnectivityState.CONNECTED
    return ConnectivityStatus(state, misses, 0)


def heartbeat_received(
    status: ConnectivityStatus, thresholds: ConnectivityThresholds
) -> ConnectivityStatus:
    """Return the status after an interval with a heartbeat; a heartbeat never worsens the state.

    Args:
        status: The status before the interval.
        thresholds: The injected counts.

    Returns:
        The status after the interval, with the miss streak reset; the state returns to
        connected once the heartbeat streak reaches the recovery count.
    """
    heartbeats = status.consecutive_heartbeats + 1
    recovered = heartbeats >= thresholds.heartbeats_to_recover
    state = ConnectivityState.CONNECTED if recovered else status.state
    return ConnectivityStatus(state, 0, heartbeats)
