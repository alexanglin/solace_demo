# ADR-0091: Consume an approval under its own row lock, and let the domain's refusal be the denial

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0006](0006-proposal-bound-single-use-approvals.md) makes an approval single-use: "a second
consumption is a hard denial, explicitly not an idempotent success". `packages/store/AGENTS.md`
turns that into a requirement on this member -- the mechanism "must yield exactly one commit and one
hard denial and cannot rely on process-local locking or an unprotected check-then-write" -- and
leaves the choice open: "Conditional updates, constraints, and row or advisory locking remain
undecided; select them in an ADR and prove the outcome with a real PostgreSQL race test."

Three earlier records constrain the answer before it is chosen.

**The caller decides in the middle.**
[ADR-0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) requires the command
gateway to read both clocks and recompute the proposal digest *while the durable transaction is
open*, and `packages/store/AGENTS.md` fixes that sequence: load, then "while that transaction
remains open, let the gateway obtain new readings from both clocks and invoke guarded domain
consumption", then persist. The mechanism therefore has to survive an arbitrary caller-controlled
gap between reading the row and writing it. That gap is exactly the lost-update window.

**That window is open at this isolation level.**
[ADR-0089](0089-state-read-committed-rather-than-inherit-it.md) states `READ COMMITTED` and says so
in its own consequences: "a plain read-then-write across two statements can be overwritten by a
concurrent writer ... The approval-consumption transaction is the first thing that will need its own
answer, and it still has none."

**The lock order is already fixed.**
[ADR-0088](0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) settled it in advance:
"within one mission, the approval row is taken before the audit sequence row ... the approval
consumption `packages/store/AGENTS.md` describes introduces the second lock, so the rule makes the
order total before that record is written." That presupposes a row lock without selecting one.

**Measured on the pinned PostgreSQL 18.6 cluster on 2026-08-24.** Two consumers on one approval row,
each running the full load-decide-write sequence; the first holds its transaction open after taking
the row, the second is started and watched for 0.5 s before the first is released.

| Candidate | What the second consumer got | Final state |
| --- | --- | --- |
| `SELECT ... FOR UPDATE`, decide, conditional `UPDATE` | **Waited**, then the domain's own `ALREADY_CONSUMED` | `executed` |
| Plain `SELECT`, decide, conditional `UPDATE` | Waited at the write, then a bare "conditional update matched no row" -- **the domain had already said yes** | `executed` |
| `SELECT ... FOR UPDATE NOWAIT` | `LockNotAvailableError` immediately, without waiting | `executed` |
| `SELECT ... FOR UPDATE SKIP LOCKED` | **No row**, indistinguishable from an approval that does not exist | `executed` |

Every candidate produced one commit. They are not equivalent in what the *denial* is, and ADR-0006
is a statement about the denial.

## Decision

**Take the approval row with `SELECT ... FOR UPDATE`, hold it across the caller's decision, and write
with an `UPDATE` conditional on the row still being approved.**

- `load_for_update` selects the row **by proposal identifier alone** and locks it. It does not filter
  on the mission: ADR-0040 fixes a refusal order in which the record's state is judged before the
  candidate's mission, and a store-side mission predicate would turn "approval binds another mission"
  into "no such approval" and destroy that order.
- The lock is plain. Neither `NOWAIT` nor `SKIP LOCKED`: the first turns contention into a failure
  before the wait that ADR-0090 bounds, and the second returns no row, which a caller cannot tell
  from an approval that was never issued. A denial that reads as an absence is not a denial.
- The row is mapped into `StoredApproval`, whose `state` is the domain's own `ApprovalState`. A
  persisted string outside that closed set is refused rather than defaulted.
- The caller reads its clocks and calls `aerial_rescue_domain.approvals.consume`. Under
  `READ COMMITTED` a second consumer that waited on this lock re-reads the committed row, so it is
  handed `executed` and the domain refuses it with `ALREADY_CONSUMED`. **The denial is the
  protocol's, not the adapter's.**
- `persist_consumed` accepts only a record already in the `EXECUTED` state and issues
  `UPDATE approval SET state = 'executed' WHERE proposal_id = :proposal AND state = 'approved'`. A
  zero-row result is a refusal, never a silent success.
- `record` writes a decision and refuses `EXECUTED`. **`EXECUTED` is reachable through exactly one
  function, and that function's write is conditional on the row still being approved.** This is the
  shape `packages/store/AGENTS.md` demands when it says "Do not expose a generic repository update
  that turns a caller-supplied state into dispatch authority".
- Nothing here opens a transaction. As with
  [ADR-0088](0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md)'s append, the
  caller's transaction is what makes the guarantee, and this module refuses to own it.

The conditional `UPDATE` is defence in depth rather than the mechanism. With the lock taken first it
can never match zero rows through this member's own API; it is what stops a caller that reached
`persist_consumed` without `load_for_update`.

### The first revision's shape for this table

| Column | Type | Why |
| --- | --- | --- |
| `proposal_id` | `varchar(64)`, primary key | The identifier bound in operating-parameters, as in `rev_0001`. One approval per proposal is the single-use property expressed as a key |
| `mission_id` | `varchar(64)` | Judged by the domain, not by the query |
| `state` | `varchar(16)`, checked against the six protocol states | The closed set ADR-0006 names, projected for defence in depth. The domain remains the transition authority |
| `operator_identity` | `varchar(64)` | Non-secret, derived by the API from the validated bearer. Never a bearer |
| `issued_wall` | `varchar(24)` | The canonical millisecond spelling, stored as text for ADR-0027's reason: the value that was accepted is the value that is stored |
| `issued_monotonic_milliseconds` | `bigint` | A duration, not an instant. It has no meaning across a process restart, which is why an approval does not survive one |
| `time_to_live_milliseconds` | `bigint` | Injected per approval rather than read from the parameter row, so an approval carries the window it was issued under |
| `proposal_digest` | `varchar(64)` | SHA-256 as lowercase hexadecimal, recomputed and compared by the domain at consumption |

There is **no separate proposal table**. The consumption transaction needs the mission, the proposal
identifier, and the recorded digest, and the approval row carries all three; the proposal's own
parameters reach the gateway from the candidate action it is about to publish, which is the whole
point of recomputing the digest rather than trusting a stored one. A durable proposal record is owed
by the agent-proposal path and is not this decision.

## Consequences

- The single-use property becomes a property of PostgreSQL rather than of a code path. Two consumers
  of one approval commit once and deny once, and the denial names the protocol reason.
- **The row lock is held across a caller's clock reads and its call into the domain.** That hand-back
  is deliberate and was already named by ADR-0085 as the reason the store needs bounds at all. It is
  contained on both sides: a second consumer waits at most the 2 s lock wait before a typed refusal,
  and a wedged caller loses its transaction at the 15 s idle-in-transaction bound
  ([ADR-0090](0090-bound-the-lock-wait-below-the-statement-time.md)).
- **Two refusals now live on one path and must never be collapsed.** A lock refusal means "someone
  else is deciding this proposal right now" and is retryable; `ALREADY_CONSUMED` means "this approval
  is spent" and is terminal. A caller that treats the first as the second denies a consumption that
  never happened. Nothing in this repository tells them apart yet.
- Consumption serialises per proposal. Two operators deciding different proposals never contend, and
  two attempts on one proposal are exactly the case that must be serialised.
- `record` refusing `EXECUTED` closes one direct-write path and not the class. A writer with database
  credentials can still `UPDATE` the table by hand, which is catalogue case B24; **this record does
  not close it**, and the detection path it names remains to build.
- The conditional `UPDATE`'s zero-row branch is unreachable through this member's own API. It still
  has to be covered to earn the member's Tier 2 gate, and the test that covers it proves the guard
  rather than the race -- the race is live evidence, by ADR-0086, and always will be.
- A caller that loads an approval and then decides not to consume it holds the lock until it commits
  or rolls back. `FOR UPDATE` takes the lock at read time; that is the cost of putting the decision
  inside it.
- The monotonic reading is stored as a duration whose origin is the writing process. An approval
  therefore cannot be consumed after a gateway restart, which `packages/store/AGENTS.md` already
  requires -- "never rebase, repair, or extend its clock reading in storage; require a new approval".
  Storing it makes that consequence durable rather than incidental.

## Alternatives considered

- **Plain `SELECT`, then a conditional `UPDATE`.** Rejected on the measurement. It does yield one
  commit and one denial, but the second consumer's domain call *succeeds* before the store stops it:
  the gateway is told by the protocol that it consumed an approval, and only a rowcount contradicts
  that afterwards. ADR-0006 requires the second consumption to be a denial, not a success that is
  later discarded, and a gateway that has already computed its command from a "successful" consume is
  one refactor away from publishing it.
- **`SELECT ... FOR UPDATE NOWAIT`.** Rejected on the measurement: the second consumer is refused
  immediately with `LockNotAvailableError` and never waits. That converts every overlap, including one
  of a few milliseconds, into a failed request, and it makes the lock wait ADR-0090 just re-derived
  unreachable.
- **`SELECT ... FOR UPDATE SKIP LOCKED`.** Rejected on the measurement, and it is the dangerous one:
  the second consumer receives **no row**. "Already being consumed" and "no such approval" become the
  same observation, and the natural handling of an absent approval is a different refusal from the
  natural handling of a denied one.
- **A unique constraint on a separate consumption table.** Rejected: it moves the single-use property
  off the approval row, so the row's own `state` stops being authoritative and two places have to
  agree. It also produces an `IntegrityError` rather than a protocol refusal, which is the same defect
  as the conditional-update-only option one layer further away.
- **A PostgreSQL advisory lock keyed on the proposal identifier.** Rejected: an advisory lock is not
  attached to the row, so nothing stops a writer that does not take it, and ADR-0088's lock ordering
  is stated over rows. It would also need its own convention for deriving a 64-bit key from a
  64-character identifier, which is a hashing decision this repository does not need.
- **`SERIALIZABLE` for this transaction.** Rejected for the reason ADR-0089 measured on the audit
  append: the stricter level refuses the second transaction with a serialization failure instead of
  ordering it, so the denial would be a retry signal rather than a protocol outcome, and the caller
  would have to distinguish "retry me" from "denied" at exactly the point where confusing them is
  unsafe.
- **Process-local locking in the gateway.** Rejected by `packages/store/AGENTS.md` in as many words,
  and by ADR-0009's replay isolation: a second gateway process, a restart, or a replay harness would
  each hold a different lock.
- **Filtering the load on the mission as well as the proposal.** Rejected: it collapses the domain's
  `MISSION` refusal into a `NOT_FOUND`, and ADR-0040 fixes the order in which those refusals are
  reported.
