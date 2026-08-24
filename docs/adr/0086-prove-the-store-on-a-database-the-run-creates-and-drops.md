# ADR-0086: Prove the durable store on a database the run creates and drops, and keep its member suite offline

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0003](0003-postgres-durable-mission-store.md) states that "deterministic test isolation now
requires a per-run database or transactional rollback strategy" and leaves the choice open.
`packages/store/AGENTS.md` requires tests to "use the per-run database or transactional-rollback
isolation strategy selected by the governing decision", so a decision has to actually select one.
This record is that selection, and it also settles a second question the guide implies but does not
state: which claims may be made by a test that never reaches PostgreSQL.

**One measured fact decides most of the shape.** `scripts/hooks/python/pytest-full.sh` excludes
`broker`, `ollama`, `paid`, `docker`, and `net` from the blocking suite at line 32, and builds the
`--cov=` arguments for every workspace member in that same invocation at lines 39 to 43. A test that
needs a container therefore contributes **nothing** to coverage. `tools/coverage_gate.py` attributes
only `packages/store/src/` to this member, at Tier 2, which is 95% of statements and 95% of branches.

So the member's coverage obligation has to be met by tests that never open a connection. That is not a
compromise forced by tooling; it is the same shape every other member already has. No file under any
`packages/*/tests/` or `services/*/tests/` carries a resource marker today, and every live probe in the
repository lives under root `tests/`.

`packages/store/AGENTS.md` also bounds what a fake may claim: "deterministic fakes may prove repository
call order and rollback intent. They do not prove PostgreSQL isolation, unique constraints, transaction
visibility, Alembic behavior, restart durability, pool cancellation, or concurrent races."

## Decision

**Two test classes, with different homes and different jobs, and neither may borrow the other's claim.**

*Member-local, offline, blocking.* `packages/store/tests/` carries no resource marker and never opens a
connection. It proves statement construction, row mapping into typed values, typed error mapping,
settings resolution, credential redaction, bounded-value refusals, and repository call order and
rollback intent against injected fakes. **This class alone earns the Tier 2 gate.** It establishes
nothing about PostgreSQL, and the member guide already says so.

*Live, per-run database, nonblocking.* `tests/integration/` carries `integration` and `docker` and is
run only under explicit human authorization for the exact file. It is the only evidence for isolation,
constraints, transaction visibility, Alembic behaviour, restart durability, pool cancellation, and
concurrent races.

**The live class uses a database the run creates and drops**, named for the run and dropped in cleanup,
never the operator's `aerial_rescue`. Transactional rollback is rejected for three reasons, each
sufficient alone:

- **It cannot test a migration.** `packages/store/AGENTS.md` requires "migration from every revision the
  project declares supported, repeat application, mismatch, and failure recovery". The migration is the
  data-definition change under test; a strategy that rolls everything back has nothing to observe.
- **It cannot produce a race.** [ADR-0006](0006-proposal-bound-single-use-approvals.md) requires exactly
  one commit and one hard denial from concurrent approval consumption. Under an outer transaction the
  two contenders either share one connection, in which case there is no race, or cannot see each other,
  in which case the answer is wrong. A commit claim cannot be proven by a test that never commits.
- **It cannot survive a restart.** Restart durability and interrupted-process rollback need committed
  state that outlives the process, and an outer rollback leaves none.

**The refusal is a precondition, not a convention.** The probe derives its database name at run time and
**refuses to run when the resolved name equals the configured `POSTGRES_DB`**, so the rule that tests
never touch persistent mission data is executable rather than remembered.

**No new resource marker.** `docker` already excludes these from every blocking stage. A new resource
class would mean editing the marker table, five hook scripts, their conformance tests, and CI, for no
change in behaviour.

`tests/integration/` stops being broker-only. Its guide currently requires the `broker` marker on every
module in the directory; that becomes per-module, because a resource marker is a declaration of what a
test needs, and declaring a broker prerequisite that does not exist is the drift the rule exists to
prevent.

## Consequences

- The member's own suite can never establish a durability claim, and the guide's sentence to that effect
  becomes structural rather than advisory. Anything about PostgreSQL has to be argued from a live record.
- Coverage stays honest: 95% of a member whose tests never connect measures the logic, and says nothing
  about the database, which is the correct division and an easy one to misread. Every live record must
  state what it adds.
- **Creating a database costs a connection outside any transaction**, because `CREATE DATABASE` cannot
  run inside one. The probe therefore opens an autocommit connection to the maintenance database, which
  is one more privileged step than a rollback strategy needs.
- A probe that dies between creating and dropping leaves a database behind. It is named for the run, so
  the leak is visible and removable, but it is a leak and cleanup has to be explicit.
- The compose `POSTGRES_USER` is the bootstrap superuser, so it can create databases. That is a
  privilege the application services do not need and should not later acquire by copying the probe's
  connection code.
- Live evidence stays out of continuous integration. `.github/workflows/` runs no service container, so
  admitting this class to a blocking stage would make the push stage require Docker -- the permanently
  red stage [ADR-0019](0019-fail-closed-quality-gates.md) and
  [ADR-0053](0053-report-scaffolded-workspace-members-instead-of-failing-them.md) exist to avoid. A later
  record may admit it once a service container exists.

## Alternatives considered

- **Transactional rollback against a pre-migrated database.** Rejected on the three grounds above. It is
  faster and it does prove constraint behaviour and typed mapping, so it stays permitted *inside* a live
  test whose claim does not depend on commit -- but never for a migration, a race, or a durability claim.
- **SQLite, in memory or on disk.** Rejected. `packages/store/AGENTS.md` forbids it twice, and
  independently it has no equivalent row-lock semantics, no comparable partial-index behaviour, and no
  serialization-failure class, so the properties under test do not exist there to be proven.
- **A schema per run inside the mission database.** Rejected. It is cheaper, but a `DROP SCHEMA` in the
  mission database is one mistake away from the outcome the rule forbids, and it does not isolate
  database-level objects.
- **One long-lived test database, migrated once and truncated between tests.** Rejected twice over: the
  base-to-head migration path would then run only when somebody remembered to recreate it, so migration
  evidence would rot silently; and the truncation list becomes a second, unreviewed definition of the
  reset scope that `POST /api/v1/scenarios/current/reset` has yet to decide, which a new table escapes
  without anyone noticing.
- **`testcontainers` or `pytest-postgresql`.** Rejected. Both add a pinned, audited dependency to obtain
  a `CREATE DATABASE`, and `testcontainers` starts a container from inside a test, which `tests/AGENTS.md`
  forbids as an implicit side effect. The human-authorized compose stack is the container.
- **A dedicated `postgres` resource marker.** Rejected: `docker` already excludes it, and the change
  would touch the marker table, five hook scripts, their conformance tests, and CI for no behavioural
  difference.
- **Putting the live probes in `packages/store/tests/` beside the member they exercise.** Rejected.
  `tests/AGENTS.md` does place member-owned behaviour beside its member, but no member suite in this
  repository carries a resource marker, and a member directory that mixes blocking and nonblocking files
  makes "run the member's tests" mean two different things depending on the invocation.
- **Admitting the live class to the blocking stage now.** Rejected as premature rather than wrong; see
  the consequence above.
