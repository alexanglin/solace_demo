"""Byte-level conformance for the canonical serialization contract.

The rules under test are normative in docs/CONTRACTS.md and decided by
docs/adr/0027-integer-only-canonical-serialization.md. Assertions compare exact bytes
because the contract is the bytes; a structural comparison would pass while Python and
TypeScript still disagreed.
"""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_contracts import canonical


def _refusal_of(value: object) -> canonical.Refusal:
    """Return the refusal a value provokes, for tests that sweep many values at once."""
    try:
        canonical.canonical_bytes(value)
    except canonical.CanonicalizationError as error:
        return error.refusal
    message = f"expected a refusal for {value!r}"
    raise AssertionError(message)


class ScalarEncodingTests(unittest.TestCase):
    def test_null_true_and_false_are_lowercase_literals(self) -> None:
        # Arrange
        values = (None, True, False)

        # Act
        encoded = tuple(canonical.canonical_bytes(value) for value in values)

        # Assert
        self.assertEqual((b"null", b"true", b"false"), encoded)

    def test_an_integer_is_the_shortest_decimal_form(self) -> None:
        # Arrange
        values = (0, -0, 7, -7, 1000)

        # Act
        encoded = tuple(canonical.canonical_bytes(value) for value in values)

        # Assert
        self.assertEqual((b"0", b"0", b"7", b"-7", b"1000"), encoded)

    def test_an_integer_at_the_safe_bound_is_accepted(self) -> None:
        # Arrange
        value = canonical.MAX_SAFE_INTEGER

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b"9007199254740991", encoded)

    def test_an_integer_one_past_the_safe_bound_is_refused(self) -> None:
        # Arrange
        value = canonical.MAX_SAFE_INTEGER + 1

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(canonical.Refusal.INTEGER_RANGE, captured.value.refusal)

    def test_a_float_is_refused_even_when_numerically_integral(self) -> None:
        # Arrange
        value = 1.0

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(canonical.Refusal.UNSUPPORTED_TYPE, captured.value.refusal)


class ContainerEncodingTests(unittest.TestCase):
    def test_object_keys_are_emitted_in_ascending_utf8_byte_order(self) -> None:
        # Arrange
        payload = {"zulu": 1, "alpha": 2, "mike": 3}

        # Act
        encoded = canonical.canonical_bytes(payload)

        # Assert
        self.assertEqual(b'{"alpha":2,"mike":3,"zulu":1}', encoded)

    def test_array_order_is_preserved_and_nothing_is_padded(self) -> None:
        # Arrange
        payload = {"missionId": "m1", "sectors": [3, 1, 2], "active": True}

        # Act
        encoded = canonical.canonical_bytes(payload)

        # Assert
        self.assertEqual(b'{"active":true,"missionId":"m1","sectors":[3,1,2]}', encoded)

    def test_empty_containers_are_representable_and_distinct(self) -> None:
        # Arrange
        payload: dict[str, object] = {"emptyObject": {}, "emptyArray": []}

        # Act
        encoded = canonical.canonical_bytes(payload)

        # Assert
        self.assertEqual(b'{"emptyArray":[],"emptyObject":{}}', encoded)

    def test_a_key_outside_the_canonical_form_is_refused(self) -> None:
        # Arrange
        payload = {"MissionId": 1}

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(payload)

        # Assert
        self.assertEqual(canonical.Refusal.KEY_FORM, captured.value.refusal)


class IntegerBoundaryTests(unittest.TestCase):
    def test_the_most_negative_representable_integer_is_accepted(self) -> None:
        # Arrange
        value = -canonical.MAX_SAFE_INTEGER

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b"-9007199254740991", encoded)

    def test_an_integer_one_below_the_negative_bound_is_refused(self) -> None:
        # Arrange
        value = -canonical.MAX_SAFE_INTEGER - 1

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(canonical.Refusal.INTEGER_RANGE, captured.value.refusal)

    def test_a_refused_integer_is_carried_on_the_error(self) -> None:
        # Arrange
        value = canonical.MAX_SAFE_INTEGER + 1

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(value, captured.value.value)


class StringEncodingTests(unittest.TestCase):
    def test_a_quote_and_a_backslash_take_their_two_character_escapes(self) -> None:
        # Arrange
        value = 'a"b\\c'

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b'"a\\"b\\\\c"', encoded)

    def test_the_five_named_control_escapes_are_emitted(self) -> None:
        # Arrange
        value = "\b\f\n\r\t"

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b'"\\b\\f\\n\\r\\t"', encoded)

    def test_any_other_control_becomes_four_lowercase_hex_digits(self) -> None:
        # Arrange
        value = "\x00\x01\x1f"

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b'"\\u0000\\u0001\\u001f"', encoded)

    def test_a_space_is_emitted_raw_because_it_is_not_a_control(self) -> None:
        # Arrange
        value = "a b"

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b'"a b"', encoded)

    def test_delete_and_non_ascii_are_emitted_raw_as_utf8(self) -> None:
        # Arrange
        value = "\x7fé"

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(b'"\x7f\xc3\xa9"', encoded)

    def test_a_decomposed_string_is_normalized_to_nfc_before_encoding(self) -> None:
        # Arrange
        decomposed = "é"

        # Act
        encoded = canonical.canonical_bytes(decomposed)

        # Assert
        self.assertEqual(canonical.canonical_bytes("é"), encoded)

    def test_a_string_at_the_byte_bound_is_accepted(self) -> None:
        # Arrange
        value = "a" * canonical.MAX_STRING_BYTES

        # Act
        encoded = canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(canonical.MAX_STRING_BYTES + 2, len(encoded))

    def test_a_string_one_byte_over_the_bound_is_refused(self) -> None:
        # Arrange
        value = "a" * (canonical.MAX_STRING_BYTES + 1)

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(canonical.Refusal.STRING_LENGTH, captured.value.refusal)
        self.assertEqual(value, captured.value.value)

    def test_both_surrogate_bounds_are_refused(self) -> None:
        # Arrange
        surrogates = ("\ud800", "\udc00", "\udfff")

        # Act
        refusals = tuple(_refusal_of(surrogate) for surrogate in surrogates)

        # Assert
        self.assertEqual((canonical.Refusal.LONE_SURROGATE,) * 3, refusals)

    def test_the_code_points_adjacent_to_the_surrogate_block_are_accepted(self) -> None:
        # Arrange
        adjacent = ("퟿", "")

        # Act
        encoded = tuple(canonical.canonical_bytes(value) for value in adjacent)

        # Assert
        self.assertEqual((b'"\xed\x9f\xbf"', b'"\xee\x80\x80"'), encoded)


class KeyValidationTests(unittest.TestCase):
    def test_a_key_at_the_length_bound_is_accepted(self) -> None:
        # Arrange
        key = "m" * canonical.MAX_KEY_LENGTH

        # Act
        encoded = canonical.canonical_bytes({key: 1})

        # Assert
        self.assertEqual(b'{"' + key.encode() + b'":1}', encoded)

    def test_a_key_one_character_over_the_length_bound_is_refused(self) -> None:
        # Arrange
        key = "m" * (canonical.MAX_KEY_LENGTH + 1)

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes({key: 1})

        # Assert
        self.assertEqual(canonical.Refusal.KEY_LENGTH, captured.value.refusal)
        self.assertEqual(key, captured.value.value)

    def test_a_non_string_key_is_refused(self) -> None:
        # Arrange
        payload = {1: "one"}

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(payload)

        # Assert
        self.assertEqual(canonical.Refusal.KEY_FORM, captured.value.refusal)
        self.assertEqual(1, captured.value.value)

    def test_lower_camel_case_ascii_keys_are_accepted(self) -> None:
        # Arrange
        accepted = ("m", "missionId", "aZ9", "droneId")

        # Act
        encoded = tuple(canonical.canonical_bytes({key: 1}) for key in accepted)

        # Assert
        self.assertEqual(tuple(b'{"' + key.encode() + b'":1}' for key in accepted), encoded)

    def test_every_other_key_shape_is_refused(self) -> None:
        # Arrange
        rejected = (
            "",
            "Mission",
            "9mission",
            "mission_id",
            "mission-id",
            "missionÍd",
            "mission id",
        )

        # Act
        refusals = tuple(_refusal_of({key: 1}) for key in rejected)

        # Assert
        self.assertEqual((canonical.Refusal.KEY_FORM,) * len(rejected), refusals)

    def test_a_refused_key_is_carried_on_the_error(self) -> None:
        # Arrange
        key = "Mission"

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes({key: 1})

        # Assert
        self.assertEqual(key, captured.value.value)


class UnsupportedValueTests(unittest.TestCase):
    def test_a_tuple_encodes_as_an_array(self) -> None:
        # Arrange
        payload = (1, 2, 3)

        # Act
        encoded = canonical.canonical_bytes(payload)

        # Assert
        self.assertEqual(b"[1,2,3]", encoded)

    def test_values_outside_the_profile_are_refused(self) -> None:
        # Arrange
        outside = (1.5, complex(1, 2), {1, 2}, b"bytes", object())

        # Act
        refusals = tuple(_refusal_of(value) for value in outside)

        # Assert
        self.assertEqual((canonical.Refusal.UNSUPPORTED_TYPE,) * len(outside), refusals)


class ErrorReportingTests(unittest.TestCase):
    def test_the_message_names_both_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = canonical.CanonicalizationError(canonical.Refusal.KEY_FORM, "Mission")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("object key outside the canonical form: 'Mission'", message)


class RefusalPayloadTests(unittest.TestCase):
    def test_a_surrogate_refusal_carries_the_offending_string(self) -> None:
        # Arrange
        value = "before\ud800after"

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(value, captured.value.value)

    def test_an_unsupported_type_refusal_carries_the_offending_value(self) -> None:
        # Arrange
        value = 1.5

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            canonical.canonical_bytes(value)

        # Assert
        self.assertEqual(value, captured.value.value)


if __name__ == "__main__":
    unittest.main()
