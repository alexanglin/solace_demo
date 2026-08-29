"""Focused mutation witnesses for reducer control and typed wire extraction."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from aerial_rescue_contracts.view import (
    Connectivity,
    DashboardEvent,
    EventClass,
    FoldApplied,
    FoldDuplicate,
    FoldOutcome,
    FoldRefused,
    MissionLifecycle,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerCheckpoint,
    ReducerRefusal,
    SectorState,
    SimulatedFleetMember,
    Telemetry,
    fold_ordered_event,
    ordered_event_digest,
    prepare_checkpoint,
)

MISSION_ID = "mission-synthetic-0001"
SIMULATED_MEMBER_ID = "drone-sim-01"
SECTOR_ID = "sector-01"
EVENT_TIME = "2026-08-24T12:00:01.000Z"


@dataclass(frozen=True)
class _OwnedState:
    """The complete state one reducer variant may observably own."""

    lifecycle: MissionLifecycle
    connectivity: Connectivity
    telemetry: Telemetry | None
    sector_state: SectorState
    assignee: str | None


@dataclass(frozen=True)
class _OrdinalOutcome:
    """The public outcome and retained ordinal expected from one ordering case."""

    outcome_type: type[FoldApplied | FoldDuplicate | FoldRefused]
    refusal: ReducerRefusal | None
    retained_ordinal: int


def _checkpoint() -> ReducerCheckpoint:
    """Return one prepared reducer checkpoint."""
    return prepare_checkpoint(
        PreparedMission(
            MISSION_ID,
            None,
            (SIMULATED_MEMBER_ID,),
            (),
            (SECTOR_ID,),
        )
    )


def _event(
    kind: str,
    event_class: EventClass,
    data: dict[str, object],
    *,
    ordinal: int = 1,
) -> OrderedDashboardEvent:
    """Return one valid first event for the prepared checkpoint."""
    return OrderedDashboardEvent(
        ordinal,
        DashboardEvent(kind, event_class, MISSION_ID, EVENT_TIME, data),
    )


def _checkpoint_after(events: tuple[OrderedDashboardEvent, ...]) -> ReducerCheckpoint:
    """Fold one valid prefix through the public reducer API."""
    checkpoint = _checkpoint()
    for event in events:
        outcome = fold_ordered_event(checkpoint, event)
        if not isinstance(outcome, FoldApplied):
            raise TypeError(outcome)
        checkpoint = outcome.checkpoint
    return checkpoint


def _outcome_refusal(outcome: FoldOutcome) -> ReducerRefusal | None:
    """Return a public fold refusal, or ``None`` for accepted outcomes."""
    return outcome.refusal if isinstance(outcome, FoldRefused) else None


REDUCER_STATE_CASES = (
    pytest.param(
        _event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),
        _OwnedState(
            MissionLifecycle.SEARCHING,
            Connectivity.CONNECTED,
            None,
            SectorState.UNASSIGNED,
            None,
        ),
        id="mission-lifecycle",
    ),
    pytest.param(
        _event(
            "connectivityChanged",
            EventClass.CONNECTIVITY,
            {"droneId": SIMULATED_MEMBER_ID, "connectivity": "DEGRADED"},
        ),
        _OwnedState(
            MissionLifecycle.PLANNED,
            Connectivity.DEGRADED,
            None,
            SectorState.UNASSIGNED,
            None,
        ),
        id="connectivity",
    ),
    pytest.param(
        _event(
            "droneTelemetry",
            EventClass.TELEMETRY,
            {
                "droneId": SIMULATED_MEMBER_ID,
                "latitudeMicrodegrees": 44_475_000,
                "longitudeMicrodegrees": -79_245_000,
                "batteryPercent": 96,
                "altitudeMetres": 83,
                "headingDegrees": 45,
                "groundSpeedCentimetresPerSecond": 950,
            },
        ),
        _OwnedState(
            MissionLifecycle.PLANNED,
            Connectivity.CONNECTED,
            Telemetry(44_475_000, -79_245_000, 96, 83, 45, 950),
            SectorState.UNASSIGNED,
            None,
        ),
        id="telemetry",
    ),
    pytest.param(
        _event(
            "sectorLifecycle",
            EventClass.MISSION,
            {
                "sectorId": SECTOR_ID,
                "state": "ASSIGNED",
                "assignedMemberId": SIMULATED_MEMBER_ID,
            },
        ),
        _OwnedState(
            MissionLifecycle.PLANNED,
            Connectivity.CONNECTED,
            None,
            SectorState.ASSIGNED,
            SIMULATED_MEMBER_ID,
        ),
        id="sector-lifecycle",
    ),
)


@pytest.mark.parametrize(
    (
        "ordered_event",
        "expected",
    ),
    REDUCER_STATE_CASES,
)
def test_each_reducer_updates_only_its_owned_state(
    ordered_event: OrderedDashboardEvent,
    expected: _OwnedState,
) -> None:
    # Arrange
    checkpoint = _checkpoint()

    # Act
    outcome = fold_ordered_event(checkpoint, ordered_event)

    # Assert
    assert isinstance(outcome, FoldApplied)
    assert outcome.checkpoint is not checkpoint
    assert outcome.checkpoint.latest_event_digest == ordered_event_digest(ordered_event)
    state = outcome.checkpoint.state
    assert state.latest_audit_ordinal == ordered_event.audit_ordinal
    assert state.current_mission is not None
    assert state.current_mission.lifecycle is expected.lifecycle
    member = state.fleet[0]
    assert isinstance(member, SimulatedFleetMember)
    assert member.connectivity is expected.connectivity
    assert member.telemetry == expected.telemetry
    sector = state.sectors[0]
    assert sector.state is expected.sector_state
    assert sector.assigned_member_id == expected.assignee


@pytest.mark.parametrize(
    ("prefix", "incoming", "expected"),
    [
        pytest.param(
            (_event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),),
            _event(
                "connectivityChanged",
                EventClass.CONNECTIVITY,
                {"droneId": SIMULATED_MEMBER_ID, "connectivity": "DEGRADED"},
                ordinal=2,
            ),
            _OrdinalOutcome(FoldApplied, None, 2),
            id="successor",
        ),
        pytest.param(
            (_event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),),
            _event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),
            _OrdinalOutcome(FoldDuplicate, None, 1),
            id="exact-duplicate",
        ),
        pytest.param(
            (_event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),),
            _event("missionLifecycle", EventClass.MISSION, {"lifecycle": "ABORTED"}),
            _OrdinalOutcome(FoldRefused, ReducerRefusal.ORDINAL_DIVERGENCE, 1),
            id="divergent-duplicate",
        ),
        pytest.param(
            (
                _event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),
                _event(
                    "connectivityChanged",
                    EventClass.CONNECTIVITY,
                    {"droneId": SIMULATED_MEMBER_ID, "connectivity": "DEGRADED"},
                    ordinal=2,
                ),
            ),
            _event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),
            _OrdinalOutcome(FoldRefused, ReducerRefusal.ORDINAL_REGRESSION, 2),
            id="regression",
        ),
        pytest.param(
            (_event("missionLifecycle", EventClass.MISSION, {"lifecycle": "SEARCHING"}),),
            _event(
                "connectivityChanged",
                EventClass.CONNECTIVITY,
                {"droneId": SIMULATED_MEMBER_ID, "connectivity": "DEGRADED"},
                ordinal=3,
            ),
            _OrdinalOutcome(FoldRefused, ReducerRefusal.ORDINAL_GAP, 1),
            id="gap",
        ),
    ],
)
def test_public_fold_distinguishes_every_ordinal_outcome(
    prefix: tuple[OrderedDashboardEvent, ...],
    incoming: OrderedDashboardEvent,
    expected: _OrdinalOutcome,
) -> None:
    # Arrange
    checkpoint = _checkpoint_after(prefix)

    # Act
    outcome = fold_ordered_event(checkpoint, incoming)

    # Assert
    assert type(outcome) is expected.outcome_type
    assert _outcome_refusal(outcome) is expected.refusal
    assert outcome.checkpoint.state.latest_audit_ordinal == expected.retained_ordinal
