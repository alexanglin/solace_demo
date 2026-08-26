# ADR-0134: Prove fleet publication separately from recorder receipt

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0124

## Context

The deterministic fleet status includes `completedTickCount` and `telemetryPublicationCount`. ADR-0124
retained those fields for fleet-run instrumentation, but incorrectly said that scenario control consumes
them to monitor a run. Scenario control actually observes fleet lifecycle; it neither copies those
counters into its own status nor changes behavior from their values. Leaving the fields justified by a
consumer that does not exist would violate the rule that production surfaces must have an observable
effect.

The live acceptance target nevertheless needs two independent facts: the fleet successfully published
20 telemetry messages on each of 14 ticks, and the best-effort recorder received some bounded subset.
Conflating those instruments would let broker or recorder loss masquerade as a fleet-publication defect.
Adding either counter to the public dashboard API would expose operator-irrelevant diagnostics and create
another compatibility surface.

## Decision

Keep both fields only on authenticated private `fleet-control-run-status` and make the production
acceptance workflow their explicit consumer:

- after the UI-driven mission reaches `EXHAUSTED`, the acceptance driver executes inside the scenario
  service container, uses that service's existing fleet-control credential and typed client, and queries
  the matching mission and run from the fleet simulator;
- the result must identify the same mission and run, report 14 completed ticks, report 280 successful
  telemetry publications, and have the terminal `EXHAUSTED` lifecycle;
- the same acceptance workflow independently queries durable audit storage for `droneTelemetry` records
  joined to that run and requires a positive recorder receipt count no greater than 280;
- neither value enters scenario status, public dashboard state, replay state, operator UI, or a new
  diagnostics route; and
- credentials remain inside their owning container. The acceptance output contains only validated
  non-secret counters and stable synthetic identities.

The fields are therefore acceptance instruments, not orchestration inputs. If this production workflow
stops consuming them, remove them from the private contract rather than reserving them for future use.

## Consequences

- The exact 14-tick and 280-publication claims have a real production-stack observer.
- Recorder receipt remains honestly best effort and is measured separately from producer success.
- Scenario control stays lifecycle-focused and cannot accidentally treat an instrumentation counter as
  operational state.
- No diagnostics-only public endpoint or browser state is introduced.
- The production acceptance driver needs container execution and read-only database access within the
  uniquely named disposable Compose project.

## Alternatives considered

- **Remove both counters.** Rejected because the required 280-publication claim would have no independent
  instrument.
- **Copy the counters into scenario or dashboard status.** Rejected because neither service behavior nor
  operator decisions depend on them.
- **Infer publication from recorder rows.** Rejected because recorder ingestion is explicitly best effort.
- **Add a public diagnostics route.** Rejected because the existing private authenticated boundary is
  sufficient and narrower.
