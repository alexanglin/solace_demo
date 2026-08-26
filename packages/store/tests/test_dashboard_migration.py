"""Offline evidence for the additive dashboard-runtime migration.

The emitted SQL proves the revision's declared representation and downgrade scope without
claiming PostgreSQL acceptance, constraint enforcement, or concurrency behavior. Those remain
the disposable-cluster integration class.
"""

from __future__ import annotations

import unittest
from typing import Final

from aerial_rescue_store.migration import (
    AUDIT_RECORD_TABLE,
    COMMAND_OUTBOX_TABLE,
    DASHBOARD_BROKER_EVENT_TABLE,
    DASHBOARD_BROKER_SOURCE_TABLE,
    DASHBOARD_CURRENT_RUN_TABLE,
    DASHBOARD_MISSION_TABLE,
    DASHBOARD_OPERATION_TABLE,
    DASHBOARD_RUN_TABLE,
    downgrade_statements,
    migration_config,
    upgrade_statements,
)

PROBE_URL: Final = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"
FOURTH_REVISION: Final = "0004_command_outbox"
FIFTH_REVISION: Final = "0005_dashboard_runtime"
FOURTH_TO_FIFTH: Final = f"{FOURTH_REVISION}:{FIFTH_REVISION}"


class DashboardRuntimeUpgradeTests(unittest.TestCase):
    def test_the_additive_step_creates_only_the_dashboard_runtime_tables(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            ((True,) * 6, False),
            (
                tuple(
                    f"CREATE TABLE {name}" in emitted
                    for name in (
                        DASHBOARD_MISSION_TABLE,
                        DASHBOARD_RUN_TABLE,
                        DASHBOARD_CURRENT_RUN_TABLE,
                        DASHBOARD_OPERATION_TABLE,
                        DASHBOARD_BROKER_SOURCE_TABLE,
                        DASHBOARD_BROKER_EVENT_TABLE,
                    )
                ),
                f"CREATE TABLE {COMMAND_OUTBOX_TABLE}" in emitted,
            ),
        )

    def test_the_run_keeps_prepared_initial_state_as_canonical_bytes(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertIn("prepared_initial_state BYTEA NOT NULL", emitted)

    def test_runtime_tables_do_not_store_unused_wall_clock_metadata(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertNotIn("created_at", emitted)
        self.assertNotIn("claimed_at", emitted)
        self.assertNotIn("completed_at", emitted)

    def test_live_and_replay_run_identities_are_mutually_exclusive(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                "mode IN ('degradedLive', 'replay')" in emitted,
                "mission_id IS NOT NULL" in emitted,
                "session_id IS NOT NULL" in emitted,
            ),
        )

    def test_the_current_pointer_is_a_single_checked_row(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "PRIMARY KEY (singleton_key)" in emitted,
                "singleton_key = 1" in emitted,
            ),
        )

    def test_one_partial_unique_index_reserves_the_pending_operation_slot(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "CREATE UNIQUE INDEX uq_dashboard_operation_one_pending" in emitted,
                "WHERE state = 'pending'" in emitted,
            ),
        )

    def test_a_completed_operation_requires_exact_status_and_response_bytes(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                "response_status SMALLINT" in emitted,
                "response_body BYTEA" in emitted,
                "state IN ('pending', 'completed')" in emitted,
            ),
        )

    def test_a_pending_operation_keeps_every_identity_needed_after_process_restart(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        operation = emitted[emitted.index("CREATE TABLE dashboard_operation") :]
        self.assertEqual(
            (True, True, True),
            (
                "scenario_id VARCHAR(64) NOT NULL" in operation,
                "scenario_revision INTEGER NOT NULL" in operation,
                "predecessor_mission_id VARCHAR(64)" in operation,
            ),
        )

    def test_broker_identity_and_sequence_are_scoped_to_the_source(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "PRIMARY KEY (source, event_id)" in emitted,
                "UNIQUE (source, source_sequence)" in emitted,
            ),
        )

    def test_each_broker_event_links_to_one_existing_audit_record(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FOURTH_TO_FIFTH)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "FOREIGN KEY(audit_mission_id, audit_ordinal)" in emitted,
                f"REFERENCES {AUDIT_RECORD_TABLE} (mission_id, ordinal)" in emitted,
            ),
        )


class DashboardRuntimeDowngradeTests(unittest.TestCase):
    def test_the_step_back_drops_only_the_six_tables_the_revision_added(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, FOURTH_REVISION)

        # Assert
        self.assertEqual(
            ((True,) * 6, False),
            (
                tuple(
                    f"DROP TABLE {name}" in emitted
                    for name in (
                        DASHBOARD_BROKER_EVENT_TABLE,
                        DASHBOARD_BROKER_SOURCE_TABLE,
                        DASHBOARD_OPERATION_TABLE,
                        DASHBOARD_CURRENT_RUN_TABLE,
                        DASHBOARD_RUN_TABLE,
                        DASHBOARD_MISSION_TABLE,
                    )
                ),
                f"DROP TABLE {COMMAND_OUTBOX_TABLE}" in emitted,
            ),
        )


if __name__ == "__main__":
    unittest.main()
