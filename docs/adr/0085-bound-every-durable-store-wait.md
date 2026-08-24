# ADR-0085: Bound every durable-store wait, and derive each from a number the repository already carries

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0003](0003-postgres-durable-mission-store.md) selects PostgreSQL reached through async
SQLAlchemy and `asyncpg`. `packages/store/AGENTS.md` requires that the member "bound pool size,
checkout time, statement time, transaction waits, retries, migration waits, and shutdown", and says
of those bounds that "open parameters block implementation; they are not permission to choose a local
default". Nothing in [operating-parameters.md](../operating-parameters.md) carries a row for any of
them, and they are absent even from that document's own "Parameters still to be set" table, so this
is a gap the ledger did not know it had.

**The defaults are not conservative. They are absent.** Measured on the running container on
2026-08-23, against the cluster [ADR-0060](0060-postgresql-18-and-its-data-directory-layout.md) pins:

```text
statement_timeout                   = 0 ms
lock_timeout                        = 0 ms
idle_in_transaction_session_timeout = 0 ms
idle_session_timeout                = 0 ms
deadlock_timeout                    = 1000 ms
default_transaction_isolation       = read committed
max_connections                     = 100
superuser_reserved_connections      = 3
reserved_connections                = 0
server_version                      = 18.6 (Debian 18.6-1.pgdg13+2)
```

Zero means no bound at all. A statement runs forever, a lock waits forever, and a transaction left
open by a caller that never returns holds its rows forever. That last one is reachable by
construction here rather than by accident: `packages/store/AGENTS.md` fixes an approval-consumption
sequence in which the transaction stays open across the command gateway's two clock reads and its
call into the domain, so the durable side deliberately hands control back to a caller while holding
a row lock.

**A driver default cannot settle this either.** The guide says so in as many words -- "do not let a
driver default decide the safety property" -- and the client-side bounds are the wrong instrument for
two of these: a cancelled coroutine abandons the wait and leaves the server executing, so only a
server-side `statement_timeout` stops the work.

## Decision

One record, and one set of bounds, because they are not independent. The lock wait and the statement
time are both components of the same transaction, and the transaction-level bound has to contain
them; setting them in separate records would put that arithmetic inside three service-local constants
where nothing checks it. This is the argument [ADR-0081](0081-give-command-dispatch-one-interval.md)
made for the dispatch interval, and it holds here for the same reason.

| Bound | Value | Derived from |
| --- | --- | --- |
| Pool size | 5 sessions per process | Two independent ceilings, below in "How the pool size is derived" |
| Pool overflow | 0 | The direct publisher's `DIRECT_BUFFER_CAPACITY` of 0: refuse rather than queue without bound |
| Checkout timeout | 2 s | The connected command path's p95 target of 2 s, so pool exhaustion is detected in about the time the whole operation should have taken |
| Connect timeout | 5 s | The broker adapter's SEMP request timeout of 10 s halved: a loopback connect that has not completed in 5 s is not slow, it is absent |
| Connect retries | 0 | The broker adapter's `CONNECTION_RETRIES` and `RECONNECTION_ATTEMPTS`, both 0. An absent database fails the caller rather than retrying silently |
| Statement timeout | 5 s, server-side | No statement in this store touches an unbounded row set; five seconds is a stuck statement, not a slow one |
| Lock timeout | 5 s, server-side | Strictly greater than the measured `deadlock_timeout` of 1 s, and one twelfth of the 60 s approval time to live |
| Idle-in-transaction timeout | 15 s, server-side | The longest legal transaction is one lock wait plus one statement, which is 10 s; 15 s contains it with margin and is far below the approval time to live |
| Shutdown grace | 15 s | Equal to the idle-in-transaction bound, so a shutdown never outlives the longest transaction the server itself tolerates |
| Migration wait | 90 s | The cluster's own healthcheck envelope in `deploy/compose.yaml`: a 10 s start period then twelve probes at 5 s, so 70 s worst case, plus margin |

The bounds nest, and the nesting is the decision rather than the individual numbers:

```text
deadlock detection   1 s   (the server's, measured)
  <  lock wait       5 s
     statement       5 s
     checkout        2 s
  <  idle in transaction  15 s   =  lock wait + statement + margin
     shutdown grace       15 s
  <<  approval time to live  60 s
```

**Three of those relations are enforced at construction rather than asserted in prose.**
`EngineBounds` refuses a set in which any duration is not positive, in which the lock wait does not
exceed the server's deadlock detection, or in which the idle-in-transaction bound does not contain a
lock wait plus a statement. A degenerate set therefore fails where it is built, in the composition
root, rather than at the first contended transaction. This is the technique
[ADR-0076](0076-evidence-score-bands.md) used for the evidence band boundaries.

The lock-wait relation is the one worth naming. If the lock timeout were at or below the server's
deadlock detection interval, a genuine deadlock would be reported as an ordinary lock timeout,
because the wait would end before the detector ran. The two failures need different responses -- a
deadlock is a defect in lock ordering, a timeout is contention -- so collapsing them would hide the
one that has to be fixed.

**Only the lock wait gates safety.** A refusal there is the difference between a denied consumption
and an indefinite hold on an approval row, and
[ADR-0006](0006-proposal-bound-single-use-approvals.md) makes single use the property that must not
be reachable twice. The rest are availability parameters: exceeding one produces a failed request,
never an unsafe one.

Every bound is injected with no default. The constants below are what a composition root supplies,
not what the adapter falls back to.

### How the pool size is derived

Two ceilings, and the smaller governs.

**Demand.** The largest concurrent caller is the dashboard API, and
[ADR-0024](0024-local-operator-api-boundary.md) scopes it to a single operator on loopback. A browser
opens at most six connections to one origin, so five pooled sessions plus a bounded refusal is more
than one operator's browser can ask for at once. The command gateway is smaller still: its intake
loop receives one message at a time, and approval consumption serialises on the approval row
regardless of how many sessions the pool holds.

**Ceiling.** The cluster reports `max_connections` of 100 with 3 reserved for superusers and 0
otherwise reserved, so 97 are available. Five services will hold a pool -- the dashboard API, the
command gateway, the evidence service, the recorder, and the scenario service; the fleet simulator
holds nothing durable. Five sessions each with no overflow is 25, which leaves 72 for live probes,
`psql`, and the per-run databases the store's own integration tests create.

Neither number is measured under load, because nothing connects yet. They are derived, and a later
measurement supersedes this record rather than editing it.

## Consequences

- A wedged caller now fails instead of holding a row. The specific case this closes is the one the
  approval sequence creates deliberately: a command gateway that reads its clocks and never returns
  releases its lock after 15 s rather than holding it until the process dies.
- A deadlock and a contended wait become distinguishable, which they were not while the lock timeout
  was unbounded and never fired at all.
- **Five of these are server-side settings, and nothing applies them yet.** They are values in a
  typed record until the engine that sets them exists, so this record bounds nothing on its own. That
  is the honest reading of a decision landing one increment ahead of its adapter.
- A composition root can no longer construct a set whose arithmetic is wrong, but it can still
  construct one whose numbers are badly chosen. The refusals check three relations, not fitness.
- The pool ceiling is derived from five services that do not exist. If the count changes, 25 against
  97 changes with it, and the derivation has to be redone rather than the number nudged.
- A 90 s migration wait is longer than the 30 s readiness target, deliberately. Migration is startup
  work that precedes readiness, and a migration abandoned because the cluster was still starting is
  a worse failure than a slow start.
- None of these is measured under load. Every row is derived, and a measurement that contradicts one
  supersedes this record.

## Alternatives considered

- **A record per bound, or one bound at a time as each adapter needs it.** Rejected: the lock wait,
  the statement time, and the idle-in-transaction bound are one piece of arithmetic. Split across
  three records, nothing would check that the transaction-level bound still contains its parts, and
  the relation would live in three constants that can drift independently.
- **The SQLAlchemy and `asyncpg` defaults.** Rejected twice over. The guide forbids it explicitly,
  and the measurement above shows what the server-side defaults actually are: no bound on a
  statement, no bound on a lock wait, and no bound on an abandoned open transaction.
- **Client-side timeouts alone, through `asyncio.wait_for`.** Rejected: cancelling the coroutine
  abandons the wait and leaves the server executing the statement. It bounds how long the caller
  waits, not how long the work runs, and the row locks are held by the work.
- **Measuring first, and setting the numbers afterwards.** Rejected as unavailable rather than wrong.
  Nothing connects to this cluster yet, so there is nothing to sample, and an engine cannot be built
  without the bounds the guide requires. The same order was taken for the dispatch interval in
  ADR-0081 and the queue parameters in
  [ADR-0080](0080-provision-one-durable-queue-per-guaranteed-consumer.md).
- **Setting `statement_timeout` and the rest on the PostgreSQL server or in `deploy/compose.yaml`
  instead of per session.** Rejected: `deploy/` owns container lifecycle and credentials, not
  application behaviour, and a cluster-wide setting would apply the store's bounds to `psql`, to the
  migration runner, and to every future consumer that wants different ones. Per-session settings keep
  the bound with the caller that owes it.
- **A pool sized from the fleet's 23 drones at 1 Hz.** Rejected: that rate describes telemetry on the
  data plane, which never reaches the durable store. Sizing a connection pool from it would import an
  unrelated number and make the derivation look rigorous while being wrong.
- **Reserving pool headroom by raising `max_connections` above 100.** Rejected: 25 against 97 needs no
  headroom, and changing a cluster-wide setting to accommodate an application is the wrong direction
  when the application has not measured its own demand.
- **Enforcing the nesting relations in a test rather than at construction.** Rejected: a test proves
  the constants in this repository are consistent, while a constructor refusal proves it for every
  set any composition root builds, including one assembled from environment values at startup.
