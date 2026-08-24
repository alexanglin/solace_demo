"""Property-based invariants of the approval protocol.

Module-level functions with ``derandomize`` so the mutation score cannot flap.
"""

from __future__ import annotations

import string
from datetime import UTC, datetime, timedelta

import pytest
from aerial_rescue_contracts import digest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from aerial_rescue_domain.approvals import (
    ApprovalError,
    ApprovalEvent,
    ApprovalState,
    ClockReading,
    Proposal,
    approve,
    consume,
    transition,
)

ISSUED = ClockReading(datetime(2026, 8, 20, 14, 0, tzinfo=UTC), timedelta(seconds=1000))
KEYS = st.from_regex("^[a-z][a-zA-Z0-9]{0,8}$", fullmatch=True).filter(
    lambda key: key != digest.DIGEST_FIELD
)
VALUES = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    st.text(alphabet=string.ascii_letters, max_size=8),
)
PARAMETERS = st.dictionaries(KEYS, VALUES, max_size=5).map(
    lambda drawn: {**drawn, digest.VERSION_FIELD: digest.CANONICALIZATION_VERSION}
)
WINDOWS = st.timedeltas(min_value=timedelta(milliseconds=1), max_value=timedelta(days=1))
OFFSETS = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=2))


def _consumed(window: timedelta, wall_offset: timedelta, monotonic_offset: timedelta) -> bool:
    """Report whether consumption at the offsets succeeds for an approval with ``window``."""
    proposal = Proposal("m-1", "p-1", {digest.VERSION_FIELD: digest.CANONICALIZATION_VERSION})
    approval = approve(ApprovalState.REQUESTED, proposal, "operator-7", ISSUED, window)
    now = ClockReading(ISSUED.wall + wall_offset, ISSUED.monotonic + monotonic_offset)
    try:
        consume(approval, proposal, now)
    except ApprovalError:
        return False
    return True


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(st.sampled_from(ApprovalState), st.sampled_from(ApprovalEvent))
def test_executed_is_reachable_only_from_approved_by_execute(
    state: ApprovalState, event: ApprovalEvent
) -> None:
    # Arrange
    expected = (state, event) == (ApprovalState.APPROVED, ApprovalEvent.EXECUTE)

    # Act
    try:
        reached = transition(state, event) is ApprovalState.EXECUTED
    except ApprovalError:
        reached = False

    # Assert
    assert reached == expected


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(WINDOWS, OFFSETS, OFFSETS)
def test_consumption_succeeds_exactly_when_both_deltas_are_below_the_window(
    window: timedelta, wall_offset: timedelta, monotonic_offset: timedelta
) -> None:
    # Arrange
    expected = wall_offset < window and monotonic_offset < window

    # Act
    succeeded = _consumed(window, wall_offset, monotonic_offset)

    # Assert
    assert succeeded == expected


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(PARAMETERS, KEYS, VALUES)
def test_any_changed_or_added_parameter_is_refused_on_digest(
    parameters: dict[str, object], key: str, value: object
) -> None:
    # Arrange
    assume(parameters.get(key) != value)
    proposal = Proposal("m-1", "p-1", parameters)
    approval = approve(
        ApprovalState.REQUESTED, proposal, "operator-7", ISSUED, timedelta(seconds=60)
    )
    candidate = Proposal("m-1", "p-1", {**parameters, key: value})

    # Act
    with pytest.raises(ApprovalError) as captured:
        consume(approval, candidate, ISSUED)

    # Assert
    assert captured.value.refusal.name == "DIGEST"


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(PARAMETERS)
def test_the_record_digest_equals_the_contracts_digest_of_the_parameters(
    parameters: dict[str, object],
) -> None:
    # Arrange
    proposal = Proposal("m-1", "p-1", parameters)

    # Act
    approval = approve(
        ApprovalState.REQUESTED, proposal, "operator-7", ISSUED, timedelta(seconds=60)
    )

    # Assert
    assert approval.proposal_digest == digest.digest(digest.Context.PROPOSAL, parameters)
