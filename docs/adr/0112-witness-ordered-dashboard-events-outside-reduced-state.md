# ADR-0112: Witness ordered dashboard events outside reduced state and correct v1 anchors

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0101

## Context

ADR-0101 requires the reducer to ignore an exact same-ordinal duplicate while refusing a
same-ordinal event with different content. Its reduced state stores only `latestAuditOrdinal`. That
integer proves neither which event produced the state nor whether another event at the same ordinal is
exactly equal.

The snapshot's timeline cannot supply the missing proof because it deliberately excludes telemetry. A
snapshot whose latest accepted event was telemetry can therefore be schema-valid and digest-valid yet
leave a new reducer unable to distinguish an exact duplicate from divergent content. The initial state
in a replay bundle has the same gap.

Putting a transport witness inside reduced mission state would solve the comparison but would make the
replay-state digest depend on reducer-session metadata. Treating every same-ordinal input as a duplicate
would instead hide divergence. The v1 snapshot and replay schemas have no production source yet, so the
anchor can be corrected before A3/R3 implement it.

## Decision

Retain ADR-0101's five-field `DashboardEvent`, its wire wrapper
`{auditOrdinal, event}`, reduced-state members, SSE frame set, opaque cursors, bounded buffer, timeline
rule, and `replay-state` digest. Correct duplicate proof by making the pure reducer operate on this
immutable checkpoint:

```text
ReducerCheckpoint = {
  state,
  latestEventDigest
}
```

`latestEventDigest` is outside the digest-covered reduced mission state. It is SHA-256 over the exact
covered document

```text
{
  canonicalizationVersion: 1,
  auditOrdinal,
  event
}
```

under the new canonical digest context `ordered-dashboard-event`. The helper adds only
`canonicalizationVersion`; the `OrderedDashboardEvent` wire remains exactly `{auditOrdinal, event}`.
The covered event includes all five normalized fields, including presentation `time`, because an exact
duplicate means identical accepted wire content, not merely the same reduced effect.

Correct both existing v1 anchors:

- every dashboard snapshot carries top-level `latestEventDigest` beside `state`;
- every replay bundle carries top-level `latestEventDigest` for `initialState`;
- the witness is `null` if and only if the corresponding state's `latestAuditOrdinal` is `0`; and
- a positive latest ordinal requires a lowercase SHA-256 witness, so a validated nonempty anchor is
  never bare or unprovable.

Schema validation owns the closed member and scalar shape; Python and TypeScript boundary validation
also enforce the ordinal/witness pairing. The snapshot constructs a checkpoint from `state` and its
witness. Replay constructs the same checkpoint from `initialState` and the bundle witness.

For an input whose ordinal is the checkpoint ordinal plus one, fold the event, advance state, and replace
the witness with that input's ordered-event digest. For an input at the checkpoint ordinal, compare its
ordered-event digest with the witness: equality is an exact duplicate and leaves the checkpoint
unchanged; inequality is divergent content and is refused. A lower ordinal is a regression and a larger
non-successor is a gap. Digest comparison uses the shared constant-time helper.

The replay-state digest continues to cover only reduced mission state. A snapshot still carries that
server digest separately, and a dashboard-event SSE frame still carries the post-fold replay-state
digest; neither substitutes for the ordered-event witness. Replay bundle integrity covers the bundle
including its anchor witness.

Keep the existing snapshot and replay version strings at v1. No running API or validated replay producer
has emitted them, A3 and R3 are not started, and correcting their manifest-owned fixtures and generated
types now prevents the first implementation from institutionalizing an unprovable promise. The schema,
fixtures, service-local models, generated TypeScript, Python fold, and cross-language parity evidence
must change together in the R1-correction/R3 increment.

## Consequences

- Exact duplicate handling becomes a proved comparison rather than an inference from ordinal alone.
- Reduced mission state and its replay-state digest remain presentation- and transport-neutral.
- A reconnecting live client and a replay session start from the same complete reducer checkpoint.
- Every accepted successor requires one additional canonical SHA-256 calculation. The fixed fixture and
  bounded stream make that cost acceptable but do not establish fleet-scale performance.
- Two distinct ordered events are treated as equal if SHA-256 collides. That cryptographic assumption is
  narrower than storing the full previous event but consistent with the repository's other digest
  identities.
- The corrected v1 fixtures and generated modules will become stale immediately when the schemas change;
  the freshness gate intentionally forces their atomic regeneration.

## Alternatives considered

- **Store the full latest event in reduced mission state.** Rejected because event time and duplicate
  proof would enter the replay-state digest and make transport history part of mission state.
- **Use the snapshot timeline as the witness.** Rejected because telemetry is deliberately absent and
  may hold the latest audit ordinal.
- **Ignore every same-ordinal input.** Rejected because divergent content would be silently accepted as a
  duplicate.
- **Refuse every same-ordinal input.** Rejected because accepted at-least-once delivery requires exact
  duplicates to be idempotent.
- **Carry the witness only in reducer memory.** Rejected because every snapshot and replay anchor would
  recreate the original unprovable state.
- **Introduce snapshot and replay v2.** Rejected because no production v1 source exists; preserving a
  known-defective first version would create migration work without compatibility value.
