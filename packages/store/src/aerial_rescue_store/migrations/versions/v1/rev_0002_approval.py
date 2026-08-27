"""Create the durable approval decision record.

Revision: 0002_approval
Parent: 0001_audit_log
Created: 2026-08-24

ADR-0091 (``docs/adr/0091-consume-an-approval-under-its-own-row-lock.md``) decides this table and
the two statements that read and write it. The primary key is the proposal identifier because one
approval per proposal *is* the single-use property ADR-0006 requires, expressed where the database
can hold it rather than where a code path has to remember it.

The state column carries a check constraint over the six states of the approval protocol. It is a
projection of the domain's closed set for defence in depth, not a second transition table:
``packages/domain`` remains the only authority for which transition is legal, and this constraint
only refuses a spelling that is no state at all. The six values are written out here rather than
derived from the enum, because a revision is history and must keep saying what it said.

Identifiers are bounded strings for ADR-0088's reason, the instant is the canonical millisecond
spelling stored as text for ADR-0027's, and both clock readings are stored as they were read: the
wall reading as that text, and the monotonic reading as a duration in milliseconds. The monotonic
origin belongs to the process that issued the approval, which is why an approval cannot be consumed
after a gateway restart and must not be repaired into one that can.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_approval"
down_revision: str | None = "0001_audit_log"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
"""The identifier bound in ``docs/operating-parameters.md``, matching the topic grammar."""

STATE_LENGTH = 16
"""``superseded`` is the longest of the six protocol states."""

INSTANT_LENGTH = 24
"""``YYYY-MM-DDTHH:MM:SS.sssZ`` exactly, which ADR-0027 fixes."""

DIGEST_LENGTH = 64
"""SHA-256 as lowercase hexadecimal, which ADR-0027 fixes."""

PROTOCOL_STATES = ("requested", "approved", "rejected", "expired", "superseded", "executed")


def upgrade() -> None:
    """Apply this revision."""
    states = ", ".join(f"'{state}'" for state in PROTOCOL_STATES)
    op.create_table(
        "approval",
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("proposal_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("operator_identity", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("issued_wall", sa.String(INSTANT_LENGTH), nullable=False),
        sa.Column("issued_monotonic_milliseconds", sa.BigInteger(), nullable=False),
        sa.Column("time_to_live_milliseconds", sa.BigInteger(), nullable=False),
        sa.Column("proposal_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("proposal_id", name="pk_approval"),
        sa.CheckConstraint(f"state IN ({states})", name="ck_approval_state_in_protocol"),
        sa.CheckConstraint(
            "time_to_live_milliseconds > 0", name="ck_approval_time_to_live_positive"
        ),
    )


def downgrade() -> None:
    """Reverse this revision exactly."""
    op.drop_table("approval")
