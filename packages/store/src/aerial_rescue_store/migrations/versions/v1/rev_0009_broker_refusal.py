"""Create bounded durable malformed-Guaranteed-ingress evidence.

Revision: 0009_broker_refusal
Parent: 0008_command_gateway_authority
Created: 2026-08-25

ADR-0146 requires a permanent-message refusal to become durable before settlement. ADR-0159
strengthens this into a bounded fact followed by ``REJECTED`` settlement to the source queue's
isolated DMQ. The raw body is represented only by its lowercase SHA-256 digest and is never stored.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_broker_refusal"
down_revision: str | None = "0008_command_gateway_authority"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
SOURCE_LENGTH = 256
KIND_LENGTH = 32
TOPIC_LENGTH = 250
DIGEST_LENGTH = 64
INSTANT_LENGTH = 24


def upgrade() -> None:
    """Create the immutable, body-free broker refusal ledger."""
    op.create_table(
        "broker_refusal",
        sa.Column("consumer", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("source", sa.String(SOURCE_LENGTH), nullable=True),
        sa.Column("family", sa.String(KIND_LENGTH), nullable=True),
        sa.Column("channel", sa.String(TOPIC_LENGTH), nullable=False),
        sa.Column("refusal_code", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("raw_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("observed_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint(
            "consumer",
            "channel",
            "raw_digest",
            name="pk_broker_refusal",
        ),
        sa.CheckConstraint(
            "octet_length(consumer) > 0",
            name="ck_broker_refusal_consumer",
        ),
        sa.CheckConstraint(
            "source IS NULL OR octet_length(source) > 0",
            name="ck_broker_refusal_source",
        ),
        sa.CheckConstraint(
            "family IS NULL OR octet_length(family) > 0",
            name="ck_broker_refusal_family",
        ),
        sa.CheckConstraint(
            "octet_length(channel) > 0",
            name="ck_broker_refusal_channel",
        ),
        sa.CheckConstraint(
            "octet_length(refusal_code) > 0",
            name="ck_broker_refusal_code",
        ),
        sa.CheckConstraint(
            "raw_digest ~ '^[0-9a-f]{64}$'",
            name="ck_broker_refusal_digest",
        ),
    )
    op.create_index(
        "ix_broker_refusal_observed",
        "broker_refusal",
        ("consumer", "observed_at", "channel", "raw_digest"),
        unique=False,
    )


def downgrade() -> None:
    """Remove only the refusal ledger owned by this revision."""
    op.drop_index("ix_broker_refusal_observed", table_name="broker_refusal")
    op.drop_table("broker_refusal")
