# ADR-0083: Pace the tick loop at a fixed rate, and count what overruns

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0077](0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) puts
`tick_interval_milliseconds` in the frozen scenario and calls it "the interval one fold step
represents, which is also the heartbeat interval of
[ADR-0039](0039-drone-connectivity-states-and-recovery.md)". The member validated that number and
read it nowhere: `serve()` looped on the runtime's predicate and the mission's terminality with no
wait of any kind, and every occurrence of the member outside its own constructor was a test literal.

Three claims already rest on a rate nothing ran:

- [operating-parameters.md](../operating-parameters.md) carries "Fleet telemetry \| 23 drones at
  1 Hz" with no instrument. Nothing in the repository could have measured it.
- The command-intake cap is *derived* from that rate: "500 critical messages must drain within 10
  seconds, and 23 drones at 1 Hz give 230 opportunities in that window, so a cap of at least
  500 / 230, which is 2.18, is needed". The arithmetic is sound and its premise was not met.
- ADR-0039 counts connectivity in consecutive missed heartbeat *intervals* and splits the work: the
  domain counts and the adapter times. A package forbidden to read a clock cannot enforce a
  duration, so the interval is this member's to keep and it was keeping none.

`services/fleet_simulator/AGENTS.md` forbids settling a "clock or random-source policy" in a
service-local constant, so how the loop keeps time is a decision rather than an implementation
detail. [ADR-0078](0078-one-tick-is-one-observation-per-drone.md) fixes one tick as one
heartbeat-or-miss observation per drone, which constrains what a recovering loop may do with a
missed interval.

## Decision

The composition root supplies a `Pacer` port with two members: `now_milliseconds()`, a monotonic
reading in whole milliseconds, and `wait(milliseconds)`. It is injected with no default, like every
other boundary ADR-0077 puts on the runtime.

**The interval is measured from the start of each tick, and the loop waits out the remainder.** The
period is therefore the interval rather than the interval plus however long the work took.

**A tick that does not finish inside its interval waits nothing and is counted.** `ON_TIME` and
`OVERRAN` are tallied in `ServeReport.pacing`, beside the tallies for readings and commands, so a
fleet that cannot hold its declared rate reports that instead of running slow and silent.

**A lost interval is never made up.** The loop does not shorten a later interval to recover it.

**The clock is monotonic and is not the stamp source's clock.** A stamp records when an event
happened and belongs on the wall clock; an interval measures how long a tick took, and a wall clock
that steps backwards over an adjustment would make a tick look instantaneous.

**The wait is the last thing in the tick**, after the fold, the publications, and the drain, so the
interval covers the tick's work rather than trailing it.

This record sets **no new number**. The wait is the scenario's own already-validated member.

## Consequences

- The 1 Hz row becomes measurable, and falsifiable: a run whose report carries `OVERRAN` is a run
  that did not hold the rate. The intake cap's published derivation gains the premise it assumed.
- The drain's non-blocking receive window is now literally what
  [operating-parameters.md](../operating-parameters.md) says it is for. "Intake must not become the
  tick loop's pacer" was a rule about a loop with no pacer at all; the loop now has one of its own.
- Offline tests are unaffected in wall-clock cost, because the pacer is injected and the scripted
  one advances a counter. Determinism is unchanged: the fold still reads no clock.
- Negative: **every live run now costs ticks × interval in wall-clock time.** The three live probes
  each gained seconds, and a fleet-scale acceptance run costs its full simulated duration.
- Negative: **a run waits once more than it needs to.** The wait at the end of the final tick happens
  even though the loop is about to stop, because the runtime's predicate is consumed by the `while`
  and cannot be peeked without changing what that predicate means. It costs one interval per run.
- Negative: the report says *that* a tick overran, not *by how much*. A tally is enough to falsify
  the rate claim and not enough to size the overshoot, so a fleet that is slightly late and one that
  is late by a minute read the same.
- Negative: a scenario whose interval is shorter than one tick's work never waits, and the whole run
  is overruns. That is the honest report, but it is a report and not a refusal: nothing here stops
  such a scenario from being composed.
- Negative: `MonotonicPacer` holds this member's only sleep, so a cancelled run blocks inside it for
  up to one interval. Bounded shutdown is not solved by this record.

## Alternatives considered

- **Wait a whole interval after each tick.** Rejected: the period becomes the interval plus the
  work, so the fleet publishes slower than the rate it declares and nothing reports the difference.
  It is the shape that makes a 1 Hz claim quietly false under load.
- **Shorten later intervals to catch up.** Rejected: it publishes two observations closer together
  than any declared rate, and ADR-0078 gives one tick one observation per drone with no rate at
  which a burst of them means anything.
- **Wait inside the fold.** Rejected: the fold is pure and clockless, and a wait inside it would put
  a clock in the deterministic core that ADR-0078 exists to keep clockless.
- **Use the wall clock the stamps already read.** Rejected: it answers a different question, and a
  backwards step would make a tick appear to take no time.
- **Give the interval a row in `operating-parameters.md`.** Rejected: the scenario already carries a
  validated interval per run, and a second number could disagree with the one the fold used.
- **Refuse a scenario whose work cannot fit its interval.** Rejected: how long a tick takes is a
  property of the machine and the fleet size, not of the scenario, so the refusal would be
  unpredictable from the value being validated. The report is the honest instrument.
