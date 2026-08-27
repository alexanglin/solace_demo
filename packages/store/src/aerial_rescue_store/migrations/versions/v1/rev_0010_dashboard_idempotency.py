"""Close generic durable idempotency over dashboard command and decision mutations.

Revision: 0010_dashboard_idempotency
Parent: 0009_broker_refusal
Created: 2026-08-26

ADR-0189 keeps scenario start and reset in ``dashboard_operation`` while giving operator command
submission and proposal decision their own generic durable kinds. The table and its rows remain
in place: this revision replaces only the defence-in-depth check constraint. History keeps the
original revision 0003 unchanged.
"""

from __future__ import annotations

from alembic import op

revision: str = "0010_dashboard_idempotency"
down_revision: str | None = "0009_broker_refusal"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "idempotency_claim"
CONSTRAINT = "ck_idempotency_claim_kind"
ESTABLISHED_KINDS = ("command", "approval consumption")
DASHBOARD_KINDS = (
    "dashboard command",
    "dashboard decision",
)


def _constraint(kinds: tuple[str, ...]) -> str:
    """Render the immutable closed vocabulary for one migration direction."""
    values = ", ".join(f"'{kind}'" for kind in kinds)
    return f"kind IN ({values})"


def upgrade() -> None:
    """Admit the two repeatable generic dashboard operations without rewriting rows."""
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        _constraint((*ESTABLISHED_KINDS, *DASHBOARD_KINDS)),
    )


def downgrade() -> None:
    """Restore the exact pre-dashboard closed set, failing if incompatible rows remain."""
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _constraint(ESTABLISHED_KINDS))
