# Phase 3 evidence: the transaction above the schema, and ADR-0006's atomic set under real concurrency

- **Recorded:** 2026-08-24, 16:44:51Z to 16:46:18Z. Event order matters here, so the runs are
  timestamped: the three race cases assert what two contenders did relative to each other.
- **Revision:** `2ff8729dd566fd875e33d68bf7e63660199fcc40`, worktree clean, on a checkout created for
  this run. Nothing was uncommitted at any point during the five runs.
- **Host:** Apple Silicon, macOS 26.6.2 arm64, Docker Desktop 29.5.3 with 16 CPUs and 7.65 GiB
  allocated to the Linux VM.
- **Versions:** the application runtime's Python 3.14.7 on the host; PostgreSQL `postgres:18.6-trixie`
  at `sha256:06cad38a5d9f5d24b4d83d86def30795d5e4b757fedbf5281172b576dedcd941`, reporting
  `PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) on aarch64-unknown-linux-gnu`; SQLAlchemy 2.0.52,
  `asyncpg` 0.31.0, Alembic 1.19.1.
- **Prerequisites and pre-existing external state:** the PostgreSQL container healthy on loopback 5432
  under the **default** profile, started `2026-08-23T18:59:32Z` and therefore up roughly 21.7 hours
  before this run, with `RestartCount` 0. It was **not** started, recreated, or reconfigured for this
  run. The credential `scripts/broker-secrets.sh` generated already on disk at
  `deploy/secrets/postgres-password`, and `POSTGRES_USER` and `POSTGRES_DB` exported into the probe's
  environment. No broker, no provisioning, no drone queues, no secret rotation, and no container
  mutation of any kind.
- **Scope:** `tests/integration/test_durable_store_live.py` alone, at 39 cases. It does **not** cover
  the `services`, `mesh`, or `event-portal` profiles, any other integration probe, the command
  gateway, evidence publication, the paid-call ledger, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, connection string, or tenant identifier appears here.
Generated material lives under the untracked `deploy/secrets/`. Only table names, column names,
constraint names, setting names, PostgreSQL's own rendering of settings, and counts are reproduced.

## Why this record exists

[`durable-store-first-run.md`](durable-store-first-run.md) is pinned to revision `7f7ee02`, covers one
Alembic revision, reports nine cases, and closes by saying in as many words what it could not reach:

> **No transaction or isolation claim.** Nothing here runs two contenders against each other. The
> durable concurrency mechanism `packages/store/AGENTS.md` section 4 requires is still unselected, and
> ADR-0006's "exactly one commit and one hard denial" is still unproven.

Since then the schema history has grown from one revision to four, a session and a bounded transaction
have been built above it, four repositories have been added, and the probe has grown from nine cases to
39. Those commits also wrote live-cluster claims into the guides, the parameter ledger, and six rows of
[`approval-bypass-catalogue.md`](../../docs/security/approval-bypass-catalogue.md), and
[`docs/security/AGENTS.md`](../../docs/security/AGENTS.md) requires a `proven live` status to be bound
to "the recorded local profile, date, configuration, transport, operation, positive control, and
evidence record". Until this record, no evidence record supported any of them.

This is an independent rerun at a changed revision under a changed acceptance scope, which
[`release-evidence/AGENTS.md`](../AGENTS.md) section 5 requires to be a new record rather than an
edit to the old one.

## What was run

```sh
export POSTGRES_USER=... POSTGRES_DB=...
uv run --frozen pytest -q tests/integration/test_durable_store_live.py
```

That selector is the one [`tests/integration/AGENTS.md`](../../tests/integration/AGENTS.md) names.
The earlier record's `-m docker` suffix is not the guide's canonical form and was not used here.

Five consecutive runs against the same container, with no intervening change:

| Suite | Result |
| --- | --- |
| `test_durable_store_live.py`, run 1 | 39 passed in 12.16s |
| `test_durable_store_live.py`, run 2 | 39 passed in 12.20s |
| `test_durable_store_live.py`, run 3 | 39 passed in 12.17s |
| `test_durable_store_live.py`, run 4 | 39 passed in 12.10s |
| `test_durable_store_live.py`, run 5 | 39 passed in 12.19s |

The runs were repeated because three of the cases are races, and one green run of a race is a weaker
observation than it looks. The spread across five runs is 100 milliseconds.

Of the 39 cases, two open no connection: `RunDatabaseNameTests` asserts the derived run-database name
and the refusal ADR-0086 requires when that name equals the configured `POSTGRES_DB`. The other 37 each
create a database of their own, apply what they need, and drop it, so the five runs created and dropped
**185 databases**. The case counts by class are `FirstRevisionLiveTests` 9, `AuditAppendLiveTests` 8,
`IdempotencyClaimLiveTests` 6, `CommandOutboxLiveTests` 6, `ApprovalConsumptionLiveTests` 5,
`AtomicSetLiveTests` 3, `RunDatabaseNameTests` 2.

## What the run establishes

Each claim below names the cases that carry it. The producer is a local live probe, so each is bounded
to the components and host recorded above.

**The four-revision history is a path, walked in both directions.** Not one revision applied at head,
but each of `0001_audit_log`, `0002_approval`, `0003_idempotency`, `0004_command_outbox` applied singly,
each stamping itself in `alembic_version` and adding exactly its own tables; then each downgrade landing
on the revision below with exactly that revision's table set. The table names are read back out of
PostgreSQL's own catalogue rather than compared against the text that was sent to it
(`test_the_history_applies_one_revision_at_a_time`,
`test_each_step_back_leaves_the_revision_below_it_intact`). A repeat application of the same head
changes nothing, and a downgrade to base leaves only `alembic_version`, holding no revision.

**Six declared constraints are enforced rather than merely written.**
`ck_audit_sequence_ordinal_positive`, `pk_audit_record`, `ck_approval_state_in_protocol`, `pk_approval`,
`ck_idempotency_claim_kind`, and `ck_command_outbox_state` each refuse a write that names them in the
refusal. Note what this is not: see "four declared constraints are never provoked" below.

**The engine's server-side bounds reach a session, not only the driver.** On one connection from the
configured engine, PostgreSQL reports `statement_timeout` `5s`, `lock_timeout` `2s`, and
`idle_in_transaction_session_timeout` `15s` (`test_every_server_side_bound_reaches_a_session_rather_than_only_the_driver`).
These are PostgreSQL's rendering of values [ADR-0090](../../docs/adr/0090-bound-the-lock-wait-below-the-statement-time.md)
already decided; the run confirms they arrive, and measures nothing new about the numbers themselves.

**The cluster's deadlock interval is now read rather than assumed.** `deadlock_timeout` reports `1s`,
which is the `SERVER_DEADLOCK_TIMEOUT_MILLISECONDS` that `EngineBounds`' `LOCK_BELOW_DEADLOCK_DETECTION`
relation is derived from (`test_the_cluster_reports_the_deadlock_interval_the_lock_wait_is_derived_from`).
This case was written for this run; see "what changed in the code" below.

**The isolation level the engine states is the one the server reports.** `transaction_isolation` reports
`read committed` (`test_the_isolation_level_the_engine_states_is_the_one_the_server_reports`), which is
[ADR-0089](../../docs/adr/0089-state-read-committed-rather-than-inherit-it.md)'s decision observed rather
than inherited. This is evidence for a decision, not a parameter measurement: the level is a constant
and has no row in [`operating-parameters.md`](../../docs/operating-parameters.md).

**One bound is observed firing.** A statement past the bound is cancelled by the server with
`canceling statement due to statement timeout` (`test_a_statement_past_the_bound_is_cancelled_by_the_server`).
It is the only one of the ten that this run provokes.

**The audit ordinal is gap-free under two concurrent appenders.** Two appenders on two connections from
one pool take ordinals 1 and 2 in commit order, and the second is observed **still unfinished** when a
500 millisecond window ends, rather than merely finishing later
(`test_two_appenders_for_one_mission_are_ordered_by_the_lock_the_first_holds`). A rolled-back append
leaves no row and burns no ordinal, so the next append is still 1
(`test_an_abandoned_append_leaves_neither_a_record_nor_a_gap`).

**ADR-0006's "exactly one commit and one hard denial" is no longer owed.** Two consumers of one approval
row: the first commits, the second waits on the row lock and is refused by the protocol's own
`ALREADY_CONSUMED` rather than by a row count, and the row ends `executed`, read back on a session that
wrote neither (`test_two_consumers_of_one_approval_commit_once_and_deny_once`). The denial survives into
a later transaction, and an abandoned consumption leaves the approval consumable.

**The idempotency key is claimed once and replayed once.** Two claimants of one key: the first executes,
the second waits on the conflicting insert and returns the prior result
(`test_two_claimants_of_one_key_execute_once_and_replay_once`). A body mismatch is a refusal rather than
a repeat, a denied consumption is denied again rather than replayed, and a recorded result is never
overwritten.

**The outbox bound of 500 refuses with nothing written.** With exactly `MAXIMUM_UNCONFIRMED_RECORDS`
staged rows, the next staging is refused `AT_CAPACITY` and the row is absent afterwards
(`test_the_record_past_the_bound_is_refused_and_nothing_is_written`). Confirming one record restores
room, so `CONFIRMED` rows stop counting. The instrument
[`operating-parameters.md`](../../docs/operating-parameters.md) names for this row is the count
evaluated inside the staging statement, which is what these cases exercise; the number 500 itself comes
from [ADR-0084](../../docs/adr/0084-give-backlog-recovery-an-instrument.md)'s workload and is not
measured here.

**ADR-0006's three writes commit and roll back together.** One transaction moves the approval from
`approved` to `executed`, the claim from absent to its recorded result, and the outbox record from
absent to `staged`; a transaction abandoned after all three writes leaves the three-tuple byte-identical
to what it was before; and the approval is genuinely consumable again afterwards rather than merely
appearing unchanged (`AtomicSetLiveTests`, three cases). The set is three writes and not four: the audit
append is deliberately outside it, because no accepted decision adds it and
[`packages/store/AGENTS.md`](../../packages/store/AGENTS.md) forbids enlarging or shrinking the set
silently.

## What changed in the code because of this run

One case was added, and it was written before the run rather than in response to it.
[`operating-parameters.md`](../../docs/operating-parameters.md) said the cluster deadlock-detection row
was "read from the running cluster rather than assumed", and `bounds.py` repeated the claim. Nothing read
it: the readback issued three `SHOW` statements and `deadlock_timeout` was not among them, so the one
number ADR-0090's lock-wait derivation rests on had never been taken off the server. The case was
written first against the wrong rendering, so that the readback could be seen working rather than
assumed to:

```text
E       AssertionError: Tuples differ: ('1000ms',) != ('1s',)
E       - ('1000ms',)
E       + ('1s',)
1 failed, 38 deselected in 0.44s
```

The cluster reports `1s`. No production code changed, and no assertion was weakened.

## What this run does not establish

Stated at length, because a green 39-case probe reads as "the store works" and the two-class split in
[ADR-0086](../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) exists to
prevent exactly that reading.

- **No durability-across-restart claim, and no interrupted-process claim.** No process was killed, no
  connection was severed mid-transaction, and no container was restarted. Every rollback here is one the
  probe asked for. `packages/store/AGENTS.md` names the blocker as "a probe that kills a process", and
  ADR-0086 says the live class "cannot survive a restart" because an outer rollback leaves no committed
  state to inspect. This is unchanged from the earlier record.
- **No caller.** No workspace member declares `packages/store` as a dependency, so nothing in the
  product opens the transaction these repositories exist for. The probe is the caller. The command
  gateway's half of the dispatch lifecycle is still owed, and command intake remains at-least-once with
  duplicates possible across a restart.
- **No migration mismatch or failure-recovery claim.** The path is walked, but no revision is applied to
  a database stamped at an incompatible revision and no migration is interrupted. This clause of the
  earlier record stands unchanged.
- **The lock refusal is never provoked, so the two refusals on one path are still not told apart.**
  ADR-0090 and [ADR-0091](../../docs/adr/0091-consume-an-approval-under-its-own-row-lock.md) both record
  the obligation: a lock refusal is retryable and a domain denial is terminal, and nothing may collapse
  them. The probe's contention window is 500 milliseconds, deliberately far below the two-second lock
  wait, so a blocked contender is still blocked when the window ends rather than refused. No case
  observes a lock timeout, and no code in the repository discriminates the two outcomes yet.
- **Nine of the ten bounds are supplied and never observed acting.** Only the statement timeout fires.
  The lock timeout, the idle-in-transaction timeout, pool checkout exhaustion, the connect timeout, and
  the connect retries are all passed unmodified and never reached. `aerial_rescue_store.session.close`
  is never called either — every teardown disposes the engine directly — so the shutdown grace and its
  refusal are unexercised live.
- **The migration wait is unreachable at runtime.** `MIGRATION_WAIT_SECONDS` is not a member of
  `EngineBounds`, reaches no engine, session, or statement, and is not imported by this probe. Its only
  reader is an offline member test asserting it contains the healthcheck envelope.
- **`RESULT_NOT_RECORDED` is closed by construction here and unhandled in the product.** The probe
  records the result inside the claiming transaction, which shuts the in-flight window
  [ADR-0092](../../docs/adr/0092-claim-an-idempotency-key-with-one-conflicting-insert.md) describes. A
  real gateway records later, which is why the outcome exists; no case asserts it and no code handles it.
- **`RECONCILIATION_NEEDED` has no reader.** One case writes the state and reads the column back, with
  the ambiguous outcome supplied by the probe rather than by a broker adapter. What reads that state,
  and how, is owed by [ADR-0093](../../docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md)
  and named by nothing.
- **The continuity-breach audit record is never written.** The refusal past the bound is observed and
  the absent row is confirmed, but the audit record ADR-0093 assigns to the caller, in its own
  transaction after the rollback, has no caller to write it.
- **The outbox bound is evidenced for `STAGED` rows only.** The staging statement counts every
  unconfirmed record, and ADR-0093 rejects counting only `STAGED` ones by name, but both cases that fill
  the outbox fill it with `STAGED` rows. The `CONFIRMED` exclusion is proven; the
  `RECONCILIATION_NEEDED` inclusion is not.
- **The concurrent overshoot is inference, not measurement.** The ledger records an effective ceiling of
  504 as a consequence of `READ COMMITTED` under a pool of five. Every outbox case here is sequential,
  so no overshoot was produced or measured.
- **ADR-0088's lock-ordering rule is neither exercised nor violated.** No transaction in this probe takes
  both the approval row lock and the audit sequence row lock, so the rule that the approval is taken
  first is untested in the correct direction, no deadlock is produced, and the detector that ADR-0090's
  relation preserves as a backstop is never observed firing.
- **Four declared constraints are never provoked.** `pk_audit_sequence`,
  `ck_audit_record_ordinal_positive`, `ck_approval_time_to_live_positive`, and `pk_command_outbox` are
  declared and never named in a live refusal. `pk_idempotency_claim` is exercised functionally, as the
  index the conflicting insert targets, but never observed refusing. This run does not establish that
  every declared constraint is enforced.
- **Ten typed refusals are never raised live.** `NOT_FOUND`, `UNKNOWN_STATE`, `NOT_A_DECISION`,
  `NOT_EXECUTED`, and `NOT_CONSUMABLE` on the approval path, and `CLAIM_VANISHED`, `UNKNOWN_KIND`,
  `KIND_MISMATCH`, `RESULT_NOT_RECORDED`, and `UNREADABLE_RESULT` on the claim path. `NOT_CONSUMABLE` in
  particular is the conditional update's zero-row branch, which ADR-0091 records as unreachable through
  this member's own API.
- **No durable proposal record, and no persisted action parameters.** ADR-0006 binds the action
  parameters into the immutable record; the `approval` table holds none, and in this probe they never
  leave process memory. ADR-0091 defers the proposal record to the agent-proposal path.
- **Catalogue case B24 stays open.** One case shows the database refusing a state *outside* the
  protocol; B24 writes a state *inside* it. A writer with database credentials can still set an approval
  to `approved` by hand, and the detection path remains to build.
- **Every race is two asyncio tasks in one process.** One event loop, one interpreter, two connections
  from one pool. That is a genuine PostgreSQL row-lock race and is not evidence about two processes, two
  pools, two hosts, or a contender that dies mid-race.
- **No least-privilege claim.** The maintenance connection that creates and drops each run database uses
  the compose bootstrap superuser, which the application services neither need nor should acquire.
- **No claim about the schema the services will use.** Every database this run migrated was dropped. The
  operator's own database still holds zero tables in `public`, and applying the history to it is a
  separate, separately authorized operation that no runbook yet describes.

## Two findings

Both are cases where a current document claims more than the tree supports.
[`release-evidence/AGENTS.md`](../AGENTS.md) section 7 makes that a finding to record rather than a
document to quietly correct, and forbids editing an Accepted decision's prose to match a later
measurement.

**ADR-0088 names a live test that does not exist.** It says `audit_record`'s append-only property is
"enforced by the absence of a method, and by a live test asserting that neither [an update nor a delete]
reaches a row". No such case is present: the probe's only update statements target the approval and
outbox tables, and it issues no delete at all. Nor is there a database-level guard — `rev_0001_audit_log`
declares no trigger, no rule, and no revoked grant, so a writer with credentials can update or delete a
written audit record. The nearest existing case asserts only that a *second append* does not disturb the
first.

**The identifier bound is half-enforced, and not by the mechanism the decision names.** ADR-0088 says
"identifiers are `text` with a check constraint, bounded to the 1 to 64 characters". The shipped revision
declares `varchar(64)`. The upper bound holds, by the column type rather than by a constraint; the lower
bound of 1 is not enforced anywhere, so an empty mission, correlation, or causation identifier would be
accepted. No case tests an over-length or empty identifier.

Both need a decision — either the guard the ADR describes, or a record that supersedes it. Neither is
opened here.

## Final external state

Read back by hand after the fifth run, and attributed to those readings rather than to the suite, which
queries neither:

| Reading | Before | After |
| --- | --- | --- |
| Databases matching `aerial_rescue_probe_%` in `pg_database` | 0 | 0 |
| Tables in `public` in the operator's `POSTGRES_DB` | 0 | 0 |
| Container `RestartCount` / `StartedAt` | 0 / `2026-08-23T18:59:32Z` | 0 / `2026-08-23T18:59:32Z` |

No database leaked across 185 create-and-drop pairs, the operator's database was never opened, and the
container was neither restarted nor recreated during the run. No cleanup or recovery was called for, and
none was performed.

## What this record changes about the earlier one

[`durable-store-first-run.md`](durable-store-first-run.md) remains the record of what was observed at
`7f7ee02`, and nothing in it is edited. Of its six closing limitations, this run settles the transaction
and isolation claim outright, settles the migration-path claim except for mismatch and failure recovery,
settles the pool-and-timeout claim for the statement bound alone, and replaces "no repository, session,
or unit-of-work claim" with the narrower and still-true "no caller". Its restart-durability limitation
and its limitation about the operator's own schema stand unchanged, and are repeated above.
