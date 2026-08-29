# ADR-0174: Recompute evidence digests at the dashboard boundary

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0172

## Context

ADR-0148 deliberately removes `evidenceDecisionDigest` from the normalized dashboard evidence-decision
projection while retaining every member the digest covers. The digest is an integrity member of the
canonical application payload, not an independently rendered fact. The same ADR requires the exact
digest in a proposal-decision request so the approval binds the selected persisted evidence decision.

ADR-0172 incorrectly says the browser never derives a bound member. No separate proposal-detail route or
dashboard state member supplies the removed digest, and adding one would weaken ADR-0148's closed
projection. A browser that copied a server-supplied digest would also miss an independent check that the
displayed evidence bytes and the approval binding agree.

## Decision

Retain ADR-0148's projection exactly: an evidence-decision dashboard event contains the complete
canonical evidence-decision payload with only `missionId` and `evidenceDecisionDigest` removed. Do not
add a hidden digest, alternate wire member, bootstrap value, or proposal-detail endpoint.

After schema validation and before enabling an operational decision control, the browser reconstructs
the digest-covered document by restoring only the event's validated `mission` as `missionId`. It computes
`evidenceDecisionDigest` with the shared canonical encoder, SHA-256, and the domain-separated `evidence`
context. That algorithm is the same cross-language oracle used by the contracts package; it accepts no
presentation state, playback state, operator input, or server-provided expected digest.

The browser binds the projected proposal and evidence decision only when all repeated proposal identity,
digest, and version members agree. Approval is enabled only for an operational, current,
`contributing` decision in the `corroborated` band whose contributors contain at least two distinct
live source identifiers. Rejection may record an operator's refusal of any current proposal/evidence
selection, but it carries the same exact binding. These browser checks are explanatory defense in depth;
they never replace the server and command-gateway authorization rules.

The proposal-decision request carries the recomputed evidence digest. The dashboard API independently
loads the persisted proposal and evidence decision, recomputes their canonical digests, compares them in
constant time with the request, validates every proposed action member, and commits or refuses the
operator event. It never treats the browser's calculation as authority. A digest mismatch, incomplete
projection, unsupported evidence outcome, stale selection, or browser cryptographic failure disables the
decision controls and emits no mutation.

ADR-0172's statement that the browser does not derive a bound member is superseded only for this exact
canonical digest calculation. The browser still cannot derive operator identity, proposal content,
evidence content, action coordinates, drone identity, approval identity, event identity, issue time, or
expiry. Replay may recompute digests to validate and display recorded facts, but it still renders no
enabled operational action and constructs no writer.

## Consequences

- The decision request can bind the exact evidence bytes without enlarging the dashboard event schema.
- The displayed evidence and the submitted digest gain an independent browser-side consistency check,
  while PostgreSQL remains authority and the server repeats the calculation.
- Web Crypto failure is fail-closed and can make a valid proposal temporarily unavailable for decision;
  it cannot authorize a command.
- Python and TypeScript evidence-digest parity becomes blocking contract evidence for the operator flow.
- The digest calculation is the only derived request member; every human-meaningful action member remains
  a validated, visibly presented server fact.

## Alternatives considered

- **Expose `evidenceDecisionDigest` in the dashboard projection.** Rejected because it would weaken the
  closed ADR-0148 projection and let a client echo an expected answer without proving displayed bytes.
- **Add a proposal-detail HTTP route.** Rejected because the existing event contains the complete
  digest-covered evidence and another authority-bearing surface is unnecessary.
- **Let the server fill in the omitted digest after receiving the request.** Rejected because the public
  contract explicitly binds the operator's decision to the digest they were shown and submitted.
- **Permit approval of supported or manual-review evidence.** Rejected because rescue escalation requires
  the exact corroborated, two-live-source gate; a browser affordance must not imply weaker authority.
