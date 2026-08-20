"""The one spelling of an instant: ``YYYY-MM-DDTHH:MM:SS.sssZ``.

Every refusal is asserted by its structured reason, not by message prose, and the
formatting side is asserted byte for byte so a platform-dependent ``strftime`` cannot
creep in unnoticed.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

import pytest
from aerial_rescue_contracts import instant

OTHER_SPELLINGS = (
    "2026-08-20T14:03:07.250+00:00",
    "2026-08-20T14:03:07.250000Z",
    "2026-08-20T14:03:07.25Z",
    "2026-08-20T14:03:07Z",
    "2026-08-20T14:03:07.250z",
    "2026-08-20",
    "0000-01-01T00:00:00.000Z",
    "2026-08-20T14:03:07.250Z\n",
    "",
)


def _refusal_of(text: object) -> tuple[instant.InstantRefusal, object]:
    """Return the refusal and value parsing ``text`` raises, failing the test if accepted."""
    try:
        instant.parse_instant(text)
    except instant.InstantError as error:
        return (error.refusal, error.value)
    message = f"accepted: {text!r}"
    raise AssertionError(message)


class InstantParsingTests(unittest.TestCase):
    def test_the_canonical_spelling_parses_to_a_utc_datetime(self) -> None:
        # Arrange
        text = "2026-08-20T14:03:07.250Z"

        # Act
        parsed = instant.parse_instant(text)

        # Assert
        self.assertEqual(datetime(2026, 8, 20, 14, 3, 7, 250000, tzinfo=UTC), parsed)

    def test_every_other_spelling_is_refused_as_form(self) -> None:
        # Arrange
        texts = OTHER_SPELLINGS

        # Act
        refusals = tuple(_refusal_of(text) for text in texts)

        # Assert
        self.assertEqual(tuple((instant.InstantRefusal.FORM, text) for text in texts), refusals)

    def test_an_impossible_calendar_date_is_refused(self) -> None:
        # Arrange
        text = "2026-02-30T00:00:00.000Z"

        # Act
        with pytest.raises(instant.InstantError) as captured:
            instant.parse_instant(text)

        # Assert
        self.assertEqual(
            (instant.InstantRefusal.CALENDAR, text),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_leap_day_is_a_real_date(self) -> None:
        # Arrange
        text = "2028-02-29T23:59:59.999Z"

        # Act
        parsed = instant.parse_instant(text)

        # Assert
        self.assertEqual(datetime(2028, 2, 29, 23, 59, 59, 999000, tzinfo=UTC), parsed)

    def test_a_non_string_is_refused(self) -> None:
        # Arrange
        value = 1724162587250

        # Act
        with pytest.raises(instant.InstantError) as captured:
            instant.parse_instant(value)

        # Assert
        self.assertEqual(
            (instant.InstantRefusal.UNSUPPORTED_TYPE, value),
            (captured.value.refusal, captured.value.value),
        )


class InstantFormattingTests(unittest.TestCase):
    def test_a_utc_datetime_formats_to_the_canonical_spelling(self) -> None:
        # Arrange
        value = datetime(2026, 8, 20, 14, 3, 7, 250000, tzinfo=UTC)

        # Act
        text = instant.format_instant(value)

        # Assert
        self.assertEqual("2026-08-20T14:03:07.250Z", text)

    def test_small_fields_are_zero_padded(self) -> None:
        # Arrange
        value = datetime(999, 1, 2, 3, 4, 5, 6000, tzinfo=UTC)

        # Act
        text = instant.format_instant(value)

        # Assert
        self.assertEqual("0999-01-02T03:04:05.006Z", text)

    def test_sub_millisecond_precision_is_floored_not_rounded(self) -> None:
        # Arrange
        value = datetime(2026, 8, 20, 14, 3, 7, 250999, tzinfo=UTC)

        # Act
        text = instant.format_instant(value)

        # Assert
        self.assertEqual("2026-08-20T14:03:07.250Z", text)

    def test_a_naive_datetime_is_refused(self) -> None:
        # Arrange
        value = datetime(2026, 8, 20, 14, 3, 7)

        # Act
        with pytest.raises(instant.InstantError) as captured:
            instant.format_instant(value)

        # Assert
        self.assertEqual(
            (instant.InstantRefusal.NAIVE, value),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_non_zero_offset_is_refused(self) -> None:
        # Arrange
        value = datetime(2026, 8, 20, 16, 3, 7, tzinfo=timezone(timedelta(hours=2)))

        # Act
        with pytest.raises(instant.InstantError) as captured:
            instant.format_instant(value)

        # Assert
        self.assertEqual(
            (instant.InstantRefusal.NOT_UTC, value),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_zero_offset_zone_that_is_not_the_utc_singleton_is_accepted(self) -> None:
        # Arrange
        value = datetime(2026, 8, 20, 14, 3, 7, tzinfo=timezone(timedelta(0), "GMT"))

        # Act
        text = instant.format_instant(value)

        # Assert
        self.assertEqual("2026-08-20T14:03:07.000Z", text)


class InstantErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = instant.InstantError(instant.InstantRefusal.FORM, "x")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("instant outside the canonical spelling: 'x'", message)


if __name__ == "__main__":
    unittest.main()
