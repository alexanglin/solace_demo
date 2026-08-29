"""The prepared-state seed must let the deployed composition's committed audit records fold.

The deployed composition seeds the projection from the run's own prepared state and then folds
every committed recorder page. Nothing previously wired the real checkpoint adapter to the real
projection over the CloudEvent-typed records the Solace application data plane commits, so a seed
that could not accept ordinal one refused ``MISSION_UNPREPARED`` and the process could not recover.
"""

from __future__ import annotations

from typing import Final

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import BINDINGS, Envelope, envelope_document
from aerial_rescue_contracts.view import EMPTY_CHECKPOINT, ReducerCheckpoint
from aerial_rescue_dashboard_api.messaging.projection import (
    ApplyDecision,
    DashboardProjectionHub,
    ProjectionError,
    ProjectionHubSettings,
    ProjectionRefusal,
)
from aerial_rescue_dashboard_api.snapshot import checkpoint_from_prepared_state
from aerial_rescue_store.audit import StoredAuditRecord

from tests.dashboard_api_support import live_prepared_state

_MISSION: Final = "mission-test-0001"
_RUN: Final = "run-test-0001"
_TIME: Final = "2026-08-28T17:04:31.352Z"
_TRACEPARENT: Final = "00-aeffe7d3411a44e198293bfd70151bfa-16819186719e4996-01"
_TELEMETRY_TYPE: Final = "aerial-rescue.v1.drone.telemetry"
_DRONES: Final = ("drone-sim-01", "drone-sim-02", "drone-sim-03")


class _Schemas:
    """Accept every projected payload, recording the schema identifier the hub selected."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate(self, schema_id: str, payload: object, /) -> None:
        """Record one requested schema identifier without narrowing the payload further."""
        assert isinstance(payload, dict)
        self.calls.append(schema_id)


def _telemetry_record(ordinal: int, drone: str) -> StoredAuditRecord:
    """Return one committed audit row exactly as the application data plane stores it."""
    envelope = Envelope(
        id=f"event-{ordinal:04d}",
        source=f"urn:aerial-rescue:drone:{drone}",
        type=_TELEMETRY_TYPE,
        subject=_MISSION,
        time=_TIME,
        dataschema=BINDINGS[_TELEMETRY_TYPE].dataschema,
        sequence=f"{ordinal:015d}",
        correlation_id=_RUN,
        traceparent=_TRACEPARENT,
        data={
            "missionId": _MISSION,
            "droneId": drone,
            "latitudeMicrodegrees": 44_474_600,
            "longitudeMicrodegrees": -79_245_500,
            "batteryPercent": 98,
            "altitudeMetres": 83,
            "headingDegrees": 0,
            "groundSpeedCentimetresPerSecond": 1_110,
        },
    )
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


def _committed_page() -> tuple[StoredAuditRecord, ...]:
    """Return one ordered committed recorder page in the data plane's own vocabulary."""
    return tuple(
        _telemetry_record(ordinal, drone) for ordinal, drone in enumerate(_DRONES, start=1)
    )


def _hub(
    checkpoint: ReducerCheckpoint,
    current_run: dict[str, object] | None,
) -> DashboardProjectionHub:
    """Build one projection hub over an exact checkpoint and current-run selection."""
    return DashboardProjectionHub(
        runtime_id="runtime-test-0001",
        checkpoint=checkpoint,
        current_run=current_run,
        cursor=lambda ordinal, _witness: f"cursor-{ordinal}",
        settings=ProjectionHubSettings(
            max_clients=2,
            client_buffer_capacity=4,
            keepalive_milliseconds=15_000,
        ),
    )


def _seeded_hub() -> DashboardProjectionHub:
    """Seed one hub exactly as the deployed composition seeds it before activating a mission."""
    return _hub(
        checkpoint_from_prepared_state(live_prepared_state()),
        {"mode": "degradedLive", "missionId": _MISSION, "runId": _RUN},
    )


def _unseeded_hub() -> DashboardProjectionHub:
    """Build the hub exactly as the composition constructs it before any seed is applied."""
    return _hub(EMPTY_CHECKPOINT, None)


@pytest.mark.asyncio
async def test_the_prepared_seed_folds_every_committed_data_plane_record() -> None:
    # Arrange
    hub = _seeded_hub()
    schemas = _Schemas()
    page = _committed_page()

    # Act
    decisions = [await hub.apply_audit(record, schemas) for record in page]

    # Assert
    assert decisions == [ApplyDecision.APPLIED] * len(page)
    assert hub.latest_audit_ordinal == len(page)


@pytest.mark.asyncio
async def test_a_restarted_process_refolds_the_same_page_from_the_prepared_seed() -> None:
    # Arrange
    page = _committed_page()
    first = _seeded_hub()
    for record in page:
        await first.apply_audit(record, _Schemas())

    # Act
    restarted = _seeded_hub()
    decisions = [await restarted.apply_audit(record, _Schemas()) for record in page]

    # Assert
    assert decisions == [ApplyDecision.APPLIED] * len(page)
    assert restarted.latest_audit_ordinal == first.latest_audit_ordinal


@pytest.mark.asyncio
async def test_an_unseeded_checkpoint_refuses_the_first_committed_record() -> None:
    # Arrange
    hub = _unseeded_hub()
    first = _committed_page()[0]

    # Act
    with pytest.raises(ProjectionError) as refusal:
        await hub.apply_audit(first, _Schemas())

    # Assert
    assert refusal.value.refusal is ProjectionRefusal.REDUCER
    assert refusal.value.value == "MISSION_UNPREPARED"
