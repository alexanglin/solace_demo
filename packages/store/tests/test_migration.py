"""The migration tree, driven offline so its revisions earn their own coverage.

[ADR-0087](../../../docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md)
puts the tree inside the member and pays its coverage through Alembic's offline mode, which runs a
revision's ``upgrade`` and ``downgrade`` bodies against a statement-emitting context with no
connection. These tests are that payment: the data definition asserted here is the data definition
the revision issues, not a description of it.

What they cannot establish is whether PostgreSQL accepts any of it. That is the live probe's job,
and [ADR-0086](../../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)
keeps it out of this suite entirely.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from aerial_rescue_store.migration import (
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    PARAMSTYLE,
    SCRIPT_DIRECTORY,
    VERSION_SERIES,
    downgrade_statements,
    environment_arguments,
    heads,
    migration_config,
    revisions,
    upgrade_statements,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection

PROBE_URL: Final = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"


class TreeLayoutTests(unittest.TestCase):
    def test_the_script_directory_and_its_environment_live_inside_the_package(self) -> None:
        # Arrange
        package = Path(__file__).resolve().parents[1] / "src" / "aerial_rescue_store"

        # Act
        location = SCRIPT_DIRECTORY

        # Assert
        self.assertEqual(
            (package / "migrations", True),
            (location, (location / "env.py").is_file()),
        )

    def test_the_versions_are_sharded_by_release_series(self) -> None:
        # Arrange
        location = SCRIPT_DIRECTORY

        # Act
        series = tuple((location / "versions" / name).is_dir() for name in VERSION_SERIES)

        # Assert
        self.assertEqual((True,) * len(VERSION_SERIES), series)

    def test_the_tree_has_exactly_one_head(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        found = heads(config)

        # Assert
        self.assertEqual(1, len(found))

    def test_every_revision_declares_a_downgrade_that_does_something(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emptied = tuple(
            revision
            for revision in revisions(config)
            if not downgrade_statements(config, revision).strip()
        )

        # Assert
        self.assertEqual((), emptied)


class FirstRevisionTests(unittest.TestCase):
    def test_the_upgrade_creates_the_sequence_and_the_record_table(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config)

        # Assert
        self.assertEqual(
            (True, True),
            (
                f"CREATE TABLE {AUDIT_SEQUENCE_TABLE}" in emitted,
                f"CREATE TABLE {AUDIT_RECORD_TABLE}" in emitted,
            ),
        )

    def test_the_record_is_keyed_by_its_mission_and_ordinal_together(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config)

        # Assert
        self.assertIn("PRIMARY KEY (mission_id, ordinal)", emitted)

    def test_the_payload_and_the_instant_keep_their_canonical_forms(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config)

        # Assert
        self.assertEqual(
            (True, True),
            ("payload BYTEA NOT NULL" in emitted, "occurred_at VARCHAR(24) NOT NULL" in emitted),
        )

    def test_the_sequence_refuses_an_ordinal_below_one(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config)

        # Assert
        self.assertIn("next_ordinal >= 1", emitted)

    def test_the_downgrade_drops_both_tables_it_created(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, "base")

        # Assert
        self.assertEqual(
            (True, True),
            (
                f"DROP TABLE {AUDIT_RECORD_TABLE}" in emitted,
                f"DROP TABLE {AUDIT_SEQUENCE_TABLE}" in emitted,
            ),
        )


class EnvironmentArgumentTests(unittest.TestCase):
    def test_no_connection_renders_statements_against_the_configured_url(self) -> None:
        # Arrange
        url = PROBE_URL

        # Act
        arguments = environment_arguments(None, url)

        # Assert
        self.assertEqual(
            (None, url, True, True, PARAMSTYLE),
            (
                arguments.connection,
                arguments.url,
                arguments.as_sql,
                arguments.literal_binds,
                arguments.dialect_opts["paramstyle"],
            ),
        )

    def test_a_connection_applies_the_revisions_and_renders_nothing(self) -> None:
        # Arrange
        connection = cast("Connection", object())

        # Act
        arguments = environment_arguments(connection, PROBE_URL)

        # Assert
        self.assertEqual(
            (connection, None, False, False, {}),
            (
                arguments.connection,
                arguments.url,
                arguments.as_sql,
                arguments.literal_binds,
                dict(arguments.dialect_opts),
            ),
        )

    def test_a_connection_wins_over_a_url_that_is_also_configured(self) -> None:
        # Arrange
        connection = cast("Connection", object())

        # Act
        rendered = environment_arguments(connection, PROBE_URL).as_sql

        # Assert
        self.assertFalse(rendered)


if __name__ == "__main__":
    unittest.main()
