"""Mode-specific dashboard runtime lifecycle and readiness tests."""

from __future__ import annotations

import pytest
from aerial_rescue_dashboard_api.lifecycle import (
    Dependency,
    RunMode,
    RuntimePhase,
    RuntimeReadiness,
)


def test_live_readiness_requires_only_live_start_dependencies() -> None:
    # Arrange
    readiness = RuntimeReadiness(RunMode.DEGRADED_LIVE)
    readiness.begin_startup()
    readiness.set_dependency(Dependency.STORE, ready=True)
    readiness.set_dependency(Dependency.SCENARIO_CONTROL, ready=True)
    readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=True)

    # Act
    readiness.activate()
    live = readiness.assess(RunMode.DEGRADED_LIVE)
    replay = readiness.assess(RunMode.REPLAY)

    # Assert
    assert live.ready is True
    assert live.reasons == ()
    assert replay.ready is False
    assert replay.reasons == ("mode-unavailable",)
    assert readiness.accepting_mutations is True


def test_replay_readiness_does_not_require_deliberately_absent_live_dependencies() -> None:
    # Arrange
    readiness = RuntimeReadiness(RunMode.REPLAY)
    readiness.begin_startup()
    readiness.set_dependency(Dependency.REPLAY_INPUT, ready=True)

    # Act
    readiness.activate()
    result = readiness.assess(RunMode.REPLAY)

    # Assert
    assert result.ready is True
    assert result.reasons == ()
    assert readiness.phase is RuntimePhase.RUNNING


def test_dependency_loss_removes_readiness_and_recovery_restores_it() -> None:
    # Arrange
    readiness = RuntimeReadiness(RunMode.DEGRADED_LIVE)
    readiness.begin_startup()
    for dependency in (
        Dependency.STORE,
        Dependency.SCENARIO_CONTROL,
        Dependency.BROKER_DELIVERY,
    ):
        readiness.set_dependency(dependency, ready=True)
    readiness.activate()

    # Act
    readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=False)
    degraded = readiness.assess(RunMode.DEGRADED_LIVE)
    readiness.set_dependency(Dependency.BROKER_DELIVERY, ready=True)
    recovered = readiness.assess(RunMode.DEGRADED_LIVE)

    # Assert
    assert degraded.ready is False
    assert degraded.reasons == ("broker-delivery-unavailable",)
    assert recovered.ready is True


def test_shutdown_stops_mutations_before_resources_are_closed() -> None:
    # Arrange
    readiness = RuntimeReadiness(RunMode.REPLAY)
    readiness.begin_startup()
    readiness.set_dependency(Dependency.REPLAY_INPUT, ready=True)
    readiness.activate()

    # Act
    readiness.begin_shutdown()
    draining = readiness.assess(RunMode.REPLAY)
    readiness.finish_shutdown()

    # Assert
    assert draining.ready is False
    assert draining.reasons == ("shutting-down",)
    assert readiness.phase is RuntimePhase.STOPPED
    assert readiness.accepting_mutations is False


def test_invalid_lifecycle_transition_fails_closed() -> None:
    # Arrange
    readiness = RuntimeReadiness(RunMode.REPLAY)

    # Act
    with pytest.raises(RuntimeError) as captured:
        readiness.activate()

    # Assert
    assert str(captured.value) == "dashboard runtime lifecycle transition refused"


def test_pre_run_and_failed_startup_phases_never_claim_readiness() -> None:
    # Arrange
    readiness = RuntimeReadiness(RunMode.REPLAY)

    # Act
    created = readiness.assess(RunMode.REPLAY)
    readiness.begin_startup()
    starting = readiness.assess(RunMode.REPLAY)
    readiness.abort_startup()
    stopped = readiness.assess(RunMode.REPLAY)

    # Assert
    assert created.reasons == ("starting",)
    assert starting.reasons == ("starting",)
    assert stopped.reasons == ("stopped",)
    assert created.ready is starting.ready is stopped.ready is False
