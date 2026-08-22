"""The telemetry payload bounds this member copies, pinned to the committed schema.

``schemas/v1/canonical.schema.json`` is the one home for these numbers and
``docs/operating-parameters.md`` records each with its instrument. The contracts package
exposes no Python constant for any of them, because nothing in Python validates a telemetry
payload today: the JSON Schema does, at the contract gate. This module is the derived copy
the simulator needs in order to refuse a scenario before it reaches the wire, and
``tests/test_bounds.py`` reads the committed schema and fails in either direction if the
two drift. It is a pinned copy, not a second home.

This module is pure: it performs no input or output and reads no clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Bound:
    """One inclusive integer range, copied from a committed schema definition."""

    low: int
    high: int

    def holds(self, value: int) -> bool:
        """Return whether a value lies inside the range, both ends included."""
        return self.low <= value <= self.high


LATITUDE_MICRODEGREES: Final = Bound(-90_000_000, 90_000_000)
LONGITUDE_MICRODEGREES: Final = Bound(-180_000_000, 180_000_000)
ALTITUDE_METRES: Final = Bound(-500, 20_000)
HEADING_DEGREES: Final = Bound(0, 359)
GROUND_SPEED_CENTIMETRES_PER_SECOND: Final = Bound(0, 10_000)
PERCENT: Final = Bound(0, 100)

SCHEMA_DEFINITIONS: Final[Mapping[str, Bound]] = {
    "latitudeMicrodegrees": LATITUDE_MICRODEGREES,
    "longitudeMicrodegrees": LONGITUDE_MICRODEGREES,
    "altitudeMetres": ALTITUDE_METRES,
    "headingDegrees": HEADING_DEGREES,
    "groundSpeedCentimetresPerSecond": GROUND_SPEED_CENTIMETRES_PER_SECOND,
    "percent": PERCENT,
}
"""Each copied bound beside the ``$defs`` name it copies; the pin test reads both sides."""

PERMILLE_PER_PERCENT: Final = 10
"""Battery is carried in permille so a slow drain is representable in integers alone."""

BATTERY_PERMILLE: Final = Bound(
    PERCENT.low * PERMILLE_PER_PERCENT, PERCENT.high * PERMILLE_PER_PERCENT
)

NORTH_MICRODEGREES_PER_TICK: Final = Bound(-LATITUDE_MICRODEGREES.high, LATITUDE_MICRODEGREES.high)
EAST_MICRODEGREES_PER_TICK: Final = Bound(-LONGITUDE_MICRODEGREES.high, LONGITUDE_MICRODEGREES.high)
"""A displacement larger than the coordinate range it moves within is a scenario defect."""
