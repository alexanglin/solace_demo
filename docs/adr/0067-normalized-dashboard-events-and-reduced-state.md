# ADR-0067: Project application events into normalized dashboard events and fold them into one reduced state

- **Status:** Superseded by ADR-0093
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) defines `GET /api/v1/events` as an "SSE stream for normalized
dashboard events" and says nothing further: no shape, no schema, no fixture, and no rule for what a
client does with one. Nothing in the repository names the normalized form, so the browser, the
recorder, and the replay oracle each have nothing to agree on.

Three accepted decisions already constrain that form.
[ADR-0009](0009-isolated-side-effect-free-replay.md) makes the *reduced dashboard state* the replay
determinism oracle and records why raw event streams are not compared: event identifiers and
timestamps legitimately differ between runs.
[ADR-0003](0003-postgres-durable-mission-store.md) makes the append-only audit ordinal the timeline
ordering authority, and the producer-scoped `sequence` of
[ADR-0037](0037-cloudevents-envelope-profile.md) explicitly not that.
[ADR-0027](0027-integer-only-canonical-serialization.md) fixes the digest-covered value space to an
integer-only JSON profile whose object keys match `^[a-z][a-zA-Z0-9]*$` and whose array order is
semantic; its `Context.REPLAY_STATE` has existed unused since that record landed.

`AGENTS.md` requires the dashboard to keep server state, mission state, and presentation state
distinct, and to render from normalized domain events rather than encoding independent mission
business rules in components. [operating-parameters.md](../operating-parameters.md) carries an open
row for the server-sent-event per-client buffer bound and the droppable event classes, whose stated
obligation is that audit, approval, and evidence events are never dropped.

One fact about identifiers forces part of the shape. A drone identifier such as `drone-07` obeys the
topic IDENTIFIER rule, which admits interior hyphens; a canonical object key does not admit a
hyphen. A per-drone map therefore cannot be keyed by drone identifier inside digest-covered bytes.

## Decision

The dashboard contract is two shapes, both inside the ADR-0027 canonical value space, so one
canonicalizer, one decoder, and one fixture oracle serve both and TypeScript reimplements them from
this record and [CONTRACTS.md](../CONTRACTS.md) alone.

**A dashboard event** is the projection of exactly one validated application envelope. It carries
the projection's `kind`, its `eventClass`, the mission identifier, the identifiers the source topic
named, and the projected fields — and nothing from the transport. `id`, `source`, `sequence`,
`dataschema`, `traceparent`, and `tracestate` do not cross this boundary; the envelope's `time` does,
because the operator reads it in the timeline. An envelope whose `type` has no projection is refused
as `UNPROJECTED`, exactly as an unbound `type` is refused by ADR-0037.

**The reduced dashboard state** is the fold of every dashboard event so far, by a pure total function
`apply(state, event) -> state`. It is the ADR-0009 determinism oracle, and its determinism is
structural: **the state carries no wall-clock instant, no event identifier, and no trace context**,
because those legitimately differ between runs of the same seeded scenario. Where the operator needs
a time, it is read from the event stream, which is presentation state; where the timeline needs an
order, the state carries the ADR-0003 audit ordinal, which is an integer and is deterministic.

Collections inside the state are **arrays in ascending byte order of their identifier**, never
objects keyed by it, because a canonical key cannot contain the hyphen an identifier may. Array order
is semantic under ADR-0027, so the sort is part of the contract rather than an implementation
detail: two states differing only in insertion order must produce one digest.

The determinism hash is `digest(Context.REPLAY_STATE, state_document(state))`, which puts the
`canonicalizationVersion` inside the hashed bytes and the context string in the hash input, so state
bytes cannot be replayed as proposal bytes.

**Event classes and droppability.** Every dashboard event carries exactly one class. `TELEMETRY` is
droppable, because routine telemetry already uses direct delivery that may be dropped under
congestion and a newer position supersedes a stale one. `CONNECTIVITY`, `MISSION`, `COMMAND`,
`EVIDENCE`, `APPROVAL`, and `AUDIT` are never droppable. The per-client buffer holds at most **256
dashboard events**. On overflow the server discards droppable events oldest-first; if the buffer is
still full, it **closes the stream with a typed reason** and the client re-synchronizes from a full
state snapshot. A non-droppable event is never silently discarded.

Adding an application event type is a Tier 1 change in the same shape ADR-0037 already established:
a projection row, a state rule, golden fixtures, and a manifest entry land together, or the type is
refused as unprojected.

## Consequences

- Replay determinism becomes testable the moment the fold exists, rather than waiting for the
  recorder: fold a committed event sequence ten times and compare one digest.
- The browser and the server run the same reduction over the same events, so a client that
  reconnects and replays a snapshot plus a suffix reaches the state the server holds. That is what
  makes a bounded buffer safe to overflow.
- Stripping the transport shrinks the stream at the 23-drone telemetry rate and stops the browser
  depending on CloudEvents at all, but it means the dashboard cannot show a `traceparent` next to a
  timeline row. Cross-system correlation is read from the audit records, which carry it.
- Excluding wall-clock instants from the state makes the determinism claim honest and makes the
  state useless as a timeline source. The timeline is therefore rendered from the event stream, and
  a client that has dropped telemetry has a complete timeline but an incomplete trail.
- Sorting collections by identifier costs a sort per fold step in the naive implementation. The
  state is small — 23 drones and a bounded sector set — so this is a real cost that is affordable
  here and would not be at fleet scales this project does not claim.
- Two reductions must now stay equal across languages, and nothing but the contract tests holds them
  equal. A divergence shows up as a replay determinism failure rather than as a type error.
- The 256-event bound is a memory ceiling, not a latency target. A client slower than the telemetry
  rate loses trail fidelity before it loses its stream, which is the intended failure order.

## Alternatives considered

- **Stream the raw CloudEvents envelope and reduce only in the browser.** Rejected: the browser
  would carry the envelope profile, the topic grammar, and the transport extensions to render a map,
  and the server would have no state to compare against for ADR-0009.
- **Key the per-drone collection by drone identifier.** Rejected: ADR-0027 canonical keys match
  `^[a-z][a-zA-Z0-9]*$` and a drone identifier may contain a hyphen, so the document would not
  canonicalize.
- **Leave collection order to insertion.** Rejected: ADR-0027 preserves array order, so insertion
  order would enter the digest and two runs that agreed on every value could still disagree on the
  hash.
- **Include the envelope `time` in the reduced state.** Rejected: it is the specific value ADR-0009
  names as legitimately differing between runs, so it would make the determinism gate fail for a
  correct system.
- **Order the state by the producer-scoped `sequence`.** Rejected: ADR-0037 states that sequence
  never orders the timeline; ADR-0003's audit ordinal does.
- **Drop the oldest event of any class on overflow.** Rejected: silently discarding an approval or
  an audit record is the failure the open parameter row exists to forbid.
- **Grow the per-client buffer without bound and never close a stream.** Rejected: the soak target
  requires no unbounded SSE-client memory growth, and an unbounded buffer converts a slow client
  into a server memory leak.
- **Reduce in `packages/domain` rather than `packages/contracts`.** Rejected: the reduction is a
  cross-language wire contract with a JSON Schema and golden fixtures, which is what
  `packages/contracts` owns; `packages/domain` owns rules no other language reimplements.
