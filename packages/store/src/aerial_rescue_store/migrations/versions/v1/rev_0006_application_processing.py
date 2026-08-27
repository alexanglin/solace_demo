"""Create durable application inbox, source, outbox, proposal, evidence, and command facts.

Revision: 0006_application_processing
Parent: 0005_dashboard_runtime
Created: 2026-08-25

ADR-0146 and ADR-0152 make PostgreSQL authoritative for these nine tables, and ADR-0151
requires every table, constraint, foreign key, and index to enter only through an append-only
Alembic revision.  Canonical payloads and headers remain bytes so the database never re-encodes
a digest-bearing value.  Domain state checks are defence in depth; domain code still owns
every legal transition.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_application_processing"
down_revision: str | None = "0005_dashboard_runtime"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
KIND_LENGTH = 32
SOURCE_LENGTH = 256
TOPIC_LENGTH = 250
INSTANT_LENGTH = 24
DIGEST_LENGTH = 64
TRACEPARENT_LENGTH = 55
TRACESTATE_LENGTH = 512
STATE_LENGTH = 24
AGENT_NAME_LENGTH = 64

PUBLICATION_STATES = ("staged", "reconciliation needed", "confirmed")
EVIDENCE_STATES = (
    "requested",
    "observed",
    "validated",
    "manual-review",
    "contributing",
    "abstained",
    "rejected",
)
EVIDENCE_ORIGINS = ("live-model", "live-sensor", "recorded")
EVIDENCE_OUTCOMES = ("contributing", "manual-review", "abstained", "rejected")
EVIDENCE_BANDS = ("none", "weak", "supported", "corroborated")
COMMAND_STATES = (
    "accepted",
    "in-flight",
    "acknowledged",
    "succeeded",
    "failed",
    "abandoned",
)


def _values(values: tuple[str, ...]) -> str:
    """Return immutable revision-local values for one check constraint."""
    return ", ".join(f"'{value}'" for value in values)


def _create_broker_inbox() -> None:
    """Create durable message identity and processing outcome storage."""
    op.create_table(
        "broker_inbox",
        sa.Column("consumer", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source", sa.String(SOURCE_LENGTH), nullable=False),
        sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("canonical_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("result", sa.LargeBinary(), nullable=True),
        sa.Column("processed_at", sa.String(INSTANT_LENGTH), nullable=True),
        sa.PrimaryKeyConstraint("consumer", "source", "event_id", name="pk_broker_inbox"),
        sa.CheckConstraint(
            "(result IS NULL AND processed_at IS NULL) OR "
            "(result IS NOT NULL AND processed_at IS NOT NULL)",
            name="ck_broker_inbox_completion",
        ),
    )
    op.create_index(
        "ix_broker_inbox_mission_processed",
        "broker_inbox",
        ("mission_id", "processed_at", "consumer", "source", "event_id"),
        unique=False,
    )


def _create_source_event() -> None:
    """Create the immutable complete CloudEvent facts used to recompute provenance."""
    op.create_table(
        "source_event",
        sa.Column("source", sa.String(SOURCE_LENGTH), nullable=False),
        sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("topic", sa.String(TOPIC_LENGTH), nullable=False),
        sa.Column("canonical_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("canonical_payload", sa.LargeBinary(), nullable=False),
        sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("source", "event_id", name="pk_source_event"),
        sa.CheckConstraint(
            "canonical_digest ~ '^[0-9a-f]{64}$'",
            name="ck_source_event_digest",
        ),
    )
    op.create_index(
        "ix_source_event_mission_event",
        "source_event",
        ("mission_id", "event_id", "source"),
        unique=False,
    )


def _create_source_evidence() -> None:
    """Create immutable digest-covered provenance facts under exact source identity."""
    op.create_table(
        "source_evidence_item",
        sa.Column("source_event_source", sa.String(SOURCE_LENGTH), nullable=False),
        sa.Column("source_event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("evidence_item_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("origin", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("provenance_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("document", sa.LargeBinary(), nullable=False),
        sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_event_source",
            "source_event_id",
            "ordinal",
            name="pk_source_evidence_item",
        ),
        sa.UniqueConstraint(
            "source_event_source",
            "source_event_id",
            "evidence_item_id",
            name="uq_source_evidence_item_identity",
        ),
        sa.ForeignKeyConstraint(
            ("source_event_source", "source_event_id"),
            ("source_event.source", "source_event.event_id"),
            name="fk_source_evidence_item_source_event",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 23",
            name="ck_source_evidence_item_ordinal",
        ),
        sa.CheckConstraint(
            f"origin IN ({_values(EVIDENCE_ORIGINS)})",
            name="ck_source_evidence_item_origin",
        ),
        sa.CheckConstraint(
            "provenance_digest ~ '^[0-9a-f]{64}$'",
            name="ck_source_evidence_item_digest",
        ),
    )


def _create_application_outbox() -> None:
    """Create exact staged publications and their confirmation state."""
    op.create_table(
        "application_outbox",
        sa.Column("producer", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("family", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("topic", sa.String(TOPIC_LENGTH), nullable=False),
        sa.Column("headers", sa.LargeBinary(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
        sa.Column("tracestate", sa.String(TRACESTATE_LENGTH), nullable=True),
        sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.Column("staged_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.Column("confirmed_at", sa.String(INSTANT_LENGTH), nullable=True),
        sa.PrimaryKeyConstraint("producer", "event_id", name="pk_application_outbox"),
        sa.CheckConstraint(
            f"state IN ({_values(PUBLICATION_STATES)})",
            name="ck_application_outbox_state",
        ),
        sa.CheckConstraint(
            "(state = 'confirmed' AND confirmed_at IS NOT NULL) OR "
            "(state <> 'confirmed' AND confirmed_at IS NULL)",
            name="ck_application_outbox_confirmation",
        ),
    )
    op.create_index(
        "ix_application_outbox_drain",
        "application_outbox",
        ("producer", "state", "staged_at", "event_id"),
        unique=False,
    )


def _create_proposal() -> None:
    """Create immutable normalized proposals and their exact canonical bytes."""
    op.create_table(
        "proposal",
        sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_event_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("agent_name", sa.String(AGENT_NAME_LENGTH), nullable=False),
        sa.Column("invocation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_type", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("proposal_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("latitude_microdegrees", sa.BigInteger(), nullable=False),
        sa.Column("longitude_microdegrees", sa.BigInteger(), nullable=False),
        sa.Column("command_type", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("issued_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("proposal_id", name="pk_proposal"),
        sa.UniqueConstraint("proposal_digest", name="uq_proposal_digest"),
        sa.CheckConstraint("sequence >= 0", name="ck_proposal_sequence_nonnegative"),
        sa.CheckConstraint(
            "latitude_microdegrees BETWEEN -90000000 AND 90000000",
            name="ck_proposal_latitude",
        ),
        sa.CheckConstraint(
            "longitude_microdegrees BETWEEN -180000000 AND 180000000",
            name="ck_proposal_longitude",
        ),
    )


def _create_evidence() -> None:
    """Create evidence provenance and append-only proposal decisions."""
    op.create_table(
        "evidence_item",
        sa.Column("evidence_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_kind", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("lifecycle", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("provenance_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence_item"),
        sa.ForeignKeyConstraint(
            ("proposal_id",),
            ("proposal.proposal_id",),
            name="fk_evidence_item_proposal",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({_values(EVIDENCE_ORIGINS)})",
            name="ck_evidence_item_source_kind",
        ),
        sa.CheckConstraint(
            f"lifecycle IN ({_values(EVIDENCE_STATES)})",
            name="ck_evidence_item_lifecycle",
        ),
    )
    op.create_table(
        "evidence_decision",
        sa.Column("decision_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("decision_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("decision_version", sa.SmallInteger(), nullable=False),
        sa.Column("score_version", sa.SmallInteger(), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("band", sa.String(STATE_LENGTH), nullable=True),
        sa.Column("outcome", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("contributors", sa.LargeBinary(), nullable=True),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("decided_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("decision_id", name="pk_evidence_decision"),
        sa.ForeignKeyConstraint(
            ("proposal_id",),
            ("proposal.proposal_id",),
            name="fk_evidence_decision_proposal",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("decision_digest", name="uq_evidence_decision_digest"),
        sa.UniqueConstraint(
            "proposal_id", "sequence", name="uq_evidence_decision_proposal_sequence"
        ),
        sa.CheckConstraint(
            f"outcome IN ({_values(EVIDENCE_OUTCOMES)})",
            name="ck_evidence_decision_outcome",
        ),
        sa.CheckConstraint(
            f"band IS NULL OR band IN ({_values(EVIDENCE_BANDS)})",
            name="ck_evidence_decision_band",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_evidence_decision_score",
        ),
        sa.CheckConstraint(
            "decision_version > 0 AND (score_version IS NULL OR score_version > 0)",
            name="ck_evidence_decision_versions",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_evidence_decision_sequence"),
        sa.CheckConstraint(
            "(outcome = 'contributing' AND score_version IS NOT NULL AND score IS NOT NULL "
            "AND band IS NOT NULL AND contributors IS NOT NULL) OR "
            "(outcome <> 'contributing' AND score_version IS NULL AND score IS NULL "
            "AND band IS NULL AND contributors IS NULL)",
            name="ck_evidence_decision_branch",
        ),
    )
    op.create_index(
        "ix_evidence_decision_proposal_sequence",
        "evidence_decision",
        ("proposal_id", "sequence"),
        unique=False,
    )


def _create_command_facts() -> None:
    """Create dispatch progress and durable per-drone exactly-once receipts."""
    op.create_table(
        "command_progress",
        sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("send_count", sa.SmallInteger(), nullable=False),
        sa.Column("last_sent_at", sa.String(INSTANT_LENGTH), nullable=True),
        sa.Column("deadline_at", sa.String(INSTANT_LENGTH), nullable=True),
        sa.Column("result_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.Column("updated_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("command_id", name="pk_command_progress"),
        sa.CheckConstraint(
            f"state IN ({_values(COMMAND_STATES)})", name="ck_command_progress_state"
        ),
        sa.CheckConstraint(
            "send_count >= 0 AND send_count <= 5", name="ck_command_progress_send_count"
        ),
    )
    op.create_table(
        "drone_command_receipt",
        sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("command_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("result", sa.LargeBinary(), nullable=True),
        sa.Column("applied_sequence", sa.BigInteger(), nullable=True),
        sa.Column("processed_at", sa.String(INSTANT_LENGTH), nullable=True),
        sa.PrimaryKeyConstraint("drone_id", "command_id", name="pk_drone_command_receipt"),
        sa.CheckConstraint(
            "applied_sequence IS NULL OR applied_sequence >= 0",
            name="ck_drone_command_receipt_sequence",
        ),
        sa.CheckConstraint(
            "(result IS NULL AND applied_sequence IS NULL AND processed_at IS NULL) OR "
            "(result IS NOT NULL AND applied_sequence IS NOT NULL AND processed_at IS NOT NULL)",
            name="ck_drone_command_receipt_completion",
        ),
    )


def upgrade() -> None:
    """Apply this additive revision in dependency order."""
    _create_broker_inbox()
    _create_source_event()
    _create_source_evidence()
    _create_application_outbox()
    _create_proposal()
    _create_evidence()
    _create_command_facts()


def downgrade() -> None:
    """Drop only this revision's objects, in reverse dependency order."""
    op.drop_table("drone_command_receipt")
    op.drop_table("command_progress")
    op.drop_table("evidence_decision")
    op.drop_table("evidence_item")
    op.drop_table("proposal")
    op.drop_table("application_outbox")
    op.drop_table("source_evidence_item")
    op.drop_table("source_event")
    op.drop_table("broker_inbox")
