"""Fleet-owned canonical wire parsing and schema selection."""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_fleet_simulator.control_plane.wire import (
    MAXIMUM_WIRE_BYTES,
    SCHEMA_PREFIX,
    FleetControlCancelRequest,
    parse_wire_document,
)
from pydantic import ValidationError

pytestmark = [pytest.mark.unit]

CANCEL_SCHEMA = f"{SCHEMA_PREFIX}fleet-control-cancel-request.schema.json"


def _cancel_document(control_version: object = 1) -> dict[str, object]:
    """Return the smallest closed cancellation document."""
    return {
        "controlVersion": control_version,
        "missionId": "m-2026-0001",
        "runId": "run-2026-0001",
    }


class FleetControlWireTests(unittest.TestCase):
    def test_known_schema_canonical_bytes_parse_to_the_owned_frozen_model(self) -> None:
        # Arrange
        raw = canonical.canonical_bytes(_cancel_document())

        # Act
        parsed = parse_wire_document(CANCEL_SCHEMA, raw)

        # Assert
        self.assertIsInstance(parsed, FleetControlCancelRequest)
        self.assertEqual(parsed.model_dump(mode="json", by_alias=True), _cancel_document())

    def test_boolean_control_version_is_not_coerced_to_integer_one(self) -> None:
        # Arrange
        raw = canonical.canonical_bytes(_cancel_document(True))

        # Act
        with pytest.raises(ValidationError) as raised:
            parse_wire_document(CANCEL_SCHEMA, raw)

        # Assert
        self.assertIn("integer 1", str(raised.value))

    def test_oversized_document_is_refused_before_canonical_decoding(self) -> None:
        # Arrange
        oversized = b" " * (MAXIMUM_WIRE_BYTES + 1)

        # Act
        with pytest.raises(ValueError, match="exceeds") as raised:
            parse_wire_document(CANCEL_SCHEMA, oversized)

        # Assert
        self.assertNotIn(oversized[:32].decode(), str(raised.value))

    def test_unowned_schema_is_refused_after_canonical_decoding(self) -> None:
        # Arrange
        raw = canonical.canonical_bytes(_cancel_document())
        unknown_schema = f"{SCHEMA_PREFIX}unknown.schema.json"

        # Act
        with pytest.raises(ValueError, match="schema is not owned") as raised:
            parse_wire_document(unknown_schema, raw)

        # Assert
        self.assertIn(unknown_schema, str(raised.value))


if __name__ == "__main__":
    unittest.main()
