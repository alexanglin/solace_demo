"""The telemetry payload bounds this member copies, held equal to the committed schema.

`schemas/v1/canonical.schema.json` is the one home for these numbers. This suite is what
makes the copy in `bounds.py` a pin rather than a second home: it reads the committed
schema and fails when the two disagree, in either direction.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_fleet_simulator.bounds import (
    BATTERY_PERMILLE,
    EAST_MICRODEGREES_PER_TICK,
    LATITUDE_MICRODEGREES,
    LONGITUDE_MICRODEGREES,
    NORTH_MICRODEGREES_PER_TICK,
    PERCENT,
    PERMILLE_PER_PERCENT,
    SCHEMA_DEFINITIONS,
    Bound,
)

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
CANONICAL_SCHEMA: Final = REPOSITORY_ROOT / "schemas" / "v1" / "canonical.schema.json"


def _committed_bounds() -> dict[str, Bound]:
    """Return every named integer range the committed canonical schema declares."""
    definitions = json.loads(CANONICAL_SCHEMA.read_text(encoding="utf-8"))["$defs"]
    return {
        name: Bound(definition["minimum"], definition["maximum"])
        for name, definition in definitions.items()
        if definition.get("type") == "integer" and name != "safeInteger"
    }


class BoundTests(unittest.TestCase):
    def test_a_bound_includes_both_of_its_ends_and_excludes_one_step_beyond(self) -> None:
        # Arrange
        bound = Bound(-2, 3)

        # Act
        verdicts = tuple(bound.holds(value) for value in (-3, -2, 0, 3, 4))

        # Assert
        self.assertEqual((False, True, True, True, False), verdicts)


class SchemaPinTests(unittest.TestCase):
    def test_every_copied_bound_equals_the_committed_schema_definition(self) -> None:
        # Arrange
        committed = _committed_bounds()

        # Act
        copied = {name: committed[name] for name in SCHEMA_DEFINITIONS}

        # Assert
        self.assertEqual(dict(SCHEMA_DEFINITIONS), copied)

    def test_the_copy_covers_every_integer_definition_the_schema_declares(self) -> None:
        # Arrange
        committed = _committed_bounds()

        # Act
        copied = set(SCHEMA_DEFINITIONS)

        # Assert
        self.assertEqual(set(committed), copied)


class DerivedBoundTests(unittest.TestCase):
    def test_the_battery_permille_range_is_the_percent_range_scaled(self) -> None:
        # Arrange
        expected = Bound(PERCENT.low * PERMILLE_PER_PERCENT, PERCENT.high * PERMILLE_PER_PERCENT)

        # Act
        derived = BATTERY_PERMILLE

        # Assert
        self.assertEqual(expected, derived)

    def test_a_displacement_cannot_exceed_the_coordinate_range_it_moves_within(self) -> None:
        # Arrange
        expected = (
            Bound(-LATITUDE_MICRODEGREES.high, LATITUDE_MICRODEGREES.high),
            Bound(-LONGITUDE_MICRODEGREES.high, LONGITUDE_MICRODEGREES.high),
        )

        # Act
        derived = (NORTH_MICRODEGREES_PER_TICK, EAST_MICRODEGREES_PER_TICK)

        # Assert
        self.assertEqual(expected, derived)


if __name__ == "__main__":
    unittest.main()
