# ADR-0227: Give the operator a rescue-escalation control

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Alex Anglin

## Context

`POST /api/v1/missions/{missionId}/commands` has been implemented since the application data plane
landed, and both its schemas are committed and code-generated. No browser code called it. Approving
a proposal recorded a durable, digest-bound, single-use approval that nothing consumed, so
`DRONE_COMMAND` and `DRONE_COMMAND_RESULT` never occurred in a browser-driven run and the project's
headline safety claim — a rescue escalation stays blocked until a human approves it, and only the
command gateway may publish it — was demonstrable only by `curl`.

## Decision

**The dashboard carries a rescue-escalation control, rendered only once an approving
`operatorApproval` for the current proposal is in the timeline.** It is a sibling of the decision
panel rather than more weight inside it.

`operator/rescue-escalation.ts` builds the request from the recorded approval and refuses
`APPROVAL_REJECTED`, `BINDING_MISMATCH`, or `DIGEST_REFUSED`. It recomputes `evidenceDecisionDigest`
from the validated evidence projection under the `evidence` digest context rather than reading the
approval's copy, because [ADR-0174](0174-recompute-evidence-digests-at-the-dashboard-boundary.md)
forbids trusting a server-supplied digest and the approval's is server-supplied.

`operator/mutation-transport.ts` carries what both submitters share: one pending guard, a
lowercase UUIDv4 idempotency key, Ajv validation of request and response against the committed
schemas, and one closed refusal vocabulary. `operator/command-client.ts` and
`operator/mutation-client.ts` are the two routes over it. The control is constructed only in the protected
live flow of [ADR-0172](0172-complete-the-protected-dashboard-operator-flow.md); replay renders no
enabled action.

`currentProposalBinding` now matches a proposal, its evidence decision and its approval **by
identity rather than by arrival order**. The three families reach the recorder on separate queues,
so capture order is not causal order: observed live on 2026-09-01, the evidence decision landed at
audit ordinal 329 and the proposal it scores at 331. The previous fold reset its evidence whenever
a proposal followed, so the approval gate never appeared at all.

## Consequences

- The chain closes in the browser. Measured live on 2026-09-01: approve, then escalate, then
  `command_outbox` holding one `escalate-rescue` row for `drone-sim-07` in state `confirmed`.
- The browser can now cause an executable command to be published. It remains a request: the
  command gateway consumes the single-use approval and is still the only publisher, and the browser
  supplies no authority the approval did not already carry.
- The dashboard gains its first browser acceptance coverage of the approval boundary; there was
  none before, in either the fixture or the production driver.
- The proposal decision and the rescue escalation are the two consumers the repository's own rule
  waits for, so their shared transport is factored into `operator/mutation-transport.ts`,
  parameterised by route, request schema, response schema, and the response-to-request check. The
  duplication gate is what surfaced this: mirroring the decision submitter took the owned tree from
  under its three-percent ceiling to 3.1%.

## Alternatives considered

- **Extend the decision panel.** Rejected: it is already 306 lines and the two surfaces have
  different preconditions and different failure vocabularies.
- **Escalate automatically once an approval is recorded.** Rejected: it would make the approval the
  action rather than the authorisation, and remove the operator's last look at what is dispatched.
- **Reuse the proposal-decision submitter unchanged.** Rejected: it is bound to the decisions route
  and its own response schema. The shared transport underneath it is extracted instead.
