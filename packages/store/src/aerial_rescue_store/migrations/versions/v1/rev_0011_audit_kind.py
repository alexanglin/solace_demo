"""Widen the audit record's kind so an event type fits.

Revision: 0011_audit_kind
Parent: 0010_dashboard_idempotency
Created: 2026-08-28

ADR-0193: the dashboard binds every audit record to its event by ``kind == type``, and a CloudEvent
type runs to 70 characters under the contracts' bounds, while revision 0001 sized the column for one
32-character KIND level. This revision changes only that column's width. History keeps revision
0001 unchanged; the downgrade restores exactly 32 and fails if a longer kind is stored.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011_audit_kind"
down_revision: str | None = "0010_dashboard_idempotency"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "audit_record"
COLUMN = "kind"
KIND_LENGTH = 32
EVENT_TYPE_LENGTH = 96


def upgrade() -> None:
    """Widen the kind column to hold any event type the contracts render."""
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(KIND_LENGTH),
        type_=sa.String(EVENT_TYPE_LENGTH),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Restore the one-level KIND width, failing if a wider kind is stored."""
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=sa.String(EVENT_TYPE_LENGTH),
        type_=sa.String(KIND_LENGTH),
        existing_nullable=False,
    )
