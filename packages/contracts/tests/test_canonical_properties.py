"""Property-based invariants of the canonical profile.

These are plain module-level tests rather than ``unittest.TestCase`` methods, unlike the
rest of this package's suite. Mutmut runs pytest repeatedly in one process, so a
Hypothesis test bound to a TestCase is invoked from a different instance on each run and
trips ``HealthCheck.differing_executors``. A module-level test has no executor to differ.

``derandomize`` is set on every property. Hypothesis choosing different examples on
different runs would make a mutant survive one mutation run and die the next, turning the
tier-one mutation score into a flapping number instead of a gate.
"""

from __future__ import annotations

import json

import pytest
from aerial_rescue_contracts import canonical
from hypothesis import given, settings
from hypothesis import strategies as st

KEYS = st.from_regex(r"\A[a-z][a-zA-Z0-9]{0,8}\Z")
SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**6), max_value=10**6)
    | st.text(max_size=16)
)
VALUES = st.recursive(
    SCALARS,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(KEYS, children, max_size=4),
    max_leaves=8,
)
LONGITUDE = st.integers(min_value=-180_000_000, max_value=180_000_000)


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(VALUES)
def test_canonicalization_of_one_value_is_stable(value: object) -> None:
    # Arrange
    first = canonical.canonical_bytes(value)

    # Act
    second = canonical.canonical_bytes(value)

    # Assert
    assert first == second


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(st.dictionaries(KEYS, SCALARS, max_size=6))
def test_object_keys_are_emitted_in_ascending_order(payload: dict[str, object]) -> None:
    # Arrange
    expected = sorted(payload)

    # Act
    emitted = list(json.loads(canonical.canonical_bytes(payload)))

    # Assert
    assert expected == emitted


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(VALUES)
def test_the_canonical_form_parses_back_to_the_same_value(value: object) -> None:
    # Arrange
    expected = json.loads(json.dumps(value))

    # Act
    parsed = json.loads(canonical.canonical_bytes(value))

    # Assert
    assert expected == parsed


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(LONGITUDE, LONGITUDE)
def test_distinct_coordinates_never_share_a_canonical_form(left: int, right: int) -> None:
    # Arrange
    payloads = ({"longitude": left}, {"longitude": right})

    # Act
    encoded = tuple(canonical.canonical_bytes(payload) for payload in payloads)

    # Assert
    assert (left == right) == (encoded[0] == encoded[1])


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(st.floats(allow_nan=True, allow_infinity=True))
def test_no_floating_point_value_is_representable(value: float) -> None:
    # Arrange
    payload = {"latitude": value}

    # Act
    with pytest.raises(canonical.CanonicalizationError) as captured:
        canonical.canonical_bytes(payload)

    # Assert
    assert captured.value.refusal == canonical.Refusal.UNSUPPORTED_TYPE
