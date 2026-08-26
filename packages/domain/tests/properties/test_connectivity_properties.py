"""Property-based invariants of the connectivity machine.

Module-level functions with ``derandomize`` for the same reason as the contracts package's
property modules: mutmut re-runs pytest in one process, and a flapping example set would turn
the mutation score into a moving number.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.connectivity import (
    INITIAL_STATUS,
    ConnectivityState,
    ConnectivityStatus,
    ConnectivityThresholds,
    heartbeat_missed,
    heartbeat_received,
)

_SEVERITY = {
    ConnectivityState.CONNECTED: 0,
    ConnectivityState.DEGRADED: 1,
    ConnectivityState.OFFLINE: 2,
}


@st.composite
def thresholds(draw: st.DrawFn) -> ConnectivityThresholds:
    """Draw a valid threshold record with small counts."""
    degraded = draw(st.integers(min_value=1, max_value=6))
    offline = degraded + draw(st.integers(min_value=1, max_value=6))
    recover = draw(st.integers(min_value=1, max_value=4))
    return ConnectivityThresholds(degraded, offline, recover)


HEARD_INTERVALS = st.lists(st.booleans(), max_size=30)


def _fold(heard: list[bool], counts: ConnectivityThresholds) -> ConnectivityStatus:
    """Apply one transition per interval, ``True`` meaning a heartbeat was heard."""
    status = INITIAL_STATUS
    for was_heard in heard:
        step = heartbeat_received if was_heard else heartbeat_missed
        status = step(status, counts)
    return status


def _trailing_run(heard: list[bool], value: bool) -> int:
    """Count how many trailing intervals equal ``value``."""
    count = 0
    for was_heard in reversed(heard):
        if was_heard is not value:
            break
        count += 1
    return count


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(HEARD_INTERVALS, thresholds())
def test_counters_equal_the_trailing_runs_of_misses_and_heartbeats(
    heard: list[bool], counts: ConnectivityThresholds
) -> None:
    # Arrange
    expected = (_trailing_run(heard, False), _trailing_run(heard, True))

    # Act
    status = _fold(heard, counts)

    # Assert
    assert (status.consecutive_misses, status.consecutive_heartbeats) == expected


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(HEARD_INTERVALS, thresholds())
def test_a_miss_never_improves_and_a_heartbeat_never_worsens_the_state(
    heard: list[bool], counts: ConnectivityThresholds
) -> None:
    # Arrange
    status = _fold(heard, counts)
    severity = _SEVERITY[status.state]

    # Act
    after_miss = heartbeat_missed(status, counts)
    after_heartbeat = heartbeat_received(status, counts)

    # Assert
    assert _SEVERITY[after_miss.state] >= severity >= _SEVERITY[after_heartbeat.state]


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(HEARD_INTERVALS, thresholds())
def test_enough_consecutive_misses_are_offline_and_enough_heartbeats_are_connected(
    heard: list[bool], counts: ConnectivityThresholds
) -> None:
    # Arrange
    lost_script = heard + [False] * counts.misses_to_offline
    recovered_script = heard + [True] * counts.heartbeats_to_recover

    # Act
    lost = _fold(lost_script, counts)
    recovered = _fold(recovered_script, counts)

    # Assert
    assert (lost.state, recovered.state) == (
        ConnectivityState.OFFLINE,
        ConnectivityState.CONNECTED,
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(HEARD_INTERVALS, thresholds())
def test_the_fold_is_deterministic(heard: list[bool], counts: ConnectivityThresholds) -> None:
    # Arrange
    first = _fold(heard, counts)

    # Act
    second = _fold(
        list(heard),
        ConnectivityThresholds(
            counts.misses_to_degraded, counts.misses_to_offline, counts.heartbeats_to_recover
        ),
    )

    # Assert
    assert first == second
