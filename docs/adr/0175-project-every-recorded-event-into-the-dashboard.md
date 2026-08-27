# ADR-0175: Project every recorded event into the ordered dashboard stream

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0101 and ADR-0112

## Context

ADR-0101 makes the recorder's per-mission audit ordinal the dashboard's only ordering authority, and
ADR-0112 strengthens the reducer so it refuses an ordinal gap. Both records assumed that a dashboard
projection could remain partial. That assumption is inconsistent with using the recorder audit as the
ordered source: the recorder assigns a contiguous ordinal to every authoritative application envelope,
including types that do not change reduced mission state.

Three currently recorded envelope types have no projection: salient drone observations, assign-sector
drone commands, and drone command results. If any one appears between two projected records, a dashboard
that skips it sees a gap at the next projected ordinal and correctly fails closed. Relaxing the reducer
would conceal genuine recorder loss. A generic or raw fallback would instead weaken the closed browser
contract and allow an unmodelled event through the public boundary.

## Decision

Treat the recorder's ordered audit as the dashboard's complete ordered source. The projection table is
total over every event type in the closed envelope binding table whose family the recorder records. The
three transport-only or non-authoritative integration families remain excluded by recorder policy:
Agent Response, Gateway Request, and Gateway Response.

Add explicit normalized projections for the three missing recorded types:

| Application event type | Dashboard kind | Event class |
| --- | --- | --- |
| `aerial-rescue.v1.drone.event.salient` | `salientObservation` | `EVIDENCE` |
| `aerial-rescue.v1.drone.command.assign-sector` | `droneCommand` | `COMMAND` |
| `aerial-rescue.v1.drone.command-result` | `commandResult` | `COMMAND` |

Each projection retains every member of its validated application payload except `missionId`, which is
represented once by the normalized event's `mission`. The established evidence-decision integrity-member
exception remains unchanged. Assign-sector and escalate-rescue commands share the closed `droneCommand`
kind but use distinct closed data branches. Salient observations and command results receive their own
closed kinds. All three enter the non-telemetry snapshot timeline.

Do not add a generic event, raw-envelope, unknown-event, or ordinal-only projection. Adding or removing a
recorded binding requires an atomic projection row, closed JSON Schema, positive and one-reason negative
golden fixtures, strict Python model, generated TypeScript, timeline decision, and a total-table test that
proves the projection keys equal the recordable binding keys.

Retain ADR-0101 and ADR-0112's strict successor, regression, exact-duplicate, and divergence behavior.
An unprojected recorded type is a contract defect and must stop recovery; it is not permission to skip an
ordinal.

## Consequences

- Every contiguous recorder audit suffix can be folded without manufacturing a gap.
- A missing recorder row still stops the dashboard at the last proven checkpoint.
- Timeline-only events advance the audit ordinal and ordered-event witness even when they do not change
  reduced mission state.
- The browser receives a closed, typed representation rather than a transport envelope or an open map.
- Adding a recorder-recorded envelope type now necessarily changes the dashboard contract in the same
  increment.

## Alternatives considered

- **Allow the reducer to skip unprojected ordinals.** Rejected because it makes recorder loss
  indistinguishable from an intentional omission.
- **Renumber only projected events.** Rejected because the dashboard would gain a second ordering
  authority unrelated to the append-only recorder audit.
- **Project an ordinal-only placeholder.** Rejected because the recorded operator-relevant fact would be
  hidden while the checkpoint falsely appeared complete.
- **Expose a generic raw-envelope event.** Rejected because it breaks the closed normalized dashboard
  boundary and moves untrusted polymorphism into the browser.
