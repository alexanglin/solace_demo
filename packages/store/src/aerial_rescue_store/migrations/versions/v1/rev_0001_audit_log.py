"""Create the append-only audit log and the per-mission counter that orders it.

Revision: 0001_audit_log
Parent: None
Created: 2026-08-23

ADR-0088 (``docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md``)
decides both tables. ``audit_sequence`` exists to be locked: one row per mission, advanced by a
conditional upsert inside the transaction that writes the record, so ordinals are issued in commit
order and a rollback leaves no gap. ``audit_record`` is append-only and this member exposes no
update or delete for it.

Identifiers are bounded strings rather than ``uuid`` because a drone identifier such as ``drone-07``
is not one, and the store persists the exact accepted value. The instant is the canonical
millisecond spelling stored as text, and the payload is the canonical bytes, because ADR-0027 makes
both part of what a digest covers and a re-encoding through a native type would let the database
decide those bytes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001_audit_log"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
"""The identifier bound in ``docs/operating-parameters.md``, matching the topic grammar."""

KIND_LENGTH = 32
INSTANT_LENGTH = 24
"""``YYYY-MM-DDTHH:MM:SS.sssZ`` exactly, which ADR-0027 fixes."""

TRACEPARENT_LENGTH = 55
"""The W3C trace-context version 00 form."""


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "audit_sequence",
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("next_ordinal", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("mission_id", name="pk_audit_sequence"),
        sa.CheckConstraint("next_ordinal >= 1", name="ck_audit_sequence_ordinal_positive"),
    )
    op.create_table(
        "audit_record",
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("occurred_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("mission_id", "ordinal", name="pk_audit_record"),
        sa.CheckConstraint("ordinal >= 1", name="ck_audit_record_ordinal_positive"),
    )


def downgrade() -> None:
    """Reverse this revision exactly."""
    op.drop_table("audit_record")
    op.drop_table("audit_sequence")
