"""Deterministic dashboard-owned mission lifecycle event construction tests."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_broker.ingress import load_runtime_schema_registry
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import BINDINGS, decode_envelope
from aerial_rescue_dashboard_api.messaging.mission_lifecycle import (
    LifecycleRefusal,
    MissionLifecycleError,
    MissionLifecycleEvents,
    lifecycle_event_id,
    lifecycle_source,
)
from aerial_rescue_dashboard_api.messaging.mutations import MutationStamp
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_store.application_outbox import StagedApplicationEvent

_ROOT = Path(__file__).parents[4]
_MISSION: Final = "mission-synthetic-0001"
_RUN: Final = "run-synthetic-0001"
_RUNTIME: Final = "runtime-synthetic-0001"
_NOW: Final = "2026-08-26T12:00:00.000Z"
_TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203332-01"
_EVENT_TYPE: Final = "aerial-rescue.v1.mission.event.lifecycle"
_IDENTIFIER_MAX_LENGTH: Final = 64


def _stamp(sequence: int = 1) -> MutationStamp:
    """Return one deterministic stamp; the builder derives the event identity itself."""
    return MutationStamp(
        event_id="event-unused-0001",
        entity_id="entity-unused-0001",
        occurred_at=_NOW,
        monotonic_milliseconds=1_000,
        sequence=sequence,
        traceparent=_TRACEPARENT,
    )


def _events(sequence: int = 1) -> MissionLifecycleEvents:
    """Return one builder bound to a fixed epoch and the real schema registry."""
    return MissionLifecycleEvents(
        runtime_id=_RUNTIME,
        stamps=lambda: _stamp(sequence),
        schemas=load_runtime_schema_registry(_ROOT / "schemas"),
    )


def _event(
    lifecycle: MissionState = MissionState.SEARCHING,
    *,
    sequence: int = 1,
) -> StagedApplicationEvent:
    """Build one staged mission-lifecycle event against the real schema registry."""
    return _events(sequence).build(_MISSION, _RUN, lifecycle)


def _builds(lifecycle: MissionState) -> bool:
    """Report whether the builder accepts ``lifecycle`` and emits the matching payload."""
    try:
        staged = _event(lifecycle)
    except MissionLifecycleError:
        return False
    return decode_envelope(staged.payload).data == {
        "missionId": _MISSION,
        "lifecycle": lifecycle.name,
    }


def test_the_staged_event_binds_its_envelope_topic_and_committed_payload_schema() -> None:
    # Arrange
    expected_topic = f"aerial-rescue/v1/{_MISSION}/mission/event/lifecycle"

    # Act
    staged = _event(MissionState.EXHAUSTED)

    # Assert
    envelope = decode_envelope(staged.payload)
    assert staged.topic == expected_topic
    assert staged.family == "mission-event"
    assert staged.producer == "dashboard-api"
    assert staged.correlation_id == _RUN
    assert envelope.type == _EVENT_TYPE
    assert envelope.dataschema == BINDINGS[_EVENT_TYPE].dataschema
    assert envelope.subject == _MISSION
    assert envelope.data == {"missionId": _MISSION, "lifecycle": "EXHAUSTED"}


def test_the_source_names_the_producer_kind_the_mission_binding_requires() -> None:
    """The dashboard-api source that carries operator mutations is not legal for this family."""
    # Arrange
    expected = f"urn:aerial-rescue:mission-lifecycle:{_RUNTIME}"

    # Act
    staged = _event()

    # Assert
    assert lifecycle_source(_RUNTIME) == expected
    assert _events().source == expected
    assert decode_envelope(staged.payload).source == expected


def test_one_mission_reaching_one_state_always_has_the_same_event_identity() -> None:
    """The outbox primary key is the idempotency, so the identity must not vary per call."""
    # Arrange
    expected = lifecycle_event_id(_MISSION, MissionState.EXHAUSTED)

    # Act
    repeated = _event(MissionState.EXHAUSTED, sequence=2)

    # Assert
    assert repeated.event_id == expected
    assert _event(MissionState.EXHAUSTED, sequence=9).event_id == expected
    assert decode_envelope(repeated.payload).id == expected


def test_distinct_missions_and_states_never_share_an_event_identity() -> None:
    # Arrange
    states = (MissionState.SEARCHING, MissionState.EXHAUSTED, MissionState.ABORTED)

    # Act
    identities = {lifecycle_event_id(_MISSION, state) for state in states}
    identities |= {lifecycle_event_id("mission-synthetic-0002", state) for state in states}

    # Assert
    assert len(identities) == len(states) * 2
    assert all(len(identity) <= _IDENTIFIER_MAX_LENGTH for identity in identities)


def test_every_domain_state_either_builds_or_is_refused_by_name() -> None:
    """Six states, four the committed schema carries and two it has no value for."""
    # Arrange
    schema_values = frozenset({"PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"})

    # Act
    built = {state.name for state in MissionState if _builds(state)}

    # Assert
    assert built == schema_values
    assert {state.name for state in MissionState} - built == {"ESCALATED", "COMPLETED"}


def test_a_state_the_committed_payload_schema_cannot_carry_is_refused_by_reason() -> None:
    """``ESCALATED`` records a published command; it is not a wire lifecycle value."""
    # Arrange
    unpublished = MissionState.ESCALATED

    # Act
    with pytest.raises(MissionLifecycleError) as refused:
        _event(unpublished)

    # Assert
    assert refused.value.refusal is LifecycleRefusal.UNPUBLISHED_STATE


def test_a_sequence_outside_the_envelope_profile_is_refused_before_any_row_exists() -> None:
    # Arrange
    unrepresentable = -1

    # Act
    with pytest.raises(MissionLifecycleError) as refused:
        _event(sequence=unrepresentable)

    # Assert
    assert refused.value.refusal is LifecycleRefusal.SEQUENCE


def test_the_staged_payload_is_the_canonical_envelope_the_recorder_reads() -> None:
    # Arrange
    staged = _event(MissionState.ABORTED)

    # Act
    document = canonical.decode(staged.payload)

    # Assert
    assert canonical.canonical_bytes(document) == staged.payload
    assert staged.staged_at == _NOW
    assert staged.traceparent == _TRACEPARENT
    assert staged.causation_id is None
