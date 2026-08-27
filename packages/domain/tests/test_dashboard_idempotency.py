"""Closed repeat behavior for the generic dashboard command and decision operations."""

from __future__ import annotations

import unittest

from aerial_rescue_domain.idempotency import (
    DASHBOARD_IDEMPOTENCY_KINDS,
    IdempotencyDecision,
    IdempotencyKind,
    idempotency_decision,
)


class DashboardIdempotencyTests(unittest.TestCase):
    def test_the_dashboard_mutation_kind_set_is_closed_and_operation_specific(self) -> None:
        # Arrange
        expected = (
            IdempotencyKind.DASHBOARD_COMMAND,
            IdempotencyKind.DASHBOARD_DECISION,
        )

        # Act
        actual = DASHBOARD_IDEMPOTENCY_KINDS

        # Assert
        self.assertEqual(expected, actual)

    def test_every_known_dashboard_mutation_returns_its_exact_prior_response(self) -> None:
        # Arrange
        kinds = DASHBOARD_IDEMPOTENCY_KINDS

        # Act
        decisions = tuple(idempotency_decision(kind, known=True) for kind in kinds)

        # Assert
        self.assertEqual((IdempotencyDecision.RETURN_PRIOR_RESULT,) * 2, decisions)


if __name__ == "__main__":
    unittest.main()
