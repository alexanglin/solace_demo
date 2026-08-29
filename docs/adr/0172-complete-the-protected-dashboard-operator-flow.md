# ADR-0172: Complete the protected dashboard operator flow

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0096, ADR-0097, and ADR-0098

## Context

ADR-0098 deliberately delivered a UI-first slice before evidence, approval, and command authority
existed. It therefore prohibited controls that would have implied an unimplemented effect. ADR-0097
removed the placeholder approval route for the same reason, and ADR-0096 excluded the corresponding
services from its initial `mission-control` profile. Those restrictions were truthful for that slice,
but they are not a permanent prohibition.

ADR-0146 now defines durable application processing and the exact approval transaction, and ADR-0148
closes the operator-command and proposal-decision documents. The application data plane can therefore
complete the operator workflow without inventing browser authority or letting a model actuate. Leaving
the controls absent after the durable path exists would make the dashboard conceal the only authorized
route to rescue escalation.

The existing public mode spelling is `degradedLive` because the reference fleet is simulated. Calling
that execution an operational real-fleet `live` mode would be a false claim. At the same time, a
connected simulation and a simulation whose broker or consumer is recovering need visibly different
operational states. Isolated replay remains a separate composition and must not acquire a writer merely
because it displays recorded proposals, decisions, commands, or results.

## Decision

Complete the follow-on protected operator flow in the dashboard. This record changes only the initial
slice deferrals in ADR-0096 through ADR-0098; it retains ADR-0094's structurally read-only replay,
ADR-0096's Caddy and Unix-socket boundary, ADR-0097's authorization and canonical refusal order, and
ADR-0098's state separation, accessibility, local-asset, and mode-truth requirements.

The operational dashboard graph consumes the validated broker-backed projection and implements the two
ADR-0148 mutations:

- an operator may submit only the closed `assign-sector` or `escalate-rescue` command document; and
- an operator may approve or reject only the exact persisted proposal, evidence decision, and proposed
  escalation action currently displayed.

The proposal-decision surface displays the proposal identifier and digest, evidence-decision identifier
and digest, proposed drone and coordinates, the evidence outcome, and the consequence of either choice.
The browser never derives, changes, or omits a bound member. It sends the exact validated projection
members, derives no operator identity, and treats `202` only as durable acceptance of an operator event.
It does not present publication, authorization, delivery, or execution as complete until the
corresponding validated events arrive through the projection.

Approve and reject are separate keyboard-accessible, screen-reader-labelled controls. A confirmation
step states that approval can authorize one rescue-escalation command and that rejection cannot. Once
either submission starts, both controls are disabled. The browser generates one UUIDv4 idempotency key,
does not silently repeat a mutation after any response or transport ambiguity, and keeps the last
validated mission state visible. A `401` requires a full reload. A `409`, expired or mismatched
selection, repeated decision, contract refusal, broker degradation, or ambiguous transport outcome is
shown as an explicit non-success state and publishes no second decision.

The existing `degradedLive` wire value remains the honest mode name for the simulated fleet. Within that
mode the presentation distinguishes `connected`, `degraded`, `retrying`, `recovered`, and `exhausted`
source states. Decision controls are enabled only while the operational graph is connected and the
selected proposal/evidence/action binding is complete and current. Recovery never automatically
submits a decision that was pending when connectivity changed.

The replay graph continues to construct no operational publisher or writer. It may render recorded
proposal, evidence, approval, command, result, and audit facts, but it renders no enabled approval,
rejection, command, rescue, or escalation action. Direct calls to either operational mutation route
return `REPLAY_READ_ONLY` before body retention, as ADR-0160 requires.

The `mission-control` Compose profile now includes the complete application data plane: broker,
PostgreSQL and migrations, fleet, scenario service, recorder, Agent Mesh gateway/tool path, command
gateway, evidence service, dashboard API, and Caddy. Model unavailability causes a redacted abstention
and prevents new proposals; it does not remove telemetry, existing audit visibility, or the human
approval boundary.

## Consequences

- The earlier absence of controls remains correct historical scope, while the completed durable path
  gains an honest operator interface.
- The browser can request an effect but cannot manufacture authority: proposal, evidence, approval,
  command authorization, publication, and result remain independently validated broker facts.
- Replay can show what happened without gaining a route that can make it happen again.
- Recovery is intentionally conservative. An operator may need to inspect the refreshed facts and make a
  new explicit choice after an ambiguous transport failure.
- The reference mode continues to say that it is a simulation; this decision does not claim that the
  three declared edge agents or a physical rescue fleet are operational.
- Production-stack browser acceptance must cover one exact approval, rejection, expiry, mismatch,
  repetition, double-submit, disconnect, recovery, and replay-read-only path.

## Alternatives considered

- **Keep the UI-first prohibition indefinitely.** Rejected because the durable authority now exists and
  hiding it would leave the end-to-end workflow incomplete.
- **Enable controls in replay but make the backend reject them.** Rejected because a visible operational
  affordance contradicts structural isolation and invites unsafe expectations.
- **Automatically retry an ambiguous decision after reconnect.** Rejected because the first request may
  have committed even when its response was lost.
- **Call the simulated fleet `live`.** Rejected because a broker-connected simulation is still a
  simulation and must remain visibly labelled as such.
- **Let the browser submit only identifiers and reload the remaining binding server-side.** Rejected
  because the operator must see and explicitly send the exact proposal, evidence decision, and action
  being authorized; the server then independently rebinds those bytes to durable facts.
