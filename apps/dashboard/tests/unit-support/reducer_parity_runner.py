"""Emit Python reducer evidence for the dashboard's cross-language replay oracle."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.view import (
    CheckpointAccepted,
    Connectivity,
    DashboardEvent,
    DashboardReducedState,
    DeclaredOnlyFleetMember,
    EventClass,
    FoldApplied,
    FoldDuplicate,
    FoldOutcome,
    FoldRefused,
    Mission,
    MissionLifecycle,
    OrderedDashboardEvent,
    ReducerCheckpoint,
    Sector,
    SectorState,
    SimulatedFleetMember,
    Telemetry,
    append_meaningful_timeline_event,
    checkpoint_from_replay,
    fold_ordered_event,
    replace_timeline_from_snapshot,
    state_digest,
    state_document,
)
from aerial_rescue_dashboard_api.wire import parse_wire_document

REPLAY_BUNDLE_SCHEMA_ID = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json"
)
MAX_FIXTURE_BYTES = 1_048_576
MAX_PARITY_RUNS = 100
EXPECTED_ARGUMENT_COUNT = 3
NOT_AN_OBJECT = "validated replay member is not an object"
NOT_AN_ARRAY = "validated replay member is not an array"
NOT_A_STRING = "validated replay member is not a string"
NOT_AN_INTEGER = "validated replay member is not an integer"
NO_FINAL_EVENT = "parity replay has no final event"
USAGE = "usage: reducer_parity_runner.py FIXTURE RUNS"
RUNS_OUT_OF_BOUNDS = "parity run count is outside the supported bound"
FIXTURE_OUT_OF_BOUNDS = "parity fixture is outside the supported byte bound"


def _mapping(value: object) -> Mapping[str, object]:
    """Return one already-validated object from the Pydantic wire projection."""
    if not isinstance(value, Mapping):
        raise TypeError(NOT_AN_OBJECT)
    return cast("Mapping[str, object]", value)


def _sequence(value: object) -> Sequence[object]:
    """Return one already-validated array from the Pydantic wire projection."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TypeError(NOT_AN_ARRAY)
    return cast("Sequence[object]", value)


def _text(value: object) -> str:
    """Return one already-validated string without coercion."""
    if not isinstance(value, str):
        raise TypeError(NOT_A_STRING)
    return value


def _integer(value: object) -> int:
    """Return one already-validated integer without accepting a boolean."""
    if type(value) is not int:
        raise TypeError(NOT_AN_INTEGER)
    return value


def _optional_text(value: object) -> str | None:
    """Return one already-validated optional string."""
    return None if value is None else _text(value)


def _telemetry(document: Mapping[str, object]) -> Telemetry:
    """Adapt one validated latest-telemetry wire object to the pure reducer type."""
    return Telemetry(
        _integer(document["latitudeMicrodegrees"]),
        _integer(document["longitudeMicrodegrees"]),
        _integer(document["batteryPercent"]),
        _integer(document["altitudeMetres"]),
        _integer(document["headingDegrees"]),
        _integer(document["groundSpeedCentimetresPerSecond"]),
    )


def _fleet_member(document: Mapping[str, object]) -> SimulatedFleetMember | DeclaredOnlyFleetMember:
    """Adapt one validated discriminated fleet member without inventing external state."""
    identifier = _text(document["identifier"])
    if document["participation"] == "DECLARED_ONLY":
        return DeclaredOnlyFleetMember(identifier)
    raw_telemetry = document["telemetry"]
    telemetry = None if raw_telemetry is None else _telemetry(_mapping(raw_telemetry))
    return SimulatedFleetMember(
        identifier,
        connectivity=Connectivity(_text(document["connectivity"])),
        telemetry=telemetry,
    )


def _state(document: Mapping[str, object]) -> DashboardReducedState:
    """Adapt one Pydantic-validated reduced-state document to the pure reducer type."""
    raw_mission = document["currentMission"]
    mission_document = None if raw_mission is None else _mapping(raw_mission)
    mission = (
        None
        if mission_document is None
        else Mission(
            _text(mission_document["identifier"]),
            MissionLifecycle(_text(mission_document["lifecycle"])),
            _optional_text(mission_document["predecessorIdentifier"]),
        )
    )
    fleet = tuple(_fleet_member(_mapping(item)) for item in _sequence(document["fleet"]))
    sectors = tuple(
        Sector(
            _text(sector["identifier"]),
            SectorState(_text(sector["state"])),
            _optional_text(sector["assignedMemberId"]),
        )
        for sector in (_mapping(item) for item in _sequence(document["sectors"]))
    )
    return DashboardReducedState(
        mission,
        fleet,
        _integer(document["latestAuditOrdinal"]),
        sectors,
    )


def _event(document: Mapping[str, object]) -> DashboardEvent:
    """Adapt one Pydantic-validated normalized event to the pure reducer type."""
    return DashboardEvent(
        _text(document["kind"]),
        EventClass[_text(document["eventClass"])],
        _text(document["mission"]),
        _text(document["time"]),
        _mapping(document["data"]),
    )


def _ordered_event(document: Mapping[str, object]) -> OrderedDashboardEvent:
    """Adapt one Pydantic-validated ordered event to the pure reducer type."""
    return OrderedDashboardEvent(
        _integer(document["auditOrdinal"]),
        _event(_mapping(document["event"])),
    )


def _bundle_parts(
    document: Mapping[str, object],
) -> tuple[DashboardReducedState, str | None, tuple[OrderedDashboardEvent, ...]]:
    """Return the reducer-owned replay members after canonical and Pydantic validation."""
    return (
        _state(_mapping(document["initialState"])),
        _optional_text(document["latestEventDigest"]),
        tuple(_ordered_event(_mapping(item)) for item in _sequence(document["events"])),
    )


def _checkpoint(initial_state: DashboardReducedState, witness: str | None) -> ReducerCheckpoint:
    """Require the production replay-anchor validator to accept the shared fixture."""
    outcome = checkpoint_from_replay(initial_state, witness)
    if not isinstance(outcome, CheckpointAccepted):
        message = f"replay anchor refused: {outcome.refusal.name}"
        raise TypeError(message)
    return outcome.checkpoint


def _disposition(outcome: FoldOutcome) -> str:
    """Normalize Python reducer outcomes to the browser's structured disposition spelling."""
    if isinstance(outcome, FoldApplied):
        return "APPLIED"
    if isinstance(outcome, FoldDuplicate):
        return "DUPLICATE"
    return f"REFUSED:{outcome.refusal.name}"


def _accepted_checkpoint(outcome: FoldOutcome) -> ReducerCheckpoint:
    """Return an accepted successor or fail the parity fixture itself."""
    if isinstance(outcome, FoldRefused):
        message = f"parity event refused: {outcome.refusal.name}"
        raise TypeError(message)
    return outcome.checkpoint


def _run_once(
    initial_state: DashboardReducedState,
    witness: str | None,
    events: tuple[OrderedDashboardEvent, ...],
) -> dict[str, object]:
    """Fold one independent replay and emit byte-for-byte checkpoint evidence."""
    checkpoint = _checkpoint(initial_state, witness)
    timeline = replace_timeline_from_snapshot(())
    steps: list[dict[str, object]] = []
    for ordered_event in events:
        outcome = fold_ordered_event(checkpoint, ordered_event)
        checkpoint = _accepted_checkpoint(outcome)
        if isinstance(outcome, FoldApplied):
            timeline = append_meaningful_timeline_event(timeline, ordered_event)
        steps.append(
            {
                "canonicalState": canonical_bytes(state_document(checkpoint.state)).decode(),
                "disposition": _disposition(outcome),
                "latestEventDigest": checkpoint.latest_event_digest,
                "stateDigest": state_digest(checkpoint.state),
                "timelineOrdinals": [item.audit_ordinal for item in timeline],
            }
        )
    return {
        "finalDigest": state_digest(checkpoint.state),
        "initialCanonicalState": canonical_bytes(state_document(initial_state)).decode(),
        "initialLatestEventDigest": witness,
        "steps": steps,
    }


def _tampered_events(final_event: OrderedDashboardEvent) -> Mapping[str, OrderedDashboardEvent]:
    """Construct the six required ordinal and five-field tampering cases."""
    event = final_event.event
    return {
        "auditOrdinal": replace(final_event, audit_ordinal=final_event.audit_ordinal + 2),
        "data": replace(final_event, event=replace(event, data={"lifecycle": "ABORTED"})),
        "eventClass": replace(final_event, event=replace(event, event_class=EventClass.TELEMETRY)),
        "kind": replace(final_event, event=replace(event, kind="unprojectedKind")),
        "compoundKindMission": replace(
            final_event,
            event=replace(event, kind="unprojectedKind", mission="INVALID_MISSION"),
        ),
        "mission": replace(
            final_event,
            event=replace(event, mission="mission-synthetic-other"),
        ),
        "time": replace(
            final_event,
            event=replace(event, time="2026-08-25T12:00:13.000Z"),
        ),
    }


def _final_checkpoint(
    initial_state: DashboardReducedState,
    witness: str | None,
    events: tuple[OrderedDashboardEvent, ...],
) -> ReducerCheckpoint:
    """Fold the accepted parity events and return their final checkpoint."""
    checkpoint = _checkpoint(initial_state, witness)
    for ordered_event in events:
        checkpoint = _accepted_checkpoint(fold_ordered_event(checkpoint, ordered_event))
    return checkpoint


def _tampering_evidence(
    initial_state: DashboardReducedState,
    witness: str | None,
    events: tuple[OrderedDashboardEvent, ...],
) -> Mapping[str, str]:
    """Emit reducer dispositions for duplicate, tampering, and digest rollback cases."""
    if not events:
        raise RuntimeError(NO_FINAL_EVENT)
    checkpoint = _final_checkpoint(initial_state, witness, events)
    final_event = events[-1]
    evidence = {"duplicate": _disposition(fold_ordered_event(checkpoint, final_event))}
    evidence.update(
        {
            name: _disposition(fold_ordered_event(checkpoint, tampered))
            for name, tampered in _tampered_events(final_event).items()
        }
    )
    evidence["serverDigest"] = _disposition(
        fold_ordered_event(checkpoint, final_event, expected_state_digest="0" * 64)
    )
    return evidence


def _provisional_checksum(document: Mapping[str, object]) -> str:
    """Hash the canonical bundle with only the checksum member absent, per the R1 fixture rule."""
    integrity = dict(_mapping(document["integrity"]))
    del integrity["checksum"]
    material = dict(document)
    material["integrity"] = integrity
    return hashlib.sha256(canonical_bytes(material)).hexdigest()


def _evidence(raw: bytes, runs: int) -> Mapping[str, object]:
    """Validate and fold one raw replay fixture through both production Python boundaries."""
    validated = parse_wire_document(REPLAY_BUNDLE_SCHEMA_ID, raw)
    document = cast(
        "Mapping[str, object]",
        validated.model_dump(mode="python", by_alias=True),
    )
    initial_state, witness, events = _bundle_parts(document)
    integrity = _mapping(document["integrity"])
    return {
        "checksum": _provisional_checksum(document),
        "expectedFinalDigest": _text(integrity["expectedFinalDigest"]),
        "runs": [_run_once(initial_state, witness, events) for _ in range(runs)],
        "tampering": _tampering_evidence(initial_state, witness, events),
    }


def main(arguments: Sequence[str]) -> int:
    """Read one bounded shared fixture and print deterministic reducer evidence as JSON."""
    if len(arguments) != EXPECTED_ARGUMENT_COUNT:
        raise ValueError(USAGE)
    fixture = Path(arguments[1]).resolve(strict=True)
    runs = int(arguments[2])
    if not 1 <= runs <= MAX_PARITY_RUNS:
        raise ValueError(RUNS_OUT_OF_BOUNDS)
    raw = fixture.read_bytes()
    if len(raw) > MAX_FIXTURE_BYTES:
        raise ValueError(FIXTURE_OUT_OF_BOUNDS)
    print(
        json.dumps(_evidence(raw, runs), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
