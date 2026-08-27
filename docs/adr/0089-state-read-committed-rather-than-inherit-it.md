# ADR-0089: State `READ COMMITTED` on the engine rather than inherit it from the cluster

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0088](0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) orders the mission
timeline with a conditional upsert, and rests the whole property on one sentence: "The row lock the
upsert takes is held until commit, so a second appender for the same mission **waits**, and the two
ordinals are issued in commit order." Gap-freedom follows from the same wait.

**That sentence is only true at one isolation level, and the record does not name one.** It rejects
`SERIALIZABLE` for appends, but it never positively selects what appends run at. Nothing else does
either: [ADR-0085](0085-bound-every-durable-store-wait.md) *measured*
`default_transaction_isolation = read committed` on the pinned cluster and put no isolation row in its
decision table, and `packages/store/src/aerial_rescue_store/engine.py` passed no `isolation_level`, so
the level reached PostgreSQL as an unstated driver and cluster default.

`packages/store/AGENTS.md` forbids exactly that twice: "never let a driver default stand in", and "Do
not let a driver default decide the safety property."

**Measured on the pinned PostgreSQL 18.6 cluster on 2026-08-24**, with two appenders for one mission on
two connections from one pool, the first holding its transaction open for one second after taking its
ordinal:

| Isolation level | Second appender |
| --- | --- |
| `READ COMMITTED` | waits, then takes ordinal 2 |
| `REPEATABLE READ` | refused with `SerializationError`, "could not serialize access due to concurrent update" |

Under `REPEATABLE READ` the second appender does not wait and gets no ordinal at all. So the level is
not a preference about strictness: at the stricter level ADR-0088's mechanism does not work.

## Decision

**The durable store's engine states `READ COMMITTED`, and states it on the engine.**

`ISOLATION_LEVEL` in `packages/store/src/aerial_rescue_store/engine.py` carries the value,
`engine_arguments` puts it in the arguments record so it is asserted without a database, and
`create_engine` passes it to `create_async_engine`, which applies it to every connection the pool
opens. A later transaction that genuinely needs a different level asks for one explicitly, and that
request is visible at the call site rather than inherited from a cluster setting.

The level is a property of the mechanism, not of the deployment. Stating it means the store behaves the
same on a cluster whose `default_transaction_isolation` was changed by someone else — including the
operator's own, which no runbook yet describes applying the schema to.

## Consequences

- ADR-0088's ordering and gap-freedom become properties of code in this repository rather than of a
  cluster setting the repository merely observed once.
- **A lost-update hazard is now this member's to handle, everywhere it does not use the upsert.** Under
  `READ COMMITTED` a plain read-then-write across two statements can be overwritten by a concurrent
  writer. The audit append is safe because the conditional upsert is a single statement that takes the
  row lock itself; nothing else here may assume it inherits that safety. The approval-consumption
  transaction is the first thing that will need its own answer, and it still has none.
- **This record does not settle the concurrency mechanism `packages/store/AGENTS.md` section 4
  requires.** That one must yield exactly one commit and one hard denial, and an ordinal race contains
  no denial. Conditional updates, constraints, and row or advisory locking for approvals remain open;
  only the isolation level they will run at is now fixed.
- Raising the level later is not a tightening. It would break the audit append, which would have to be
  rewritten around a retry loop — the design ADR-0088 rejected when it rejected `SERIALIZABLE`.
- The value is a constant rather than an injected bound, unlike everything in `bounds.py`. Bounds are
  availability parameters a composition root tunes; this is the mechanism, and a root that could change
  it could silently break the timeline.

## Alternatives considered

- **Leave it to the cluster's default.** Rejected: it is what the guide forbids, and it makes a claim
  this repository publishes depend on a setting anybody with cluster access can change.
- **`REPEATABLE READ`.** Rejected on the measurement above: the second appender is refused rather than
  ordered, so the ordinal would have to come from a retry loop.
- **`SERIALIZABLE`.** Rejected by ADR-0088 already, for the same reason and more strongly.
- **Set it per transaction at each call site.** Rejected: it puts the invariant in every caller, which
  is the shape ADR-0088 rejected when it rejected a reader-held watermark. A caller written later that
  forgets it silently reintroduces the failure.
- **Set it cluster-wide in `deploy/compose.yaml`.** Rejected for the reason ADR-0085 gives for the
  server-side bounds: a cluster-wide setting applies this member's choice to `psql`, to the migration
  runner, and to every later consumer that needs a different one.
- **Record nothing and rely on ADR-0088's rejection of `SERIALIZABLE`.** Rejected: rejecting one level
  is not selecting another, and the measurement shows the difference between the remaining two is
  whether the mechanism works.
