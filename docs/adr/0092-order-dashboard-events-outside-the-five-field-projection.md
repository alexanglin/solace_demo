# ADR-0092: Order dashboard events outside the five-field projection and resnapshot bounded SSE

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0067

## Context

ADR-0067 retains a five-field `DashboardEvent` while also requiring its fold to advance the durable audit
ordinal. A bare dashboard event carries no ordinal, so the specified reducer cannot produce the specified
state. It also leaves the snapshot, resume cursor, digest comparison, timeline transfer, and exact SSE
control frames open.

## Decision

Keep `DashboardEvent` unchanged and wrap it:

```text
OrderedDashboardEvent = {
  auditOrdinal,
  event
}
```

`auditOrdinal` is a positive integer assigned by the durable audit transaction. The pure reducer accepts
an ordered event, rejects a gap or regression, ignores an exact duplicate, and stores the latest ordinal.
The reduced document contains the current mission and lifecycle, sorted fleet members and latest validated
telemetry, explicit connectivity, sorted sectors and their lifecycle, and the latest audit ordinal. It
excludes source timestamps, run mode, server connection state, traces, animation, filters, selection, and
playback.

The dashboard API emits only these SSE data frames:

- `snapshot`: runtime identifier, opaque cursor, reduced state, server digest, current run, and the full
  ordered non-telemetry timeline for that prepared run;
- `dashboard-event`: one `OrderedDashboardEvent`, an opaque suffix cursor, and the server digest after
  folding it; and
- `stream-overloaded`: a reserved terminal control frame instructing the browser to resnapshot.

Keepalives are comments only. Event time remains presentation metadata; audit ordinal is the only
timeline ordering authority. Opaque cursors bind a mission or replay session to a suffix and never enter
mission state or its digest.

Each client retains at most 256 data events plus one reserved terminal-control slot. On pressure it
removes the oldest telemetry first. If a non-droppable frame still cannot be retained, it sends the
terminal frame and closes. A fresh connection receives a snapshot atomically followed by its suffix; an
unknown, stale, or cross-session cursor receives a new snapshot.

The browser validates every frame, folds the ordered event through the same reducer used by replay,
recomputes the replay-state digest after every accepted event, and fails closed on ordinal or digest
divergence while retaining the last validated state. Seeking a replay always rebuilds by folding from the
beginning.

Adding an event type still requires its projection, reduced-state rule, schema, fixtures, Python and
TypeScript parity, and timeline inclusion decision in one change.

## Consequences

- Audit order becomes explicit without contaminating the transport-neutral five-field event.
- A client can detect a missed or divergent fold immediately rather than displaying plausible stale
  state.
- Snapshots deliberately duplicate the non-telemetry timeline because reduced state is not a timeline
  source.
- Resume cursors cannot be inspected or compared as ordinals; they are transport capabilities scoped to
  one run.
- Sorting and digesting after every event is acceptable for the fixed twenty-member fixture and is not a
  fleet-scale performance claim.

## Alternatives considered

- **Add `auditOrdinal` as a sixth `DashboardEvent` field.** Rejected because it mixes audit storage
  metadata into the normalized application projection and breaks the accepted five-field shape.
- **Use SSE arrival order.** Rejected because reconnects and concurrent writers make it transport order,
  not durable audit order.
- **Use an ordinal as the public resume cursor.** Rejected because it cannot bind the cursor to one
  mission or replay session and invites cross-run reuse.
- **Trust the server digest without browser recomputation.** Rejected because malformed or divergent
  client reduction would remain invisible to the operator.
