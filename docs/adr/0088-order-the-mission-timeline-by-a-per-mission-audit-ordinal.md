# ADR-0088: Order the mission timeline by a per-mission audit ordinal advanced inside the writing transaction

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0003](0003-postgres-durable-mission-store.md) makes "an append-only audit table with a monotonic
ordinal ... the ordering authority for the mission timeline", and says explicitly that per-producer
sequence numbers are scoped to their source and must not be used to order it.
[ADR-0037](0037-cloudevents-envelope-profile.md) repeats the second half from the other side.
[ADR-0067](0067-normalized-dashboard-events-and-reduced-state.md) then puts that ordinal **inside the
reduced dashboard state**, which is the replay determinism oracle of
[ADR-0009](0009-isolated-side-effect-free-replay.md), hashed under the `replay-state` context.
`packages/store/AGENTS.md` requires evidence of "monotonic audit ordinals under concurrent writers".

**A generated identity column does not deliver that, and the failure is silent.** PostgreSQL assigns a
sequence value when the row is inserted, not when the transaction commits. Two concurrent appends can
take 6 and 7 and commit in the opposite order, so a reader that polls "everything above the highest
ordinal I have seen" observes 7, records 7 as its high-water mark, and never sees 6 -- which then
exists in the table forever, invisible to that reader. A rolled-back transaction also consumes a
number, leaving a gap that is indistinguishable from the one above while it is still open.

Both consequences land on claims this repository already makes. A reader that skips a record produces a
different reduced state from one that waits for it, so `docs/operating-parameters.md`'s "identical hash
of the canonical reduced dashboard state across 10 runs" would fail for a system that is behaving
correctly. And the recorder exports replay fixtures from this history, so a gap becomes a committed
fixture that omits a record.

`packages/store/AGENTS.md` says a persistent data shape "requires the decision and coordinated work
specified by the root guide. Do not settle one in an ORM default, migration comment, or repository
method." A column type is exactly the place this would otherwise be settled by accident.

## Decision

**The ordinal is per mission, and it is advanced by a conditional upsert inside the same transaction
that writes the record.**

```sql
INSERT INTO audit_sequence (mission_id, next_ordinal) VALUES ($1, 1)
ON CONFLICT (mission_id)
  DO UPDATE SET next_ordinal = audit_sequence.next_ordinal + 1
RETURNING next_ordinal;
```

The row lock the upsert takes is held until commit, so a second appender for the same mission waits,
and the two ordinals are issued in commit order. A rolled-back transaction releases the lock without
advancing the counter, so the sequence is **gap-free** as well as ordered. The first record of a
mission needs no separate initialisation, which is why this is an upsert rather than an update.

**Per mission is the scope the claim was always about.** ADR-0003 calls it "the ordering authority for
the mission timeline", and a timeline belongs to a mission. Ordering appends for one mission against
appends for another buys nothing a reader can use and serialises work that never needed serialising.

**Lock ordering: within one mission, the approval row is taken before the audit sequence row.** Two
transactions taking two locks in opposite orders is the one way this deadlocks, and the approval
consumption `packages/store/AGENTS.md` describes introduces the second lock, so the rule makes the
order total before that record is written. It is also why
[ADR-0085](0085-bound-every-durable-store-wait.md) requires the lock wait to exceed the server's
deadlock detection: if the rule is ever broken, the detector must be what reports it.

### The first revision's shape

Two tables, and a retention class for each, assigned now rather than when a reset endpoint asks.

| Table | Holds | Retention |
| --- | --- | --- |
| `audit_sequence` | One row per mission: the mission identifier and the last ordinal issued | Never deleted |
| `audit_record` | One append-only record: mission, ordinal, kind, the canonical instant, the canonical payload, and the correlation, causation, and trace-parent values a reader needs to reach another system | Never deleted |

- **Identifiers are `text` with a check constraint**, bounded to the 1 to 64 characters
  `docs/operating-parameters.md` gives an identifier and matching the topic grammar's rule. A native
  `uuid` is rejected: a drone identifier such as `drone-07` is not one, and the store must "persist the
  exact accepted values" rather than a re-encoding of them.
- **The instant is stored as the canonical text**, not as `timestamptz`.
  [ADR-0027](0027-integer-only-canonical-serialization.md) fixes the exact millisecond spelling and
  makes those bytes part of what a digest covers, so the value that was accepted is the value that is
  stored. A `timestamptz` would store microseconds and require re-rendering into the canonical form on
  every read, which is a formatting rule in a second place. A queryable form can be added later by a
  record that needs one.
- **The payload is stored as the canonical bytes.** `packages/contracts` owns the canonicalizer, and
  re-encoding through a JSON column type would let the database's own serialisation decide bytes that a
  digest covers.
- `audit_record` has no update or delete path in this member. Append-only is enforced by the absence of
  a method, and by a live test asserting that neither reaches a row.

## Consequences

- The determinism claim becomes reachable: a reader can consume every record of a mission up to the
  highest ordinal with no gap, because a gap cannot exist and an out-of-order commit cannot happen.
- **Appends for one mission are serialised.** That is the point, and it is also a real throughput bound:
  one mission's audit writes proceed one at a time. At 23 drones with one operator this is far from
  binding, and it would be at a scale this project does not claim.
- **The counter is a hot row per mission.** Every append for a mission contends on it, so a transaction
  that holds the sequence row while doing slow work blocks every other append for that mission. The
  idle-in-transaction bound from ADR-0085 is what stops that being unbounded.
- A second lock means a lock-ordering rule, and a rule can be broken by code written later. The
  deadlock detector is the backstop rather than the design.
- Storing instants as text means no range query on time without a later record adding a form that
  supports one. Ordering by time is not affected, because ordering is the ordinal's job.
- `audit_sequence` is a table whose only purpose is to be locked. It carries no mission state and must
  not acquire any: the mission's own row, when it exists, is a different table with a different
  lifetime.
- The retention classes are assigned before anything can delete, so the reset scope that
  `POST /api/v1/scenarios/current/reset` still owes becomes an enumeration of decided properties rather
  than a fresh argument per table.

## Alternatives considered

- **`bigserial` or an identity column.** Rejected: it assigns before commit, so it delivers neither
  commit order nor gap-freedom, and the resulting reader skip is silent. This is the alternative the
  record exists to refuse.
- **A global ordinal rather than a per-mission one.** Rejected: it serialises every mission against
  every other for an ordering no reader uses, and ADR-0003 scopes the claim to the mission timeline.
- **Serialising appends with an exclusive table lock.** Rejected for the same reason, and because a
  table-level lock is a coarse instrument to introduce at the first revision when a row lock is exact.
- **A plain sequence plus a reader watermark from `pg_snapshot_xmin(pg_current_snapshot())`.** Rejected,
  though it is correct and is what change-data-capture tools do. It puts the invariant in every reader
  rather than in the schema, so a reader written later that forgets the predicate silently reintroduces
  the skip -- and there will be several readers: the recorder, the dashboard projection, and the replay
  export.
- **`SERIALIZABLE` isolation for appends.** Rejected: it converts the race into a serialization failure
  that is retryable by contract, so ordering would be produced by a retry loop rather than by a rule,
  and a retry that re-reads the counter is the same design with more moving parts.
- **An application-held counter, or one in a cache.** Rejected: process memory is authority for nothing
  durable, and a restart would reissue an ordinal.
- **Ordering the timeline by the envelope `time`.** Rejected: ADR-0009 names event times as legitimately
  differing between runs, and two records can share a millisecond.
- **Ordering by the producer-scoped `sequence`.** Rejected: ADR-0003 and ADR-0037 both state it never
  orders the timeline, and it is scoped to one producer where the timeline spans many.
- **A `timestamptz` for the instant with the canonical string derived on read.** Rejected: it puts the
  canonical formatting rule in a second place, and the digest depends on it.
