# ADR-0078: One tick is one observation per drone, ordered by drone identifier

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0077](0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) fixes what the fleet simulator
accepts. It does not say what the simulator *does* with it, and four accepted records each constrain one
corner of that fold without any of them naming the fold itself.

- [ADR-0039](0039-drone-connectivity-states-and-recovery.md) puts the interval in the adapter: "the
  adapter decides once per interval whether a heartbeat was observed and applies exactly one
  transition", and the pure module counts consecutive outcomes and reads no clock.
- [operating-parameters.md](../operating-parameters.md) states that "the heartbeat is a dedicated
  liveness signal, not an inference from the telemetry stream: routine telemetry uses direct delivery
  and may be dropped under congestion, so absence of telemetry is not evidence of absence of the drone".
- [ADR-0073](0073-sector-lifecycle-states.md) applies `IMPERIL` when the holding drone's connectivity
  enters `OFFLINE` and `RECOVER` when it leaves, by the adapter comparing the state before and after.
- [ADR-0072](0072-mission-lifecycle-states.md) separates an exhausted search from an aborted one and
  makes `COMPLETED` reachable only from `ESCALATED`.
- [ADR-0067](0067-normalized-dashboard-events-and-reduced-state.md) fixed ascending byte order of the
  identifier as the order of every collection inside the reduced dashboard state, because array order is
  semantic under [ADR-0027](0027-integer-only-canonical-serialization.md) and insertion order would
  otherwise enter the digest.
- [ADR-0027](0027-integer-only-canonical-serialization.md) makes no floating-point value representable
  where a digest can reach.
- [LIMITATIONS.md](../LIMITATIONS.md) bounds the model to "a uniform sector sweep" with no
  probability-of-area weighting and "a simplified point-mass model driven by committed scenario
  parameters" with no wind, no weather effect on flight, and no turn-radius constraint.

Nothing says what one tick is, in what order its events occur, how a position advances, or which
lifecycle edges the fold is entitled to apply.

## Decision

**One tick is one heartbeat-or-miss observation per drone**, read from the scenario's absent-heartbeat
schedule. It is never inferred from whether a telemetry event was published or acknowledged.

**Within a tick, drones fold in ascending byte order of drone identifier** — the ADR-0067 rule — so one
tick has exactly one event order and the reading sequence is reproducible.

**Motion is integer addition.** Each drone's position advances by the per-tick displacement its scenario
entry declares, in microdegrees. There is no trigonometry: the published `headingDegrees` and
`groundSpeedCentimetresPerSecond` are scenario-declared constants describing the leg the drone is
flying, not values the fold derives from the displacement. The fold checks only that the two agree about
whether the drone is moving at all — a zero displacement requires a zero ground speed and a non-zero
displacement requires a non-zero one — and ADR-0077 makes that a construction-time refusal.

**A step that would take latitude or longitude outside the documented coordinate range is a typed
refusal naming the drone.** It is not clamped and not wrapped.

**Battery is held in integer permille**, drained by the scenario's per-tick rate, floored at zero, and
published as `permille // 10`. The floor divide is deliberate: a drone at 999 permille publishes 99.

**Sector edges come from the connectivity delta**, never from a state name. Comparing the connectivity
state before and after the tick's single observation: entering `OFFLINE` applies `IMPERIL`, leaving
`OFFLINE` applies `RECOVER`, and every other movement — including into and out of `DEGRADED` — applies
nothing.

**The uniform sweep is a tick count.** A sector accumulates one swept tick on any tick where it is
`ASSIGNED` and its holding drone's post-observation connectivity is not `OFFLINE`. At `ticks_to_sweep`
accumulated ticks the fold applies `SWEEP`. The counter does not advance while a sector is `AT_RISK`,
and a recovery does not reset it: the sweep the drone actually flew is the fact ADR-0073 says the audit
trail needs.

**The fold applies exactly these lifecycle edges and no others:**

| Machine | Edge | When |
| --- | --- | --- |
| Mission | `START` | The first tick of a run |
| Mission | `EXHAUST` | The tick on which the last sector reaches `SEARCHED` |
| Sector | `ASSIGN` | The first tick, once for each sector the roster names |
| Sector | `IMPERIL` | The holding drone's observation moves it into `OFFLINE` |
| Sector | `RECOVER` | The holding drone's observation moves it out of `OFFLINE` |
| Sector | `SWEEP` | The sector reaches `ticks_to_sweep` swept ticks |
| Connectivity | miss or heartbeat | Once per drone per tick |

`ESCALATE`, `COMPLETE`, and `ABORT` are not the fold's to apply: an escalation records a published
`escalate-rescue` command that only a consumed approval authorizes, a completion follows it, and an
abort is an operator act. `REASSIGN` is not the fold's either: ADR-0041 makes it the result of an
accepted `assign-sector` command, which is command intake.

**A tick after `EXHAUSTED` is refused.** The mission machine has no edge out of it, and folding one
would manufacture state.

**A refused telemetry publication is counted, not fatal.** Telemetry is contractually droppable, and a
simulator that stopped on one dropped event would model the wrong failure.

**Two machines are deliberately not driven, and the reason is a parameter rather than effort.** The
command dispatch lifecycle of [ADR-0074](0074-command-dispatch-lifecycle.md) needs the command send
budget, and the evidence score of [ADR-0076](0076-evidence-score-bands.md) needs the band boundaries;
both are `open` rows in [operating-parameters.md](../operating-parameters.md). Command intake is blocked
a second time by the absence of a durable queue. They land with their parameters.

## Consequences

- Determinism becomes an asserted property rather than a claim: two folds of one scenario produce
  identical machine states and an identical reading order, and no wall clock, random source, hash order,
  or scheduler participates.
- Because motion is integer addition, the same scenario produces the same track on both supported
  platforms, with no dependence on the C library's `cos` and `sin`.
- The reassignment step of the release scenario becomes reachable end to end for the first time: a
  scheduled link loss imperils a real sector through the real connectivity machine.
- Negative: the published heading and ground speed are scenario-declared, and beyond the moving/not-moving
  check the fold does not verify they agree with the displacement. A scenario can declare a drone
  reporting north at 30 m/s while displacing it east. Catching that needs the trigonometry ADR-0027 keeps
  out of digest-covered values, so it is a scenario-authoring obligation.
- Negative: altitude never changes. It is a scenario constant, so no drone climbs or descends, and the
  dashboard's altitude reading is decoration until another displacement field and another record exist.
- Negative: the sweep is a timer, not coverage. A sector's size does not affect how long it takes,
  because the scenario carries no geometry. That is inside the uniform sweep LIMITATIONS.md permits, and
  it is also the reason this fold cannot be described as a search model.
- Negative: an out-of-range step fails mid-run rather than at scenario acceptance, because whether a
  scenario reaches the coordinate bound depends on how many ticks it runs, and the run length is not part
  of the scenario.
- Negative: `REASSIGN` stays unreachable from this fold, so the fifth row of ADR-0073's table is
  exercised only by the domain's own tests until command intake lands.
- Negative: every drone holds exactly one sector, because every roster entry names one. A spare drone,
  or two drones over one sector, cannot be expressed.
- Adding an edge to the table above is a new record, because the table is what stops the adapter from
  turning a state name into authority.

## Alternatives considered

- **Infer the heartbeat from published telemetry.** Rejected: operating-parameters.md states that absence
  of telemetry is not absence of the drone, precisely because telemetry is droppable. Inferring it would
  make a congested broker look like a lost fleet.
- **Compute the displacement from heading and ground speed with trigonometry.** Rejected: no
  floating-point value is representable where a digest can reach (ADR-0027), and the last-bit behaviour
  of `cos` and `sin` differs between C libraries, so the determinism claim would rest on the platform.
  A committed integer trigonometric table would fix both and is machinery this fold does not need.
- **Clamp a position at the coordinate bound.** Rejected: it publishes a position the drone is not at,
  and a dashboard would render a fleet piled against a meridian as if that were the search.
- **Wrap longitude at ±180.** Rejected: it models a circumnavigating search this project does not claim,
  and the wrap renders as a jump across the map.
- **Reset the sweep counter when a sector is imperilled.** Rejected: it discards the sweep the drone
  actually flew, which ADR-0073 identifies as the fact the audit trail needs.
- **Round the published battery up, or to nearest.** Rejected: an optimistic battery reading is the wrong
  direction for a number an operator uses to decide whether a drone can finish a leg.
- **Apply `EXHAUST` when the last battery reaches zero.** Rejected: ADR-0072 separates an exhausted search
  from a failed one, and a flat fleet is a failure rather than a completed search.
- **Order a tick's drones by roster order.** Rejected: ADR-0067 already fixed ascending identifier order
  for the reduced state's collections, and two orders for one fleet would make a replay comparison depend
  on which order the producer happened to use.
- **Continue folding after `EXHAUSTED`.** Rejected: the mission machine has no edge out, so the fold would
  either manufacture a state or silently keep publishing telemetry for a mission that has ended.
- **Apply `REASSIGN` when a sector stays at risk for some number of ticks.** Rejected: reassignment is a
  gateway command under ADR-0041, and inventing a timer for it here would put command policy in the
  adapter.
