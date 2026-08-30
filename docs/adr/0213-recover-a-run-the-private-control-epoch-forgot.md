# ADR-0213: Recover a run the private control epoch forgot, rather than stranding reset

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0143, for the case where private control has no representation at all

## Context

`ScenarioCoordinator` keeps its run bindings in process memory: `self._bindings` is a dictionary, and
[ADR-0158](0158-keep-scenario-control-brokerless.md) deliberately gives the
scenario service no durable store. The dashboard's `dashboard_current_run` pointer *is* durable. So
recreating the scenario service — which `just mission-control-up --force-recreate` does every time an
image is rebuilt — leaves a durable pointer naming a run the private service will answer
`RUN_NOT_FOUND` for.

For a nonterminal predecessor that is a dead end, and the first live run of ADR-0209 walked straight
into it:

- **Start** refuses `409 OPERATION_CONFLICT`, because a non-reusable live run is current.
- **Reset** calls `cancel`, receives `RUN_NOT_FOUND`, and
  [ADR-0143](0143-let-durable-terminal-state-establish-reset-cancellation.md) only skips private
  cancellation for a *durably terminal* predecessor. A `SEARCHING` predecessor therefore completes
  `409 CANCELLATION_NOT_ESTABLISHED`.

Both refusals are durable and exact, so retrying reproduces them forever. The operator has no in-app
path at all, and the only remedy is deleting the pointer row by hand — which is what the live record
had to do.

ADR-0143's rule is right about what it addressed: a live private service that reports a run as
unfinished must not have its answer overridden by the dashboard. But "the private service does not
have this run at all" is a different fact from "the private service says this run is still going", and
the reset path treated them the same.

The mechanism for the first fact already exists on both sides. `ScenarioCoordinator.recover` — "a run
the fleet lost is ABORTED for good" — admits an unknown run, asks the fleet, and pins `ABORTED` only
when the fleet has also lost it. `ScenarioPort.recover` is already declared and already used:
`_complete_live_start` falls back to it when `status` answers `RUN_NOT_FOUND`. Only reset did not.

## Decision

When private cancellation answers `RUN_NOT_FOUND`, reset asks `recover` and uses its answer.

Nothing else changes. The recovered status passes the same `_require_private_status` identity check as
a cancellation, and cancellation is established only for `ABORTED` or `EXHAUSTED` — so a fleet that
still holds the run answers with its real state and the reset still refuses. Recovery is a question,
not an assumption: it establishes an ending only for a run that *both* the scenario service and the
fleet have lost, and on this single-host reference deployment those are the only two processes that
could be executing it.

ADR-0143's durably-terminal skip is untouched and still runs first, so a predecessor the recorder has
already ended costs no private call at all.

## Consequences

- Recreating the private control plane no longer strands the operator. Reset works from the browser,
  which is the path the dashboard is built around, and the hand-deletion of a pointer row stops being
  an operating procedure.
- Reset becomes consistent with start: both reconcile a forgotten run through `recover` rather than
  one succeeding and the other refusing.
- Negative: reset can now make two private calls where it made one, on the path where the first
  answers `RUN_NOT_FOUND`. Both are inside the one shared fifteen-second budget, but the budget is not
  re-checked between them, so a slow recovery can spend more wall clock than a cancellation alone.
- Negative: the safety of establishing `ABORTED` rests on there being exactly one scenario service and
  one fleet. That is true of this reference deployment and stated by
  [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md); a second fleet reachable by another
  scenario process would make "the fleet lost it" a weaker claim than it is here.
- Negative: an established expectation changes. The test that pinned
  `CANCELLATION_NOT_ESTABLISHED` for a missing nonterminal predecessor now pins it for a predecessor
  recovery cannot establish as terminal, which is the reason the refusal should exist.

## Alternatives considered

- **Give the scenario service a durable binding store.** The real fix for the underlying fact, and
  rejected here: ADR-0158 decided the private control plane holds no durable authority, and reversing
  that to solve a recovery case would move mission authority out of the dashboard's store.
- **Let the dashboard abort a forgotten predecessor on its own.** Rejected: it makes the dashboard the
  judge of whether a run is still executing, which is precisely what ADR-0143 refused. Asking the
  service that can ask the fleet keeps the judgement where the evidence is.
- **Treat `RUN_NOT_FOUND` from cancel as cancellation established.** Rejected as the same thing
  without the question: it would abort a predecessor whose fleet is still sweeping, if the scenario
  service had merely lost its binding while the fleet kept running.
- **Let Start reuse a current run the private epoch forgot.** Rejected: it would silently continue a
  mission whose fleet no longer exists, and the operator's reset is the explicit act that should end
  a mission they are replacing.
