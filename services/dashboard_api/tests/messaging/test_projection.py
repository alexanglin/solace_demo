"""Broker-audit projection, checkpoint recovery, and bounded fan-out tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Final, cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import BINDINGS, Envelope, envelope_document
from aerial_rescue_contracts.view import (
    PreparedMission,
    ReducerCheckpoint,
    prepare_checkpoint,
)
from aerial_rescue_dashboard_api.boundary.application import DataFrame, Keepalive
from aerial_rescue_dashboard_api.messaging import projection as projection_module
from aerial_rescue_dashboard_api.messaging.projection import (
    ApplyDecision,
    DashboardProjectionHub,
    ProjectionError,
    ProjectionHubSettings,
    ProjectionRefusal,
)
from aerial_rescue_dashboard_api.sse_buffer import BufferDecision, BufferedFrame
from aerial_rescue_store.audit import StoredAuditRecord

_MISSION: Final = "mission-01"
_TIME: Final = "2026-08-25T14:00:01.000Z"
_TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203332-01"
_LATEST_ORDINAL: Final = 2
_TIMELINE_CAPACITY: Final = 256


class _Schemas:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def validate(self, schema_id: str, payload: object, /) -> None:
        assert isinstance(payload, dict)
        self.calls.append((schema_id, payload))


def _envelope(ordinal: int, *, telemetry: bool = False) -> Envelope:
    event_type = (
        "aerial-rescue.v1.drone.telemetry"
        if telemetry
        else "aerial-rescue.v1.mission.event.lifecycle"
    )
    data: dict[str, object]
    if telemetry:
        data = {
            "missionId": _MISSION,
            "droneId": "drone-sim-01",
            "latitudeMicrodegrees": 44_475_000,
            "longitudeMicrodegrees": -79_245_000,
            "batteryPercent": 96,
            "altitudeMetres": 83,
            "headingDegrees": 45,
            "groundSpeedCentimetresPerSecond": 950,
        }
        source = "urn:aerial-rescue:drone:drone-sim-01"
    else:
        data = {"missionId": _MISSION, "lifecycle": "SEARCHING"}
        source = "urn:aerial-rescue:mission-lifecycle:run-synthetic-0001"
    return Envelope(
        id=f"event-{ordinal:04d}",
        source=source,
        type=event_type,
        subject=_MISSION,
        time=_TIME,
        dataschema=BINDINGS[event_type].dataschema,
        sequence=f"{ordinal:015d}",
        correlation_id="correlation-0001",
        traceparent=_TRACEPARENT,
        data=data,
    )


def _record(ordinal: int, *, telemetry: bool = False) -> StoredAuditRecord:
    envelope = _envelope(ordinal, telemetry=telemetry)
    return StoredAuditRecord(
        mission_id=_MISSION,
        ordinal=ordinal,
        kind=envelope.type,
        occurred_at=envelope.time,
        payload=canonical.canonical_bytes(envelope_document(envelope)),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        traceparent=envelope.traceparent,
    )


def _checkpoint() -> ReducerCheckpoint:
    return prepare_checkpoint(
        PreparedMission(
            _MISSION,
            None,
            ("drone-sim-01",),
            (),
            (),
        )
    )


def _hub(
    *,
    capacity: int = 2,
    clients: int = 2,
    keepalive_milliseconds: int = 15_000,
) -> DashboardProjectionHub:
    return DashboardProjectionHub(
        runtime_id="runtime-synthetic-0001",
        checkpoint=_checkpoint(),
        current_run={
            "mode": "degradedLive",
            "missionId": _MISSION,
            "runId": "run-synthetic-0001",
        },
        cursor=lambda ordinal, _witness: f"cursor-{ordinal}",
        settings=ProjectionHubSettings(
            max_clients=clients,
            client_buffer_capacity=capacity,
            keepalive_milliseconds=keepalive_milliseconds,
        ),
    )


@pytest.mark.asyncio
async def test_hub_projects_validated_audit_records_and_emits_a_complete_snapshot() -> None:
    # Arrange
    schemas = _Schemas()
    hub = _hub()

    # Act
    first = await hub.apply_audit(_record(1), schemas)
    second = await hub.apply_audit(_record(2, telemetry=True), schemas)
    stream = await hub.open_events()
    snapshot = cast("DataFrame", await anext(stream))
    await stream.close()
    document = canonical.decode(snapshot.body)

    # Assert
    assert (first, second) == (ApplyDecision.APPLIED, ApplyDecision.APPLIED)
    assert snapshot.event == "snapshot"
    assert isinstance(document, dict)
    assert document["latestEventDigest"] == hub.checkpoint.latest_event_digest
    assert document["state"]["latestAuditOrdinal"] == _LATEST_ORDINAL
    assert len(document["timeline"]) == 1
    assert [call[0] for call in schemas.calls] == [
        BINDINGS[_envelope(1).type].dataschema,
        BINDINGS[_envelope(2, telemetry=True).type].dataschema,
    ]


@pytest.mark.asyncio
async def test_exact_duplicate_is_not_broadcast_and_divergence_leaves_state_unchanged() -> None:
    # Arrange
    schemas = _Schemas()
    hub = _hub()
    stream = await hub.open_events()
    await anext(stream)
    await hub.apply_audit(_record(1), schemas)
    applied = cast("DataFrame", await anext(stream))
    checkpoint = hub.checkpoint

    # Act
    duplicate = await hub.apply_audit(_record(1), schemas)
    divergent = replace(_record(1), kind="aerial-rescue.v1.mission.event.changed")
    with pytest.raises(ProjectionError) as captured:
        await hub.apply_audit(divergent, schemas)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream), timeout=0.01)
    await stream.close()

    # Assert
    assert applied.event == "dashboard-event"
    assert duplicate is ApplyDecision.DUPLICATE
    assert captured.value.refusal is ProjectionRefusal.AUDIT_BINDING
    assert hub.checkpoint is checkpoint


@pytest.mark.asyncio
async def test_non_droppable_buffer_overload_emits_one_terminal_frame_and_closes() -> None:
    # Arrange
    schemas = _Schemas()
    hub = _hub(capacity=2)
    stream = await hub.open_events()
    await anext(stream)

    # Act
    for ordinal in range(1, 4):
        await hub.apply_audit(_record(ordinal), schemas)
    frames = [cast("DataFrame", await anext(stream)) for _index in range(3)]
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    # Assert
    assert [frame.event for frame in frames] == [
        "dashboard-event",
        "dashboard-event",
        "stream-overloaded",
    ]
    assert hub.client_count == 0


@pytest.mark.asyncio
async def test_hub_refuses_clients_beyond_the_injected_bound_without_allocating_a_buffer() -> None:
    # Arrange
    hub = _hub(clients=1)
    first = await hub.open_events()

    # Act
    with pytest.raises(ProjectionError) as captured:
        await hub.open_events()
    await first.close()

    # Assert
    assert captured.value.refusal is ProjectionRefusal.CLIENT_CAPACITY
    assert hub.client_count == 0


@pytest.mark.asyncio
async def test_idle_stream_emits_a_comment_keepalive_without_changing_client_state() -> None:
    # Arrange
    hub = _hub(keepalive_milliseconds=1)
    stream = await hub.open_events()
    snapshot = await anext(stream)

    # Act
    frame = await anext(stream)
    checkpoint = hub.checkpoint
    client_count = hub.client_count
    await stream.close()

    # Assert
    assert isinstance(snapshot, DataFrame)
    assert isinstance(frame, Keepalive)
    assert checkpoint == hub.checkpoint
    assert client_count == 1


@pytest.mark.asyncio
async def test_replacing_the_active_run_closes_old_clients_and_resets_the_audit_checkpoint() -> (
    None
):
    # Arrange
    hub = _hub()
    old_stream = await hub.open_events()
    await anext(old_stream)
    replacement = prepare_checkpoint(
        PreparedMission(
            "mission-02",
            _MISSION,
            ("drone-sim-01",),
            (),
            (),
        ),
    )

    # Act
    await hub.replace_run(
        replacement,
        {
            "mode": "degradedLive",
            "missionId": "mission-02",
            "runId": "run-synthetic-0002",
        },
    )
    with pytest.raises(StopAsyncIteration):
        await anext(old_stream)
    new_stream = await hub.open_events()
    snapshot = cast("DataFrame", await anext(new_stream))
    await new_stream.close()
    document = cast("dict[str, object]", canonical.decode(snapshot.body))

    # Assert
    assert hub.checkpoint is replacement
    assert document["currentRun"] == {
        "mode": "degradedLive",
        "missionId": "mission-02",
        "runId": "run-synthetic-0002",
    }
    assert document["timeline"] == []


@pytest.mark.asyncio
async def test_blocked_stream_is_released_by_close_and_repeated_close_is_a_noop() -> None:
    # Arrange
    hub = _hub(keepalive_milliseconds=15_000)
    stream = await hub.open_events()
    snapshot = await anext(stream)
    blocked = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    # Act
    await stream.close()
    await stream.close()
    with pytest.raises(StopAsyncIteration) as captured:
        await blocked

    # Assert
    assert isinstance(snapshot, DataFrame)
    assert captured.type is StopAsyncIteration
    assert hub.client_count == 0


def test_stream_retains_pending_frames_and_does_not_wake_for_dropped_or_closed_input() -> None:
    # Arrange
    hub = _hub(capacity=1)
    stream = projection_module.ProjectionEventStream(hub, 1, b"snapshot", 1, 1)
    retained = BufferedFrame(b"retained", telemetry=False)
    telemetry = BufferedFrame(b"telemetry", telemetry=True)
    critical = BufferedFrame(b"critical", telemetry=False)

    # Act
    first = stream.offer(retained)
    dropped = stream.offer(telemetry)
    overloaded = stream.offer(critical)
    closed = stream.offer(critical)

    # Assert
    assert first is BufferDecision.RETAINED
    assert dropped is BufferDecision.DROPPED_TELEMETRY
    assert overloaded is BufferDecision.OVERLOADED
    assert closed is BufferDecision.CLOSED


@pytest.mark.parametrize(
    ("runtime_id", "settings"),
    [
        ("", ProjectionHubSettings(max_clients=1)),
        ("runtime-synthetic-0001", ProjectionHubSettings(max_clients=0)),
        ("runtime-synthetic-0001", ProjectionHubSettings(max_clients=True)),
        (
            "runtime-synthetic-0001",
            ProjectionHubSettings(max_clients=1, client_buffer_capacity=0),
        ),
        (
            "runtime-synthetic-0001",
            ProjectionHubSettings(max_clients=1, keepalive_milliseconds=0),
        ),
    ],
)
def test_hub_refuses_invalid_identity_or_allocation_bounds(
    runtime_id: str,
    settings: ProjectionHubSettings,
) -> None:
    # Arrange
    values = (runtime_id, settings)

    # Act
    with pytest.raises(ProjectionError) as captured:
        DashboardProjectionHub(
            runtime_id=values[0],
            checkpoint=_checkpoint(),
            current_run=None,
            cursor=lambda ordinal, _witness: f"cursor-{ordinal}",
            settings=values[1],
        )

    # Assert
    assert captured.value.refusal is ProjectionRefusal.CONFIGURATION


@pytest.mark.asyncio
async def test_reducer_gap_is_mapped_to_a_redacted_projection_refusal() -> None:
    # Arrange
    hub = _hub()
    checkpoint = hub.checkpoint

    # Act
    with pytest.raises(ProjectionError) as captured:
        await hub.apply_audit(_record(2), _Schemas())

    # Assert
    assert captured.value.refusal is ProjectionRefusal.REDUCER
    assert captured.value.value == "ORDINAL_GAP"
    assert hub.checkpoint is checkpoint


@pytest.mark.asyncio
async def test_unknown_reducer_outcome_is_refused_without_advancing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    hub = _hub()
    checkpoint = hub.checkpoint
    monkeypatch.setattr(projection_module, "fold_ordered_event", lambda _prior, _event: object())

    # Act
    with pytest.raises(ProjectionError) as captured:
        await hub.apply_audit(_record(1), _Schemas())

    # Assert
    assert captured.value.refusal is ProjectionRefusal.REDUCER
    assert captured.value.value == "unknown-outcome"
    assert hub.checkpoint is checkpoint


@pytest.mark.asyncio
async def test_timeline_evicts_only_its_oldest_entry_at_the_fixed_capacity() -> None:
    # Arrange
    hub = _hub()
    oldest = {"auditOrdinal": -1, "event": {}}
    hub._timeline = [
        oldest,
        *[
            cast("dict[str, object]", {"auditOrdinal": index})
            for index in range(1, _TIMELINE_CAPACITY)
        ],
    ]

    # Act
    decision = await hub.apply_audit(_record(1), _Schemas())

    # Assert
    assert decision is ApplyDecision.APPLIED
    assert len(hub._timeline) == _TIMELINE_CAPACITY
    assert oldest not in hub._timeline


@pytest.mark.asyncio
async def test_hub_close_handles_empty_and_registered_client_sets() -> None:
    # Arrange
    empty = _hub()
    populated = _hub()
    stream = await populated.open_events()

    # Act
    await empty.close()
    await populated.close()
    with pytest.raises(StopAsyncIteration) as captured:
        await anext(stream)

    # Assert
    assert captured.type is StopAsyncIteration
    assert empty.client_count == 0
    assert populated.client_count == 0
