"""Alembic and SQLAlchemy ownership for durable simulated-drone processing."""

from __future__ import annotations

import unittest

from aerial_rescue_store.database.schema import (
    DRONE_COMMAND_EFFECT,
    DRONE_STREAM_STATE,
    METADATA,
)
from aerial_rescue_store.migration import (
    APPLICATION_OUTBOX_TABLE,
    DRONE_COMMAND_EFFECT_TABLE,
    DRONE_COMMAND_RECEIPT_TABLE,
    DRONE_STREAM_STATE_TABLE,
    downgrade_statements,
    migration_config,
    upgrade_statements,
)
from sqlalchemy import BigInteger, LargeBinary, String, Table

PROBE_URL = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"
FIFTH_REVISION = "0005_application_processing"
SIXTH_REVISION = "0006_durable_fleet_processing"
FIFTH_TO_SIXTH = f"{FIFTH_REVISION}:{SIXTH_REVISION}"


class SixthRevisionTests(unittest.TestCase):
    def test_the_additive_step_creates_only_the_fleet_authority_tables(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True, False, False),
            (
                f"CREATE TABLE {DRONE_STREAM_STATE_TABLE}" in emitted,
                f"CREATE TABLE {DRONE_COMMAND_EFFECT_TABLE}" in emitted,
                f"CREATE TABLE {APPLICATION_OUTBOX_TABLE}" in emitted,
                f"CREATE TABLE {DRONE_COMMAND_RECEIPT_TABLE}" in emitted,
            ),
        )

    def test_stream_identity_sequence_and_effect_binding_are_database_constrained(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True, True, True, True, True),
            (
                "PRIMARY KEY (drone_id)" in emitted,
                "UNIQUE (producer)" in emitted,
                "high_water BETWEEN 0 AND 999999999999999" in emitted,
                "PRIMARY KEY (drone_id, command_id)" in emitted,
                "FOREIGN KEY(drone_id, command_id)" in emitted,
                "REFERENCES drone_command_receipt (drone_id, command_id) ON DELETE RESTRICT"
                in emitted,
            ),
        )

    def test_effects_are_append_only_per_command_and_per_applied_sequence(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True, True, True),
            (
                "UNIQUE (drone_id, applied_sequence)" in emitted,
                "CHECK (outcome IN ('succeeded', 'failed'))" in emitted,
                "effect_payload BYTEA NOT NULL" in emitted,
                "octet_length(effect_payload) > 0" in emitted,
            ),
        )

    def test_the_step_back_drops_only_the_two_tables_owned_by_this_revision(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, FIFTH_REVISION)

        # Assert
        self.assertEqual(
            (True, True, False, False),
            (
                f"DROP TABLE {DRONE_COMMAND_EFFECT_TABLE}" in emitted,
                f"DROP TABLE {DRONE_STREAM_STATE_TABLE}" in emitted,
                f"DROP TABLE {APPLICATION_OUTBOX_TABLE}" in emitted,
                f"DROP TABLE {DRONE_COMMAND_RECEIPT_TABLE}" in emitted,
            ),
        )


class FleetMetadataTests(unittest.TestCase):
    def test_current_metadata_contains_both_complete_migrated_tables(self) -> None:
        # Arrange
        expected = {DRONE_STREAM_STATE_TABLE, DRONE_COMMAND_EFFECT_TABLE}

        # Act
        actual = expected.intersection(METADATA.tables)

        # Assert
        self.assertEqual((expected, (Table, Table)), (actual, tuple(map(type, actual_tables()))))

    def test_stream_and_effect_columns_match_the_migration_contract_exactly(self) -> None:
        # Arrange
        expected = (
            ("drone_id", "producer", "high_water"),
            (
                "drone_id",
                "command_id",
                "mission_id",
                "command_digest",
                "outcome",
                "effect_payload",
                "applied_sequence",
                "applied_at",
            ),
        )

        # Act
        actual = tuple(tuple(column.name for column in table.columns) for table in actual_tables())

        # Assert
        self.assertEqual(expected, actual)

    def test_fleet_metadata_uses_explicit_text_integer_and_binary_types(self) -> None:
        # Arrange
        columns = (
            DRONE_STREAM_STATE.c.producer,
            DRONE_STREAM_STATE.c.high_water,
            DRONE_COMMAND_EFFECT.c.effect_payload,
            DRONE_COMMAND_EFFECT.c.applied_sequence,
        )

        # Act
        kinds = tuple(type(column.type) for column in columns)

        # Assert
        self.assertEqual((String, BigInteger, LargeBinary, BigInteger), kinds)


def actual_tables() -> tuple[Table, Table]:
    """Return the tables in dependency order for compact metadata assertions."""
    return DRONE_STREAM_STATE, DRONE_COMMAND_EFFECT


if __name__ == "__main__":
    unittest.main()
