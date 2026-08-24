# ADR-0094: Persist only the UI slice lifecycle, idempotency, and audit facts

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The UI-first slice needs durable start, reset, recovery, audit order, and exact idempotent responses.
Neither process memory nor a generalized dispatch framework is an acceptable authority. The current
`0001_audit_log` revision is append-only groundwork and must remain unchanged, while reset retention and
the transaction that coordinates a fleet handoff are still undecided.

## Decision

Add one new Alembic revision after `0001_audit_log`; never edit that revision. Add only:

- live missions with a nullable predecessor link and lifecycle state;
- live and replay runs plus one current-run pointer;
- idempotency operation, canonical request digest, exact response bytes and status, and stable run ID;
- one pending fleet operation for the current live run; and
- broker-event source and event identity deduplication linked to the audit record and ordinal.

Use PostgreSQL `READ COMMITTED` with explicit row locks. A unique idempotency key is locked before the
singleton current-run pointer, followed by the mission/run row and then the existing per-mission audit
sequence row. Same-key and same-body requests replay the stored status and exact response bytes. The same
key with a different operation or canonical body digest refuses without an effect.

Start persists the stable mission, run, pending operation, audit fact, and provisional exact response in
one transaction before calling the fleet worker with the same run ID. On acceptance it records
`SEARCHING` and the final stored `202` response. A retry of a pending operation queries that same fleet
run and reconciles it; it never launches a second run under a new identifier. On service startup the one
current pending operation is reconciled. An unknown lost fleet run becomes `ABORTED` rather than
pretending to resume.

Reset first requests bounded cancellation of the current live run and waits at most fifteen seconds. If
cancellation cannot be established it returns a typed failure and changes neither current pointer nor
mission state. On success it retains all history, aborts only a nonterminal old mission, and creates a
fresh `PLANNED` successor linked to the predecessor. Reset deletes no mission, audit, idempotency, run,
approval, outbox, ledger, or provenance row.

Replay start and reset create fresh replay sessions at cursor zero. They create or mutate no operational
mission and require no generalized dispatcher, lease, outbox, or crash-resumption engine.

## Consequences

- The browser can trust that a reset preserves history and that a duplicate mutation has one durable
  answer across a process restart.
- The synchronous fleet handoff has a narrow reconciliation state instead of a reusable workflow engine.
- One current pointer serializes start and reset, which is appropriate for the single-operator local
  simulation.
- A fifteen-second cancellation failure is visible and recoverable; reset never lies about success.
- Full dispatch recovery, approval atomicity, paid-call ledger behavior, and general multi-run scheduling
  remain follow-on work.

## Alternatives considered

- **Keep idempotency and current run in process memory.** Rejected because restart would permit duplicate
  effects and lose the operator's history.
- **Delete the old mission on reset.** Rejected because ADR-0072 prohibits rewind and ADR-0088 makes the
  audit history permanent.
- **Build a generalized outbox, lease, or workflow engine.** Rejected because this slice has one current
  operation and no second consumer for that abstraction.
- **Claim an unknown fleet run resumed.** Rejected because there is no evidence that its worker state
  survived.
