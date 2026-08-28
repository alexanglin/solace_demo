"""Revision 0010 closes generic idempotency over dashboard command and decision mutations."""

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
NINTH_REVISION: Final = "0009_broker_refusal"
TENTH_REVISION: Final = "0010_dashboard_idempotency"
ELEVENTH_REVISION: Final = "0011_audit_kind"
NINTH_TO_TENTH: Final = f"{NINTH_REVISION}:{TENTH_REVISION}"
DASHBOARD_KINDS: Final = (
    "dashboard command",
    "dashboard decision",
)
ESTABLISHED_KINDS: Final = ("command", "approval consumption")


class DashboardIdempotencyMigrationTests(unittest.TestCase):
    def test_revision_0010_is_the_step_before_the_single_linear_head(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        actual = heads(config)
        emitted = upgrade_statements(config, f"{TENTH_REVISION}:{ELEVENTH_REVISION}")

        # Assert
        self.assertEqual(((ELEVENTH_REVISION,), True), (actual, "ALTER TABLE" in emitted))

    def test_upgrade_replaces_only_the_kind_constraint_with_the_closed_total_set(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, NINTH_TO_TENTH)

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
        emitted = downgrade_statements(config, NINTH_REVISION)

        # Assert
        self.assertIn(
            "CHECK (kind IN ('command', 'approval consumption'))",
            emitted,
        )
        self.assertTrue(all(f"'{kind}'" not in emitted for kind in DASHBOARD_KINDS))


if __name__ == "__main__":
    unittest.main()
