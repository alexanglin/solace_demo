# ADR-0226: Make the recorder the single writer of audit rows

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Alex Anglin
- **Amends:** [ADR-0205](0205-project-the-committed-envelope-at-the-dashboard-store-adapter.md), as to which component writes the evidence service's audit row

## Context

The first live run to produce an agent proposal killed the dashboard. The SSE stream answered
`DEPENDENCY_UNAVAILABLE` for the rest of the epoch and kept answering it, so the operator saw
nothing after the proposal landed.

The evidence service's `_persist` did two things with the same audit record: it appended it
directly through `append_audit`, and it staged the identical audit event to the application
outbox. The recorder then captured that published event and appended it again. The direct append
takes an ordinal from the shared sequence but writes no `dashboard_broker_event` link, and the
dashboard's projection reads `audit_record ⋈ dashboard_broker_event`. The unlinked row is therefore
invisible to that join while still consuming an ordinal, and the reducer's fold requires
`ordinal == current + 1`, so it refused the next event as `ORDINAL_GAP`.

Reproduced on both successful runs of 2026-09-01: exactly one unlinked row each, ordinal 329 and
331, both `aerial-rescue.v1.audit.evidence-decision`. No other producer left one. The command
gateway's proposal-normalisation path stages to the outbox and does not self-append, so the
evidence service was the only component doing both.

This was invisible for as long as it was, because no agent proposal had ever reached the evidence
service in a live run.

## Decision

**The recorder is the only component that writes `audit_record`.** The evidence service stages its
audit event to the application outbox and no longer appends the row itself. `DecisionArtifacts`
loses its `audit_record` member, and the `append_audit` port and its store adapter are removed with
their only caller.

## Consequences

- The dashboard's contiguous-ordinal fold holds, because every ordinal the sequence issues now has
  a broker-event link. The stream survives a proposal, which is what the approval gate needs.
- The evidence service is consistent with the command gateway: one write path, through the outbox.
- **The audit row is no longer written in the decision's own transaction.** It now depends on the
  outbox publishing and the recorder capturing, so a recorder outage delays the audit record rather
  than losing the decision — the decision, its items and its outbox row still commit atomically.
  This is the durability property the command gateway already had; it is weaker than what the
  evidence service had alone, and it is the price of a projection the dashboard can read.
- ADR-0205's kind-binding test for this service goes with the artifact it guarded. The guarantee
  itself is unaffected: the recorder derives `kind` from the envelope it captured.

## Alternatives considered

- **Let the dashboard tolerate an unlinked ordinal.** Rejected: it softens a deliberate contiguity
  check that exists to detect a genuinely missing event, to accommodate a row that should not have
  been written twice.
- **Have the recorder link the producer's row.** Rejected: the recorder never sees that row; it is
  written in another service's transaction.
- **Stop staging the audit event and keep the direct append.** Rejected: the row would stay
  unlinked and therefore permanently invisible to the operator.
