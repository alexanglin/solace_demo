# ADR-0093: Give the command outbox three states, a counted bound, and an overflow that writes nothing

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0006](0006-proposal-bound-single-use-approvals.md) fixes an atomic set of three: approval
consumption, the idempotency claim, and outbox staging.
[ADR-0091](0091-consume-an-approval-under-its-own-row-lock.md) and
[ADR-0092](0092-claim-an-idempotency-key-with-one-conflicting-insert.md) built the first two. Until
this record there is no third, so nothing in the set is atomic with anything.

`packages/store/AGENTS.md` section 5 leaves four things open at once and says so: "Define the durable
outbox state machine before naming concrete row states", and "The only named outbox size bound in
`docs/operating-parameters.md` is per-drone. The adjacent generic continuity-breach overflow rule does
not say explicitly whether it also governs the central command outbox; resolve that scope together
with the central bound, overflow-and-audit transaction, and the claim and reconciliation state
machine."

It also fixes what the states must and must not mean: "Persist a durable confirmation fact only after
the broker adapter reports publisher confirmation. That confirmation proves broker acceptance, not
drone delivery, consumer acknowledgement, or command completion"; "Persist a missing or ambiguous
outcome as a distinct reconciliation-needed state; never promote it to confirmed without broker
evidence"; and "Keep ADR-0074 command progress and its send count distinct from outbox publication
state ... do not let an outbox row label invent a lifecycle transition or treat publisher confirmation
as `ACKNOWLEDGE`, `SUCCEED`, or `FAIL`."

## Decision

### The state machine is a domain machine, not a column vocabulary

Three states and four edges, deny-by-default, in `packages/domain/src/aerial_rescue_domain/outbox.py`
alongside the five machines [ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) already names.
The store persists what this machine returns and never derives a transition of its own.

| From | Event | To |
| --- | --- | --- |
| `STAGED` | `CONFIRM` | `CONFIRMED` |
| `STAGED` | `AMBIGUOUS` | `RECONCILIATION_NEEDED` |
| `RECONCILIATION_NEEDED` | `CONFIRM` | `CONFIRMED` |
| `RECONCILIATION_NEEDED` | `AMBIGUOUS` | `RECONCILIATION_NEEDED` |

`CONFIRMED` is terminal, and terminality is derived from the table rather than declared beside it, as
[ADR-0072](0072-mission-lifecycle-states.md) does for missions.

**A refused publication is not a state.** When the broker refuses a publish outright, the record stays
`STAGED` and remains recoverable for bounded retry under its original command identifier. Only an
*ambiguous* outcome -- no answer, or an answer that does not establish acceptance -- moves it, because
only that leaves the question of whether the broker took it unanswered.

**`CONFIRM` means the broker accepted the bytes and nothing more.** It is not ADR-0074's
`ACKNOWLEDGE`, `SUCCEED`, or `FAIL`, which are what a drone did with the command and are persisted
separately. Two records about one command is the point, not an accident.

### The bound is 500 records, counted, and is scoped to the central outbox

The central command outbox holds at most **500 unconfirmed records**. The number is the workload
[ADR-0084](0084-give-backlog-recovery-an-instrument.md)'s instrument uses and
`release-evidence/phase-2/backlog-recovery-first-run.md` measured draining in 7.141 s: 500 commands
across the 23-drone fleet. It is the largest burst this repository has ever observed itself handling,
which makes it the only honest anchor available. It is derived, not modelled, and a measurement of
real gateway demand supersedes this record rather than editing it.

**There is no separate byte ceiling for this table, and its absence is a decision rather than an
omission.** A staged record is one command envelope, and every member of one is already bounded by the
topic and envelope rows in [operating-parameters.md](../operating-parameters.md) over
[ADR-0027](0027-integer-only-canonical-serialization.md)'s integer-only profile. A byte row here would
restate those bounds and then drift from them. The **per-drone** outbox keeps its open records-and-bytes
row: an edge outbox holds telemetry backlogs rather than commands, and that row stays Phase 6's.

### The overflow writes nothing, and its audit record is the caller's

Staging is one conditional insert whose row count is evaluated inside the statement, in the shape
ADR-0091 and ADR-0092 both use: the guard is in the statement rather than in a preceding read, because
`READ COMMITTED` ([ADR-0089](0089-state-read-committed-rather-than-inherit-it.md)) makes a
check-then-write losable. When the count is at the bound the insert writes no row, and the absent
`RETURNING` value is the refusal.

**The continuity-breach audit record is appended by the caller, in its own transaction, after the
refusal has rolled the set back.** Appending it inside the staging transaction was the obvious design
and is rejected: `packages/store/AGENTS.md` states that "no accepted decision currently adds the audit
append to this atomic set. Do not silently enlarge or shrink it", and an audit record written inside a
transaction that must roll back would be rolled back with it. The refusal loses nothing, because the
write did not happen; the record of the refusal is what can be lost, and losing it is the smaller harm.

## Consequences

- ADR-0006's set becomes expressible. A caller can consume an approval, claim its key, and stage its
  command in one transaction that commits together or not at all. Nothing in this repository does that
  yet; this record makes it possible rather than actual.
- **The bound can be exceeded, by a stated amount.** Under `READ COMMITTED` two sessions staging at
  once each count the other's uncommitted row as absent, so `N` concurrent stagers can overshoot by
  `N - 1`. `N` is bounded by the pool: five sessions per process with no overflow
  ([ADR-0090](0090-bound-the-lock-wait-below-the-statement-time.md)). The effective ceiling is
  therefore 504 per process, and the bound is a continuity-breach detector rather than a hard limit.
  Making it hard would need a lock held across every staging transaction, which would serialise every
  command in the system on one row.
- **A continuity breach can be refused and go unaudited**, if the process dies between the rollback and
  the audit append. That is the cost of not enlarging ADR-0006's set, and it is stated here so that a
  later record can reverse it deliberately rather than discover it.
- `RECONCILIATION_NEEDED` has no reconciler. The state exists so an ambiguous publication has somewhere
  to be recorded rather than being guessed at; what reads it, and how, is owed.
- Two durable records now describe one command -- its outbox publication state and its ADR-0074
  dispatch progress -- and a reader that conflates them will report a command as delivered when the
  broker merely accepted it. Keeping them apart is a standing obligation on every consumer, not a
  property this record enforces.
- A fifth Tier 1 machine, sixth counting the evidence score, means 100% statements and branches and a
  mutation score on a module whose whole content is a four-row table. That cost is the point: the table
  is executable policy.
- The number 500 arrives from a *test workload*. If the gateway's real demand is smaller, the bound is
  loose and will not detect a breach until long after one matters; if it is larger, staging refuses
  legitimate work. Neither is knowable before a gateway exists.

## Alternatives considered

- **Outbox states as a column vocabulary in `packages/store`.** Rejected by `packages/store/AGENTS.md`
  in as many words: an outbox row label may not invent a lifecycle transition. The five existing
  machines all live in the domain, and a sixth that did not would be the exception that explains the
  next one away.
- **A `FAILED` state for a refused publication.** Rejected: a refusal is answered evidence that the
  broker did *not* take the command, so the record is simply still staged and still retryable. A state
  for it would invite a reader to treat "we know it was not sent" as a terminal outcome.
- **Promoting an ambiguous outcome to `CONFIRMED` after a timeout.** Rejected: it is exactly the
  "never promote it to confirmed without broker evidence" the guide forbids, and it would make the
  durable confirmation fact a guess.
- **A hard bound, enforced with a lock held across staging.** Rejected: every command in the system
  would serialise on one counter row, and the bound exists to notice a continuity breach rather than to
  be exact to the record.
- **A byte ceiling alongside the record count.** Rejected as a restatement: the envelope's own bounded
  members already cap a record, and a second number would drift from the first.
- **Counting only `STAGED` rows rather than every unconfirmed one.** Rejected: a backlog of records
  awaiting reconciliation occupies the same spool and is exactly the continuity breach the bound is
  meant to notice.
- **Appending the continuity-breach audit record inside the staging transaction.** Rejected because it
  enlarges ADR-0006's atomic set, which the store guide forbids doing silently, and because the append
  would roll back with the refusal it exists to record. Reversing this needs a record that enlarges the
  set deliberately.
- **Deriving the bound from the 23-drone fleet and the five-send budget.** Rejected: a retry
  re-publishes an existing row rather than staging a new one, so 23 x 5 counts something the table does
  not hold. It would look derived while being wrong.
