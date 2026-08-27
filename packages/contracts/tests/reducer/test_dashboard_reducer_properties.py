"""Property evidence for ordered dashboard reduction."""

from __future__ import annotations

from aerial_rescue_contracts.view import (
    DashboardEvent,
    EventClass,
    FoldApplied,
    FoldDuplicate,
    FoldRefused,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerRefusal,
    fold_ordered_event,
    prepare_checkpoint,
    state_digest,
)
from hypothesis import given
from hypothesis import strategies as st

MISSION = "mission-synthetic-0001"


def _prepared(simulated: tuple[str, ...]) -> PreparedMission:
    """Return a prepared mission for a generated simulated roster."""
    return PreparedMission(MISSION, None, simulated, ("drone-vision-01",), ("sector-01",))


def _mission_event(ordinal: int) -> OrderedDashboardEvent:
    """Return one searching event at ``ordinal``."""
    return OrderedDashboardEvent(
        ordinal,
        DashboardEvent(
            "missionLifecycle",
            EventClass.MISSION,
            MISSION,
            "2026-08-24T12:00:01.000Z",
            {"lifecycle": "SEARCHING"},
        ),
    )


@given(st.permutations(("drone-sim-01", "drone-sim-02", "drone-sim-03")))
def test_preparation_order_never_changes_state_or_replay_digest(order: list[str]) -> None:
    # Arrange
    baseline = prepare_checkpoint(_prepared(("drone-sim-01", "drone-sim-02", "drone-sim-03")))

    # Act
    candidate = prepare_checkpoint(_prepared(tuple(order)))

    # Assert
    assert candidate.state == baseline.state
    assert state_digest(candidate.state) == state_digest(baseline.state)


@given(st.integers(min_value=2, max_value=1000))
def test_every_positive_non_successor_gap_refuses_without_mutation(ordinal: int) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared(("drone-sim-01",)))
    event = _mission_event(ordinal)

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.ORDINAL_GAP
    assert outcome.checkpoint is checkpoint


@given(st.sampled_from(("SEARCHING", "EXHAUSTED", "ABORTED")))
def test_every_exact_duplicate_is_idempotent_for_all_non_planned_lifecycles(
    lifecycle: str,
) -> None:
    # Arrange
    initial = prepare_checkpoint(_prepared(("drone-sim-01",)))
    event = OrderedDashboardEvent(
        1,
        DashboardEvent(
            "missionLifecycle",
            EventClass.MISSION,
            MISSION,
            "2026-08-24T12:00:01.000Z",
            {"lifecycle": lifecycle},
        ),
    )
    applied = fold_ordered_event(initial, event)
    if not isinstance(applied, FoldApplied):
        raise TypeError(applied)

    # Act
    duplicate = fold_ordered_event(applied.checkpoint, event)

    # Assert
    assert isinstance(duplicate, FoldDuplicate)
    assert duplicate.checkpoint is applied.checkpoint
