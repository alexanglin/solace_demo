# ADR-0006: Approvals bind to a proposal digest, are single-use, and expire

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

An approval keyed only on a mission identifier authorizes whatever escalation the mission happens to hold at dispatch time, not the one the operator actually read. Because the Mission Coordinator replans continuously and the Evidence Fusion agent republishes candidate locations, this is a genuine time-of-check-to-time-of-use defect: the action can change between the operator reading it and the system executing it.

## Decision

Adopt the approval protocol `REQUESTED -> APPROVED | REJECTED | EXPIRED | SUPERSEDED -> EXECUTED`.

The immutable proposal and decision record binds operator identity, issue and expiry times, and the exact mission ID, proposal ID, proposal digest/version, and action parameters. Only the command gateway may move an approved proposal to `EXECUTED`, and it must do so atomically together with idempotency and outbox persistence.

Approvals are **single-use**: a second consumption is a hard denial, explicitly not an idempotent success. This is the one place where the general idempotency rule for mutation endpoints does not apply, and both the API and the domain must say so.

## Consequences

- The gate becomes provable. Hash mismatch, expiry, supersession, and double-consumption each have a deterministic, testable refusal.
- A replan or a change to the contributing evidence set invalidates outstanding proposals rather than silently repointing them.
- The canonical serialization used to compute the digest becomes a contract in its own right — field order, encoding, float formatting, and exclusion of the digest field itself must be specified precisely enough to reimplement, or two components will disagree.
- Operators can be asked to re-approve after a replan, which is a deliberate usability cost paid for correctness.
- An expiry window must be chosen and justified; too short is an annoyance, too long reintroduces staleness.

## Alternatives considered

- **Approval keyed on mission only.** Rejected: the time-of-check-to-time-of-use defect described above.
- **Idempotent re-consumption of an approval.** Rejected: it would let a duplicate or replayed message cause a second dispatch, which is precisely the failure the gate exists to prevent.
- **A two-person rule.** Deferred: appropriate for a real deployment, out of scope for a single-operator local simulation, and recorded here as a documented non-goal rather than an oversight.
