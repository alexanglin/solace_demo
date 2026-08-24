# Phase 3 evidence: the first schema this project has ever applied to a cluster

- **Recorded:** 2026-08-23
- **Revision:** `7f7ee02695246756aa7d6d6907533f53cc385d00`, worktree clean apart from this record
  and the documents this run produced.
- **Host:** Apple Silicon, macOS arm64, Docker Desktop.
- **Versions:** the application runtime's Python 3.14.7 on the host; PostgreSQL
  `postgres:18.6-trixie` at
  `sha256:06cad38a5d9f5d24b4d83d86def30795d5e4b757fedbf5281172b576dedcd941`, reporting
  `18.6 (Debian 18.6-1.pgdg13+2)`, up seven hours; SQLAlchemy 2.0.52, `asyncpg` 0.31.0,
  Alembic 1.19.1.
- **Prerequisites:** the PostgreSQL container healthy on loopback 5432 under the **default**
  profile, the credential `scripts/broker-secrets.sh` generated already on disk at
  `deploy/secrets/postgres-password`, and `POSTGRES_USER` and `POSTGRES_DB` exported into the
  probe's environment. No broker, no provisioning, no drone queues, and no secret rotation.
- **Scope:** the durable store alone. It does **not** cover the `services`, `mesh`, or
  `event-portal` profiles, any repository or session, the command gateway's dispatch half,
  evidence publication, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Only table names, column names, constraint
names, database names, and counts are reproduced.

## Why this record exists

`packages/store/AGENTS.md` had said, since the member was created, that **nothing here has opened
a connection or applied anything to a cluster**. That was exactly true. The member could resolve a
target, refuse a bad set of bounds, build an engine, and render a revision's data definition as
text -- and the rendering is asserted character by character, which is what earns its Tier 2 gate.

What none of it could establish is whether PostgreSQL accepts any of it.
[ADR-0086](../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) drew
that line explicitly: the member's suite "establishes nothing about PostgreSQL", and a live class
on a database the run creates and drops is the only evidence for constraints, isolation,
transaction visibility, Alembic behaviour, restart durability, pool cancellation, and races.

This is the run where the line is crossed for the first time. It is also the first `async` code in
the repository and the first database connection of any kind.

## What was run

```sh
docker compose --env-file .env --env-file deploy/secrets/.env.roles \
    -f deploy/compose.yaml up --detach --wait
export POSTGRES_USER=... POSTGRES_DB=...
uv run --frozen pytest -q tests/integration/test_durable_store_live.py -m docker
```

The probe was run three times in succession against the same container.

| Suite | Result |
| --- | --- |
| `test_durable_store_live.py`, run 1 | 9 passed in 1.13s |
| `test_durable_store_live.py`, run 2 | 9 passed in 1.11s |
| `test_durable_store_live.py`, run 3 | 9 passed in 1.19s |

Each of the seven live cases creates a database of its own, applies what it needs, and drops it,
so the three runs created and dropped twenty-one databases in total.

## What the run found that no offline test could

**The revision is accepted.** `audit_sequence` and `audit_record` exist after the upgrade, beside
Alembic's own `alembic_version`, and the database reports itself at `0001_audit_log`. The eight
columns of `audit_record` are the eight the revision declares, read back from PostgreSQL rather
than from the text that was sent to it.

**Both constraints are enforced, not merely written.** This is the finding that offline rendering
structurally could not reach, and the reason the two cases exist:

- an insert of `next_ordinal = 0` is refused by `ck_audit_sequence_ordinal_positive`; and
- a second record at a mission ordinal already taken is refused by `pk_audit_record`.

[ADR-0088](../../docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) rests
the gap-free mission timeline on that primary key and on the per-mission counter beside it. Until
this run, the evidence for both was that the correct `CREATE TABLE` text had been emitted.

**A second application of the same head changes nothing.** The table set and the stamped revision
are identical afterwards, which is the "repeat application" case `packages/store/AGENTS.md`
section 8 requires.

**The downgrade returns the database to empty.** Both tables are gone; only `alembic_version`
remains, holding no revision. A downgrade that silently left a table behind would have been
invisible to the offline test, which asserts only that `DROP TABLE` appears in the rendered text.

**Nothing leaked and nothing else was touched.** After three runs, `pg_database` holds zero
databases matching `aerial_rescue_probe_%`, and the operator's `aerial_rescue` database still
holds zero tables in `public`. The probe never opened it.

## What changed in the code because of this run

One assertion, and it was the probe's own defect rather than the product's. The first live run
reported:

```text
FAILED ...::FirstRevisionLiveTests::test_postgresql_accepts_the_first_revision
E   AssertionError: Tuples differ:
E   ((), ('audit_record', 'audit_sequence', 'alembic_version')) !=
E   ((), ('alembic_version', 'audit_record', 'audit_sequence'))
1 failed, 8 passed in 1.31s
```

The helper sorts what PostgreSQL reports and the expected literal had been written in declaration
order. The expected set is now derived by sorting the same constants, so the ordering cannot be
got wrong by hand again. **Eight of the nine cases passed on the first live execution**, including
both constraint cases and the downgrade.

## What this run does not establish

Stated plainly, because the temptation to read a green durable-store probe as "the store works" is
the exact misreading ADR-0086's two-class split exists to prevent:

- **No transaction or isolation claim.** Nothing here runs two contenders against each other. The
  durable concurrency mechanism `packages/store/AGENTS.md` section 4 requires is still unselected,
  and ADR-0006's "exactly one commit and one hard denial" is still unproven.
- **No durability-across-restart claim.** No process was interrupted and no container was
  restarted.
- **No repository, session, or unit-of-work claim.** None of those exist. The inserts here are
  typed SQLAlchemy expressions written by the probe, not a persistence adapter.
- **No migration-path claim beyond the first revision.** There is one revision, so "migration from
  every revision the project declares supported" is currently a claim about a history of length
  one. Mismatch and failure recovery are untested; both need a second revision.
- **No pool-cancellation or timeout claim.** The bounds in `bounds.py` were supplied unmodified and
  nothing here provoked one.
- **No claim about the schema the services will use.** The database this run migrated was dropped.
  The operator's `aerial_rescue` database is still empty, and applying the history to it is a
  separate, separately authorized operation that no runbook yet describes.

## What this unblocks

The durable-concurrency ADR that `packages/store/AGENTS.md` section 4 requires before approval
consumption, the idempotency claim, and outbox staging can be built. That record needs "a real
PostgreSQL race" as its evidence, and until this run there was no live PostgreSQL class to write
one in. `docs/IMPLEMENTATION_PLAN.md` carries the delivery consequence.
