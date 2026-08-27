"""The complete SQLAlchemy schema contract for every Alembic-owned durable table."""

from __future__ import annotations

import re
import unittest

from aerial_rescue_store.database.schema import (
    APPLICATION_OUTBOX,
    APPROVAL_BINDING,
    BROKER_INBOX,
    BROKER_REFUSAL,
    COMMAND_PROGRESS,
    DRONE_COMMAND_EFFECT,
    DRONE_COMMAND_RECEIPT,
    DRONE_STREAM_STATE,
    EVIDENCE_DECISION,
    EVIDENCE_ITEM,
    METADATA,
    PENDING_INVOCATION,
    PROPOSAL,
    SOURCE_EVENT,
    SOURCE_EVIDENCE_ITEM,
)
from aerial_rescue_store.migration import (
    APPLICATION_OUTBOX_TABLE,
    APPROVAL_BINDING_TABLE,
    APPROVAL_TABLE,
    AUDIT_RECORD_TABLE,
    AUDIT_SEQUENCE_TABLE,
    BROKER_INBOX_TABLE,
    BROKER_REFUSAL_TABLE,
    COMMAND_OUTBOX_TABLE,
    COMMAND_PROGRESS_TABLE,
    DRONE_COMMAND_EFFECT_TABLE,
    DRONE_COMMAND_RECEIPT_TABLE,
    DRONE_STREAM_STATE_TABLE,
    EVIDENCE_DECISION_TABLE,
    EVIDENCE_ITEM_TABLE,
    IDEMPOTENCY_CLAIM_TABLE,
    PENDING_INVOCATION_TABLE,
    PROPOSAL_TABLE,
    SOURCE_EVENT_TABLE,
    SOURCE_EVIDENCE_ITEM_TABLE,
    migration_config,
    upgrade_statements,
)
from sqlalchemy import BigInteger, LargeBinary, MetaData, SmallInteger, String, Table


class MetadataOwnershipTests(unittest.TestCase):
    def test_metadata_is_total_over_the_tables_rendered_by_the_complete_alembic_history(
        self,
    ) -> None:
        # Arrange
        configuration = migration_config("postgresql+asyncpg://probe@127.0.0.1:5432/probe")

        # Act
        rendered = upgrade_statements(configuration)
        migrated = set(re.findall(r"CREATE TABLE ([a-z_]+)", rendered)) - {"alembic_version"}

        # Assert
        self.assertEqual(set(METADATA.tables), migrated)

    def test_metadata_is_package_owned_and_total_over_every_migrated_table(self) -> None:
        # Arrange
        expected = {
            AUDIT_SEQUENCE_TABLE,
            AUDIT_RECORD_TABLE,
            APPROVAL_TABLE,
            IDEMPOTENCY_CLAIM_TABLE,
            COMMAND_OUTBOX_TABLE,
            BROKER_INBOX_TABLE,
            BROKER_REFUSAL_TABLE,
            SOURCE_EVENT_TABLE,
            SOURCE_EVIDENCE_ITEM_TABLE,
            APPLICATION_OUTBOX_TABLE,
            PROPOSAL_TABLE,
            EVIDENCE_ITEM_TABLE,
            EVIDENCE_DECISION_TABLE,
            COMMAND_PROGRESS_TABLE,
            DRONE_COMMAND_RECEIPT_TABLE,
            DRONE_STREAM_STATE_TABLE,
            DRONE_COMMAND_EFFECT_TABLE,
            PENDING_INVOCATION_TABLE,
            APPROVAL_BINDING_TABLE,
        }

        # Act
        actual = set(METADATA.tables)

        # Assert
        self.assertEqual((MetaData, expected), (type(METADATA), actual))

    def test_every_repository_table_is_a_full_sqlalchemy_table(self) -> None:
        # Arrange
        application_tables = (
            BROKER_INBOX,
            BROKER_REFUSAL,
            SOURCE_EVENT,
            SOURCE_EVIDENCE_ITEM,
            APPLICATION_OUTBOX,
            PROPOSAL,
            EVIDENCE_ITEM,
            EVIDENCE_DECISION,
            COMMAND_PROGRESS,
            DRONE_COMMAND_RECEIPT,
            DRONE_STREAM_STATE,
            DRONE_COMMAND_EFFECT,
            PENDING_INVOCATION,
            APPROVAL_BINDING,
        )

        # Act
        kinds = tuple(type(table) for table in application_tables)

        # Assert
        self.assertEqual((Table,) * len(application_tables), kinds)


class ApplicationTableShapeTests(unittest.TestCase):
    def test_each_new_table_has_exactly_the_migrated_columns(self) -> None:
        # Arrange
        expected = {
            BROKER_INBOX_TABLE: (
                "consumer",
                "source",
                "event_id",
                "mission_id",
                "canonical_digest",
                "result",
                "processed_at",
            ),
            BROKER_REFUSAL_TABLE: (
                "consumer",
                "source",
                "family",
                "channel",
                "refusal_code",
                "raw_digest",
                "observed_at",
            ),
            SOURCE_EVENT_TABLE: (
                "source",
                "event_id",
                "mission_id",
                "topic",
                "canonical_digest",
                "canonical_payload",
                "observed_at",
            ),
            SOURCE_EVIDENCE_ITEM_TABLE: (
                "source_event_source",
                "source_event_id",
                "ordinal",
                "evidence_item_id",
                "source_id",
                "origin",
                "provenance_digest",
                "document",
                "observed_at",
            ),
            APPLICATION_OUTBOX_TABLE: (
                "producer",
                "event_id",
                "family",
                "topic",
                "headers",
                "payload",
                "state",
                "traceparent",
                "tracestate",
                "correlation_id",
                "causation_id",
                "staged_at",
                "confirmed_at",
            ),
            PROPOSAL_TABLE: (
                "proposal_id",
                "mission_id",
                "source_event_id",
                "source_event_digest",
                "agent_name",
                "invocation_id",
                "proposal_type",
                "proposal_digest",
                "payload",
                "drone_id",
                "latitude_microdegrees",
                "longitude_microdegrees",
                "command_type",
                "issued_at",
                "sequence",
                "correlation_id",
                "causation_id",
                "traceparent",
            ),
            EVIDENCE_ITEM_TABLE: (
                "evidence_id",
                "mission_id",
                "proposal_id",
                "source_id",
                "source_kind",
                "lifecycle",
                "provenance_digest",
                "payload",
                "observed_at",
            ),
            EVIDENCE_DECISION_TABLE: (
                "decision_id",
                "mission_id",
                "proposal_id",
                "proposal_digest",
                "decision_digest",
                "decision_version",
                "score_version",
                "score",
                "band",
                "outcome",
                "contributors",
                "payload",
                "decided_at",
                "sequence",
            ),
            COMMAND_PROGRESS_TABLE: (
                "command_id",
                "mission_id",
                "drone_id",
                "state",
                "send_count",
                "last_sent_at",
                "deadline_at",
                "result_id",
                "updated_at",
            ),
            DRONE_COMMAND_RECEIPT_TABLE: (
                "drone_id",
                "command_id",
                "mission_id",
                "command_digest",
                "result",
                "applied_sequence",
                "processed_at",
            ),
        }

        # Act
        actual = {
            name: tuple(column.name for column in METADATA.tables[name].columns)
            for name in expected
        }

        # Assert
        self.assertEqual(expected, actual)

    def test_primary_keys_express_inbox_outbox_and_receipt_identity(self) -> None:
        # Arrange
        tables = (
            BROKER_INBOX,
            SOURCE_EVENT,
            SOURCE_EVIDENCE_ITEM,
            APPLICATION_OUTBOX,
            DRONE_COMMAND_RECEIPT,
        )

        # Act
        keys = tuple(tuple(column.name for column in table.primary_key.columns) for table in tables)

        # Assert
        self.assertEqual(
            (
                ("consumer", "source", "event_id"),
                ("source", "event_id"),
                ("source_event_source", "source_event_id", "ordinal"),
                ("producer", "event_id"),
                ("drone_id", "command_id"),
            ),
            keys,
        )

    def test_receipt_claim_fields_are_nullable_only_as_one_completion_group(self) -> None:
        # Arrange
        receipt = DRONE_COMMAND_RECEIPT

        # Act
        nullable = tuple(
            receipt.c[name].nullable for name in ("result", "applied_sequence", "processed_at")
        )
        constraints = tuple(
            str(constraint.sqltext)
            for constraint in receipt.constraints
            if hasattr(constraint, "sqltext")
        )

        # Assert
        self.assertEqual(
            ((True, True, True), True),
            (
                nullable,
                any(
                    "result IS NULL AND applied_sequence IS NULL AND processed_at IS NULL"
                    in constraint
                    for constraint in constraints
                ),
            ),
        )

    def test_binary_integer_and_text_types_are_explicit(self) -> None:
        # Arrange
        columns = (
            BROKER_INBOX.c.result,
            SOURCE_EVENT.c.canonical_payload,
            SOURCE_EVIDENCE_ITEM.c.document,
            PROPOSAL.c.sequence,
            EVIDENCE_DECISION.c.score,
            APPLICATION_OUTBOX.c.topic,
        )

        # Act
        kinds = tuple(type(column.type) for column in columns)

        # Assert
        self.assertEqual(
            (LargeBinary, LargeBinary, LargeBinary, BigInteger, SmallInteger, String), kinds
        )

    def test_source_event_digest_is_constrained_to_the_accepted_lowercase_sha256_form(self) -> None:
        # Arrange
        source_event = SOURCE_EVENT

        # Act
        constraints = tuple(
            str(constraint.sqltext)
            for constraint in source_event.constraints
            if hasattr(constraint, "sqltext")
        )

        # Assert
        self.assertIn("canonical_digest ~ '^[0-9a-f]{64}$'", constraints)

    def test_source_event_columns_have_exact_types_lengths_nullability_and_no_defaults(
        self,
    ) -> None:
        # Arrange
        expected = (
            ("source", "String", 256, False, None),
            ("event_id", "String", 64, False, None),
            ("mission_id", "String", 64, False, None),
            ("topic", "String", 250, False, None),
            ("canonical_digest", "String", 64, False, None),
            ("canonical_payload", "LargeBinary", None, False, None),
            ("observed_at", "String", 24, False, None),
        )

        # Act
        actual = tuple(
            (
                column.name,
                type(column.type).__name__,
                getattr(column.type, "length", None),
                column.nullable,
                column.server_default,
            )
            for column in SOURCE_EVENT.columns
        )

        # Assert
        self.assertEqual(expected, actual)

    def test_source_evidence_references_exact_source_identity_without_cascade(self) -> None:
        # Arrange
        table = SOURCE_EVIDENCE_ITEM

        # Act
        constraint = next(iter(table.foreign_key_constraints))
        references = tuple(element.target_fullname for element in constraint.elements)

        # Assert
        self.assertEqual(
            (("source_event.source", "source_event.event_id"), "RESTRICT"),
            (references, constraint.ondelete),
        )

    def test_evidence_rows_reference_proposals_without_cascade_deletion(self) -> None:
        # Arrange
        tables = (EVIDENCE_ITEM, EVIDENCE_DECISION)

        # Act
        references = tuple(
            tuple(
                (foreign_key.target_fullname, foreign_key.ondelete)
                for foreign_key in table.foreign_keys
            )
            for table in tables
        )

        # Assert
        self.assertEqual(
            (
                ((f"{PROPOSAL_TABLE}.proposal_id", "RESTRICT"),),
                ((f"{PROPOSAL_TABLE}.proposal_id", "RESTRICT"),),
            ),
            references,
        )

    def test_drain_and_ordering_indexes_are_named_and_complete(self) -> None:
        # Arrange
        tables = (BROKER_INBOX, SOURCE_EVENT, APPLICATION_OUTBOX, EVIDENCE_DECISION)

        # Act
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for table in tables
            for index in table.indexes
        }

        # Assert
        self.assertEqual(
            {
                "ix_broker_inbox_mission_processed": (
                    "mission_id",
                    "processed_at",
                    "consumer",
                    "source",
                    "event_id",
                ),
                "ix_source_event_mission_event": ("mission_id", "event_id", "source"),
                "ix_application_outbox_drain": (
                    "producer",
                    "state",
                    "staged_at",
                    "event_id",
                ),
                "ix_evidence_decision_proposal_sequence": (
                    "proposal_id",
                    "sequence",
                ),
            },
            indexes,
        )


if __name__ == "__main__":
    unittest.main()
