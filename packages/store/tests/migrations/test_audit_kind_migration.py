"""Revision 0011 widens the audit record's kind so an event type fits."""

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
TENTH_REVISION: Final = "0010_dashboard_idempotency"
ELEVENTH_REVISION: Final = "0011_audit_kind"
TENTH_TO_ELEVENTH: Final = f"{TENTH_REVISION}:{ELEVENTH_REVISION}"


class AuditKindMigrationTests(unittest.TestCase):
    def test_revision_0011_is_the_single_linear_head(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        actual = heads(config)

        # Assert
        self.assertEqual((ELEVENTH_REVISION,), actual)

    def test_upgrade_widens_only_the_audit_kind_column(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, TENTH_TO_ELEVENTH)

        # Assert
        self.assertEqual(
            (True, False, False),
            (
                "ALTER TABLE audit_record ALTER COLUMN kind TYPE VARCHAR(96)" in emitted,
                "CREATE TABLE" in emitted,
                "DROP" in emitted,
            ),
        )

    def test_downgrade_restores_the_exact_thirty_two_character_kind(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, TENTH_REVISION)

        # Assert
        self.assertIn("ALTER TABLE audit_record ALTER COLUMN kind TYPE VARCHAR(32)", emitted)
