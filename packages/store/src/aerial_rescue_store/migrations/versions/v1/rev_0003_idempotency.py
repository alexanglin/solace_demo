"""Create the durable idempotency claim.

Revision: 0003_idempotency
Parent: 0002_approval
Created: 2026-08-24

ADR-0092 (``docs/adr/0092-claim-an-idempotency-key-with-one-conflicting-insert.md``) decides this
table. The primary key is the idempotency key because the conflict on that key *is* the claim: one
insert either takes it or does nothing, so there is no read-then-write for a concurrent caller to
slip through.

``result`` is nullable on purpose, and the null is a state rather than an absence. A claim taken and
not yet answered is a command still in flight, which ADR-0092 makes a refusal rather than a prior
result -- answering with nothing would present "I do not know yet" as "here is the answer".

``body_digest`` holds the hash the caller computed under the idempotency-body context, so a key
replayed with different content is refused rather than answered from another request's record. It is
compared here and never recomputed; ``packages/contracts`` owns canonical bytes.

The kind check constraint projects the domain's closed set for defence in depth. The two values are
written out rather than derived, because a revision is history and must keep saying what it said.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_idempotency"
down_revision: str | None = "0002_approval"
branch_labels: str | None = None
depends_on: str | None = None

IDENTIFIER_LENGTH = 64
"""The identifier bound in ``docs/operating-parameters.md``, matching the topic grammar."""

KIND_LENGTH = 24
"""``approval consumption`` is the longer of the two kinds."""

DIGEST_LENGTH = 64
"""SHA-256 as lowercase hexadecimal, which ADR-0027 fixes."""

INSTANT_LENGTH = 24
"""``YYYY-MM-DDTHH:MM:SS.sssZ`` exactly, which ADR-0027 fixes."""

CLAIM_KINDS = ("command", "approval consumption")


def upgrade() -> None:
    """Apply this revision."""
    kinds = ", ".join(f"'{kind}'" for kind in CLAIM_KINDS)
    op.create_table(
        "idempotency_claim",
        sa.Column("idempotency_key", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("kind", sa.String(KIND_LENGTH), nullable=False),
        sa.Column("body_digest", sa.String(DIGEST_LENGTH), nullable=False),
        sa.Column("mission_id", sa.String(IDENTIFIER_LENGTH), nullable=False),
        sa.Column("result", sa.LargeBinary(), nullable=True),
        sa.Column("claimed_at", sa.String(INSTANT_LENGTH), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key", name="pk_idempotency_claim"),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_idempotency_claim_kind"),
    )


def downgrade() -> None:
    """Reverse this revision exactly."""
    op.drop_table("idempotency_claim")
