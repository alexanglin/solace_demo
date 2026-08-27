"""Close durable idempotency over all public dashboard mutations.

Revision: 0009_dashboard_idempotency
Parent: 0008_broker_refusal
Created: 2026-08-26

ADR-0171 gives scenario start, scenario reset, operator command submission, and proposal
decision their own durable kinds. The table and its rows remain in place: this revision replaces
only the defence-in-depth check constraint. History keeps the original revision 0003 unchanged.
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_dashboard_idempotency"
down_revision: str | None = "0008_broker_refusal"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "idempotency_claim"
CONSTRAINT = "ck_idempotency_claim_kind"
ESTABLISHED_KINDS = ("command", "approval consumption")
DASHBOARD_KINDS = (
    "dashboard start",
    "dashboard reset",
    "dashboard command",
    "dashboard decision",
)


def _constraint(kinds: tuple[str, ...]) -> str:
    """Render the immutable closed vocabulary for one migration direction."""
    values = ", ".join(f"'{kind}'" for kind in kinds)
    return f"kind IN ({values})"


def upgrade() -> None:
    """Admit the four repeatable public dashboard operations without rewriting rows."""
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
