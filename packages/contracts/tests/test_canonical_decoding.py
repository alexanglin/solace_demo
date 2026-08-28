"""Parsing JSON text into the canonical profile.

The idempotency record hashes a canonicalized request body, so text arriving from outside
must reduce to exactly one value or be refused.
"""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_contracts import canonical


class DecodingTests(unittest.TestCase):
    def test_decoded_text_canonicalizes_to_sorted_minified_bytes(self) -> None:
        # Arrange
        text = '{ "missionId": "m1", "sectors": [1, 2], "active": true, "note": null }'

        # Act
        value = canonical.decode(text)

        # Assert
        self.assertEqual(
            b'{"active":true,"missionId":"m1","note":null,"sectors":[1,2]}',
            canonical.canonical_bytes(value),
        )

    def test_a_document_nested_beyond_the_parser_is_refused_as_malformed_text(self) -> None:
        # Arrange
        depth = 100_000
        text = "[" * depth + "]" * depth

        # Act
        with pytest.raises(canonical.CanonicalizationError) as refused:
            canonical.decode(text)

        # Assert
        self.assertIs(canonical.Refusal.MALFORMED_TEXT, refused.value.refusal)

    def test_utf8_bytes_are_accepted_as_well_as_text(self) -> None:
        # Arrange
        text = b'{"missionId":"m1"}'

        # Act
        value = canonical.decode(text)

        # Assert
        self.assertEqual({"missionId": "m1"}, value)

    def test_a_repeated_key_is_refused_rather_than_merged(self) -> None:
        # Arrange
        text = '{"missionId":"m1","missionId":"m2"}'

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.decode(text)

        # Assert
        self.assertEqual(canonical.Refusal.DUPLICATE_KEY, captured.value.refusal)
        self.assertEqual("missionId", captured.value.value)

    def test_a_repeated_key_nested_in_an_array_is_also_refused(self) -> None:
        # Arrange
        text = '{"items":[{"aa":1,"aa":2}]}'

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.decode(text)

        # Assert
        self.assertEqual(canonical.Refusal.DUPLICATE_KEY, captured.value.refusal)

    def test_malformed_text_is_refused(self) -> None:
        # Arrange
        text = "{not json"

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.decode(text)

        # Assert
        self.assertEqual(canonical.Refusal.MALFORMED_TEXT, captured.value.refusal)
        self.assertEqual(text, captured.value.value)

    def test_bytes_that_decode_as_no_json_encoding_are_refused_as_malformed(self) -> None:
        # Arrange
        payload = b"a\x00\x00\x00\xff\xff\xff\xff"

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.decode(payload)

        # Assert
        self.assertEqual(canonical.Refusal.MALFORMED_TEXT, captured.value.refusal)
        self.assertEqual(payload, captured.value.value)

    def test_a_real_number_in_the_text_is_refused(self) -> None:
        # Arrange
        text = '{"latitude":47.1}'

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.decode(text)

        # Assert
        self.assertEqual(canonical.Refusal.UNSUPPORTED_TYPE, captured.value.refusal)

    def test_the_json_extensions_for_nan_and_infinity_are_refused(self) -> None:
        # Arrange
        texts = ('{"latitude":NaN}', '{"latitude":Infinity}', '{"latitude":-Infinity}')

        # Act
        refusals = tuple(_refusal_of(text) for text in texts)

        # Assert
        self.assertEqual((canonical.Refusal.UNSUPPORTED_TYPE,) * 3, refusals)


def _refusal_of(text: str) -> canonical.Refusal:
    """Return the refusal a text provokes, for tests that sweep several texts at once."""
    try:
        canonical.decode(text)
    except canonical.CanonicalizationError as error:
        return error.refusal
    message = f"expected a refusal for {text!r}"
    raise AssertionError(message)


if __name__ == "__main__":
    unittest.main()
