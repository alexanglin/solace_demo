"""Revision 0009 closes durable idempotency over public dashboard mutations."""

from __future__ import annotations

import unittest
from typing import Final

from aerial_rescue_store.migration import (
    downgrade_statements,
    heads,
    migration_config,
    upgrade_statements,
)

PROBE_URL: Final = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"
EIGHTH_REVISION: Final = "0008_broker_refusal"
NINTH_REVISION: Final = "0009_dashboard_idempotency"
EIGHTH_TO_NINTH: Final = f"{EIGHTH_REVISION}:{NINTH_REVISION}"
DASHBOARD_KINDS: Final = (
    "dashboard start",
    "dashboard reset",
    "dashboard command",
    "dashboard decision",
)
ESTABLISHED_KINDS: Final = ("command", "approval consumption")


class DashboardIdempotencyMigrationTests(unittest.TestCase):
    def test_revision_0009_is_the_single_linear_head(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        actual = heads(config)

        # Assert
        self.assertEqual((NINTH_REVISION,), actual)

    def test_upgrade_replaces_only_the_kind_constraint_with_the_closed_total_set(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, EIGHTH_TO_NINTH)

        # Assert
        self.assertIn(
            "ALTER TABLE idempotency_claim DROP CONSTRAINT ck_idempotency_claim_kind",
            emitted,
        )
        self.assertTrue(
            all(f"'{kind}'" in emitted for kind in (*ESTABLISHED_KINDS, *DASHBOARD_KINDS))
        )
        self.assertNotIn("CREATE TABLE", emitted)

    def test_downgrade_restores_the_exact_pre_dashboard_kind_constraint(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, EIGHTH_REVISION)

        # Assert
        self.assertIn(
            "CHECK (kind IN ('command', 'approval consumption'))",
            emitted,
        )
        self.assertTrue(all(f"'{kind}'" not in emitted for kind in DASHBOARD_KINDS))


if __name__ == "__main__":
    unittest.main()
