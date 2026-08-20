# ADR-0040: Consume approvals by recomputing the proposal digest and reading two clocks

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0006](0006-proposal-bound-single-use-approvals.md) binds an approval to the exact mission ID,
proposal ID, proposal digest and version, and action parameters; makes it single-use with a hard
denial on repeat that "both the API and the domain must say so"; and requires an expiry window.
[CONTRACTS.md](../CONTRACTS.md) restates the consumption predicate. The
[approval-bypass catalogue](../security/approval-bypass-catalogue.md) adds two clock rules: B08,
expiry is evaluated inside the consuming transaction, and B09, consumption with the system clock
moved backwards is refused because expiry does not rely on a mutable wall clock alone. No clock
port is defined anywhere, and `packages/domain` declared no dependencies although the only digest
implementation is `aerial_rescue_contracts.digest`, where the canonicalization version sits inside
the hashed bytes ([ADR-0027](0027-integer-only-canonical-serialization.md)) and, per catalogue B16,
so does the score version. The approval protocol now lands as pure Tier 1 code, so the domain has
to decide what it compares, what it reads, and what it refuses.

## Decision

- `packages/domain` depends on `aerial-rescue-contracts`. At approval the domain computes the
  proposal digest over the action parameters with `digest.digest` in the `proposal-digest` context
  and records it; at consumption it recomputes the digest over the parameters the gateway is about
  to publish and compares the two with `digest.matches`. It never accepts a caller-supplied digest
  string as proof. No separate version field is compared: the canonicalization version and the
  score version are inside the digested parameters, so a change to either is a digest mismatch.
- A clock reading is a record of two values the caller supplies per call: an aware UTC wall-clock
  instant and a monotonic elapsed duration. The approval record stores the reading at issue and
  the injected time to live, which has no default. Consumption is refused as `CLOCK_REGRESSION`
  when either value of the new reading is earlier than at issue, and as `EXPIRED` when either
  delta has reached the time to live. An equal reading is not a regression; a delta exactly at the
  window is expired.
- The protocol's legal transitions are the seven pairs [SAFETY.md](../SAFETY.md) lists: `REQUESTED`
  to `APPROVED`, `REJECTED`, `EXPIRED`, or `SUPERSEDED`; `APPROVED` to `EXECUTED`, `EXPIRED`, or
  `SUPERSEDED`. `EXECUTED` is reachable only from `APPROVED` and only through consumption, which
  refuses in a fixed order — state, mission, proposal, parameters and digest, clock — and names a
  repeated consumption `ALREADY_CONSUMED`, never a success.
- The composition root supplies readings: the operating system's monotonic clock in a live run and
  the scenario tick in simulation and replay.

## Consequences

- B12, B15, B16, and the server half of B34 are pure unit tests: altering any parameter, the
  mission, the proposal, or the score version is a named refusal with no store or transport
  involved.
- A wall clock moved backwards cannot revive an approval, because the monotonic half still counts;
  moved forwards, it expires the approval early, which is the safe direction. A time step
  backwards during an open approval denies a fresh consumption until the operator re-approves.
- A gateway restart gives the monotonic half a new origin, so a reading from the new process
  either regresses, and is refused, or over-counts, and expires early; either way the operator
  re-approves, the cost ADR-0006 already accepts. No approval survives a restart.
- `packages/domain` gains one workspace dependency and remains free of input, output, clocks, and
  random sources; the import gates are unchanged.
- The domain produces no `REJECTED` record type; a rejection is a transition the store's audit
  row carries.
- Negative: correctness depends on the port supplying a genuinely monotonic second reading, which
  the domain cannot verify; the record carries a clock value the operator never sees, because the
  displayed expiry is the wall instant plus the time to live; and a caller that passes a stale
  reading by mistake sees a refusal rather than an error.

## Alternatives considered

- **Compare two caller-supplied digest strings.** Rejected: the domain would trust the caller's
  hashing for the safety predicate, and B12 — an altered parameter behind the recorded digest —
  would be unprovable in a unit test.
- **Compare the action parameters as well as the digest, or a separate version field as well.**
  Rejected: the digest already covers both, so the extra comparison is unreachable and survives
  as an unkillable mutant.
- **Wall clock only.** Rejected by B09.
- **Monotonic clock only.** Rejected: it cannot be displayed or audited as an instant and is
  undefined across restarts.
- **A clock-origin identifier in the reading.** Rejected: both failure directions after a restart
  are already fail-closed, so the field adds a concept without adding safety.
- **A generic state-advance function the gateway could call with `EXECUTE`.** Rejected: it would
  reach `EXECUTED` without the binding and clock checks.
