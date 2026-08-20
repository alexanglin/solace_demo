"""Property-based invariants of the canonical instant.

Module-level functions with ``derandomize`` for the same reason as
``test_canonical_properties.py``: mutmut re-runs pytest in one process, and a flapping
example set would turn the mutation score into a moving number.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from aerial_rescue_contracts import instant
from hypothesis import given, settings
from hypothesis import strategies as st

UTC_DATETIMES = st.datetimes(
    min_value=datetime(1, 1, 1),
    max_value=datetime(9999, 12, 31, 23, 59, 59, 999999),
    timezones=st.just(UTC),
)
NON_CANONICAL_TEXT = st.text(max_size=30).filter(
    lambda text: re.fullmatch(instant.INSTANT_PATTERN, text) is None
)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(UTC_DATETIMES)
def test_parse_then_format_is_the_identity_on_canonical_text(value: datetime) -> None:
    # Arrange
    text = instant.format_instant(value)

    # Act
    round_tripped = instant.format_instant(instant.parse_instant(text))

    # Assert
    assert (round_tripped, re.fullmatch(instant.INSTANT_PATTERN, text) is not None) == (text, True)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(UTC_DATETIMES)
def test_format_then_parse_floors_to_the_millisecond(value: datetime) -> None:
    # Arrange
    expected = value.replace(microsecond=value.microsecond // 1000 * 1000)

    # Act
    parsed = instant.parse_instant(instant.format_instant(value))

    # Assert
    assert parsed == expected


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(NON_CANONICAL_TEXT)
def test_every_text_outside_the_pattern_is_refused_as_form(text: str) -> None:
    # Arrange
    expected = instant.InstantRefusal.FORM

    # Act
    with pytest.raises(instant.InstantError) as captured:
        instant.parse_instant(text)

    # Assert
    assert captured.value.refusal is expected
