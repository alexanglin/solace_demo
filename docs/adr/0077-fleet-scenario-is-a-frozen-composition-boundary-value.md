# ADR-0077: The fleet scenario is a frozen value the composition root supplies, not a file the simulator reads

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ARCHITECTURE.md](../ARCHITECTURE.md) gives the fleet simulator the job of adapting "the deterministic
scenario to broker and clock ports" and gives the scenario service the job of loading "versioned
scenario definitions" and applying "a deterministic random seed". Neither end of that sentence exists:
`services/fleet_simulator` and `services/scenario_service` are both scaffolds, and the simulator cannot
fold a single tick without knowing which drones exist, where they start, which sector each holds, and
when each link drops.

What the repository already fixes:

- [ADR-0039](0039-drone-connectivity-states-and-recovery.md) requires the connectivity thresholds to be
  injected with no default, and `packages/domain` enforces that: `ConnectivityThresholds` has no default
  values and refuses a configuration that could never degrade or never recover.
- [ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md) extends deny-by-default
  to issuing identities, and `deploy/compose.yaml` gives the scenario service no broker identity at all
  by that decision. There is therefore no broker path from a scenario to the simulator, and creating one
  would be a grant change rather than an adapter change.
- [CONTRACTS.md](../CONTRACTS.md) names eleven topic families. None of them carries a scenario.
- [ADR-0027](0027-integer-only-canonical-serialization.md) fixes the digest-covered value space to an
  integer-only JSON profile, and [ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md) fixes the
  identifier grammar the mission and drone levels obey.
- [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) lists `scenarios/` as a planned directory. It is
  empty, and no scenario schema, fixture, or manifest entry exists.

`services/fleet_simulator/AGENTS.md` states the constraint directly: accept "already validated,
versioned scenario inputs and their resolved deterministic seed at the composition boundary", and do not
"invent one in this member, import another service's implementation, read an arbitrary scenario file
from deep inside the simulator". What that boundary *is* has never been decided, so the simulator has
had nothing to accept.

## Decision

The simulator accepts one frozen `FleetScenario` value at its composition boundary. Obtaining that value
is the caller's job: nothing inside the member reads a file, an environment variable, a broker message,
a clock, or a random source to build one.

The value carries exactly what one tick needs and nothing else:

| Member | Meaning |
| --- | --- |
| `mission_id` | The mission every event names, obeying the ADR-0036 IDENTIFIER rule |
| `drones` | A roster of `DroneStart` records, one per simulated drone |
| `tick_interval_milliseconds` | The interval one fold step represents, which is also the heartbeat interval of ADR-0039 |
| `thresholds` | The domain's `ConnectivityThresholds`, passed through unchanged |
| `ticks_to_sweep` | How many swept ticks a sector needs, uniform across the search area |
| `absent_heartbeats` | Per drone, the tick ordinals on which no heartbeat is observed |

Each `DroneStart` carries its identifier, the sector it holds, its starting latitude and longitude in
microdegrees, its altitude, the heading and ground speed it reports, its starting battery in permille,
its per-tick displacement north and east in microdegrees, and its per-tick battery drain in permille.

Every member is an integer or an identifier inside the ADR-0027 and ADR-0036 value spaces, so a later
committed scenario document decodes onto this value without a second representation of the same fact.

The value validates once, at construction, and is an accepted value thereafter — the arrangement
`packages/contracts` already uses, so the fold never re-validates. It refuses an empty roster; a
duplicate drone identifier; a drone or sector identifier outside the IDENTIFIER rule; a non-positive
tick interval or ticks-to-sweep; a starting position, altitude, heading, ground speed, or battery
outside the telemetry payload bounds in [operating-parameters.md](../operating-parameters.md); a
negative battery drain; a heading or ground speed that disagrees with the displacement about whether the
drone is moving at all; a schedule naming a drone the roster does not; and a negative tick ordinal.

The value carries **no random seed**, because nothing in the fold is random: the link schedule is
explicit and the motion is integer arithmetic. A seed with no consumer would be a determinism promise
the code does not keep.

This record does not decide the scenario document's file format, its schema, its version marker, its
directory, or how the scenario service delivers it. It decides what that later work must produce.

## Consequences

- The simulator becomes testable with no file, no broker, and no clock: a scenario is a literal in a
  test, and the same literal is what the live run uses.
- Every value ADR-0039 requires be injected arrives through one place, so no service-local default for a
  provisional operating parameter can appear anywhere in the member.
- Because the value is integer-only and identifier-conformant, the scenario document that eventually
  lands can be decoded straight onto it; the boundary does not move when the file format arrives.
- Negative: the member declares **no console script** and `deploy/compose.yaml` keeps its shell command,
  because a process entry point would need a scenario and there is nowhere honest to get one. The
  composition root is exercised by tests rather than by a running container until the scenario service
  exists. That is an owed obligation, recorded in [TECH_DEBT.md](../../TECH_DEBT.md), not a defect in
  this boundary.
- Negative: the reference fleet's composition therefore lives in test and evidence code rather than in a
  committed scenario. Nothing yet proves that the 23-drone fleet the plan names is the fleet the
  simulator can carry.
- Negative: nothing on the wire identifies which scenario produced an event, because the value carries
  no scenario identity and no topic level names one. An operator reading a recording cannot tell two
  scenarios apart from the events alone.
- Negative: the refusals are structural. A scenario whose drones all start on one point, or whose
  schedule never drops a link, is accepted, because "uninteresting" is not a property this value can
  check.
- Adding a member is a new record together with its refusal and its tests, because the same value is
  what a committed scenario document will have to satisfy.

## Alternatives considered

- **Read a TOML or JSON scenario file from inside the simulator.** Rejected: `AGENTS.md` for this member
  forbids reading an arbitrary scenario file from inside it, and loading and versioning are the scenario
  service's recorded responsibility in ARCHITECTURE.md. Putting a reader here would create the second
  owner that the split exists to prevent.
- **Deliver the scenario over the broker.** Rejected: no topic family carries one, the scenario service
  holds no broker identity by ADR-0061, and adding either is a contracts and grant change that the fold
  does not need in order to exist.
- **Pass the scenario as loose keyword arguments to the fold.** Rejected: every refusal would have to be
  repeated at every call site, and there would be nothing for a later scenario decoder to produce.
- **Default the connectivity thresholds to the provisional values in operating-parameters.md.** Rejected:
  ADR-0039 injects them with no default precisely so a provisional number cannot become a shipped one by
  nobody noticing.
- **Give each sector its own ticks-to-sweep.** Rejected: [LIMITATIONS.md](../LIMITATIONS.md) documents a
  uniform sector sweep with no probability-of-area weighting, and per-sector effort is exactly the
  non-uniform weighting that record excludes.
- **Carry a random seed for future use.** Rejected: an injected value with no consumer cannot be tested,
  and a seed present in the record would imply the fold has a random branch that a reader would then
  look for.
- **Let the scenario carry the fleet size and generate the roster.** Rejected: a generated roster is a
  second scenario format hidden inside a constructor, and the generation rule would be an undecided
  physics choice with no record.
