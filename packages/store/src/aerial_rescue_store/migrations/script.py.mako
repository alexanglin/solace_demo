"""${message}.

Revision: ${up_revision}
Parent: ${down_revision | comma,n}
Created: ${create_date}
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    """Apply this revision."""
    ${upgrades if upgrades else "raise NotImplementedError"}


def downgrade() -> None:
    """Reverse this revision exactly."""
    ${downgrades if downgrades else "raise NotImplementedError"}
