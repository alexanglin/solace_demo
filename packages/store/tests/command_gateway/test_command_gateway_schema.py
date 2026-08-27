"""Migrated authority required by the command-gateway store adapters."""

from __future__ import annotations

import unittest

from aerial_rescue_store.database.schema import APPROVAL_BINDING, PENDING_INVOCATION
from aerial_rescue_store.migration import (
    APPROVAL_BINDING_TABLE,
    APPROVAL_TABLE,
    EVIDENCE_DECISION_TABLE,
    PENDING_INVOCATION_TABLE,
    PROPOSAL_TABLE,
    downgrade_statements,
    migration_config,
    upgrade_statements,
)
from sqlalchemy import LargeBinary, String, Table

PROBE_URL = "postgresql+asyncpg://probe@127.0.0.1:5432/probe"
SEVENTH_REVISION = "0007_durable_fleet_processing"
EIGHTH_REVISION = "0008_command_gateway_authority"
SEVENTH_TO_EIGHTH = f"{SEVENTH_REVISION}:{EIGHTH_REVISION}"


class EighthRevisionTests(unittest.TestCase):
    def test_the_additive_step_creates_only_the_two_missing_authority_tables(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SEVENTH_TO_EIGHTH)

        # Assert
        self.assertEqual(
            (True, True, False, False),
            (
                f"CREATE TABLE {PENDING_INVOCATION_TABLE}" in emitted,
                f"CREATE TABLE {APPROVAL_BINDING_TABLE}" in emitted,
                f"CREATE TABLE {APPROVAL_TABLE} (" in emitted,
                f"CREATE TABLE {PROPOSAL_TABLE} (" in emitted,
            ),
        )

    def test_the_approval_binding_references_every_authoritative_parent(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = upgrade_statements(config, SEVENTH_TO_EIGHTH)

        # Assert
        self.assertEqual(
            (True, True, True, True, True, True, True, True),
            (
                f"REFERENCES {APPROVAL_TABLE} (proposal_id) ON DELETE RESTRICT" in emitted,
                f"REFERENCES {PROPOSAL_TABLE} (proposal_id) ON DELETE RESTRICT" in emitted,
                f"REFERENCES {EVIDENCE_DECISION_TABLE} (decision_id) ON DELETE RESTRICT" in emitted,
                "CHECK (decision IN ('approve', 'reject'))" in emitted,
                "octet_length(action_payload) > 0" in emitted,
                "(decision = 'approve' AND expires_at IS NOT NULL) OR "
                "(decision = 'reject' AND expires_at IS NULL)" in emitted,
                "decision_runtime_id VARCHAR(64) NOT NULL" in emitted,
                "(authority_runtime_epoch IS NULL AND "
                "authority_issued_monotonic_milliseconds IS NULL) OR "
                "(authority_runtime_epoch IS NOT NULL AND "
                "authority_issued_monotonic_milliseconds IS NOT NULL)" in emitted,
            ),
        )

    def test_the_step_back_drops_only_the_two_tables_owned_by_this_revision(self) -> None:
        # Arrange
        config = migration_config(PROBE_URL)

        # Act
        emitted = downgrade_statements(config, SEVENTH_REVISION)

        # Assert
        self.assertEqual(
            (True, True, False, False),
            (
                f"DROP TABLE {APPROVAL_BINDING_TABLE}" in emitted,
                f"DROP TABLE {PENDING_INVOCATION_TABLE}" in emitted,
                f"DROP TABLE {APPROVAL_TABLE};" in emitted,
                f"DROP TABLE {PROPOSAL_TABLE};" in emitted,
            ),
        )


class CommandGatewayMetadataTests(unittest.TestCase):
    def test_both_migrated_tables_have_the_complete_repository_shape(self) -> None:
        # Arrange
        expected = (
            (
                "invocation_id",
                "mission_id",
                "agent_name",
                "correlation_id",
                "source_event_id",
                "source_event_digest",
            ),
            (
                "approval_id",
                "proposal_id",
                "proposal_version",
                "evidence_decision_id",
                "evidence_decision_digest",
                "evidence_decision_version",
                "decision",
                "action_payload",
                "decision_runtime_id",
                "authority_runtime_epoch",
                "authority_issued_monotonic_milliseconds",
                "expires_at",
            ),
        )

        # Act
        actual = tuple(
            tuple(column.name for column in table.columns)
            for table in (PENDING_INVOCATION, APPROVAL_BINDING)
        )

        # Assert
        self.assertEqual((expected, (Table, Table)), (actual, tuple(map(type, actual_tables()))))

    def test_canonical_and_identifier_values_keep_explicit_database_types(self) -> None:
        # Arrange
        columns = (
            PENDING_INVOCATION.c.source_event_digest,
            APPROVAL_BINDING.c.action_payload,
            APPROVAL_BINDING.c.expires_at,
        )

        # Act
        kinds = tuple(type(column.type) for column in columns)

        # Assert
        self.assertEqual(
            ((String, LargeBinary, String), True),
            (kinds, APPROVAL_BINDING.c.expires_at.nullable),
        )

    def test_gateway_authority_is_an_all_or_nothing_nullable_pair(self) -> None:
        # Arrange
        authority = (
            APPROVAL_BINDING.c.authority_runtime_epoch,
            APPROVAL_BINDING.c.authority_issued_monotonic_milliseconds,
        )

        # Act
        nullable = tuple(column.nullable for column in authority)
        constraints = {constraint.name for constraint in APPROVAL_BINDING.constraints}

        # Assert
        self.assertEqual(
            ((True, True), True, False),
            (
                nullable,
                "ck_approval_binding_authority_pair" in constraints,
                APPROVAL_BINDING.c.decision_runtime_id.nullable,
            ),
        )


def actual_tables() -> tuple[Table, Table]:
    """Return both command-gateway authority tables in dependency order."""
    return PENDING_INVOCATION, APPROVAL_BINDING


if __name__ == "__main__":
    unittest.main()
