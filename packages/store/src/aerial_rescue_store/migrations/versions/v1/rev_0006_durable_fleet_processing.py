"""Create durable simulated-drone stream and command-effect authority.

Revision: 0006_durable_fleet_processing
Parent: 0005_application_processing
Created: 2026-08-25

ADR-0146 requires each simulated drone's sequence high-water and command effect to share
the receipt and exact critical-result transaction. ADR-0151 requires the complete schema to
enter only through Alembic and be represented by package-owned SQLAlchemy metadata.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_durable_fleet_processing"
down_revision: str | None = "0005_application_processing"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
SOURCE_LENGTH = 256
DIGEST_LENGTH = 64
STATE_LENGTH = 24
INSTANT_LENGTH = 24
MAXIMUM_PRODUCER_SEQUENCE = 999_999_999_999_999


def _create_stream_state() -> None:
    """Create one producer identity and nullable pre-first-event high-water per drone."""
    op.create_table(
        "drone_stream_state",
        sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("producer", sa.String(SOURCE_LENGTH), nullable=False),
        sa.Column("high_water", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("drone_id", name="pk_drone_stream_state"),
        sa.UniqueConstraint("producer", name="uq_drone_stream_state_producer"),
        sa.CheckConstraint(
            f"high_water IS NULL OR high_water BETWEEN 0 AND {MAXIMUM_PRODUCER_SEQUENCE}",
            name="ck_drone_stream_state_high_water",
        ),
    )


def _create_command_effect() -> None:
    """Create immutable command effects bound to an already claimed receipt."""
    op.create_table(
        "drone_command_effect",
        sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("command_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("outcome", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("effect_payload", sa.LargeBinary(), nullable=False),
        sa.Column("applied_sequence", sa.BigInteger(), nullable=False),
        sa.Column("applied_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("drone_id", "command_id", name="pk_drone_command_effect"),
        sa.UniqueConstraint(
            "drone_id",
            "applied_sequence",
            name="uq_drone_command_effect_applied_sequence",
        ),
        sa.ForeignKeyConstraint(
            ("drone_id", "command_id"),
            ("drone_command_receipt.drone_id", "drone_command_receipt.command_id"),
            name="fk_drone_command_effect_receipt",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "command_digest ~ '^[0-9a-f]{64}$'",
            name="ck_drone_command_effect_digest",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed')",
            name="ck_drone_command_effect_outcome",
        ),
        sa.CheckConstraint(
            "octet_length(effect_payload) > 0",
            name="ck_drone_command_effect_payload",
        ),
        sa.CheckConstraint(
            "applied_sequence >= 0",
            name="ck_drone_command_effect_sequence",
        ),
    )


def upgrade() -> None:
    """Add fleet durability after its receipt and application outbox dependencies."""
    _create_stream_state()
    _create_command_effect()


def downgrade() -> None:
    """Drop only the fleet objects introduced by this revision."""
    op.drop_table("drone_command_effect")
    op.drop_table("drone_stream_state")
