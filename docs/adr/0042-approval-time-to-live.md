# ADR-0042: Approval time to live of 60 seconds

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0006](0006-proposal-bound-single-use-approvals.md) requires an expiry window "chosen and
justified" and warns that too short is an annoyance while too long reintroduces staleness.
[operating-parameters.md](../operating-parameters.md) lists the approval time-to-live as open.
[ADR-0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) fixes where the window
runs: from the operator's decision reading to the gateway's consumption, on both clocks.
`packages/domain` takes the value as an injected parameter with no default, so this record changes
no code when accepted; it fills the operating-parameters row. The committed service-level targets
bound the path between the two readings: restart recovery within 30 seconds with a recovery point
objective of zero, backlog recovery of 500 critical messages within 10 seconds after reconnect, and
a connected command path of at most 2 seconds at the 95th percentile; the agent replan target is
30 seconds.

## Decision

Proposed: sixty seconds, injected at the composition root as `timedelta(seconds=60)`.

The documented worst case between decision and consumption — a gateway restart of 30 seconds
followed by a backlog drain of 10 seconds and the command path of 2 seconds — is 42 seconds,
leaving 18 seconds of margin. Sixty seconds also bounds the staleness of an unexecuted human
decision to two replan windows, while a replan supersedes outstanding proposals explicitly, so the
window guards a stalled path rather than a changed plan.

## Consequences

- Nothing changes in code on acceptance; the operating-parameters row moves from open to
  60 seconds and this record moves to Accepted.
- A gateway outage longer than a minute during an open approval forces re-approval, the usability
  cost ADR-0006 accepts for correctness.
- The dashboard must show the expiry instant so the operator sees the window running.
- Negative: the value is a judgement derived from targets, not a measurement; Phase 0 resource
  measurements may move it, and moving it is a superseding record because the parameter gates
  safety behaviour.

## Alternatives considered

- **30 seconds.** Rejected: it equals the restart-recovery target with no margin, the same
  zero-margin pattern the offline-detection target already carries.
- **120 seconds.** Rejected: it doubles the staleness bound with no documented latency that needs
  it.
- **The mission's length.** Rejected: no staleness bound at all, which is what ADR-0006 rejected.
- **Measuring the window from the proposal's presentation rather than from the decision.**
  Rejected: the operator's reading time is not the staleness the gate guards, and supersession
  covers a replan while the operator reads.
