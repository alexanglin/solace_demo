"""Reducer coverage for the application data plane's recorded timeline variants."""

from __future__ import annotations

from aerial_rescue_contracts.view import (
    DashboardEvent,
    EventClass,
    FoldApplied,
    FoldOutcome,
    FoldRefused,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerCheckpoint,
    ReducerRefusal,
    fold_ordered_event,
    prepare_checkpoint,
)

MISSION = "mission-synthetic-0001"
TIME = "2026-08-24T12:00:01.000Z"


def _prepared() -> PreparedMission:
    """Return one closed synthetic scenario anchor."""
    return PreparedMission(
        MISSION,
        None,
        ("drone-sim-01",),
        ("drone-vision-01",),
        ("sector-01",),
    )


def _event(
    ordinal: int,
    kind: str,
    event_class: EventClass,
    data: dict[str, object],
) -> OrderedDashboardEvent:
    """Return one normalized event at a durable audit ordinal."""
    event = DashboardEvent(kind, event_class, MISSION, TIME, data)
    return OrderedDashboardEvent(ordinal, event)


def test_recorded_timeline_variants_advance_only_the_ordinal_and_event_witness() -> None:
    # Arrange
    initial = prepare_checkpoint(_prepared())
    variants = (
        ("operatorCommand", EventClass.COMMAND),
        ("operatorApproval", EventClass.APPROVAL),
        ("agentProposal", EventClass.EVIDENCE),
        ("evidenceDecision", EventClass.EVIDENCE),
        ("salientObservation", EventClass.EVIDENCE),
        ("gatewayResponse", EventClass.AUDIT),
        ("droneCommand", EventClass.COMMAND),
        ("commandResult", EventClass.COMMAND),
        ("auditRecord", EventClass.AUDIT),
    )
    events = tuple(
        _event(ordinal, kind, event_class, {"recordId": f"record-{ordinal:02d}"})
        for ordinal, (kind, event_class) in enumerate(variants, start=1)
    )

    # Act
    checkpoints: list[ReducerCheckpoint] = [initial]
    outcomes: list[FoldOutcome] = []
    for event in events:
        outcome = fold_ordered_event(checkpoints[-1], event)
        outcomes.append(outcome)
        if isinstance(outcome, FoldApplied):
            checkpoints.append(outcome.checkpoint)

    # Assert
    assert all(isinstance(outcome, FoldApplied) for outcome in outcomes)
    assert len(checkpoints) == len(events) + 1
    baseline = initial.state
    assert all(
        checkpoint.state.current_mission is baseline.current_mission for checkpoint in checkpoints
    )
    assert all(checkpoint.state.fleet is baseline.fleet for checkpoint in checkpoints)
    assert all(checkpoint.state.sectors is baseline.sectors for checkpoint in checkpoints)
    assert tuple(checkpoint.state.latest_audit_ordinal for checkpoint in checkpoints) == tuple(
        range(len(events) + 1)
    )
    assert len({checkpoint.latest_event_digest for checkpoint in checkpoints[1:]}) == len(events)


def test_a_timeline_kind_with_the_wrong_event_class_is_refused_copy_on_write() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _event(1, "operatorCommand", EventClass.AUDIT, {"commandId": "command-0001"})

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.checkpoint is checkpoint
    assert (outcome.refusal, outcome.attribute, outcome.value) == (
        ReducerRefusal.EVENT_DATA,
        "eventClass",
        "AUDIT",
    )
