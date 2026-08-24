# ADR-0092: Claim an idempotency key with one conflicting insert, and let the domain say what a repeat means

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0006](0006-proposal-bound-single-use-approvals.md) fixes an atomic set: approval consumption,
the idempotency claim, and outbox staging commit together or not at all.
[ADR-0091](0091-consume-an-approval-under-its-own-row-lock.md) built the first of the three. This
record builds the second.

[CONTRACTS.md](../CONTRACTS.md) states the durable requirement: "Mutation endpoints require an
idempotency key, and every idempotency record stores a hash of the canonicalized request body so a
key replayed with a different body is refused rather than treated as a repeat." It also states the
exception: "approvals are single-use, so a second consumption is a hard denial and never an
idempotent success."

**The decision itself already exists and is not this record's to make.**
`packages/domain/src/aerial_rescue_domain/idempotency.py` holds `idempotency_decision(kind, known=)`
and its three outcomes -- execute, return the prior result, deny -- and `packages/store/AGENTS.md`
forbids copying a decision table "into SQL, an ORM event, a trigger, or a repository branch". What
is undecided is the durable half: how a key is claimed, what is compared, and what happens when the
comparison fails.

**This is a different concurrency problem from ADR-0091's, and needs a different answer.** There, the
caller decides in the middle -- ADR-0040 requires the clock reads and the domain call to happen
inside the transaction -- so the row lock has to be *held* across a gap this repository does not
control. A claim has no such gap: what to write is known before the statement runs. A single
conflicting insert is therefore sufficient, and it is the primitive
[ADR-0088](0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) already uses for the
audit ordinal.

**Measured on the pinned PostgreSQL 18.6 cluster on 2026-08-24**, with
`INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING idempotency_key`:

| Case | Observed |
| --- | --- |
| Two claimants of one key, the first holding its transaction open | First claimed; the second **waited** on the conflicting key and then saw it known. Exactly one claim |
| A claim whose transaction is abandoned before commit | The key is claimable again; the next claim takes it |
| The same key presented with a different body digest | Observable as a repeat whose stored digest differs |

The second row is the one that makes the whole shape safe: a claim taken and then rolled back does
not burn its key, so an operation that failed before commit can be retried under the identifier the
client already has.

## Decision

**One statement claims, and the domain says what a repeat means.**

- The claim is `INSERT INTO idempotency_claim ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING
  idempotency_key`. A returned key is a first claim; no returned key is a repeat. There is no
  read-then-write, so there is no lost-update window and no lock for a caller to hold.
- On a repeat, the stored row is read and **compared, never recomputed**. The body digest arrives
  from the caller, computed under `aerial_rescue_contracts.digest.Context.IDEMPOTENCY_BODY`. This
  member holds no canonicalizer and creates none.
- A stored digest that differs is `BODY_MISMATCH`, and a stored kind that differs is `KIND_MISMATCH`.
  Both are refusals rather than decisions: a key replayed with different content is not a repeat of
  anything, and answering it with a prior result would return one operation's answer for another's
  request.
- When kind and digest agree, the outcome is whatever
  `aerial_rescue_domain.idempotency.idempotency_decision` returns for that kind. This member calls
  it; it does not reimplement it, and it has no branch on `IdempotencyKind` of its own.
- **A known command whose result is not yet recorded is `RESULT_NOT_RECORDED`, a refusal.** The
  claim and the result are written in different transactions -- the result is not known until the
  command has been dispatched and answered -- so the window is real. Returning
  `RETURN_PRIOR_RESULT` with nothing to return would be a lie, and dispatching again would be the
  duplicate the key exists to prevent. A known *approval consumption* is denied without reaching
  this question, because a denial needs no result.
- `record_result` writes the result once, conditional on the row still having none, and a write that
  matches no row is `RESULT_ALREADY_RECORDED`. As in ADR-0091, the guard is in the `WHERE` clause
  rather than in a preceding read.

### The revision's shape

| Column | Type | Why |
| --- | --- | --- |
| `idempotency_key` | `varchar(64)`, primary key | The `id` of the envelope, which CONTRACTS.md names as the idempotency key. The primary key *is* the claim |
| `kind` | `varchar(24)`, checked against the two kinds | The closed set the domain names. A key claimed for one operation cannot be answered as the other |
| `body_digest` | `varchar(64)` | SHA-256 of the canonical body, as lowercase hexadecimal, exactly as the caller computed it |
| `mission_id` | `varchar(64)` | Which mission the claim belongs to, for the reset scope that is still undecided and for the audit trail |
| `result` | `bytea`, nullable | The canonical bytes of the prior result. Null means claimed and unanswered, which is a distinct outcome rather than an empty one |
| `claimed_at` | `varchar(24)` | The canonical millisecond spelling, for ADR-0027's reason |

## Consequences

- A duplicated request is answered from the durable record rather than from process-local receipts.
  `TECH_DEBT.md` records today's claim as "at-least-once with duplicates possible across a restart";
  this is the durable half of what will clear it, and it does not clear it alone.
- **The idempotency claim is not what makes an approval single-use, and must never be described as
  though it were.** A repeat consumption can arrive under a *fresh* key, in which case this table has
  nothing to say and the approval row refuses it (ADR-0091). The `DENY` outcome here is a second
  line of defence for the case where the client retries with the same key. `packages/store/AGENTS.md`
  already requires both behaviours -- "a known approval consumption is a hard denial, with the same
  or a fresh idempotency key" -- and only the pair satisfies it.
- **A claim does not survive its own rollback**, which is what makes it safe to take before the work
  and what makes it useless as a record that work already happened. Anything with an external effect
  has to be recoverable on its own terms; that is the outbox's job, staged in the same transaction so
  it rolls back with the claim.
- `RESULT_NOT_RECORDED` is a real outcome a caller has to handle, and there is no code to handle it
  yet. A client that retries during the window gets a refusal rather than an answer, which is correct
  and is also worse service than an answer would be. Narrowing that window means recording the result
  sooner, which is a dispatch decision rather than a durable one.
- Two refusals here are indistinguishable to a well-behaved client and highly informative about a
  misbehaving one. `BODY_MISMATCH` on a key a client believes it owns means two different requests
  are travelling under one identifier, which is a defect in the client rather than a race.
- The reset scope for this table is still undecided, as it is for every table here. `mission_id` is
  carried so that a mission-scoped reset is expressible when that decision is made; carrying it is
  not the decision.
- Nothing here is atomic with anything yet. The set ADR-0006 fixes is complete only when the outbox
  joins it, and until then this is a repository with no transaction spanning it.

## Alternatives considered

- **`SELECT` the key, then `INSERT` if absent.** Rejected: it is the unprotected check-then-write
  `packages/store/AGENTS.md` forbids, and under the `READ COMMITTED` that
  [ADR-0089](0089-state-read-committed-rather-than-inherit-it.md) states, two callers can both find
  the key absent.
- **`ON CONFLICT DO UPDATE`, refreshing the row on a repeat.** Rejected: the record is evidence that
  an operation was claimed, and rewriting it on every repeat destroys the evidence. It would also
  make a replay with a different body overwrite the digest that was supposed to refuse it.
- **A unique constraint and catching `IntegrityError`.** Rejected: the failed insert aborts the
  transaction, so the caller would have to open a second one to read the stored row -- outside the
  atomic set ADR-0006 requires. `DO NOTHING` leaves the transaction usable.
- **Taking the row with `SELECT ... FOR UPDATE` as ADR-0091 does.** Rejected: that mechanism exists
  to hold a lock across a caller's decision, and there is no decision here to hold it across. Using
  it anyway would serialise claims on one key for the length of a caller's transaction and buy
  nothing.
- **Storing the request body rather than its digest.** Rejected: it would put an arbitrary caller
  payload in the durable store for comparison purposes only, and CONTRACTS.md asks for the hash.
- **Answering `RETURN_PRIOR_RESULT` with a null result while the first request is in flight.**
  Rejected: it presents "I do not know yet" as "here is the answer". The refusal is the honest
  outcome even though it is the less convenient one.
- **Letting this table deny repeat approval consumptions on its own.** Rejected as insufficient
  rather than wrong: a fresh key evades it entirely, which is exactly why ADR-0091 puts the authority
  on the approval row.
