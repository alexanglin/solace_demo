# ADR-0084: Give backlog recovery an instrument, and say what it does not measure

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[operating-parameters.md](../operating-parameters.md) has carried "Backlog recovery \| 500 critical
messages drain within 10 seconds after reconnect" since the service-level profile was written. The
table it sits in has no instrument column, and the same document's open-parameter table demands, for
every service-level row, an "instrument definition ... start point, end point, clock, sample count,
statistic, warm-up discarded, machine-state precondition". None of those exists for this row.

The row is also load-bearing rather than decorative. Two other parameters are derived from it: the
queue spool ("500 critical messages must drain within 10 s, so a queue must hold at least 1 MB") and
the command-intake cap ("a cap of at least 500 / 230, which is 2.18, is needed"), and the approval
time-to-live derives from it in turn through
[ADR-0042](0042-approval-time-to-live.md)'s "30 s restart recovery, a 10 s backlog drain, and a 2 s
connected command path".

What blocked the measurement has been removed twice.
[ADR-0080](0080-provision-one-durable-queue-per-guaranteed-consumer.md) provisioned the endpoints and
recorded that the measurement was "now blocked by the absence of a consumer rather than by the
absence of an endpoint"; the fleet simulator's command intake became that consumer; and
[ADR-0083](0083-pace-the-tick-loop-at-a-fixed-rate.md) gave its loop the rate the cap's derivation
assumed. The remaining obstacle was the instrument itself: queue depth was read with a single
unpaged `count=100` request, which reports 100 for a 500-deep queue — wrong at exactly the value
being measured.

## Decision

The backlog-recovery row is measured as follows, and the definition is the row's instrument.

| Member | Definition |
| --- | --- |
| Model of "reconnect" | Every command is published while no consumer is bound, then one fleet-simulator run binds every declared drone's queue and drains. A backlog that accumulated during an absence is what the row is about |
| Start point | The call to `run()`, taken immediately before it on the host clock |
| End point | The settlement of the last of the published commands, stamped by the probe as it passes through a counting wrapper around the run's receivers. Deliberately not the return from `run()`: [ADR-0083](0083-pace-the-tick-loop-at-a-fixed-rate.md) records that a run waits out one final interval it does not need, and on a 10-second target that artifact is a tenth of the value |
| Clock | `time.monotonic()` in the probe process, never a broker or wall-clock timestamp |
| Workload | 500 drone commands, each a valid schema-bound `assign-sector` CloudEvent, distributed as evenly as 500 divides across the drone queues of a fleet of the reference size |
| Fleet size | 23 drones, because the target's own derivation is "23 drones at 1 Hz give 230 opportunities in that window". A run at a smaller fleet measures the per-drone rate and is **not** evidence for this row |
| Sample count | 3 runs |
| Statistic | The maximum of the 3, compared against a bound rather than a percentile, because the row states a bound |
| Warm-up discarded | The first run after provisioning, because a freshly bound flow does not deliver instantly and that cost belongs to binding rather than to draining |
| Machine-state precondition | The default Compose profile healthy; every queue the run touches at depth zero before it starts; the dead-message queue's depth recorded before and after; no other probe, publisher, or consumer against the same broker |

**The first run asserts completeness, not the threshold.** A probe run under this instrument asserts
that every published command left its queue, that the intake tally accounts for all of them with no
superseded or unreadable outcome, and that the dead-message queue did not move. The elapsed duration
is measured and recorded. The `≤ 10 s` comparison becomes an assertion only after this instrument has
produced a baseline, because `release-evidence/AGENTS.md` is explicit that "a measurement may support
a parameter decision, but the evidence record is not where the parameter is selected".

**The probe carries the `performance` class marker** alongside `integration`, `docker`, and `broker`,
and lives beside the other live probes in `tests/integration/`. The class marker carries what it is;
the directory carries the live-broker authorization, drain, and depth-delta rules that already govern
its neighbours.

## Consequences

- The row stops being an unmeasurable assertion, and the three parameters derived from it gain a
  measured basis rather than an argued one.
- A measurement that misses the target is a finding about the system, not a failing test to tune
  away. Nothing in the drain path is a safety control: exceeding the target costs delivery latency,
  never an authorization.
- Negative: **this models an absent consumer, not a transport reconnect.** No TCP session is broken
  and no flow is re-established mid-run, so nothing here measures reconnect reconciliation, an
  in-flight redelivery, or an unsettled message's fate across a dropped connection.
- Negative: the drain rate is bounded by the intake cap times the fleet size divided by the tick
  interval — 69 commands per second at the reference fleet — so this instrument measures the
  *configured* drain rate at least as much as the broker's delivery rate. A result near the target
  says the configuration is adequate, not that the broker is the constraint.
- Negative: 23 drone queues must be provisioned for a measurement to count, and the provisioner
  converges by deleting what the matrix no longer grants, so the backlog fleet has to be named in the
  same invocation as every other probe's drones or their queues disappear.
- Negative: discarding the first run makes the measurement quieter but hides the binding cost, which
  is real and is paid on every reconnect a real drone makes. It is excluded here because it is a
  different quantity, not because it is small; nothing yet measures it.
- Negative: three samples on one workstation is a thin statistic. It bounds the value under one
  machine state and says nothing about variance under load.
- Negative: stamping the end point inside a counting wrapper means the probe observes settlement
  through an object the production run does not have. The wrapper delegates and counts, adding no
  decision of its own, but it is one more thing between the measurement and the code being measured.

## Alternatives considered

- **Keep the row without an instrument until the whole open-parameter table is closed.** Rejected:
  the row already has three parameters derived from it, so it is doing work whether or not anyone can
  measure it, and the table's other rows do not block this one.
- **Assert the 10-second target in the first probe run.** Rejected: the target was derived, never
  measured, and a first run that asserts it either passes and proves nothing new or fails and invites
  tuning the number to the machine. Measure first, then assert.
- **Measure with the five drones the existing probes already provision.** Rejected: 500 messages
  across five queues at three per drone per tick is a 33-second drain by arithmetic, and comparing it
  to a target derived for 23 drones would be a category error.
- **Break the broker connection mid-run to model a real reconnect.** Rejected as a larger and
  different measurement: it needs reconnect reconciliation, which waits on `packages/store`, and it
  would conflate the drain rate with the reconnect's own cost. Recorded above as what this does not
  measure.
- **Use `spooledMsgCount` as the completion signal.** Rejected: it is cumulative and never falls, so
  it cannot say when a queue is empty. Counting the queue's own message collection is the instrument.
- **Publish the backlog through the fleet-simulator identity.** Rejected: only the command gateway
  may publish a drone command, and a probe that borrowed an identity to make setup easier would be
  asserting against a broker configured differently from the one the system runs on.
