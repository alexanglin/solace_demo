"""Alembic and SQLAlchemy schema metadata for bounded malformed-ingress evidence."""

from __future__ import annotations

import unittest
from typing import cast

from aerial_rescue_store.database.schema import BROKER_REFUSAL
from aerial_rescue_store.migration import (
    BROKER_REFUSAL_TABLE,
    PENDING_INVOCATION_TABLE,
    downgrade_statements,
    migration_config,
    upgrade_statements,
)
from sqlalchemy import String, Table

PROBE_URL = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"
SEVENTH_REVISION = "0007_command_gateway_authority"
EIGHTH_REVISION = "0008_broker_refusal"
SEVENTH_TO_EIGHTH = f"{SEVENTH_REVISION}:{EIGHTH_REVISION}"


class EighthRevisionTests(unittest.TestCase):
    def test_the_additive_step_creates_only_the_bounded_refusal_table(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SEVENTH_TO_EIGHTH)

        # Assert
        self.assertEqual(
            (True, False, False),
            (
                f"CREATE TABLE {BROKER_REFUSAL_TABLE}" in emitted,
                f"CREATE TABLE {PENDING_INVOCATION_TABLE}" in emitted,
                "payload BYTEA" in emitted,
            ),
        )

    def test_identity_digest_and_required_bounded_members_are_migrated_exactly(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SEVENTH_TO_EIGHTH)

        # Assert
        self.assertEqual(
            (True, True, True, True),
            (
                "PRIMARY KEY (consumer, channel, raw_digest)" in emitted,
                "raw_digest ~ '^[0-9a-f]{64}$'" in emitted,
                "source VARCHAR(256)" in emitted and "family VARCHAR(32)" in emitted,
                "observed_at VARCHAR(24) NOT NULL" in emitted,
            ),
        )

    def test_the_step_back_drops_only_the_table_owned_by_this_revision(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, SEVENTH_REVISION)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"DROP TABLE {BROKER_REFUSAL_TABLE}" in emitted,
                f"DROP TABLE {PENDING_INVOCATION_TABLE};" in emitted,
            ),
        )


class BrokerRefusalMetadataTests(unittest.TestCase):
    def test_metadata_matches_the_migration_and_carries_no_raw_payload(self) -> None:
        # Arrange
        expected = (
            "consumer",
            "source",
            "family",
            "channel",
            "refusal_code",
            "raw_digest",
            "observed_at",
        )

        # Act
        actual = tuple(column.name for column in BROKER_REFUSAL.columns)

        # Assert
        self.assertEqual(
            (expected, Table, False),
            (actual, type(BROKER_REFUSAL), "payload" in actual),
        )

    def test_optional_parsed_context_and_bounded_text_types_are_explicit(self) -> None:
        # Arrange
        columns = BROKER_REFUSAL.c
        source_type = cast("String", columns.source.type)
        family_type = cast("String", columns.family.type)
        channel_type = cast("String", columns.channel.type)

        # Act
        actual = (
            (type(source_type), source_type.length, columns.source.nullable),
            (type(family_type), family_type.length, columns.family.nullable),
            (type(channel_type), channel_type.length, columns.channel.nullable),
            tuple(column.name for column in BROKER_REFUSAL.primary_key.columns),
        )

        # Assert
        self.assertEqual(
            (
                (String, 256, True),
                (String, 32, True),
                (String, 250, False),
                ("consumer", "channel", "raw_digest"),
            ),
            actual,
        )


if __name__ == "__main__":
    unittest.main()
