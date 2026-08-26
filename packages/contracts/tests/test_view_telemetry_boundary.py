from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

import pytest
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.view import ViewError, ViewRefusal, project

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TELEMETRY_FIXTURE = REPOSITORY_ROOT / "fixtures/golden/v1/event/drone-telemetry/baseline.json"


class TelemetryProjectionBoundaryTests(unittest.TestCase):
    def test_telemetry_projection_refuses_payloads_outside_the_bound_schema(self) -> None:
        # Arrange
        envelope = decode_envelope(TELEMETRY_FIXTURE.read_bytes())
        cases = (
            {**envelope.data, "unexpected": True},
            {key: value for key, value in envelope.data.items() if key != "batteryPercent"},
            {**envelope.data, "batteryPercent": True},
            {**envelope.data, "latitudeMicrodegrees": 90_000_001},
            {**envelope.data, "droneId": "Drone-01"},
        )

        # Act
        refusals: list[ViewRefusal] = []
        for data in cases:
            with self.subTest(data=data), pytest.raises(ViewError) as captured:
                project(replace(envelope, data=data))
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ViewRefusal.MALFORMED_PAYLOAD] * len(cases), refusals)
