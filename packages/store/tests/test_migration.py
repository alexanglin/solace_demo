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
    APPLICATION_OUTBOX_TABLE,
    APPROVAL_TABLE,
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    BROKER_INBOX_TABLE,
    COMMAND_OUTBOX_TABLE,
    COMMAND_PROGRESS_TABLE,
    CONNECTION_ATTRIBUTE,
    DRONE_COMMAND_RECEIPT_TABLE,
    EVIDENCE_DECISION_TABLE,
    EVIDENCE_ITEM_TABLE,
    IDEMPOTENCY_CLAIM_TABLE,
    PARAMSTYLE,
    PROPOSAL_TABLE,
    SCRIPT_DIRECTORY,
    SOURCE_EVENT_TABLE,
    SOURCE_EVIDENCE_ITEM_TABLE,
    URL_OPTION,
    VERSION_SERIES,
    downgrade_statements,
    environment_arguments,
    heads,
    live_config,
    migration_config,
    revisions,
    upgrade_statements,
)

if TYPE_CHECKING:
    from sqlalchemy import Connection

PROBE_URL: Final = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"

FIRST_REVISION: Final = "0001_audit_log"
SECOND_REVISION: Final = "0002_approval"
THIRD_REVISION: Final = "0003_idempotency"
FOURTH_REVISION: Final = "0004_command_outbox"
FIFTH_REVISION: Final = "0005_dashboard_runtime"
SIXTH_REVISION: Final = "0006_application_processing"
FIRST_TO_SECOND: Final = f"{FIRST_REVISION}:{SECOND_REVISION}"
SECOND_TO_THIRD: Final = f"{SECOND_REVISION}:{THIRD_REVISION}"
THIRD_TO_FOURTH: Final = f"{THIRD_REVISION}:{FOURTH_REVISION}"
FOURTH_TO_FIFTH: Final = f"{FOURTH_REVISION}:{FIFTH_REVISION}"
FIFTH_TO_SIXTH: Final = f"{FIFTH_REVISION}:{SIXTH_REVISION}"
"""Alembic's range form, so a step renders on its own rather than the whole history."""


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


class SecondRevisionTests(unittest.TestCase):
    """Rendered as the step from the first revision, so what is asserted is the path."""

    def test_the_step_from_the_first_revision_creates_the_approval_table(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIRST_TO_SECOND)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"CREATE TABLE {APPROVAL_TABLE}" in emitted,
                f"CREATE TABLE {AUDIT_RECORD_TABLE}" in emitted,
            ),
        )

    def test_one_proposal_has_one_approval_because_the_proposal_is_the_key(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIRST_TO_SECOND)

        # Assert
        self.assertIn("PRIMARY KEY (proposal_id)", emitted)

    def test_the_state_is_constrained_to_the_protocols_own_spellings(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIRST_TO_SECOND)

        # Assert
        self.assertIn(
            "CHECK (state IN ('requested', 'approved', 'rejected', 'expired', "
            "'superseded', 'executed'))",
            emitted,
        )

    def test_both_clock_readings_keep_the_forms_they_were_read_in(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIRST_TO_SECOND)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "issued_wall VARCHAR(24) NOT NULL" in emitted,
                "issued_monotonic_milliseconds BIGINT NOT NULL" in emitted,
            ),
        )

    def test_the_step_back_drops_only_the_table_this_revision_created(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, FIRST_REVISION)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"DROP TABLE {APPROVAL_TABLE}" in emitted,
                f"DROP TABLE {AUDIT_RECORD_TABLE}" in emitted,
            ),
        )


class ThirdRevisionTests(unittest.TestCase):
    """Rendered as the step from the second revision, so what is asserted is the path."""

    def test_the_step_from_the_second_revision_creates_the_claim_table(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SECOND_TO_THIRD)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"CREATE TABLE {IDEMPOTENCY_CLAIM_TABLE}" in emitted,
                f"CREATE TABLE {APPROVAL_TABLE}" in emitted,
            ),
        )

    def test_the_key_is_the_claim_because_the_key_is_what_conflicts(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SECOND_TO_THIRD)

        # Assert
        self.assertIn("PRIMARY KEY (idempotency_key)", emitted)

    def test_the_kind_is_constrained_to_the_two_the_domain_names(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SECOND_TO_THIRD)

        # Assert
        self.assertIn("CHECK (kind IN ('command', 'approval consumption'))", emitted)

    def test_an_unanswered_claim_is_a_null_result_rather_than_an_absent_row(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SECOND_TO_THIRD)

        # Assert
        self.assertIn("result BYTEA", emitted)

    def test_the_step_back_drops_only_the_table_this_revision_created(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, SECOND_REVISION)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"DROP TABLE {IDEMPOTENCY_CLAIM_TABLE}" in emitted,
                f"DROP TABLE {APPROVAL_TABLE}" in emitted,
            ),
        )


class FourthRevisionTests(unittest.TestCase):
    """Rendered as the step from the third revision, so what is asserted is the path."""

    def test_the_step_from_the_third_revision_creates_the_outbox_table(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, THIRD_TO_FOURTH)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"CREATE TABLE {COMMAND_OUTBOX_TABLE}" in emitted,
                f"CREATE TABLE {IDEMPOTENCY_CLAIM_TABLE}" in emitted,
            ),
        )


class SixthRevisionTests(unittest.TestCase):
    """Rendered from immutable 0005 so application processing is one additive step."""

    def test_the_step_creates_every_new_authoritative_table_and_no_prior_table(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)
        expected = (
            BROKER_INBOX_TABLE,
            SOURCE_EVENT_TABLE,
            SOURCE_EVIDENCE_ITEM_TABLE,
            APPLICATION_OUTBOX_TABLE,
            PROPOSAL_TABLE,
            EVIDENCE_ITEM_TABLE,
            EVIDENCE_DECISION_TABLE,
            COMMAND_PROGRESS_TABLE,
            DRONE_COMMAND_RECEIPT_TABLE,
        )

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            ((True,) * len(expected), False),
            (
                tuple(f"CREATE TABLE {table}" in emitted for table in expected),
                f"CREATE TABLE {COMMAND_OUTBOX_TABLE}" in emitted,
            ),
        )

    def test_inbox_identity_is_consumer_source_and_event_id_together(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertIn("PRIMARY KEY (consumer, source, event_id)", emitted)

    def test_source_events_keep_complete_canonical_bytes_under_exact_cloudevent_identity(
        self,
    ) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True, True, True, True),
            (
                "PRIMARY KEY (source, event_id)" in emitted,
                "topic VARCHAR(250) NOT NULL" in emitted,
                "canonical_payload BYTEA NOT NULL" in emitted,
                "canonical_digest ~ '^[0-9a-f]{64}$'" in emitted,
                "CREATE INDEX ix_source_event_mission_event" in emitted,
            ),
        )

    def test_source_evidence_is_ordered_bounded_and_bound_to_exact_source_identity(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True, True, True, True),
            (
                "PRIMARY KEY (source_event_source, source_event_id, ordinal)" in emitted,
                "FOREIGN KEY(source_event_source, source_event_id)" in emitted,
                "REFERENCES source_event (source, event_id) ON DELETE RESTRICT" in emitted,
                "ordinal BETWEEN 1 AND 23" in emitted,
                "document BYTEA NOT NULL" in emitted,
            ),
        )

    def test_application_outbox_has_a_closed_state_and_deterministic_drain_index(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "CHECK (state IN ('staged', 'reconciliation needed', 'confirmed'))" in emitted,
                "CREATE INDEX ix_application_outbox_drain" in emitted,
            ),
        )

    def test_evidence_and_decisions_are_bound_to_immutable_proposals(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (2, True),
            (
                emitted.count("REFERENCES proposal (proposal_id)"),
                "UNIQUE (proposal_id, sequence)" in emitted,
            ),
        )

    def test_command_progress_and_receipts_keep_progress_separate_from_prior_results(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, FIFTH_TO_SIXTH)

        # Assert
        self.assertEqual(
            (True, True, True, True, False),
            (
                "send_count <= 5" in emitted,
                "PRIMARY KEY (drone_id, command_id)" in emitted,
                "ck_drone_command_receipt_completion" in emitted,
                "(result IS NULL AND applied_sequence IS NULL AND processed_at IS NULL)" in emitted,
                "result BYTEA NOT NULL" in emitted,
            ),
        )

    def test_the_step_back_drops_only_the_nine_tables_this_revision_created(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)
        expected = (
            BROKER_INBOX_TABLE,
            SOURCE_EVENT_TABLE,
            SOURCE_EVIDENCE_ITEM_TABLE,
            APPLICATION_OUTBOX_TABLE,
            PROPOSAL_TABLE,
            EVIDENCE_ITEM_TABLE,
            EVIDENCE_DECISION_TABLE,
            COMMAND_PROGRESS_TABLE,
            DRONE_COMMAND_RECEIPT_TABLE,
        )

        # Act
        emitted = downgrade_statements(config, FIFTH_REVISION)

        # Assert
        self.assertEqual(
            ((True,) * len(expected), False),
            (
                tuple(f"DROP TABLE {table}" in emitted for table in expected),
                f"DROP TABLE {COMMAND_OUTBOX_TABLE}" in emitted,
            ),
        )


class FourthRevisionConstraintTests(unittest.TestCase):
    """The remaining constraints owned by the fourth revision."""

    def test_one_command_holds_one_record_because_a_retry_republishes_it(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, THIRD_TO_FOURTH)

        # Assert
        self.assertIn("PRIMARY KEY (command_id)", emitted)

    def test_the_state_is_constrained_to_the_three_the_lifecycle_names(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, THIRD_TO_FOURTH)

        # Assert
        self.assertIn("CHECK (state IN ('staged', 'reconciliation needed', 'confirmed'))", emitted)

    def test_the_payload_keeps_the_canonical_bytes_it_was_accepted_as(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, THIRD_TO_FOURTH)

        # Assert
        self.assertIn("payload BYTEA NOT NULL", emitted)

    def test_the_step_back_drops_only_the_table_this_revision_created(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, THIRD_REVISION)

        # Assert
        self.assertEqual(
            (True, False),
            (
                f"DROP TABLE {COMMAND_OUTBOX_TABLE}" in emitted,
                f"DROP TABLE {IDEMPOTENCY_CLAIM_TABLE}" in emitted,
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


class LiveConfigurationTests(unittest.TestCase):
    def test_a_live_configuration_carries_the_connection_it_applies_through(self) -> None:
        # Arrange
        connection = cast("Connection", object())

        # Act
        config = live_config(connection)

        # Assert
        self.assertIs(connection, config.attributes[CONNECTION_ATTRIBUTE])

    def test_a_live_configuration_reads_this_package_own_history(self) -> None:
        # Arrange
        offline = heads(migration_config(PROBE_URL))

        # Act
        live = heads(live_config(cast("Connection", object())))

        # Assert
        self.assertEqual(offline, live)

    def test_a_live_configuration_applies_revisions_instead_of_rendering_them(self) -> None:
        # Arrange
        config = live_config(cast("Connection", object()))

        # Act
        arguments = environment_arguments(
            config.attributes[CONNECTION_ATTRIBUTE], config.get_main_option(URL_OPTION)
        )

        # Assert
        self.assertFalse(arguments.as_sql)


if __name__ == "__main__":
    unittest.main()
