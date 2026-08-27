"""Create pending-invocation and exact approval-binding authority.

Revision: 0007_command_gateway_authority
Parent: 0006_durable_fleet_processing
Created: 2026-08-25

ADR-0146 requires trusted invocation context before Agent Response normalization and binds an
operator decision to the exact proposal, evidence decision, and action. ADR-0151 requires both
records and every supporting constraint to enter only through Alembic.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_command_gateway_authority"
down_revision: str | None = "0006_durable_fleet_processing"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
AGENT_NAME_LENGTH = 64
DIGEST_LENGTH = 64
INSTANT_LENGTH = 24


def upgrade() -> None:
    """Create both immutable command-gateway authority records."""
    op.create_table(
        "pending_invocation",
        sa.Column("invocation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("agent_name", sa.String(AGENT_NAME_LENGTH), nullable=False),
        sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_event_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source_event_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("invocation_id", name="pk_pending_invocation"),
        sa.CheckConstraint(
            "source_event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_pending_invocation_source_digest",
        ),
    )
    op.create_table(
        "operator_decision_binding",
        sa.Column("approval_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_version", sa.SmallInteger(), nullable=False),
        sa.Column("evidence_decision_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("evidence_decision_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("evidence_decision_version", sa.SmallInteger(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("action_payload", sa.LargeBinary(), nullable=False),
        sa.Column("decision_runtime_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("authority_runtime_epoch", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.Column("authority_issued_monotonic_milliseconds", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.String(INSTANT_LENGTH), nullable=True),
        sa.PrimaryKeyConstraint("approval_id", name="pk_approval_binding"),
        sa.UniqueConstraint("proposal_id", name="uq_approval_binding_proposal"),
        sa.ForeignKeyConstraint(
            ("proposal_id",),
            ("approval.proposal_id",),
            name="fk_approval_binding_approval",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("proposal_id",),
            ("proposal.proposal_id",),
            name="fk_approval_binding_proposal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("evidence_decision_id",),
            ("evidence_decision.decision_id",),
            name="fk_approval_binding_evidence_decision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "proposal_version > 0 AND evidence_decision_version > 0",
            name="ck_approval_binding_versions",
        ),
        sa.CheckConstraint(
            "evidence_decision_digest ~ '^[0-9a-f]{64}$'",
            name="ck_approval_binding_evidence_digest",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="ck_approval_binding_decision",
        ),
        sa.CheckConstraint(
            "(decision = 'approve' AND expires_at IS NOT NULL) OR "
            "(decision = 'reject' AND expires_at IS NULL)",
            name="ck_approval_binding_expiry",
        ),
        sa.CheckConstraint(
            "octet_length(action_payload) > 0",
            name="ck_approval_binding_action_payload",
        ),
        sa.CheckConstraint(
            "(authority_runtime_epoch IS NULL AND "
            "authority_issued_monotonic_milliseconds IS NULL) OR "
            "(authority_runtime_epoch IS NOT NULL AND "
            "authority_issued_monotonic_milliseconds IS NOT NULL)",
            name="ck_approval_binding_authority_pair",
        ),
    )


def downgrade() -> None:
    """Remove only the two authority tables owned by this revision."""
    op.drop_table("operator_decision_binding")
    op.drop_table("pending_invocation")
