# ADR-0003: Postgres in Docker as the durable mission store

- **Status:** Accepted
- **Date:** 2026-08-18
- **Supersedes:** the local SQLite-in-WAL-mode store described in the implementation plan

## Context

The original plan named no datastore anywhere. That made the safety architecture undemonstrable: approvals could not survive a process restart, the idempotency index backing "command handlers return the prior result for a known command ID" was implicitly in-process, and the audit timeline had no ordering authority. A subsequent revision introduced local SQLite in WAL mode.

Docker is already a hard prerequisite for the local PubSub+ test broker, so a database container introduces no new class of dependency.

## Decision

Use PostgreSQL, run as a Docker Compose service, as the authoritative durable store for mission state, inbox and outbox records, proposals, approvals, idempotency results, evidence provenance, and audit records.

Access it through async SQLAlchemy 2.x with `asyncpg`, and manage schema with Alembic migrations. Broker acknowledgement occurs only after the related durable transaction commits. An append-only audit table with a monotonic ordinal is the ordering authority for the mission timeline; per-producer sequence numbers are scoped to their source and must not be used to order it.

## Consequences

- Approvals, the idempotency index, and audit order survive a process restart, which is what makes the RPO-0 target and the approval-bypass gate demonstrable.
- The store matches production practice, and concurrent access from multiple local processes is well defined rather than dependent on SQLite's locking behaviour.
- **The clean-checkout path gains a container, a connection dependency, and a migration step.** First-run setup is slower and has one more failure mode than a single file would.
- Deterministic test isolation now requires a per-run database or transactional rollback strategy, and `POST /api/v1/scenarios/current/reset` must define its delete scope in SQL rather than by deleting a file.
- The recorder exports replay fixtures from the audit table, so fixtures and the audit authority cannot diverge.

## Alternatives considered

- **SQLite in WAL mode.** Rejected by decision, though it remains technically defensible: zero extra services, trivially resettable, and a natural fit for a replay-first reference implementation. Its weaknesses are multi-process write concurrency and a lower-fidelity match to production deployments.
- **An append-only NDJSON log as the sole system of record.** Rejected: point lookups for the idempotency index and approval state would need a separate in-memory index rebuilt on every start, and restart cost grows with mission length.
- **No datastore, in-memory only.** Rejected: makes the approval-bypass release gate impossible to demonstrate across a restart.
