# ADR-0190: Count active queue binds through transmit-flow aggregates

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0157's parent-queue bind projection and routine fan-out clauses

## Context

ADR-0157 correctly requires queue depth to come from the parent queue collection's aligned
`msgs.count` aggregate, but it carried forward ADR-0154's assumption that the same parent row exposes
bind state. The implementation consequently requested
`select=queueName,bindCount,msgs.count`, percent-encoded the comma separators, and decoded
`data[].bindCount`.

A certificate-validating, credential-redacted read against the pinned PubSub+ 10.26.0.8799 broker on
2026-08-27 disproved that assumption. Its served monitor specification has no `bindCount` member on
`MsgVpnQueue`; the parent collection refuses both `bindCount` and `txFlows.count`. It accepts the
literal selection `queueName,msgs.count` and returns each message count in the `collections[]` entry
aligned by index with its `data[]` queue row. Percent-encoding the commas makes the selection one
invalid attribute.

The same served specification exposes each queue's `txFlows` child collection and a response
`meta.count`. A transmit flow is the broker object through which a client consumes Guaranteed
messages from that queue, so its collection count is the pinned broker's active-consumer instrument.
Solace documents literal nested collection selections, index-aligned `data` and `collections` rows,
collection totals in `meta.count`, a minimum page count of one, cursor pagination, and an average SEMP
rate below ten requests per second in
[SEMP Features](https://docs.solace.com/Admin/SEMP/SEMP-Features.htm).

## Decision

Carry forward ADR-0157's dedicated read-only identity, TLS boundary, five-request-per-second process
share, 30-second successful-or-failed coalescing, aggregate-only message depth, DMQ preservation, and
fail-closed semantics. Replace only its queue-query and fan-out mechanics as follows.

The parent inventory query is exactly `select=queueName,msgs.count`, with literal comma separation.
The decoder continues to require equal-length `data[]` and `collections[]` arrays, exact queue identity,
unique names, and an exact non-negative integer `collections[].msgs.count`. Depth-only operations,
including backlog reads and retirement planning, issue no transmit-flow request.

An exact runtime-state or retirement read enriches one present queue through
`/msgVpns/{vpn}/queues/{queue}/txFlows?count=1`. The transport reads only the exact non-negative integer
`meta.count`; it does not enumerate or expose flow rows. A missing, malformed, refused, or ambiguous
flow count fails before deletion. Zero active binds means exactly zero transmit flows.

Routine health first completes the bounded parent inventory, then reads flow counts sequentially only
for queue names that are both desired and observed. Missing, unexpected project-owned, and unrelated
queues cause no child fan-out. The parent inventory retains its 20-page, 100-row, 2,000-row bound. The
desired bind-count fan-out is separately capped at 89, the exact durable queue count of the fixed
23-drone reference topology. One refresh therefore makes at most 109 paced requests: 20 parent pages
and 89 count-only child reads. The single HTTPS session is never parallelized.

Any parent or child failure becomes one coalesced typed read refusal, retains the last complete
snapshot, and cannot report readiness as healthy. Only a complete parent inventory plus every required
desired-queue flow count can produce a new health snapshot.

## Consequences

- Queue monitoring and retirement use fields the pinned broker actually exposes while preserving
  message-content confidentiality and fail-closed deletion.
- Backlog reads and retirement planning remain one bounded parent collection walk; only operations that
  need active-consumer state pay for child counts.
- The reference routine refresh has a deterministic maximum SEMP fan-out that remains inside the
  process's reserved share and nominal 30-second attempt interval.
- Negative: routine health now makes one additional SEMP request per observed desired queue, up to 89,
  instead of obtaining all health values from one parent walk.
- Negative: a fleet topology requiring more than 89 durable queues cannot start this routine monitor
  until a new bound and its request-budget evidence are accepted.
- Negative: `txFlows` measures active consumer flows, not configured ownership. Configuration readback
  remains a separate provisioning control.

## Alternatives considered

- **Keep `bindCount` in the parent selection.** Rejected because the pinned broker's own specification
  omits it and the live endpoint refuses it.
- **Select `txFlows.count` on the parent collection.** Rejected because the pinned endpoint refuses that
  nested selection even though its schema describes the child collection.
- **Enumerate every transmit-flow row.** Rejected because `meta.count` supplies the needed aggregate and
  row data would add unnecessary exposure and pagination work.
- **Assume an unqueried queue has zero binds.** Rejected because that could delete an actively consumed
  stale queue or report a disconnected desired consumer as healthy.
- **Probe every observed queue concurrently.** Rejected because it would bypass the single-session pacer
  and could violate the broker-wide SEMP request ceiling.
