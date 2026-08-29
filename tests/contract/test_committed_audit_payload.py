"""Every dashboard reader of ``audit_record`` must accept what the deployed recorder writes.

The recorder has two compositions (``TECH_DEBT.md`` section 3). Compose runs the
``aerial-rescue-recorder`` console script, whose ``capture.Recorder`` commits the canonical
CloudEvent envelope under the envelope's own type. The dashboard API reads those same rows from
two places -- the broker-recovery projection and the snapshot reconstruction -- and both must read
the committed form. A reader that expects the parallel composition's normalized view document
cannot serve the deployed runtime.
"""

from __future__ import annotations

import asyncio
from typing import Final

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import BINDINGS, Envelope, envelope_document
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_dashboard_api.messaging.projection import (
    ApplyDecision,
    DashboardProjectionHub,
    ProjectionHubSettings,
)
from aerial_rescue_dashboard_api.snapshot import _ordered_event as ordered_snapshot_event
from aerial_rescue_dashboard_api.snapshot import checkpoint_from_prepared_state
from aerial_rescue_dashboard_api.store_adapter import _normalized_event
from aerial_rescue_recorder.capture import ReceivedNotification, _recording_fact
from aerial_rescue_store.audit import StoredAuditRecord
from aerial_rescue_store.dashboard.events import StoredDashboardEvent

from tests.dashboard_api_support import live_prepared_state

pytestmark = [pytest.mark.contract]

_MISSION: Final = "mission-test-0001"
_RUN: Final = "run-test-0001"
_DRONE: Final = "drone-sim-01"
_TIME: Final = "2026-08-28T17:04:31.352Z"
_TRACEPARENT: Final = "00-aeffe7d3411a44e198293bfd70151bfa-16819186719e4996-01"
_TELEMETRY_TYPE: Final = "aerial-rescue.v1.drone.telemetry"
_TOPIC: Final = f"aerial-rescue/v1/{_MISSION}/drone/{_DRONE}/telemetry"


class _Schemas:
    """Accept every projected payload the hub validates at its own trust boundary."""

    def validate(self, schema_id: str, payload: object, /) -> None:
        """Accept one already-validated projected payload without further narrowing."""


def _envelope() -> Envelope:
    """Return one telemetry envelope exactly as the fleet publishes it."""
    return Envelope(
        id="event-0001",
        source=f"urn:aerial-rescue:drone:{_DRONE}",
        type=_TELEMETRY_TYPE,
        subject=_MISSION,
        time=_TIME,
        dataschema=BINDINGS[_TELEMETRY_TYPE].dataschema,
        sequence=f"{1:015d}",
        correlation_id=_RUN,
        traceparent=_TRACEPARENT,
        data={
            "missionId": _MISSION,
            "droneId": _DRONE,
            "latitudeMicrodegrees": 44_474_600,
            "longitudeMicrodegrees": -79_245_500,
            "batteryPercent": 98,
            "altitudeMetres": 83,
            "headingDegrees": 0,
            "groundSpeedCentimetresPerSecond": 1_110,
        },
    )


def _committed_columns() -> tuple[str, bytes]:
    """Return the exact kind and payload the deployed recorder commits for one notification."""
    notification = ReceivedNotification(
        topic=parse_topic(_TOPIC),
        envelope=_envelope(),
        observed_at=_TIME,
    )
    audit = _recording_fact("recorder", notification).audit
    return audit.kind, audit.canonical_event


def _stored_audit_record(kind: str, payload: bytes) -> StoredAuditRecord:
    """Adapt the recorder's committed columns into the projection's stored row."""
    envelope = _envelope()
    return StoredAuditRecord(
        mission_id=_MISSION,
        ordinal=1,
        kind=kind,
        occurred_at=_TIME,
        payload=payload,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        traceparent=envelope.traceparent,
    )


def _seeded_hub() -> DashboardProjectionHub:
    """Seed one hub exactly as the deployed composition seeds it before activating a mission."""
    return DashboardProjectionHub(
        runtime_id="runtime-test-0001",
        checkpoint=checkpoint_from_prepared_state(live_prepared_state()),
        current_run={"mode": "degradedLive", "missionId": _MISSION, "runId": _RUN},
        cursor=lambda ordinal, _witness: f"cursor-{ordinal}",
        settings=ProjectionHubSettings(
            max_clients=2,
            client_buffer_capacity=4,
            keepalive_milliseconds=15_000,
        ),
    )


def test_the_recorder_commits_the_canonical_envelope_under_its_own_type() -> None:
    # Arrange
    envelope = _envelope()

    # Act
    kind, payload = _committed_columns()

    # Assert
    assert kind == envelope.type
    assert payload == canonical.canonical_bytes(envelope_document(envelope))


def test_the_broker_recovery_projection_reads_the_committed_form() -> None:
    # Arrange
    kind, payload = _committed_columns()
    hub = _seeded_hub()

    # Act
    decision = asyncio.run(hub.apply_audit(_stored_audit_record(kind, payload), _Schemas()))

    # Assert
    assert decision is ApplyDecision.APPLIED


def test_the_snapshot_reconstruction_reads_the_committed_form() -> None:
    # Arrange
    kind, payload = _committed_columns()
    committed = StoredDashboardEvent(audit_ordinal=1, kind=kind, payload=payload)

    # Act
    ordered, _document = ordered_snapshot_event(_normalized_event(committed))

    # Assert
    assert ordered.event.kind == "droneTelemetry"


def test_both_dashboard_readers_agree_on_the_committed_event() -> None:
    # Arrange
    kind, payload = _committed_columns()
    committed = StoredDashboardEvent(audit_ordinal=1, kind=kind, payload=payload)
    hub = _seeded_hub()

    # Act
    snapshot_event, _document = ordered_snapshot_event(_normalized_event(committed))
    asyncio.run(hub.apply_audit(_stored_audit_record(kind, payload), _Schemas()))

    # Assert
    assert snapshot_event.event.kind == "droneTelemetry"
    assert hub.latest_audit_ordinal == snapshot_event.audit_ordinal
