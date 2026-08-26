# ADR-0121: Reconstruct synthetic mission-lifecycle witnesses from stable run identity

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0111

## Context

ADR-0111 requires scenario-service lifecycle publication to survive reconciliation without reusing a
producer sequence for different content. The first runtime kept the next sequence, event identifier,
presentation timestamp, trace identifier, and unacknowledged bytes only in process memory. If that
process published `PLANNED` and then disappeared during an uncertain fleet handoff, a fresh recovery
process built `ABORTED` at sequence zero with new random values. The recorder correctly rejected that
as a reused or divergent producer position instead of inventing an audit order.

Adding a database, write-ahead log, or generalized outbox to the scenario service would duplicate the
dashboard operation and broker-event persistence already selected by ADR-0113. This bounded synthetic
slice has only four lifecycle facts and stable run, mission, and lifecycle inputs, so it can reconstruct
the exact publication witness without another runtime component.

## Decision

Mission-lifecycle publication is deterministic for one stable `(runId, missionId, lifecycle)` input.
The scenario service constructs canonical witness version 1 containing those values and the lifecycle
sequence slot, and derives independent SHA-256 values under explicit `mission-lifecycle-* /v1` byte
contexts for the CloudEvent identifier, trace identifier, span identifier, and presentation epoch.

- `PLANNED` occupies source sequence 0, `SEARCHING` sequence 1, and `EXHAUSTED` sequence 2.
- `ABORTED` uses the next in-process sequence when earlier transitions were acknowledged. A fresh
  lost-start recovery reconstructs sequence 1, because the durable prerequisite is `PLANNED` at
  sequence 0.
- The event identifier is `event-` plus the first 32 lowercase hex characters of its contextual digest.
- Trace and span values are contextual lowercase digests with a fixed non-zero leading nibble.
- Presentation time is a deterministic instant in synthetic year 2026 derived from only the run and
  mission identities, plus one second per sequence slot. It is explicitly synthetic metadata; audit
  ordinal remains the sole ordering authority.
- Source, topic, data schema, correlation identifier, and payload remain ADR-0111's exact values.

The publisher no longer accepts clock or random-identifier collaborators because they would have no
honest role in this reconstruction. It still retains an unacknowledged byte sequence in memory during
bounded same-process attempts. A fresh process asked to recover the same lost run produces byte-for-byte
identical topic and payload. The recorder therefore accepts the first delivery and treats any later one
as the exact broker-event duplicate it is.

This decision changes no public or private wire schema. It is limited to the deterministic synthetic
mission-lifecycle producer; it does not claim to be reusable crash recovery for arbitrary workflows.

## Consequences

- Restart recovery cannot reuse a source sequence with new event bytes merely because a process-local
  clock or UUID source changed.
- The scenario service needs no inert persistence service, table, lease, or recovery daemon.
- Mission lifecycle presentation timestamps are synthetic rather than wall-clock observations and must
  remain labeled as simulation metadata.
- A semantically different lifecycle occupying an already recorded sequence still fails closed as a
  divergent source sequence.
- A future non-synthetic producer that requires actual occurrence time must persist its exact witness;
  it cannot reuse this deterministic-time convention without a new decision.

## Alternatives considered

- **Persist a scenario-service outbox.** Rejected because this slice needs one reconstructable witness,
  not a second workflow engine or durable authority.
- **Query and rewrite the recorder high-water mark.** Rejected because a producer may not mutate the
  consumer's audit authority, and sequence reuse with different content must remain a refusal.
- **Ignore timestamp and identifier differences during deduplication.** Rejected because all envelope
  fields are contract content and the ordered-event witness includes presentation time.
- **Retry with a new source identity.** Rejected because it would hide one logical producer behind two
  streams and make an already accepted transition appear new.
