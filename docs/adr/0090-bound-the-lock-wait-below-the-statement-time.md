# ADR-0090: Bound the lock wait below the statement time, so a contended row is distinguishable

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0085

## Context

[ADR-0085](0085-bound-every-durable-store-wait.md) bounded every wait the durable store may make and
set the lock wait and the statement time to the same value, 5 s. It then claimed, as a consequence:
"A deadlock and a contended wait become distinguishable, which they were not while the lock timeout
was unbounded and never fired at all."

**Measured on the pinned PostgreSQL 18.6 cluster on 2026-08-24, that consequence is false.** Two
sessions from one pool contend for one row; the first holds its transaction open past the bound.

| The waiting session's bounds | What the server tells it |
| --- | --- |
| `lock_timeout` 5000 ms, `statement_timeout` 5000 ms -- ADR-0085's set | `QueryCanceledError`, "canceling statement due to statement timeout", after 5.023 s |
| `lock_timeout` 2000 ms, `statement_timeout` 5000 ms | `LockNotAvailableError`, "canceling statement due to lock timeout" |

With the two bounds equal, **`lock_timeout` never fires.** A statement that begins by waiting for a
lock reaches both deadlines at the same instant, and the server reports the statement timeout. The
error class is `QueryCanceledError` -- the same class a genuinely stuck statement raises -- so there
is nothing left for a caller to discriminate on. The lock wait is bounded in the sense that the wait
ends; it is not bounded in the sense that anything can tell why.

**ADR-0085 made this exact argument once, in the other direction, and did not make it twice.** It
required the lock wait to exceed the server's 1 s deadlock detection because "a genuine deadlock
would be reported as an ordinary lock timeout ... The two failures need different responses -- a
deadlock is a defect in lock ordering, a timeout is contention -- so collapsing them would hide the
one that has to be fixed." A lock wait at or above the statement time collapses the pair one level
up, for the same reason and with the same cost. `EngineBounds` enforces three relations at
construction; none of them is this one, so a set that hides contention is a set the composition root
accepts.

The cost is not diagnostic comfort. [operating-parameters.md](../operating-parameters.md) says "Only
the lock timeout gates safety: a refusal there is the difference between a denied approval
consumption and an indefinite hold on the approval row", and ADR-0085 says the same in its own words.
That claim rests on a refusal that, as configured, cannot occur. The approval-consumption transaction
`packages/store/AGENTS.md` fixes is the one place in this repository that deliberately holds a row
lock across a caller's clock reads and its call into the domain, so it is also the one place where
"the second caller waited on a contended row" and "the second caller's statement was stuck" must not
arrive as the same error.

**Why this supersedes rather than amends.** ADR-0085's decision opens by refusing to split these
numbers: "One record, and one set of bounds, because they are not independent. The lock wait and the
statement time are both components of the same transaction, and the transaction-level bound has to
contain them; setting them in separate records would put that arithmetic inside three service-local
constants where nothing checks it." A later record that changed one row and left the other nine
elsewhere would be precisely the split that argument rejects. The set moves as a set.

## Decision

**The lock wait is 2 s, strictly below the statement time, and `EngineBounds` refuses a set in which
it is not.** Everything else ADR-0085 decided is restated unchanged.

| Bound | Value | Derived from |
| --- | --- | --- |
| Pool size | 5 sessions per process | Two independent ceilings, in ADR-0085's "How the pool size is derived", which this record does not disturb |
| Pool overflow | 0 | The direct publisher's `DIRECT_BUFFER_CAPACITY` of 0: refuse rather than queue without bound |
| Checkout timeout | 2 s | The connected command path's p95 target of 2 s, so pool exhaustion is detected in about the time the whole operation should have taken |
| Connect timeout | 5 s | The broker adapter's SEMP request timeout of 10 s halved: a loopback connect that has not completed in 5 s is not slow, it is absent |
| Connect retries | 0 | The broker adapter's `CONNECTION_RETRIES` and `RECONNECTION_ATTEMPTS`, both 0. An absent database fails the caller rather than retrying silently |
| Statement timeout | 5 s, server-side | No statement in this store touches an unbounded row set; five seconds is a stuck statement, not a slow one |
| **Lock timeout** | **2 s, server-side** | **The connected command path's p95 target, the same row the checkout bound derives from: both are waits on that path, and neither may outlast it. Twice the measured `deadlock_timeout` of 1 s, and strictly below the statement time** |
| Idle-in-transaction timeout | 15 s, server-side | The longest legal transaction is one lock wait plus one statement, now 7 s; 15 s contains it with more margin than before and is far below the approval time to live |
| Shutdown grace | 15 s | Equal to the idle-in-transaction bound, so a shutdown never outlives the longest transaction the server itself tolerates |
| Migration wait | 90 s | The cluster's own healthcheck envelope in `deploy/compose.yaml`: a 10 s start period then twelve probes at 5 s, so 70 s worst case, plus margin |

The nesting is still the decision rather than the individual numbers, and it now has one more step:

```text
deadlock detection   1 s   (the server's, measured)
  <  lock wait       2 s   =  checkout 2 s, the same path's target
  <  statement       5 s
  <  idle in transaction  15 s   >  lock wait + statement
     shutdown grace       15 s
  <<  approval time to live  60 s
```

**Four relations are now enforced at construction rather than asserted in prose.** `EngineBounds`
refuses a set in which any duration is not positive, in which the lock wait does not exceed the
server's deadlock detection, **in which the lock wait is not strictly below the statement time**, or
in which the idle-in-transaction bound does not contain a lock wait plus a statement.

The new relation is the mirror of the one ADR-0085 named. Below the deadlock detector, a deadlock
reads as contention; at or above the statement time, contention reads as a stuck statement. The lock
wait has to sit strictly between them for either refusal to mean anything, and a composition root
that violates either bound now fails where it is built rather than at the first contended
transaction.

**Only the lock wait gates safety**, unchanged from ADR-0085, and it is now the reason this record
exists: the refusal that distinguishes a denied consumption from an indefinite hold is a refusal that
can actually be raised and identified.

## Consequences

- The refusal `operating-parameters.md` and ADR-0085 both rest a safety claim on becomes reachable
  and typed. A caller can discriminate `LockNotAvailableError` from `QueryCanceledError` and give
  them different answers, which is the whole point of bounding them separately.
- **A contended approval row is refused after 2 s rather than 5 s.** The row is per proposal, so two
  operators deciding different proposals never contend; what contends is a repeat consumption of one
  proposal, which is the case that must be denied anyway. The cost lands on a legitimate consumer
  whose predecessor is slow rather than wedged.
- **This creates an obligation the store does not yet meet: a lock refusal is retryable and a domain
  denial is terminal, and nothing may collapse them.** A caller that treats "could not take the
  approval row" as "the approval was already consumed" would deny a consumption that never happened;
  one that treats a hard denial as retryable would keep asking. Both outcomes now exist on the same
  path and the repository has no code that tells them apart yet.
- The lock wait loses its anchor in the 60 s approval window -- it was "one twelfth", and 2 s is one
  thirtieth. That anchor is deliberately abandoned. The window bounds how long an approval stays
  consumable; it says nothing about how long one consumer should wait behind another, and the
  connected command path's own target does.
- The idle-in-transaction bound stays at 15 s while the transaction it must contain shrinks from 10 s
  to 7 s. Leaving it is a choice to change one number rather than three: 15 s still contains its
  parts, still equals the shutdown grace, and still sits far below the approval window.
- Any composition root holding the previous set now fails at construction. That is intended, and in
  this repository it is one call site plus the constants it reads.
- **The measurement is one workstation, one cluster, one probe, one run.** It establishes that the
  two bounds collapse when equal and separate when unequal. It does not establish the right value,
  and 2 s is derived rather than measured, exactly as every other row here is.
- ADR-0085's nine other rows are restated rather than referenced, which is the cost of moving the set
  as a set: a transcription error would silently change a bound nobody meant to change.

## Alternatives considered

- **Raise the statement time above the lock wait instead.** Rejected: the statement bound is derived
  from "a stuck statement, not a slow one", and lengthening it to make room for a lock wait weakens
  the bound that stops runaway work in order to fix a problem in the other one. The correct direction
  is to shorten the wait that is nested, not to lengthen the wait that contains it.
- **Leave both at 5 s and discriminate on the driver's error class.** Rejected on the measurement:
  both arrive as `QueryCanceledError` with the same message. There is nothing to discriminate on,
  which is what makes this a decision rather than a preference.
- **Leave both at 5 s and record the collapse as accepted debt.** Rejected: the safety-gating row of
  the parameter ledger would keep a claim its own instrument cannot support, and the approval
  transaction that needs the distinction is the next thing to be built.
- **Amend ADR-0085 in place.** Rejected: the ADR log is never edited except to change a status, which
  is what preserves the reasoning that later proved mistaken.
- **A narrow record superseding only the lock row.** Rejected by ADR-0085's own argument, quoted
  above: these bounds are one piece of arithmetic and belong in one record.
- **1 s, or anything at or below the deadlock detector.** Rejected by ADR-0085's original relation,
  which this record keeps: a deadlock would then be reported as contention.
- **3 s, keeping an anchor in the approval window as one twentieth.** Rejected as the weaker
  derivation. It is a fraction chosen to look derived; 2 s is the target the same path already
  carries for its other wait.
- **Set the lock wait per statement at the call site rather than per session.** Rejected for the
  reason [ADR-0089](0089-state-read-committed-rather-than-inherit-it.md) rejected it for the
  isolation level: it puts the invariant in every caller, and a caller written later that forgets it
  silently reintroduces the failure.
