"""Durable mission store, migrations, and repository adapters.

``docs/adr/0003-postgres-durable-mission-store.md`` makes PostgreSQL the authoritative store
for mission state, inbox and outbox records, proposals, approvals, idempotency results,
evidence provenance, and audit records, reached through async SQLAlchemy with ``asyncpg`` and
versioned by Alembic. This package owns durable repository and transaction adapters and
nothing else: the rules attached to those records stay in ``aerial_rescue_domain``, the bytes
and digests stay in ``aerial_rescue_contracts``, and the use cases stay in the services.

Every engine, session factory, resolved setting, and timeout is injected. Nothing here
connects, reads an environment variable, runs a migration, or starts a task at import.

Refusals are :class:`aerial_rescue_domain.DomainError` subclasses, which is the shape that
package documents as existing so the command gateway can audit every denied attempt through a
single handler.
"""

from __future__ import annotations

from aerial_rescue_domain import DomainError


class StoreError(DomainError):
    """A value the durable store refuses, carrying the refusal as structured data."""
