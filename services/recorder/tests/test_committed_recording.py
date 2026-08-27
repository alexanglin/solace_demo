from __future__ import annotations

import unittest
from pathlib import Path

import pytest
from aerial_rescue_contracts.view import (
    DeclaredOnlyFleetMember,
    MissionLifecycle,
    SectorState,
)
from aerial_rescue_recorder.recording import validate_recording

pytestmark = [pytest.mark.integration]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RECORDING = REPOSITORY_ROOT / "recordings/v1/wilderness-missing-person.r1.ndjson"


class CommittedRecordingTests(unittest.TestCase):
    def test_recording_replays_the_full_truthful_wilderness_roster_to_exhaustion(self) -> None:
        # Arrange
        raw = RECORDING.read_bytes()

        # Act
        checkpoint = validate_recording(raw, return_checkpoint=True)

        # Assert
        self.assertEqual(23, len(checkpoint.state.fleet))
        self.assertEqual(20, len(checkpoint.state.sectors))
        self.assertEqual(48, checkpoint.state.latest_audit_ordinal)
        mission = checkpoint.state.current_mission
        if mission is None:
            self.fail()
        self.assertEqual(MissionLifecycle.EXHAUSTED, mission.lifecycle)
        self.assertTrue(
            all(sector.state is SectorState.SEARCHED for sector in checkpoint.state.sectors)
        )
        declared_only = tuple(
            member
            for member in checkpoint.state.fleet
            if isinstance(member, DeclaredOnlyFleetMember)
        )
        self.assertEqual(3, len(declared_only))

    def test_recording_and_session_neutral_bundle_exclude_transport_and_session_data(self) -> None:
        # Arrange
        raw = RECORDING.read_bytes()
        forbidden = (b"sessionId", b"traceparent", b"credential", b"authorization")

        # Act
        bundle = validate_recording(raw)

        # Assert
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(49, len(raw.splitlines()))
        for member in forbidden:
            self.assertNotIn(member, raw)
            self.assertNotIn(member, bundle)
