"""Ordered dashboard reduction and replay-state identities.

The reducer is the contracts-owned, side-effect-free twin used by live snapshots and replay.
These tests pin its immutable checkpoint, exact ordinal/witness discipline, four state-owner
rules, refusal precedence, and canonical documents before a browser adapter depends on them.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from typing import Protocol, cast

import pytest
from aerial_rescue_contracts.digest import Context, digest
from aerial_rescue_contracts.view import (
    EMPTY_CHECKPOINT,
    MAX_SAFE_INTEGER,
    CheckpointAccepted,
    CheckpointRefused,
    Connectivity,
    DashboardEvent,
    DashboardReducedState,
    DeclaredOnlyFleetMember,
    EventClass,
    FoldApplied,
    FoldDuplicate,
    FoldRefused,
    MissionLifecycle,
    OrderedDashboardEvent,
    Participation,
    PreparedMission,
    ReducerCheckpoint,
    ReducerError,
    ReducerRefusal,
    SectorState,
    SimulatedFleetMember,
    append_meaningful_timeline_event,
    checkpoint_from_replay,
    checkpoint_from_snapshot,
    fold_ordered_event,
    is_timeline_event,
    ordered_event_digest,
    ordered_event_document,
    prepare_checkpoint,
    replace_timeline_from_snapshot,
    state_digest,
    state_document,
    timeline_from_events,
)

MISSION = "mission-synthetic-0001"
OTHER_MISSION = "mission-synthetic-0002"
SIMULATED_ONE = "drone-sim-01"
SIMULATED_TWO = "drone-sim-02"
DECLARED_ONLY = "drone-vision-01"
SECTOR_ONE = "sector-01"
SECTOR_TWO = "sector-02"
TIME = "2026-08-24T12:00:01.000Z"
SHA256_HEX_LENGTH = 64
ORDERED_WITNESS_VARIANTS = 3
EXPECTED_BATTERY_PERCENT = 96
EXPECTED_LATEST_ORDINAL = 4


class CheckpointFactory(Protocol):
    """The common snapshot and replay anchor-construction interface."""

    def __call__(
        self,
        state: DashboardReducedState,
        latest_event_digest: str | None,
        *,
        expected_state_digest: str | None = None,
    ) -> CheckpointAccepted | CheckpointRefused:
        """Build one validated checkpoint anchor."""


@dataclass(frozen=True)
class _EventContext:
    """Optional mission and presentation-time overrides for a normalized test event."""

    mission: str = MISSION
    time: str = TIME


DEFAULT_EVENT_CONTEXT = _EventContext()


def _prepared(
    *,
    simulated: tuple[str, ...] = (SIMULATED_TWO, SIMULATED_ONE),
    declared_only: tuple[str, ...] = (DECLARED_ONLY,),
    sectors: tuple[str, ...] = (SECTOR_TWO, SECTOR_ONE),
) -> PreparedMission:
    """Return one deliberately unsorted prepared mission."""
    return PreparedMission(MISSION, None, simulated, declared_only, sectors)


def _event(
    ordinal: int,
    kind: str,
    event_class: EventClass,
    data: dict[str, object],
    context: _EventContext = DEFAULT_EVENT_CONTEXT,
) -> OrderedDashboardEvent:
    """Return one normalized event at a durable audit ordinal."""
    return OrderedDashboardEvent(
        ordinal,
        DashboardEvent(kind, event_class, context.mission, context.time, data),
    )


def _mission_event(
    ordinal: int,
    lifecycle: str = "SEARCHING",
    *,
    mission: str = MISSION,
    time: str = TIME,
) -> OrderedDashboardEvent:
    """Return one mission-lifecycle event."""
    return _event(
        ordinal,
        "missionLifecycle",
        EventClass.MISSION,
        {"lifecycle": lifecycle},
        context=_EventContext(mission, time),
    )


def _connectivity_event(
    ordinal: int,
    drone_id: str = SIMULATED_ONE,
    connectivity: str = "DEGRADED",
    *,
    mission: str = MISSION,
) -> OrderedDashboardEvent:
    """Return one explicit connectivity event."""
    return _event(
        ordinal,
        "connectivityChanged",
        EventClass.CONNECTIVITY,
        {"droneId": drone_id, "connectivity": connectivity},
        context=_EventContext(mission),
    )


def _telemetry_event(
    ordinal: int,
    drone_id: str = SIMULATED_ONE,
    *,
    mission: str = MISSION,
) -> OrderedDashboardEvent:
    """Return one complete telemetry event."""
    return _event(
        ordinal,
        "droneTelemetry",
        EventClass.TELEMETRY,
        {
            "droneId": drone_id,
            "latitudeMicrodegrees": 44_475_000,
            "longitudeMicrodegrees": -79_245_000,
            "batteryPercent": 96,
            "altitudeMetres": 83,
            "headingDegrees": 45,
            "groundSpeedCentimetresPerSecond": 950,
        },
        context=_EventContext(mission),
    )


def _sector_event(
    ordinal: int,
    *,
    sector_id: str = SECTOR_ONE,
    state: str = "ASSIGNED",
    assigned_member_id: str | None = SIMULATED_ONE,
    mission: str = MISSION,
) -> OrderedDashboardEvent:
    """Return one sector-lifecycle event."""
    return _event(
        ordinal,
        "sectorLifecycle",
        EventClass.MISSION,
        {
            "sectorId": sector_id,
            "state": state,
            "assignedMemberId": assigned_member_id,
        },
        context=_EventContext(mission),
    )


def _checkpoint_after(*events: OrderedDashboardEvent) -> ReducerCheckpoint:
    """Return the checkpoint after an all-applied event sequence."""
    checkpoint = prepare_checkpoint(_prepared())
    for ordered_event in events:
        outcome = fold_ordered_event(checkpoint, ordered_event)
        if not isinstance(outcome, FoldApplied):
            raise TypeError(outcome)
        checkpoint = outcome.checkpoint
    return checkpoint


def test_the_empty_checkpoint_has_no_prepared_mission_or_event_witness() -> None:
    # Arrange
    checkpoint = EMPTY_CHECKPOINT

    # Act
    observed = (
        checkpoint.state.current_mission,
        checkpoint.state.fleet,
        checkpoint.state.latest_audit_ordinal,
        checkpoint.state.sectors,
        checkpoint.latest_event_digest,
    )

    # Assert
    assert observed == (None, (), 0, (), None)


def test_a_prepared_checkpoint_is_planned_explicit_and_utf8_byte_sorted() -> None:
    # Arrange
    prepared = _prepared()

    # Act
    checkpoint = prepare_checkpoint(prepared)

    # Assert
    assert checkpoint.state.current_mission is not None
    assert checkpoint.state.current_mission.identifier == MISSION
    assert checkpoint.state.current_mission.lifecycle is MissionLifecycle.PLANNED
    assert checkpoint.state.current_mission.predecessor_identifier is None
    assert tuple(member.identifier for member in checkpoint.state.fleet) == (
        SIMULATED_ONE,
        SIMULATED_TWO,
        DECLARED_ONLY,
    )
    assert tuple(sector.identifier for sector in checkpoint.state.sectors) == (
        SECTOR_ONE,
        SECTOR_TWO,
    )
    assert all(
        member.connectivity is Connectivity.CONNECTED and member.telemetry is None
        for member in checkpoint.state.fleet
        if isinstance(member, SimulatedFleetMember)
    )
    assert isinstance(checkpoint.state.fleet[-1], DeclaredOnlyFleetMember)
    assert all(
        sector.state is SectorState.UNASSIGNED and sector.assigned_member_id is None
        for sector in checkpoint.state.sectors
    )
    assert checkpoint.state.latest_audit_ordinal == 0
    assert checkpoint.latest_event_digest is None


@pytest.mark.parametrize(
    ("prepared", "expected_refusal", "expected_value"),
    [
        (
            _prepared(simulated=(SIMULATED_ONE, SIMULATED_ONE)),
            ReducerRefusal.DUPLICATE_MEMBER,
            SIMULATED_ONE,
        ),
        (
            _prepared(simulated=(SIMULATED_ONE,), declared_only=(SIMULATED_ONE,)),
            ReducerRefusal.DUPLICATE_MEMBER,
            SIMULATED_ONE,
        ),
        (
            _prepared(sectors=(SECTOR_ONE, SECTOR_ONE)),
            ReducerRefusal.DUPLICATE_SECTOR,
            SECTOR_ONE,
        ),
    ],
)
def test_preparation_refuses_duplicate_semantic_identifiers(
    prepared: PreparedMission,
    expected_refusal: ReducerRefusal,
    expected_value: str,
) -> None:
    # Arrange
    candidate = prepared

    # Act
    with pytest.raises(ReducerError) as captured:
        prepare_checkpoint(candidate)

    # Assert
    assert captured.value.refusal is expected_refusal
    assert captured.value.attribute == "identifier"
    assert captured.value.value == expected_value


def test_normalized_event_data_is_snapshotted_and_cannot_be_mutated() -> None:
    # Arrange
    source = {"lifecycle": "SEARCHING"}
    event = DashboardEvent("missionLifecycle", EventClass.MISSION, MISSION, TIME, source)

    # Act
    source["lifecycle"] = "ABORTED"

    # Assert
    assert event.data == {"lifecycle": "SEARCHING"}
    with pytest.raises(TypeError):
        cast("dict[str, object]", event.data)["lifecycle"] = "EXHAUSTED"


@pytest.mark.parametrize("factory", [checkpoint_from_snapshot, checkpoint_from_replay])
def test_snapshot_and_replay_anchors_build_the_same_validated_checkpoint(
    factory: CheckpointFactory,
) -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1))
    # Act
    outcome = factory(checkpoint.state, checkpoint.latest_event_digest)

    # Assert
    assert isinstance(outcome, CheckpointAccepted)
    assert outcome.checkpoint == checkpoint


def test_the_empty_snapshot_anchor_is_canonical_without_an_event_witness() -> None:
    # Arrange
    state = EMPTY_CHECKPOINT.state

    # Act
    outcome = checkpoint_from_snapshot(state, None)

    # Assert
    assert isinstance(outcome, CheckpointAccepted)
    assert outcome.checkpoint == EMPTY_CHECKPOINT


@pytest.mark.parametrize(
    ("ordinal", "witness", "expected_refusal"),
    [
        (0, "a" * 64, ReducerRefusal.ORDINAL_WITNESS),
        (1, None, ReducerRefusal.ORDINAL_WITNESS),
        (1, "A" * 64, ReducerRefusal.WITNESS_FORM),
        (1, "a" * 63, ReducerRefusal.WITNESS_FORM),
    ],
)
def test_checkpoint_anchor_validation_refuses_invalid_witnesses(
    ordinal: int,
    witness: str | None,
    expected_refusal: ReducerRefusal,
) -> None:
    # Arrange
    prepared = prepare_checkpoint(_prepared()).state
    state = replace(prepared, latest_audit_ordinal=ordinal)

    # Act
    outcome = checkpoint_from_snapshot(state, witness)

    # Assert
    assert isinstance(outcome, CheckpointRefused)
    assert outcome.refusal is expected_refusal


def test_checkpoint_anchor_witness_form_precedes_state_semantics_and_pairing() -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1))
    noncanonical = replace(checkpoint.state, fleet=tuple(reversed(checkpoint.state.fleet)))

    # Act
    outcome = checkpoint_from_snapshot(noncanonical, "NOT-A-DIGEST")

    # Assert
    assert isinstance(outcome, CheckpointRefused)
    assert outcome.refusal is ReducerRefusal.WITNESS_FORM


def test_fold_event_boundary_precedes_a_malformed_checkpoint_witness() -> None:
    # Arrange
    checkpoint = replace(
        _checkpoint_after(_mission_event(1)),
        latest_event_digest=cast("str", 7),
    )
    malformed_event = _mission_event(1, "NOT_A_LIFECYCLE")

    # Act
    outcome = fold_ordered_event(checkpoint, malformed_event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.EVENT_DATA
    assert outcome.attribute == "lifecycle"
    assert outcome.checkpoint is checkpoint


@pytest.mark.parametrize(
    ("defect", "expected_refusal", "expected_attribute"),
    [
        ("witnessForm", ReducerRefusal.WITNESS_FORM, "latestEventDigest"),
        ("noncanonicalState", ReducerRefusal.NONCANONICAL_ANCHOR_STATE, "fleet"),
        ("missingWitness", ReducerRefusal.ORDINAL_WITNESS, "latestEventDigest"),
        ("unexpectedWitness", ReducerRefusal.ORDINAL_WITNESS, "latestEventDigest"),
    ],
)
def test_fold_validates_the_checkpoint_anchor_before_ordinal_handling(
    defect: str,
    expected_refusal: ReducerRefusal,
    expected_attribute: str,
) -> None:
    # Arrange
    event = _mission_event(1)
    applied = _checkpoint_after(event)
    if defect == "witnessForm":
        checkpoint = replace(applied, latest_event_digest=cast("str", 7))
    elif defect == "noncanonicalState":
        state = replace(applied.state, fleet=tuple(reversed(applied.state.fleet)))
        checkpoint = replace(applied, state=state)
    elif defect == "missingWitness":
        checkpoint = replace(applied, latest_event_digest=None)
    else:
        initial = prepare_checkpoint(_prepared())
        checkpoint = replace(initial, latest_event_digest="a" * SHA256_HEX_LENGTH)

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.attribute == expected_attribute
    assert outcome.checkpoint is checkpoint


@pytest.mark.parametrize("factory", [checkpoint_from_snapshot, checkpoint_from_replay])
@pytest.mark.parametrize(
    "defect",
    [
        "unsorted",
        "duplicate",
        "assignment",
        "missingAssignment",
        "externalAssignment",
        "missingMission",
    ],
)
def test_checkpoint_anchors_refuse_noncanonical_reduced_state_with_a_valid_witness(
    factory: CheckpointFactory,
    defect: str,
) -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1), _sector_event(2))
    state = checkpoint.state
    if defect == "unsorted":
        candidate = replace(state, fleet=tuple(reversed(state.fleet)))
    elif defect == "duplicate":
        candidate = replace(state, sectors=(state.sectors[0], state.sectors[0]))
    elif defect == "assignment":
        invalid_sector = replace(
            state.sectors[0],
            state=SectorState.UNASSIGNED,
            assigned_member_id=SIMULATED_ONE,
        )
        candidate = replace(state, sectors=(invalid_sector, *state.sectors[1:]))
    elif defect == "missingAssignment":
        invalid_sector = replace(state.sectors[0], assigned_member_id=None)
        candidate = replace(state, sectors=(invalid_sector, *state.sectors[1:]))
    elif defect == "externalAssignment":
        invalid_sector = replace(state.sectors[0], assigned_member_id=DECLARED_ONLY)
        candidate = replace(state, sectors=(invalid_sector, *state.sectors[1:]))
    else:
        candidate = replace(state, current_mission=None)

    # Act
    outcome = factory(candidate, checkpoint.latest_event_digest)

    # Assert
    assert isinstance(outcome, CheckpointRefused)
    assert outcome.refusal is ReducerRefusal.NONCANONICAL_ANCHOR_STATE
    assert outcome.attribute in {"fleet", "sectors", "assignedMemberId", "currentMission"}


@pytest.mark.parametrize("factory", [checkpoint_from_snapshot, checkpoint_from_replay])
def test_anchor_server_state_digest_verification_occurs_after_anchor_semantics(
    factory: CheckpointFactory,
) -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1))

    # Act
    accepted = factory(
        checkpoint.state,
        checkpoint.latest_event_digest,
        expected_state_digest=state_digest(checkpoint.state),
    )
    malformed = factory(
        checkpoint.state,
        checkpoint.latest_event_digest,
        expected_state_digest="NOT-A-DIGEST",
    )
    mismatched = factory(
        checkpoint.state,
        checkpoint.latest_event_digest,
        expected_state_digest="f" * 64,
    )

    # Assert
    assert isinstance(accepted, CheckpointAccepted)
    assert isinstance(malformed, CheckpointRefused)
    assert malformed.refusal is ReducerRefusal.SERVER_DIGEST_FORM
    assert isinstance(mismatched, CheckpointRefused)
    assert mismatched.refusal is ReducerRefusal.SERVER_DIGEST_MISMATCH


def test_ordered_event_hash_uses_the_exact_versioned_document_and_context() -> None:
    # Arrange
    ordered_event = OrderedDashboardEvent(
        1,
        DashboardEvent(
            "missionLifecycle",
            EventClass.MISSION,
            "mission-synthetic-0001",
            "2026-08-24T12:00:00.000Z",
            {"lifecycle": "PLANNED"},
        ),
    )
    expected_document = {
        "canonicalizationVersion": 1,
        "auditOrdinal": 1,
        "event": {
            "kind": "missionLifecycle",
            "eventClass": "MISSION",
            "mission": "mission-synthetic-0001",
            "time": "2026-08-24T12:00:00.000Z",
            "data": {"lifecycle": "PLANNED"},
        },
    }

    # Act
    document = ordered_event_document(ordered_event)
    computed = ordered_event_digest(ordered_event)

    # Assert
    assert document == expected_document
    assert computed == "eafd46f76f706183272a016f99d5468c7ebde22de44600092f81992903509c25"
    assert computed == digest(Context.ORDERED_DASHBOARD_EVENT, expected_document)


def test_ordered_event_witness_includes_ordinal_and_presentation_time() -> None:
    # Arrange
    baseline = _mission_event(1, time="2026-08-24T12:00:01.000Z")
    another_ordinal = _mission_event(2, time="2026-08-24T12:00:01.000Z")
    another_time = _mission_event(1, time="2026-08-24T12:00:02.000Z")

    # Act
    digests = tuple(
        ordered_event_digest(candidate) for candidate in (baseline, another_ordinal, another_time)
    )

    # Assert
    assert len(set(digests)) == ORDERED_WITNESS_VARIANTS


def test_mission_lifecycle_updates_only_the_current_mission_and_ordinal() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _mission_event(1, "SEARCHING")

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldApplied)
    assert outcome.checkpoint.state.current_mission is not None
    assert outcome.checkpoint.state.current_mission.lifecycle is MissionLifecycle.SEARCHING
    assert outcome.checkpoint.state.fleet is checkpoint.state.fleet
    assert outcome.checkpoint.state.sectors is checkpoint.state.sectors
    assert outcome.checkpoint.state.latest_audit_ordinal == 1
    assert outcome.checkpoint.latest_event_digest == ordered_event_digest(event)


def test_a_matching_server_state_digest_accepts_the_successor_after_all_state_rules() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _mission_event(1)
    projected = fold_ordered_event(checkpoint, event)
    if not isinstance(projected, FoldApplied):
        raise TypeError(projected)
    expected = state_digest(projected.checkpoint.state)

    # Act
    outcome = fold_ordered_event(checkpoint, event, expected_state_digest=expected)

    # Assert
    assert isinstance(outcome, FoldApplied)
    assert outcome.checkpoint == projected.checkpoint


@pytest.mark.parametrize(
    ("server_digest", "expected_refusal"),
    [
        ("f" * 64, ReducerRefusal.SERVER_DIGEST_MISMATCH),
        ("NOT-A-DIGEST", ReducerRefusal.SERVER_DIGEST_FORM),
    ],
)
def test_server_state_digest_mismatch_refuses_the_completed_fold_with_exact_rollback(
    server_digest: str,
    expected_refusal: ReducerRefusal,
) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _mission_event(1)

    # Act
    outcome = fold_ordered_event(checkpoint, event, expected_state_digest=server_digest)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.attribute == "digest"
    assert outcome.value == server_digest
    assert outcome.checkpoint is checkpoint


def test_server_state_digest_verification_occurs_after_event_semantics() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    malformed = _connectivity_event(1, "unknown-member")

    # Act
    outcome = fold_ordered_event(
        checkpoint,
        malformed,
        expected_state_digest="NOT-A-DIGEST",
    )

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.UNKNOWN_MEMBER
    assert outcome.checkpoint is checkpoint


def test_an_exact_duplicate_also_verifies_the_server_state_digest() -> None:
    # Arrange
    event = _mission_event(1)
    checkpoint = _checkpoint_after(event)

    # Act
    outcome = fold_ordered_event(
        checkpoint,
        event,
        expected_state_digest="f" * 64,
    )

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.SERVER_DIGEST_MISMATCH
    assert outcome.checkpoint is checkpoint


def test_connectivity_updates_only_one_simulated_members_explicit_state() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _connectivity_event(1, SIMULATED_TWO, "OFFLINE")

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldApplied)
    members = outcome.checkpoint.state.fleet
    first = cast("SimulatedFleetMember", members[0])
    second = cast("SimulatedFleetMember", members[1])
    assert first is checkpoint.state.fleet[0]
    assert second.connectivity is Connectivity.OFFLINE
    assert second.telemetry is None
    assert outcome.checkpoint.state.sectors is checkpoint.state.sectors


def test_telemetry_supersedes_only_the_reading_and_never_infers_connectivity() -> None:
    # Arrange
    degraded = _checkpoint_after(_connectivity_event(1))
    event = _telemetry_event(2)

    # Act
    outcome = fold_ordered_event(degraded, event)

    # Assert
    assert isinstance(outcome, FoldApplied)
    member = cast("SimulatedFleetMember", outcome.checkpoint.state.fleet[0])
    assert member.connectivity is Connectivity.DEGRADED
    assert member.telemetry is not None
    assert member.telemetry.battery_percent == EXPECTED_BATTERY_PERCENT
    assert outcome.checkpoint.state.sectors is degraded.state.sectors


def test_sector_lifecycle_updates_only_the_sector_authority() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _sector_event(1, state="AT_RISK")

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldApplied)
    assert outcome.checkpoint.state.fleet is checkpoint.state.fleet
    assert outcome.checkpoint.state.sectors[0].state is SectorState.AT_RISK
    assert outcome.checkpoint.state.sectors[0].assigned_member_id == SIMULATED_ONE
    assert not hasattr(outcome.checkpoint.state.fleet[0], "assigned_member_id")


def test_an_unassigned_sector_event_with_null_assignment_is_a_valid_successor() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _sector_event(1, state="UNASSIGNED", assigned_member_id=None)

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldApplied)
    assert outcome.checkpoint.state.sectors[0].state is SectorState.UNASSIGNED
    assert outcome.checkpoint.state.sectors[0].assigned_member_id is None


def test_an_exact_same_ordinal_duplicate_returns_the_identical_checkpoint() -> None:
    # Arrange
    event = _mission_event(1)
    checkpoint = _checkpoint_after(event)

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldDuplicate)
    assert outcome.checkpoint is checkpoint


@pytest.mark.parametrize(
    ("event", "expected_refusal"),
    [
        (_mission_event(2, "ABORTED"), ReducerRefusal.ORDINAL_DIVERGENCE),
        (_mission_event(1), ReducerRefusal.ORDINAL_REGRESSION),
        (_mission_event(4), ReducerRefusal.ORDINAL_GAP),
    ],
)
def test_ordinal_refusals_preserve_the_prior_checkpoint(
    event: OrderedDashboardEvent,
    expected_refusal: ReducerRefusal,
) -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1), _connectivity_event(2))

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.checkpoint is checkpoint


def test_ordinal_refusal_precedes_mission_and_target_validation() -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1))
    cases = (
        (
            _connectivity_event(1, "unknown-member", mission=OTHER_MISSION),
            ReducerRefusal.ORDINAL_DIVERGENCE,
        ),
        (
            _connectivity_event(0, "unknown-member", mission=OTHER_MISSION),
            ReducerRefusal.EVENT_DATA,
        ),
        (
            _connectivity_event(3, "unknown-member", mission=OTHER_MISSION),
            ReducerRefusal.ORDINAL_GAP,
        ),
    )

    # Act
    outcomes = tuple(fold_ordered_event(checkpoint, event) for event, _ in cases)

    # Assert
    assert tuple(cast("FoldRefused", outcome).refusal for outcome in outcomes) == tuple(
        expected for _, expected in cases
    )


@pytest.mark.parametrize(
    ("checkpoint", "event", "expected_refusal"),
    [
        (EMPTY_CHECKPOINT, _mission_event(1), ReducerRefusal.MISSION_UNPREPARED),
        (
            prepare_checkpoint(_prepared()),
            _connectivity_event(1, "unknown-member", mission=OTHER_MISSION),
            ReducerRefusal.MISSION_MISMATCH,
        ),
    ],
)
def test_successor_events_require_the_prepared_mission_before_target_validation(
    checkpoint: ReducerCheckpoint,
    event: OrderedDashboardEvent,
    expected_refusal: ReducerRefusal,
) -> None:
    # Arrange
    prior = checkpoint

    # Act
    outcome = fold_ordered_event(prior, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.checkpoint is prior


@pytest.mark.parametrize(
    "event",
    [
        _connectivity_event(1, "unknown-member"),
        _telemetry_event(1, "unknown-member"),
    ],
)
def test_member_events_refuse_an_unknown_member(event: OrderedDashboardEvent) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.UNKNOWN_MEMBER
    assert outcome.attribute == "droneId"


@pytest.mark.parametrize(
    "event",
    [
        _connectivity_event(1, DECLARED_ONLY),
        _telemetry_event(1, DECLARED_ONLY),
    ],
)
def test_declared_only_members_refuse_connectivity_and_telemetry(
    event: OrderedDashboardEvent,
) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.DECLARED_ONLY_MEMBER
    assert outcome.value == DECLARED_ONLY


def test_sector_events_refuse_an_unknown_sector_before_assignment_lookup() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = _sector_event(1, sector_id="unknown-sector", assigned_member_id="unknown-member")

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.UNKNOWN_SECTOR
    assert outcome.attribute == "sectorId"


@pytest.mark.parametrize(
    ("event", "expected_refusal"),
    [
        (
            _sector_event(1, state="UNASSIGNED", assigned_member_id=SIMULATED_ONE),
            ReducerRefusal.ASSIGNMENT_FORBIDDEN,
        ),
        (
            _sector_event(1, state="ASSIGNED", assigned_member_id=None),
            ReducerRefusal.ASSIGNMENT_REQUIRED,
        ),
        (
            _sector_event(1, assigned_member_id="unknown-member"),
            ReducerRefusal.INVALID_ASSIGNEE,
        ),
        (
            _sector_event(1, assigned_member_id=DECLARED_ONLY),
            ReducerRefusal.INVALID_ASSIGNEE,
        ),
    ],
)
def test_sector_assignment_refusals_are_explicit_and_copy_on_write(
    event: OrderedDashboardEvent,
    expected_refusal: ReducerRefusal,
) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.checkpoint is checkpoint


@pytest.mark.parametrize(
    ("event", "expected_refusal", "attribute"),
    [
        (
            _event(1, "missionLifecycle", EventClass.CONNECTIVITY, {"lifecycle": "SEARCHING"}),
            ReducerRefusal.EVENT_DATA,
            "eventClass",
        ),
        (
            _event(1, "missionLifecycle", EventClass.MISSION, {}),
            ReducerRefusal.EVENT_DATA,
            "lifecycle",
        ),
        (
            _event(
                1,
                "missionLifecycle",
                EventClass.MISSION,
                {"lifecycle": "SEARCHING", "unexpected": True},
            ),
            ReducerRefusal.EVENT_DATA,
            "unexpected",
        ),
        (
            _event(1, "unknownKind", EventClass.MISSION, {}),
            ReducerRefusal.UNPROJECTED,
            "kind",
        ),
    ],
)
def test_event_variant_refusals_are_typed(
    event: OrderedDashboardEvent,
    expected_refusal: ReducerRefusal,
    attribute: str,
) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.attribute == attribute


@pytest.mark.parametrize(
    ("event", "expected_refusal", "attribute"),
    [
        (
            _event(
                4,
                "unknownKind",
                EventClass.MISSION,
                {},
                _EventContext(OTHER_MISSION),
            ),
            ReducerRefusal.UNPROJECTED,
            "kind",
        ),
        (
            _event(
                4,
                cast("str", 7),
                EventClass.MISSION,
                {"lifecycle": "SEARCHING"},
            ),
            ReducerRefusal.UNPROJECTED,
            "kind",
        ),
        (
            _event(
                4,
                "missionLifecycle",
                EventClass.CONNECTIVITY,
                {},
                context=_EventContext(OTHER_MISSION),
            ),
            ReducerRefusal.EVENT_DATA,
            "eventClass",
        ),
        (
            _event(
                4,
                "missionLifecycle",
                EventClass.MISSION,
                {"lifecycle": "SEARCHING"},
                context=_EventContext("NOT AN IDENTIFIER"),
            ),
            ReducerRefusal.EVENT_DATA,
            "mission",
        ),
        (
            _mission_event(4, time="not-an-instant"),
            ReducerRefusal.EVENT_DATA,
            "time",
        ),
        (
            _event(
                4,
                "missionLifecycle",
                cast("EventClass", "not-an-event-class"),
                {"lifecycle": "SEARCHING"},
            ),
            ReducerRefusal.EVENT_DATA,
            "eventClass",
        ),
        (
            _event(
                4,
                "missionLifecycle",
                EventClass.MISSION,
                {"lifecycle": "SEARCHING"},
                _EventContext(time=cast("str", 7)),
            ),
            ReducerRefusal.EVENT_DATA,
            "time",
        ),
    ],
)
def test_event_boundary_preflight_precedes_ordinal_mission_and_target_semantics(
    event: OrderedDashboardEvent,
    expected_refusal: ReducerRefusal,
    attribute: str,
) -> None:
    # Arrange
    checkpoint = _checkpoint_after(_mission_event(1))

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is expected_refusal
    assert outcome.attribute == attribute
    assert outcome.checkpoint is checkpoint


@pytest.mark.parametrize(
    ("event", "attribute"),
    [
        (_mission_event(1, "NOT_A_LIFECYCLE"), "lifecycle"),
        (
            _event(
                1,
                "connectivityChanged",
                EventClass.CONNECTIVITY,
                {"droneId": 7, "connectivity": "CONNECTED"},
            ),
            "droneId",
        ),
        (
            _event(
                1,
                "droneTelemetry",
                EventClass.TELEMETRY,
                {
                    **dict(_telemetry_event(1).event.data),
                    "latitudeMicrodegrees": 90_000_001,
                },
            ),
            "latitudeMicrodegrees",
        ),
    ],
)
def test_event_values_are_refused_without_coercion(
    event: OrderedDashboardEvent,
    attribute: str,
) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.EVENT_DATA
    assert outcome.attribute == attribute
    assert outcome.checkpoint is checkpoint


@pytest.mark.parametrize("ordinal", [True, -1, 0, MAX_SAFE_INTEGER + 1])
def test_an_ordinal_outside_the_canonical_integer_boundary_is_event_data(
    ordinal: object,
) -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    event = replace(_mission_event(1), audit_ordinal=cast("int", ordinal))

    # Act
    outcome = fold_ordered_event(checkpoint, event)

    # Assert
    assert isinstance(outcome, FoldRefused)
    assert outcome.refusal is ReducerRefusal.EVENT_DATA
    assert outcome.attribute == "auditOrdinal"
    assert outcome.value == ordinal


def test_state_document_matches_the_closed_wire_shape_and_excludes_the_witness() -> None:
    # Arrange
    checkpoint = _checkpoint_after(
        _mission_event(1),
        _connectivity_event(2),
        _telemetry_event(3),
        _sector_event(4),
    )

    # Act
    document = state_document(checkpoint.state)

    # Assert
    assert set(document) == {
        "canonicalizationVersion",
        "stateVersion",
        "currentMission",
        "fleet",
        "latestAuditOrdinal",
        "sectors",
    }
    assert document["latestAuditOrdinal"] == EXPECTED_LATEST_ORDINAL
    assert "latestEventDigest" not in document
    assert "'time':" not in repr(document)
    fleet = cast("list[dict[str, object]]", document["fleet"])
    assert fleet[0]["participation"] == "SIMULATED"
    assert fleet[-1] == {"identifier": DECLARED_ONLY, "participation": "DECLARED_ONLY"}
    assert "assignedMemberId" not in fleet[0]


def test_replay_state_digest_excludes_event_time_and_checkpoint_witness() -> None:
    # Arrange
    first = _checkpoint_after(_mission_event(1, time="2026-08-24T12:00:01.000Z"))
    second = _checkpoint_after(_mission_event(1, time="2026-08-24T12:00:02.000Z"))

    # Act
    state_digests = (state_digest(first.state), state_digest(second.state))
    witness_digests = (first.latest_event_digest, second.latest_event_digest)

    # Assert
    assert state_digests[0] == state_digests[1]
    assert witness_digests[0] != witness_digests[1]
    assert state_digests[0] == digest(Context.REPLAY_STATE, state_document(first.state))


def test_the_replay_state_digest_is_deterministic_across_ten_complete_folds() -> None:
    # Arrange
    events = (
        _mission_event(1),
        _connectivity_event(2),
        _telemetry_event(3),
        _sector_event(4),
        _sector_event(5, state="SEARCHED"),
        _mission_event(6, "EXHAUSTED"),
    )

    # Act
    digests = tuple(state_digest(_checkpoint_after(*events).state) for _ in range(10))

    # Assert
    assert len(set(digests)) == 1
    assert digests[0].startswith(tuple("0123456789abcdef"))
    assert len(digests[0]) == SHA256_HEX_LENGTH


def test_timeline_helpers_include_only_meaningful_non_telemetry_events_in_input_order() -> None:
    # Arrange
    events = (
        _mission_event(1),
        _telemetry_event(2),
        _connectivity_event(3),
        _sector_event(4),
    )

    # Act
    timeline = timeline_from_events(events)

    # Assert
    assert tuple(event.audit_ordinal for event in timeline) == (1, 3, 4)
    assert tuple(is_timeline_event(event.event) for event in events) == (True, False, True, True)


def test_snapshot_timeline_replacement_sorts_deduplicates_copies_and_excludes_telemetry() -> None:
    # Arrange
    first_at_three = _connectivity_event(3)
    source = [
        _mission_event(4),
        _telemetry_event(2),
        first_at_three,
        _sector_event(3),
    ]

    # Act
    timeline = replace_timeline_from_snapshot(source)
    source.clear()

    # Assert
    assert tuple(event.audit_ordinal for event in timeline) == (3, 4)
    assert timeline[0] is first_at_three
    assert all(is_timeline_event(event.event) for event in timeline)


def test_meaningful_suffix_append_is_ordered_copy_on_write_and_idempotent() -> None:
    # Arrange
    prior = (_mission_event(1), _sector_event(3))
    meaningful = _connectivity_event(2)

    # Act
    appended = append_meaningful_timeline_event(prior, meaningful)
    ignored = (
        append_meaningful_timeline_event(prior, _telemetry_event(4)),
        append_meaningful_timeline_event(prior, _connectivity_event(3)),
    )

    # Assert
    assert tuple(event.audit_ordinal for event in appended) == (1, 2, 3)
    assert appended is not prior
    assert ignored == (prior, prior)
    assert ignored[0] is prior
    assert ignored[1] is prior


def test_state_and_checkpoint_records_are_frozen_and_applied_updates_are_copy_on_write() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())
    outcome = fold_ordered_event(checkpoint, _mission_event(1))

    # Act
    with pytest.raises(FrozenInstanceError) as captured:
        checkpoint.state.latest_audit_ordinal = 9  # type: ignore[misc]

    # Assert
    assert "latest_audit_ordinal" in str(captured.value)
    assert isinstance(outcome, FoldApplied)
    assert checkpoint.state.latest_audit_ordinal == 0
    assert outcome.checkpoint is not checkpoint
    assert outcome.checkpoint.state is not checkpoint.state


def test_prepared_fleet_members_expose_only_their_participation_variant() -> None:
    # Arrange
    checkpoint = prepare_checkpoint(_prepared())

    # Act
    variants = tuple(member.participation for member in checkpoint.state.fleet)

    # Assert
    assert variants == (
        Participation.SIMULATED,
        Participation.SIMULATED,
        Participation.DECLARED_ONLY,
    )
