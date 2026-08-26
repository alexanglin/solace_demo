"""Create the central command outbox.

Revision: 0004_command_outbox
Parent: 0003_idempotency
Created: 2026-08-24

ADR-0093 (``docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md``) decides this table
and the three states of the publication lifecycle above it. The primary key is the command
identifier, because a retry re-publishes the record it already has rather than staging a second
one -- which is also why the bound counts records rather than sends.

The state check constraint projects the domain's closed set for defence in depth. The three values
are written out rather than derived, because a revision is history and must keep saying what it
said. The domain remains the only authority for which transition is legal; this constraint only
refuses a spelling that is no state at all.

The payload is the canonical bytes of the command envelope and the instant its canonical
millisecond spelling, both for ADR-0027's reason. The correlation, causation, and trace-parent
values are carried so a reader can reach the request that produced the command without joining
back through the audit log.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_command_outbox"
down_revision: str | None = "0003_idempotency"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
"""The identifier bound in ``docs/operating-parameters.md``, matching the topic grammar."""

STATE_LENGTH = 24
"""``reconciliation needed`` is the longest of the three states."""

INSTANT_LENGTH = 24
"""``YYYY-MM-DDTHH:MM:SS.sssZ`` exactly, which ADR-0027 fixes."""

TRACEPARENT_LENGTH = 55
"""The W3C trace-context version 00 form."""

PUBLICATION_STATES = ("staged", "reconciliation needed", "confirmed")


def upgrade() -> None:
    """Apply this revision."""
    states = ", ".join(f"'{state}'" for state in PUBLICATION_STATES)
    op.create_table(
        "command_outbox",
        sa.Column("command_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("drone_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.String(STATE_LENGTH), nullable=False),
        sa.Column("correlation_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("causation_id", sa.String(IDENTIFIER_LENGTH), nullable=True),
        sa.Column("traceparent", sa.String(TRACEPARENT_LENGTH), nullable=False),
        sa.Column("staged_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("command_id", name="pk_command_outbox"),
        sa.CheckConstraint(f"state IN ({states})", name="ck_command_outbox_state"),
    )


def downgrade() -> None:
    """Reverse this revision exactly."""
    op.drop_table("command_outbox")
