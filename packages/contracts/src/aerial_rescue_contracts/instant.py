"""The canonical instant: ``YYYY-MM-DDTHH:MM:SS.sssZ`` and nothing else.

``docs/CONTRACTS.md`` gives an instant exactly one spelling — millisecond precision, the
literal ``Z``, never a numeric offset — so that one instant has one canonical form
(``docs/adr/0027-integer-only-canonical-serialization.md``). This module is pure: it
reads no clock.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final

INSTANT_PATTERN: Final = (
    "^(?!0000)[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    "T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\\.[0-9]{3}Z$"
)
"""The form of an instant, shared verbatim with the JSON Schemas.

``[0-9]`` rather than ``\\d``: Python's ``re`` reads ``\\d`` as any Unicode digit while
ECMA-262 reads it as ASCII, and the two engines must agree on every fixture.
"""

_STRPTIME_FORM: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
_NO_OFFSET: Final = timedelta(0)


class InstantRefusal(Enum):
    """Why a value is not a canonical instant."""

    UNSUPPORTED_TYPE = "instant is not a string"
    FORM = "instant outside the canonical spelling"
    CALENDAR = "instant names a date that does not exist"
    NAIVE = "datetime carries no time zone"
    NOT_UTC = "datetime offset is not zero"


class InstantError(ValueError):
    """A value that is not a canonical instant, carrying the refusal as structured data."""

    def __init__(self, refusal: InstantRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


def parse_instant(text: object) -> datetime:
    """Parse canonical instant text into an aware UTC datetime.

    Args:
        text: The candidate instant.

    Returns:
        An aware datetime in UTC whose microsecond field is a whole number of milliseconds.

    Raises:
        InstantError: If the value is not a string, is not in the canonical spelling, or
            names a calendar date that does not exist.
    """
    if not isinstance(text, str):
        raise InstantError(InstantRefusal.UNSUPPORTED_TYPE, text)
    if re.fullmatch(INSTANT_PATTERN, text) is None:
        raise InstantError(InstantRefusal.FORM, text)
    try:
        naive = datetime.strptime(text, _STRPTIME_FORM)
    except ValueError as error:
        raise InstantError(InstantRefusal.CALENDAR, text) from error
    return naive.replace(tzinfo=UTC)


def format_instant(value: datetime) -> str:
    """Format an aware UTC datetime as the canonical instant.

    Sub-millisecond precision is floored, never rounded: ADR-0027 discards sub-millisecond
    ordering, and rounding could carry a value into the next second.

    Args:
        value: An aware datetime whose offset is zero.

    Returns:
        The canonical spelling.

    Raises:
        InstantError: If the datetime is naive or its offset is not zero.
    """
    if value.tzinfo is None:
        raise InstantError(InstantRefusal.NAIVE, value)
    if value.utcoffset() != _NO_OFFSET:
        raise InstantError(InstantRefusal.NOT_UTC, value)
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
        f"T{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
        f".{value.microsecond // 1000:03d}Z"
    )
