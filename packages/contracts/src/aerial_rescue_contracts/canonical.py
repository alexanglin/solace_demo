"""Canonical serialization of digest-covered payloads.

The normative rules are in ``docs/CONTRACTS.md`` and the decision behind them is
``docs/adr/0027-integer-only-canonical-serialization.md``. The profile is deliberately
narrower than JSON: no floating-point value is representable, so two distinct
coordinates cannot collapse onto one digest before hashing.

This module is pure. It performs no input or output, reads no clock, and consumes no
random source, so a caller can rely on identical bytes for the same logical value on
every run and in every process.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Final

MAX_SAFE_INTEGER: Final = 2**53 - 1
"""Largest integer a TypeScript ``number`` represents exactly."""

MAX_KEY_LENGTH: Final = 64
"""Longest admissible object key, in characters."""

MAX_STRING_BYTES: Final = 4096
"""Longest admissible string value, in UTF-8 bytes."""

CANONICAL_KEY_PATTERN: Final = "[a-z][a-zA-Z0-9]*"
"""Object keys are lower camel case ASCII, matching the identifiers in CONTRACTS.md."""

SURROGATE_FIRST_CODE_POINT: Final = 0xD800
SURROGATE_LAST_CODE_POINT: Final = 0xDFFF
"""The UTF-16 surrogate block, which carries no character on its own."""


class Refusal(Enum):
    """Why a value cannot be represented in the canonical profile."""

    UNSUPPORTED_TYPE = "unsupported type"
    INTEGER_RANGE = "integer outside the exactly representable range"
    KEY_FORM = "object key outside the canonical form"
    KEY_LENGTH = "object key longer than the bound"
    STRING_LENGTH = "string longer than the bound"
    LONE_SURROGATE = "string carries an unpaired surrogate"
    DUPLICATE_KEY = "object repeats a key"
    MALFORMED_TEXT = "text is not well-formed JSON"


class CanonicalizationError(ValueError):
    """A value the canonical profile cannot represent.

    Carries the refusal as structured data so a caller and a test assert on the reason
    rather than on message prose.
    """

    def __init__(self, refusal: Refusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


_SHORT_ESCAPES: Final = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _utf8(text: str) -> bytes:
    """Return the UTF-8 encoding of a string.

    ``str.encode`` is UTF-8 by default in Python 3, so the codec is not named here.
    """
    return text.encode()


def canonical_bytes(value: object) -> bytes:
    """Return the canonical UTF-8 bytes for one admissible value.

    Args:
        value: A value drawn from the canonical profile.

    Returns:
        The minified UTF-8 encoding, with object keys in ascending byte order.

    Raises:
        CanonicalizationError: If the value falls outside the canonical profile.
    """
    return _utf8(_encode(value))


def decode(text: str | bytes) -> object:
    """Parse JSON text into the canonical profile.

    A repeated key is refused rather than merged last-value-wins, because two texts that
    differ only in a repeated key would otherwise reduce to one digest.

    Args:
        text: JSON text, as a string or UTF-8 bytes.

    Returns:
        The parsed value, guaranteed to lie inside the canonical profile.

    Raises:
        CanonicalizationError: If the text is malformed, repeats a key, or carries a
            value the profile cannot represent.
    """
    try:
        value = json.loads(text, object_pairs_hook=_object_from_pairs)
    except json.JSONDecodeError as error:
        raise CanonicalizationError(Refusal.MALFORMED_TEXT, text) from error
    canonical_bytes(value)
    return value


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build an object from parsed pairs, refusing a repeated key."""
    members: dict[str, object] = {}
    for key, item in pairs:
        if key in members:
            raise CanonicalizationError(Refusal.DUPLICATE_KEY, key)
        members[key] = item
    return members


def _encode(value: object) -> str:
    """Return the canonical text for one admissible value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return _encode_boolean(value)
    if isinstance(value, int):
        return _encode_integer(value)
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, Mapping):
        return _encode_object(value)
    if isinstance(value, (list, tuple)):
        return _encode_array(value)
    raise CanonicalizationError(Refusal.UNSUPPORTED_TYPE, value)


def _encode_boolean(value: bool) -> str:
    """Return the lowercase literal for a boolean."""
    if value:
        return "true"
    return "false"


def _encode_integer(value: int) -> str:
    """Return the shortest decimal form of an exactly representable integer."""
    if value < -MAX_SAFE_INTEGER or value > MAX_SAFE_INTEGER:
        raise CanonicalizationError(Refusal.INTEGER_RANGE, value)
    return str(value)


def _encode_string(value: str) -> str:
    """Return the quoted, NFC-normalized, minimally escaped form of a string."""
    _reject_lone_surrogate(value)
    normalized = unicodedata.normalize("NFC", value)
    if len(_utf8(normalized)) > MAX_STRING_BYTES:
        raise CanonicalizationError(Refusal.STRING_LENGTH, value)
    body = "".join(_escape(character) for character in normalized)
    return '"' + body + '"'


def _reject_lone_surrogate(value: str) -> None:
    """Refuse a string carrying an unpaired UTF-16 surrogate code point.

    The bounds are code point numbers rather than character literals on purpose. An
    escape written with uppercase hexadecimal denotes the same character as the
    lowercase spelling, so a character literal here would leave an unkillable mutant
    standing inside a rule that guards the encoding.
    """
    for character in value:
        if SURROGATE_FIRST_CODE_POINT <= ord(character) <= SURROGATE_LAST_CODE_POINT:
            raise CanonicalizationError(Refusal.LONE_SURROGATE, value)


def _escape(character: str) -> str:
    """Return the canonical escape for one character, or the character unchanged."""
    short = _SHORT_ESCAPES.get(character)
    if short is not None:
        return short
    if character < " ":
        return "\\u" + format(ord(character), "04x")
    return character


def _canonical_key(key: object) -> str:
    """Return a validated object key, refusing anything outside the canonical form."""
    if not isinstance(key, str):
        raise CanonicalizationError(Refusal.KEY_FORM, key)
    if len(key) > MAX_KEY_LENGTH:
        raise CanonicalizationError(Refusal.KEY_LENGTH, key)
    if re.fullmatch(CANONICAL_KEY_PATTERN, key) is None:
        raise CanonicalizationError(Refusal.KEY_FORM, key)
    return key


def _encode_object(value: Mapping[object, object]) -> str:
    """Return the object form with keys ordered by ascending UTF-8 byte sequence."""
    members = {_canonical_key(key): item for key, item in value.items()}
    body = ",".join(_encode_string(key) + ":" + _encode(members[key]) for key in sorted(members))
    return "{" + body + "}"


def _encode_array(value: Sequence[object]) -> str:
    """Return the array form, preserving order because order is semantic."""
    return "[" + ",".join(_encode(item) for item in value) + "]"
