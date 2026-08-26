"""Property-based invariants of sequence admission.

Module-level functions with ``derandomize`` so the mutation score cannot flap.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.idempotency import SequenceVerdict, Stream, receive

SEQUENCES = st.integers(min_value=0, max_value=10**15 - 1)


@st.composite
def arrivals(draw: st.DrawFn) -> tuple[list[int], list[int]]:
    """Draw one batch of sequences in two arrival orders."""
    batch = draw(st.lists(SEQUENCES, min_size=1, max_size=20))
    return (batch, draw(st.permutations(batch)))


def _fold(sequences: list[int]) -> Stream:
    """Admit every sequence in order and return the resulting stream."""
    stream = Stream()
    for sequence in sequences:
        stream = receive(stream, sequence).stream
    return stream


@pytest.mark.property
@settings(derandomize=True, max_examples=300)
@given(SEQUENCES, SEQUENCES)
def test_the_verdict_agrees_with_integer_order(accepted: int, candidate: int) -> None:
    # Arrange
    if candidate > accepted:
        expected = SequenceVerdict.ADVANCES
    elif candidate == accepted:
        expected = SequenceVerdict.DUPLICATE
    else:
        expected = SequenceVerdict.STALE

    # Act
    reception = receive(Stream(accepted), candidate)

    # Assert
    assert reception.verdict is expected


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(arrivals())
def test_arrival_order_does_not_change_the_high_water_mark(
    orders: tuple[list[int], list[int]],
) -> None:
    # Arrange
    first_order, second_order = orders

    # Act
    streams = (_fold(first_order), _fold(second_order))

    # Assert
    assert streams == (Stream(max(first_order)), Stream(max(first_order)))
