# ADR-0119: Require migrated SQLAlchemy durable tables

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none; strengthens the persistence mechanism selected by ADR-0003 and ADR-0114

## Context

[ADR-0003](0003-postgres-durable-mission-store.md) selects PostgreSQL, async SQLAlchemy 2.x,
`asyncpg`, and Alembic. [ADR-0114](0114-define-durable-application-processing.md) then names the durable
broker inbox, application outbox, proposal, evidence, command-progress, and per-drone receipt facts the
application data plane needs. Neither record says whether every physical table, constraint, index, and
sequence must enter through a complete migration, whether a service may create missing schema at startup,
or whether a repository may bypass SQLAlchemy and call the driver directly.

That omission permits two incompatible implementations to appear compliant. One can put only the main
tables in Alembic and create supporting objects at runtime; another can use SQLAlchemy for connection
management while issuing driver SQL for the actual repositories. Either path makes an upgrade from an
existing revision differ from a clean install, leaves rollback behavior undefined, and weakens the single
typed persistence boundary that ADR-0003 intended.

The existing store uses hand-written append-only Alembic revisions and SQLAlchemy Core expressions. The
stronger rule can preserve that explicit style while making its completeness testable. A declarative ORM
is not required to obtain SQLAlchemy's typed statements and transaction behavior, and ORM callbacks must
not become a second home for domain transitions.

## Decision

Every project-owned durable PostgreSQL schema object is introduced, changed, and removed only by an
append-only Alembic revision under `packages/store/src/aerial_rescue_store/migrations/`. This includes
tables, columns, sequences, primary and foreign keys, unique and check constraints, indexes, server
defaults, and enum or other database types. A service never runs `create_all`, emits startup DDL, patches
the schema opportunistically, or treats a repository import as schema installation.

Every application read and write of those objects goes through a purpose-specific, typed, asynchronous
SQLAlchemy 2.x Core repository in `packages/store`. Repository statements use package-owned `Table`
metadata and execute through the injected `AsyncSession` or `AsyncConnection`. `asyncpg` remains the
SQLAlchemy dialect and is not imported or called by application code. Raw SQL is permitted only inside an
Alembic revision when SQLAlchemy or Alembic cannot express the required PostgreSQL object; such use must be
local to that revision and covered by upgrade and downgrade evidence. Application repositories may not use
raw SQL strings as an alternate data path.

Migration revisions are immutable after merge and form one linear history. Each new revision must prove:

1. upgrade from its immediate predecessor and from an empty database to `head`;
2. downgrade back to its immediate predecessor without touching an earlier revision's objects;
3. the exact columns, types, nullability, defaults, keys, constraints, indexes, and sequences it owns;
4. that the SQLAlchemy table metadata used by repositories agrees with the migrated PostgreSQL schema;
5. rollback of every multi-table repository transaction on an injected failure; and
6. restart recovery, concurrency, and ordering behavior on a PostgreSQL database created and dropped by
   the authorized live migration test.

Offline Alembic rendering and deterministic repository tests remain required, but they do not substitute
for the PostgreSQL migration and transaction probe. Applying revisions to the operator's persistent
database remains a separately authorized deployment step and never permits volume deletion as a shortcut.

## Consequences

- A clean installation and every supported upgrade path produce the same schema, including the indexes and
  constraints that carry concurrency and integrity assumptions.
- All durable access stays inside one typed SQLAlchemy boundary, while domain transitions remain in the
  domain package rather than moving into ORM events or database triggers.
- Migration completeness and repository-to-schema drift become executable integration claims instead of
  review-only conventions.
- Negative: each table now needs both immutable migration definitions and current repository metadata.
  Their deliberate duplication requires drift tests and increases the cost of a schema change.
- Negative: correcting an already merged migration requires a new revision, even when editing the old file
  would be simpler in a disposable development database.
- Negative: complete downgrade and live introspection tests take longer than testing generated SQL against
  a fake session, and require the authorized PostgreSQL integration environment.
- Negative: SQLAlchemy Core mapping is more explicit than a declarative ORM and provides no automatic
  relationship loading. Repositories must spell out joins and row-to-domain conversion.

## Alternatives considered

- **Require Alembic only for tables and let services create indexes or constraints at startup.** Rejected:
  clean installs and upgraded databases can diverge, and concurrent service startup is not a migration
  authority.
- **Use SQLAlchemy for the engine but issue repository SQL through `asyncpg`.** Rejected: this creates two
  persistence boundaries, bypasses SQLAlchemy transaction and typing controls, and exposes an untyped driver
  throughout application code.
- **Adopt SQLAlchemy's declarative ORM for every domain object.** Rejected: it is not stronger for this
  append-only, transaction-oriented store, and ORM state, relationships, or callbacks could duplicate the
  domain's transition rules. Typed SQLAlchemy Core retains the selected library while keeping persistence
  mechanics explicit.
- **Generate migrations automatically from mutable metadata at service startup.** Rejected: autogeneration
  is a developer aid, not a reviewed immutable upgrade history, and startup schema mutation makes readiness
  dependent on uncoordinated DDL.
- **Keep lightweight SQLAlchemy clauses without package-owned table metadata.** Rejected for durable tables:
  those clauses cannot be introspected as a complete repository-side schema contract, so migration drift is
  harder to detect.
