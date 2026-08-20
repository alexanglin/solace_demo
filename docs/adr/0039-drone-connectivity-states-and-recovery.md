# ADR-0039: Name the drone connectivity states and count transitions in heartbeat intervals

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) says lost connectivity changes a drone to `DEGRADED`, then
`OFFLINE`, on consecutive missed heartbeats, and
[operating-parameters.md](../operating-parameters.md) counts those transitions in consecutive missed
heartbeat intervals rather than as a wall-clock gap, so the behaviour is reproducible under a
deterministic clock. The provisional values are 3 misses to enter `DEGRADED`, 6 to enter `OFFLINE`,
and 2 consecutive heartbeats to leave `OFFLINE`, at a 1-second interval. Three things are unnamed:
the healthy state a drone starts in, how a `DEGRADED` drone recovers, and which state an `OFFLINE`
drone returns to. [ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) places the drone
connectivity lifecycle in the Tier 1 core, so the machine lands as a pure function with every
transition tested and mutation-scored, and the Tier 2 fleet simulator drives it from a clock port
rather than the machine reading a clock. The 6-second offline-detection target equals the
provisional interval multiplied by the misses to `OFFLINE`.

## Decision

- The states are `CONNECTED`, `DEGRADED`, and `OFFLINE`. Every drone starts `CONNECTED`.
- The status record carries the state and two counters: consecutive missed intervals and
  consecutive heartbeats. Any heartbeat zeroes the miss count; any miss zeroes the heartbeat count.
- The three counts — misses to `DEGRADED`, misses to `OFFLINE`, heartbeats to recover — are injected
  with no defaults, and the record refuses construction unless the degraded count is at least 1 and
  below the offline count, and the recovery count is at least 1.
- A missed interval never improves the state and a heartbeat never worsens it. Reaching the
  degraded count from `CONNECTED` enters `DEGRADED`; reaching the offline count from any state
  enters `OFFLINE`.
- One recovery count returns both `DEGRADED` and `OFFLINE` directly to `CONNECTED`; fewer
  consecutive heartbeats leave the state unchanged.
- The domain counts intervals. The adapter owns the timer and applies exactly one transition per
  interval, reporting whether a heartbeat was observed in it.

## Consequences

- The machine is reproducible under a deterministic clock and needs no clock of its own.
- Hysteresis applies to both recoveries: a marginal link alternating one heartbeat and one miss
  stays impaired rather than flapping, and a link that recovers for one heartbeat and then drops
  stays impaired until it sustains the recovery count.
- A reconnecting drone's command reconciliation keys on the `OFFLINE` to `CONNECTED` edge; there is
  no separate reconnecting state, so a caller detects the edge by comparing the state before and
  after the transition.
- A degenerate configuration fails at construction instead of silently never degrading.
- Negative: the 6-second offline-detection target has zero margin against the interval multiplied
  by the misses to `OFFLINE`, so a Phase 0 change to either provisional value must revisit the
  target. The miss counter grows without bound while a drone stays `OFFLINE`; it is informational
  and bounded by the run length. The operating-parameters row for the recovery count now covers
  `DEGRADED` as well as `OFFLINE`.

## Alternatives considered

- **`ONLINE`, `HEALTHY`, or `LINKED` as the healthy name.** Rejected: the documents speak of
  connecting, disconnecting, and reconnecting, and the fleet panel column is "connectivity";
  `HEALTHY` conflates the link with battery, and no document uses `ONLINE` or `LINKED`.
- **A single heartbeat leaves `DEGRADED`.** Rejected: it makes `DEGRADED` mean both degrading and
  recovering, and flaps on a three-miss, one-heartbeat pattern; the documented anti-flap rationale
  applies to both impaired states.
- **Recovery through `DEGRADED`, so `OFFLINE` returns to `DEGRADED` and then `CONNECTED`.**
  Rejected: `DEGRADED` is defined by a miss streak, so using it as a stop-over turns a warning state
  into a recovery state, and it needs a second count nobody has set.
- **Separate recovery counts for `DEGRADED` and `OFFLINE`.** Rejected: two parameters where one
  provisional value exists.
- **A wall-clock gap instead of counted intervals.** Rejected by operating-parameters.md: a gap is
  not reproducible under a deterministic clock and gives no hysteresis.
- **Defaults in code for the three counts.** Rejected: the values are provisional and have one home.
