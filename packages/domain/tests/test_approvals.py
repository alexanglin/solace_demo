"""The ADR-0006 approval protocol as pure records and transitions.

Every refusal is asserted by its structured reason and the value it carries, the
consumption order is asserted as a contract, and the binding is asserted against a digest
computed independently through the contracts package, so a mutated comparison or a
dropped check fails loudly rather than passing on the state alone.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from itertools import product

import pytest
from aerial_rescue_contracts import digest
from aerial_rescue_contracts.canonical import CanonicalizationError

from aerial_rescue_domain.approvals import (
    Approval,
    ApprovalError,
    ApprovalEvent,
    ApprovalRefusal,
    ApprovalState,
    ClockReading,
    Proposal,
    approve,
    consume,
    expire,
    proposal_digest,
    supersede,
    transition,
)

PARAMETERS: dict[str, object] = {
    "canonicalizationVersion": 1,
    "missionId": "m-1",
    "latitudeMicrodegrees": 45000000,
    "longitudeMicrodegrees": -75000000,
    "scoreVersion": 1,
}
PROPOSAL = Proposal("m-1", "p-1", PARAMETERS)
OPERATOR = "operator-7"
ISSUED = ClockReading(datetime(2026, 8, 20, 14, 0, tzinfo=UTC), timedelta(seconds=1000))
TTL = timedelta(seconds=60)
ONE_MILLISECOND = timedelta(milliseconds=1)
LEGAL_TRANSITIONS = {
    (ApprovalState.REQUESTED, ApprovalEvent.APPROVE): ApprovalState.APPROVED,
    (ApprovalState.REQUESTED, ApprovalEvent.REJECT): ApprovalState.REJECTED,
    (ApprovalState.REQUESTED, ApprovalEvent.EXPIRE): ApprovalState.EXPIRED,
    (ApprovalState.REQUESTED, ApprovalEvent.SUPERSEDE): ApprovalState.SUPERSEDED,
    (ApprovalState.APPROVED, ApprovalEvent.EXECUTE): ApprovalState.EXECUTED,
    (ApprovalState.APPROVED, ApprovalEvent.EXPIRE): ApprovalState.EXPIRED,
    (ApprovalState.APPROVED, ApprovalEvent.SUPERSEDE): ApprovalState.SUPERSEDED,
}
ALL_PAIRS = tuple(product(ApprovalState, ApprovalEvent))


def _approved() -> Approval:
    """Return a fresh approval of the baseline proposal."""
    return approve(ApprovalState.REQUESTED, PROPOSAL, OPERATOR, ISSUED, TTL)


def _at(wall_offset: timedelta, monotonic_offset: timedelta) -> ClockReading:
    """Return a reading offset from the issue reading on each clock independently."""
    return ClockReading(ISSUED.wall + wall_offset, ISSUED.monotonic + monotonic_offset)


def _candidate(**changes: object) -> Proposal:
    """Return the baseline proposal with some parameters replaced or added."""
    return Proposal("m-1", "p-1", {**PARAMETERS, **changes})


def _transition_outcome_of(state: ApprovalState, event: ApprovalEvent) -> object:
    """Return the target state, or the refusal and value when the pair is refused."""
    try:
        return transition(state, event)
    except ApprovalError as error:
        return (error.refusal, error.value)


def _consume_refusal_of(
    approval: Approval, candidate: Proposal, now: ClockReading
) -> tuple[Enum, object]:
    """Return the refusal consuming ``candidate`` raises, failing the test if it is accepted."""
    try:
        consume(approval, candidate, now)
    except ApprovalError as error:
        return (error.refusal, error.value)
    message = f"accepted: {candidate!r} at {now!r}"
    raise AssertionError(message)


def _approve_refusal_of(time_to_live: timedelta) -> tuple[Enum, object]:
    """Return the refusal approving with ``time_to_live`` raises, failing if it is accepted."""
    try:
        approve(ApprovalState.REQUESTED, PROPOSAL, OPERATOR, ISSUED, time_to_live)
    except ApprovalError as error:
        return (error.refusal, error.value)
    message = f"accepted: {time_to_live!r}"
    raise AssertionError(message)


def _move_refusal_of(
    move: Callable[[Approval], Approval], approval: Approval
) -> tuple[Enum, object]:
    """Return the refusal ``move`` raises on ``approval``, failing if it is accepted."""
    try:
        move(approval)
    except ApprovalError as error:
        return (error.refusal, error.value)
    message = f"accepted: {move.__name__} on {approval.state!r}"
    raise AssertionError(message)


class TransitionTests(unittest.TestCase):
    def test_the_thirty_state_event_pairs_resolve_to_the_documented_table(self) -> None:
        # Arrange
        expected = tuple(
            LEGAL_TRANSITIONS.get(pair, (ApprovalRefusal.TRANSITION, pair)) for pair in ALL_PAIRS
        )

        # Act
        outcomes = tuple(_transition_outcome_of(state, event) for state, event in ALL_PAIRS)

        # Assert
        self.assertEqual(expected, outcomes)

    def test_executed_is_reachable_only_from_an_approved_proposal(self) -> None:
        # Arrange
        pairs = ALL_PAIRS

        # Act
        executing = {
            pair for pair in pairs if _transition_outcome_of(*pair) is ApprovalState.EXECUTED
        }

        # Assert
        self.assertEqual({(ApprovalState.APPROVED, ApprovalEvent.EXECUTE)}, executing)

    def test_a_refused_transition_names_both_states(self) -> None:
        # Arrange
        pair = (ApprovalState.REJECTED, ApprovalEvent.EXECUTE)

        # Act
        with pytest.raises(ApprovalError) as captured:
            transition(*pair)

        # Assert
        self.assertEqual(
            (ApprovalRefusal.TRANSITION, pair), (captured.value.refusal, captured.value.value)
        )


class ApproveTests(unittest.TestCase):
    def test_approving_binds_identity_clock_window_mission_proposal_and_digest(self) -> None:
        # Arrange
        expected = Approval(
            ApprovalState.APPROVED,
            OPERATOR,
            ISSUED,
            TTL,
            "m-1",
            "p-1",
            digest.digest(digest.Context.PROPOSAL, PARAMETERS),
        )

        # Act
        approval = approve(ApprovalState.REQUESTED, PROPOSAL, OPERATOR, ISSUED, TTL)

        # Assert
        self.assertEqual(expected, approval)

    def test_expires_at_is_the_issue_wall_instant_plus_the_window(self) -> None:
        # Arrange
        approval = _approved()

        # Act
        expires_at = approval.expires_at

        # Assert
        self.assertEqual(ISSUED.wall + TTL, expires_at)

    def test_b10_a_superseded_proposal_cannot_be_approved(self) -> None:
        # Arrange
        state = ApprovalState.SUPERSEDED

        # Act
        with pytest.raises(ApprovalError) as captured:
            approve(state, PROPOSAL, OPERATOR, ISSUED, TTL)

        # Assert
        self.assertEqual(
            (ApprovalRefusal.TRANSITION, (state, ApprovalEvent.APPROVE)),
            (captured.value.refusal, captured.value.value),
        )

    def test_a_zero_or_negative_time_to_live_is_refused(self) -> None:
        # Arrange
        windows = (timedelta(0), timedelta(seconds=-1))

        # Act
        refusals = tuple(_approve_refusal_of(window) for window in windows)

        # Assert
        self.assertEqual(
            (
                (ApprovalRefusal.TIME_TO_LIVE, timedelta(0)),
                (ApprovalRefusal.TIME_TO_LIVE, timedelta(seconds=-1)),
            ),
            refusals,
        )

    def test_a_time_to_live_of_one_millisecond_is_accepted(self) -> None:
        # Arrange
        window = ONE_MILLISECOND

        # Act
        approval = approve(ApprovalState.REQUESTED, PROPOSAL, OPERATOR, ISSUED, window)

        # Assert
        self.assertEqual((ApprovalState.APPROVED, window), (approval.state, approval.time_to_live))

    def test_parameters_outside_the_canonical_profile_cannot_be_approved(self) -> None:
        # Arrange
        proposal = _candidate(latitudeMicrodegrees=45.5)

        # Act
        with pytest.raises(ApprovalError) as captured:
            approve(ApprovalState.REQUESTED, proposal, OPERATOR, ISSUED, TTL)

        # Assert
        self.assertEqual(
            (ApprovalRefusal.PARAMETERS, proposal.parameters, CanonicalizationError),
            (captured.value.refusal, captured.value.value, type(captured.value.__cause__)),
        )

    def test_parameters_at_another_canonicalization_version_cannot_be_approved(self) -> None:
        # Arrange
        proposal = _candidate(canonicalizationVersion=2)

        # Act
        with pytest.raises(ApprovalError) as captured:
            approve(ApprovalState.REQUESTED, proposal, OPERATOR, ISSUED, TTL)

        # Assert
        self.assertEqual(
            (ApprovalRefusal.PARAMETERS, digest.DigestError),
            (captured.value.refusal, type(captured.value.__cause__)),
        )


class SupersedeAndExpireTests(unittest.TestCase):
    def test_supersede_and_expire_change_only_the_state(self) -> None:
        # Arrange
        approval = _approved()

        # Act
        moved = (supersede(approval), expire(approval))

        # Assert
        self.assertEqual(
            (
                replace(approval, state=ApprovalState.SUPERSEDED),
                replace(approval, state=ApprovalState.EXPIRED),
            ),
            moved,
        )

    def test_an_executed_approval_cannot_expire_or_be_superseded(self) -> None:
        # Arrange
        executed = consume(_approved(), PROPOSAL, ISSUED)

        # Act
        refusals = (_move_refusal_of(expire, executed), _move_refusal_of(supersede, executed))

        # Assert
        self.assertEqual(
            (
                (ApprovalRefusal.TRANSITION, (ApprovalState.EXECUTED, ApprovalEvent.EXPIRE)),
                (ApprovalRefusal.TRANSITION, (ApprovalState.EXECUTED, ApprovalEvent.SUPERSEDE)),
            ),
            refusals,
        )


class ConsumeTests(unittest.TestCase):
    def test_consuming_an_approved_proposal_executes_it(self) -> None:
        # Arrange
        approval = _approved()

        # Act
        executed = consume(approval, PROPOSAL, _at(timedelta(seconds=1), timedelta(seconds=1)))

        # Assert
        self.assertEqual(replace(approval, state=ApprovalState.EXECUTED), executed)

    def test_a_second_consumption_is_a_hard_denial_not_an_idempotent_success(self) -> None:
        # Arrange
        executed = consume(_approved(), PROPOSAL, ISSUED)

        # Act
        refusal = _consume_refusal_of(executed, PROPOSAL, ISSUED)

        # Assert
        self.assertEqual((ApprovalRefusal.ALREADY_CONSUMED, "p-1"), refusal)

    def test_b10_approve_then_supersede_then_consume_is_refused(self) -> None:
        # Arrange
        superseded = supersede(_approved())

        # Act
        refusal = _consume_refusal_of(superseded, PROPOSAL, ISSUED)

        # Assert
        self.assertEqual((ApprovalRefusal.SUPERSEDED, "p-1"), refusal)

    def test_an_expired_record_is_refused_before_the_clock_is_read(self) -> None:
        # Arrange
        expired = expire(_approved())

        # Act
        refusal = _consume_refusal_of(expired, PROPOSAL, ISSUED)

        # Assert
        self.assertEqual((ApprovalRefusal.EXPIRED, "p-1"), refusal)

    def test_a_requested_or_rejected_record_is_not_approved(self) -> None:
        # Arrange
        records = tuple(
            replace(_approved(), state=state)
            for state in (ApprovalState.REQUESTED, ApprovalState.REJECTED)
        )

        # Act
        refusals = tuple(_consume_refusal_of(record, PROPOSAL, ISSUED) for record in records)

        # Assert
        self.assertEqual(
            ((ApprovalRefusal.NOT_APPROVED, "p-1"), (ApprovalRefusal.NOT_APPROVED, "p-1")),
            refusals,
        )

    def test_b15_an_approval_for_mission_a_is_refused_against_a_proposal_in_mission_b(
        self,
    ) -> None:
        # Arrange
        candidate = Proposal("m-2", "p-1", PARAMETERS)

        # Act
        refusal = _consume_refusal_of(_approved(), candidate, ISSUED)

        # Assert
        self.assertEqual((ApprovalRefusal.MISSION, "m-2"), refusal)

    def test_another_proposal_in_the_same_mission_is_refused(self) -> None:
        # Arrange
        candidate = Proposal("m-1", "p-2", PARAMETERS)

        # Act
        refusal = _consume_refusal_of(_approved(), candidate, ISSUED)

        # Assert
        self.assertEqual((ApprovalRefusal.PROPOSAL, "p-2"), refusal)

    def test_b12_altering_an_action_parameter_after_approval_is_refused_on_digest(self) -> None:
        # Arrange
        candidate = _candidate(latitudeMicrodegrees=45000001)

        # Act
        refusal = _consume_refusal_of(_approved(), candidate, ISSUED)

        # Assert
        self.assertEqual(
            (ApprovalRefusal.DIGEST, digest.digest(digest.Context.PROPOSAL, candidate.parameters)),
            refusal,
        )

    def test_b16_a_different_score_version_is_refused_on_digest(self) -> None:
        # Arrange
        candidate = _candidate(scoreVersion=2)

        # Act
        refusal = _consume_refusal_of(_approved(), candidate, ISSUED)

        # Assert
        self.assertEqual(ApprovalRefusal.DIGEST, refusal[0])

    def test_b25_an_approval_claim_inside_the_parameters_is_just_a_digest_mismatch(self) -> None:
        # Arrange
        candidate = _candidate(approved=True, approvalToken="granted")

        # Act
        refusal = _consume_refusal_of(_approved(), candidate, ISSUED)

        # Assert
        self.assertEqual(ApprovalRefusal.DIGEST, refusal[0])

    def test_a_candidate_that_cannot_be_digested_is_refused_as_parameters(self) -> None:
        # Arrange
        candidate = _candidate(latitudeMicrodegrees=45.5)

        # Act
        refusal = _consume_refusal_of(_approved(), candidate, ISSUED)

        # Assert
        self.assertEqual((ApprovalRefusal.PARAMETERS, candidate.parameters), refusal)

    def test_b08_consumption_one_millisecond_before_the_window_executes(self) -> None:
        # Arrange
        now = _at(TTL - ONE_MILLISECOND, TTL - ONE_MILLISECOND)

        # Act
        executed = consume(_approved(), PROPOSAL, now)

        # Assert
        self.assertIs(ApprovalState.EXECUTED, executed.state)

    def test_b08_consumption_exactly_at_the_window_is_expired(self) -> None:
        # Arrange
        now = _at(TTL, timedelta(0))

        # Act
        refusal = _consume_refusal_of(_approved(), PROPOSAL, now)

        # Assert
        self.assertEqual((ApprovalRefusal.EXPIRED, now), refusal)

    def test_the_monotonic_half_alone_expires_the_approval(self) -> None:
        # Arrange
        now = _at(timedelta(seconds=1), TTL)

        # Act
        refusal = _consume_refusal_of(_approved(), PROPOSAL, now)

        # Assert
        self.assertEqual((ApprovalRefusal.EXPIRED, now), refusal)

    def test_b09_a_wall_clock_earlier_than_issue_is_refused_as_regression(self) -> None:
        # Arrange
        now = _at(timedelta(hours=-1), TTL + timedelta(seconds=1))

        # Act
        refusal = _consume_refusal_of(_approved(), PROPOSAL, now)

        # Assert
        self.assertEqual((ApprovalRefusal.CLOCK_REGRESSION, now), refusal)

    def test_a_monotonic_reading_earlier_than_issue_is_refused_as_regression(self) -> None:
        # Arrange
        now = _at(timedelta(seconds=1), timedelta(seconds=-1))

        # Act
        refusal = _consume_refusal_of(_approved(), PROPOSAL, now)

        # Assert
        self.assertEqual((ApprovalRefusal.CLOCK_REGRESSION, now), refusal)

    def test_a_reading_equal_to_issue_is_not_a_regression(self) -> None:
        # Arrange
        approval = _approved()

        # Act
        executed = consume(approval, PROPOSAL, ISSUED)

        # Assert
        self.assertEqual(replace(approval, state=ApprovalState.EXECUTED), executed)

    def test_refusals_are_evaluated_in_the_documented_order(self) -> None:
        # Arrange
        approval = _approved()
        executed = consume(approval, PROPOSAL, ISSUED)
        regressed = _at(timedelta(hours=-1), TTL)
        stages = (
            (executed, Proposal("m-2", "p-2", {"latitudeMicrodegrees": 45.5}), regressed),
            (approval, Proposal("m-2", "p-2", {"latitudeMicrodegrees": 45.5}), regressed),
            (approval, Proposal("m-1", "p-2", {"latitudeMicrodegrees": 45.5}), regressed),
            (approval, _candidate(latitudeMicrodegrees=45.5), regressed),
            (approval, _candidate(latitudeMicrodegrees=45000001), regressed),
            (approval, PROPOSAL, regressed),
            (approval, PROPOSAL, _at(TTL, TTL)),
        )

        # Act
        refusals = tuple(_consume_refusal_of(*stage)[0] for stage in stages)

        # Assert
        self.assertEqual(
            (
                ApprovalRefusal.ALREADY_CONSUMED,
                ApprovalRefusal.MISSION,
                ApprovalRefusal.PROPOSAL,
                ApprovalRefusal.PARAMETERS,
                ApprovalRefusal.DIGEST,
                ApprovalRefusal.CLOCK_REGRESSION,
                ApprovalRefusal.EXPIRED,
            ),
            refusals,
        )


class DigestTests(unittest.TestCase):
    def test_the_proposal_digest_is_the_contracts_digest_in_the_proposal_context(self) -> None:
        # Arrange
        expected = digest.digest(digest.Context.PROPOSAL, PARAMETERS)

        # Act
        computed = proposal_digest(PROPOSAL)

        # Assert
        self.assertEqual(expected, computed)


class ApprovalErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = ApprovalError(ApprovalRefusal.DIGEST, "x")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("proposal digest does not match the approved digest: 'x'", message)


if __name__ == "__main__":
    unittest.main()
