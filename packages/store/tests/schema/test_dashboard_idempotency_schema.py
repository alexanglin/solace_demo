"""Current SQLAlchemy metadata matches revision 0009's closed kind set."""

from __future__ import annotations

import unittest

from aerial_rescue_store.database.schema import IDEMPOTENCY_CLAIM
from sqlalchemy import CheckConstraint, String, Table


class DashboardIdempotencySchemaTests(unittest.TestCase):
    def test_the_claim_is_a_full_sqlalchemy_table_with_a_bounded_kind_column(self) -> None:
        # Arrange
        table = IDEMPOTENCY_CLAIM

        # Act
        kind = table.c.kind
        kind_length = kind.type.length if isinstance(kind.type, String) else None

        # Assert
        self.assertEqual((Table, String, 24), (type(table), type(kind.type), kind_length))

    def test_the_current_constraint_names_all_established_and_dashboard_kinds(self) -> None:
        # Arrange
        constraints = tuple(
            constraint
            for constraint in IDEMPOTENCY_CLAIM.constraints
            if isinstance(constraint, CheckConstraint)
            and constraint.name == "ck_idempotency_claim_kind"
        )

        # Act
        rendered = tuple(str(constraint.sqltext) for constraint in constraints)

        # Assert
        self.assertEqual(
            (
                "kind IN ('command', 'approval consumption', 'dashboard start', "
                "'dashboard reset', 'dashboard command', 'dashboard decision')",
            ),
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
