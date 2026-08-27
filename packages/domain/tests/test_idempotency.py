"""Producer-scoped sequence admission and the known-identifier decision.

A stream admits one candidate at a time against its own high-water mark and nothing else:
there is no cross-stream order, which is how the rule that per-producer sequences never order
the timeline (ADR-0003) is encoded. Approval consumption is the documented exception to
replay-as-success: a known identifier is a denial, never a prior result.
"""

from __future__ import annotations

import unittest

from aerial_rescue_domain.idempotency import (
    IdempotencyDecision,
    IdempotencyKind,
    Reception,
    SequenceVerdict,
    Stream,
    idempotency_decision,
    receive,
)

FIFTEEN_DIGIT_MAXIMUM = 10**15 - 1


class ReceiveTests(unittest.TestCase):
    def test_the_first_sequence_of_a_stream_advances_it(self) -> None:
        # Arrange
        stream = Stream()

        # Act
        reception = receive(stream, 0)

        # Assert
        self.assertEqual(Reception(SequenceVerdict.ADVANCES, Stream(0)), reception)

    def test_a_higher_sequence_advances_the_stream(self) -> None:
        # Arrange
        stream = Stream(9)

        # Act
        reception = receive(stream, 10)

        # Assert
        self.assertEqual(Reception(SequenceVerdict.ADVANCES, Stream(10)), reception)

    def test_an_equal_sequence_is_a_duplicate_and_leaves_the_stream(self) -> None:
        # Arrange
        stream = Stream(9)

        # Act
        reception = receive(stream, 9)

        # Assert
        self.assertEqual(Reception(SequenceVerdict.DUPLICATE, Stream(9)), reception)

    def test_a_lower_sequence_is_stale_and_leaves_the_stream(self) -> None:
        # Arrange
        stream = Stream(10)

        # Act
        reception = receive(stream, 9)

        # Assert
        self.assertEqual(Reception(SequenceVerdict.STALE, Stream(10)), reception)

    def test_the_fifteen_digit_maximum_is_accepted(self) -> None:
        # Arrange
        stream = Stream(FIFTEEN_DIGIT_MAXIMUM - 1)

        # Act
        reception = receive(stream, FIFTEEN_DIGIT_MAXIMUM)

        # Assert
        self.assertEqual(
            Reception(SequenceVerdict.ADVANCES, Stream(FIFTEEN_DIGIT_MAXIMUM)), reception
        )


class DecisionTests(unittest.TestCase):
    def test_an_unknown_identifier_executes_for_every_kind(self) -> None:
        # Arrange
        kinds = tuple(IdempotencyKind)

        # Act
        decisions = tuple(idempotency_decision(kind, known=False) for kind in kinds)

        # Assert
        self.assertEqual((IdempotencyDecision.EXECUTE,) * len(kinds), decisions)

    def test_a_known_command_identifier_returns_the_prior_result(self) -> None:
        # Arrange
        kind = IdempotencyKind.COMMAND

        # Act
        decision = idempotency_decision(kind, known=True)

        # Assert
        self.assertIs(IdempotencyDecision.RETURN_PRIOR_RESULT, decision)

    def test_a_repeated_approval_consumption_is_denied_not_replayed(self) -> None:
        # Arrange
        kind = IdempotencyKind.APPROVAL_CONSUMPTION

        # Act
        decision = idempotency_decision(kind, known=True)

        # Assert
        self.assertIs(IdempotencyDecision.DENY, decision)

    def test_the_decision_table_is_total_over_kinds_and_both_truth_values(self) -> None:
        # Arrange
        rows = tuple((kind, known) for kind in IdempotencyKind for known in (False, True))

        # Act
        decisions = tuple(idempotency_decision(kind, known=known) for kind, known in rows)

        # Assert
        self.assertEqual(
            (
                IdempotencyDecision.EXECUTE,
                IdempotencyDecision.RETURN_PRIOR_RESULT,
                IdempotencyDecision.EXECUTE,
                IdempotencyDecision.DENY,
                IdempotencyDecision.EXECUTE,
                IdempotencyDecision.RETURN_PRIOR_RESULT,
                IdempotencyDecision.EXECUTE,
                IdempotencyDecision.RETURN_PRIOR_RESULT,
                IdempotencyDecision.EXECUTE,
                IdempotencyDecision.RETURN_PRIOR_RESULT,
                IdempotencyDecision.EXECUTE,
                IdempotencyDecision.RETURN_PRIOR_RESULT,
            ),
            decisions,
        )


if __name__ == "__main__":
    unittest.main()
