# ADR-0090: Make the wilderness mission dashboard a UI-first real slice

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Phase 3 owes a first operator dashboard, but the broader implementation plan also describes evidence,
approval, rescue escalation, generalized dispatch recovery, executable model-backed edge agents, and
full crash resumption. Implementing those together would make the browser experience a thin view over a
large infrastructure build and would delay the first coherent operator workflow.

The prepared wilderness demonstration can make a smaller, honest claim. The existing deterministic
fleet fold can execute twenty simulated drones and exercise connectivity, sector, and mission state. The
three canonical edge agents can be present in the declared roster while remaining visibly unexecuted.
Replay can exercise the same reduced-state contract without creating any command or approval surface.

## Decision

Deliver a **UI-first real slice** centered on one map-first command center for the prepared Wilderness
Missing Person scenario. Approximately two thirds of implementation and acceptance effort belongs to the
browser experience.

The slice executes exactly twenty deterministic simulated drones. It also declares
`drone-vision-01`, `drone-navigation-02`, and `drone-comms-03`, but labels each one
`DECLARED ONLY — NOT EXECUTED` everywhere it appears. The dashboard never infers their connectivity,
telemetry, or participation.

The initial operator workflow is limited to degraded-live mission start, history-preserving reset, fleet
and sector inspection, an ordered non-telemetry timeline, and isolated replay. It renders no approval,
command, evidence, model, rescue, or escalation control. Those capabilities remain follow-on work and do
not receive placeholder routes or disabled controls that imply an implementation exists.

The map is the dominant surface at 1440 by 900 pixels. The interface also includes a persistent mode
badge, scenario controls, a synchronized semantic fleet table, a collapsible fleet/timeline rail, replay
controls, and explicit loading, empty, starting, running, resetting, retrying, offline, recovered,
stale-runtime, contract-failure, exhausted, aborted, and replay states. Color never carries a state by
itself.

The browser keeps server state, reduced mission state, and presentation state separate. Live SSE,
validated replay bundles, and deterministic test fixtures implement one event-source interface and feed
the same pure TypeScript reducer. Map motion, filters, selection, panel state, and playback timing never
enter mission state or its digest.

MapLibre renders only committed local geometry and an empty local style. No basemap, glyph, sprite,
analytics, font, or other browser asset may be requested from a remote origin. The semantic fleet table is
the accessible alternative to the canvas.

## Consequences

- The first release-quality dashboard can be reviewed as a complete product surface before the wider
  orchestration and evidence stack exists.
- The three edge agents remain truthful catalog facts instead of becoming fake running processes.
- Degraded live simulation and replay move earlier than the original phase sequence, while approval and
  evidence remain deferred.
- The slice demonstrates mission exhaustion rather than rescue completion. It is not the complete
  initial-release scenario in `docs/IMPLEMENTATION_PLAN.md`.
- A later approval/evidence increment must add new contracts and UI states without weakening the
  absence-of-controls replay guarantee established here.

## Alternatives considered

- **Implement the complete approval and evidence workflow before the dashboard.** Rejected because it
  makes infrastructure the dominant deliverable and delays the first operator-usable slice.
- **Render the three edge agents as offline.** Rejected because offline is a connectivity fact and no
  process runs to establish it.
- **Use a static mock with no real runtime.** Rejected because the operator-facing claim would not be
  backed by the deterministic fleet fold or the production reducer.
- **Create placeholder approval and evidence controls.** Rejected because a visible control implies an
  authority or workflow the slice deliberately does not implement.
