# ADR-0073: Name the sector lifecycle states and drive them from the connectivity edges

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) places the sector lifecycle in the Tier 1 core
and [ARCHITECTURE.md](../ARCHITECTURE.md) names it as one of the five pure domain state machines the
Tier 2 fleet simulator drives. It has the thinnest existing specification of the five.

What the documents fix:

- [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) makes dividing a search area into sectors and
  assigning them across the fleet a success criterion, and its scenario step for a lost drone reads
  "its sector is marked at risk, and Agent Mesh coordinates reassignment".
- [ADR-0041](0041-deny-by-default-command-authority-table.md) already decided that reassignment after
  a connectivity loss is a new `assign-sector` command to another drone, and that `assign-sector` is
  decided by the gateway's deterministic policy with no operator approval.
- [ADR-0039](0039-drone-connectivity-states-and-recovery.md) names the connectivity states and says a
  caller detects the `OFFLINE` to `CONNECTED` edge by comparing the state before and after a
  transition, because there is no separate reconnecting state.
- [LIMITATIONS.md](../LIMITATIONS.md) bounds the search model: coverage is a uniform sector sweep
  allocated against geometry, with no probability-of-area weighting and no sweep-width determination,
  and ordering sector priority by a plausible travel radius is named as follow-on work rather than
  implemented behaviour.
- [CONTRACTS.md](../CONTRACTS.md) lists `missionId`, `droneId`, `commandId`, and `requestId` as the
  identifier levels of the topic grammar. There is no `sectorId` level.

"Marked at risk" is the closest the documentation set comes to naming a sector state, and it is
lowercase prose inside a scenario step. No sector state, transition, or terminal condition is named
anywhere.

## Decision

- The sector states are `UNASSIGNED`, `ASSIGNED`, `AT_RISK`, and `SEARCHED`. A sector starts
  `UNASSIGNED`.
- The events are `ASSIGN`, `IMPERIL`, `REASSIGN`, `RECOVER`, and `SWEEP`.
- The transition table is total over the five pairs below and deny-by-default. Every other pairing of
  a state with an event is refused.

  | From | Event | To |
  | --- | --- | --- |
  | `UNASSIGNED` | `ASSIGN` | `ASSIGNED` |
  | `ASSIGNED` | `IMPERIL` | `AT_RISK` |
  | `AT_RISK` | `REASSIGN` | `ASSIGNED` |
  | `AT_RISK` | `RECOVER` | `ASSIGNED` |
  | `ASSIGNED` | `SWEEP` | `SEARCHED` |

- `SEARCHED` is the only terminal state.
- A sector at risk cannot be swept. The drone that would report the sweep is the one whose link was
  lost, so `SWEEP` is refused from `AT_RISK`.
- `IMPERIL` is applied when the holding drone's connectivity machine enters `OFFLINE`, and `RECOVER`
  when it leaves `OFFLINE`. The adapter compares the connectivity state before and after a transition
  and applies at most one sector event; this machine reads no connectivity status of its own.
- `REASSIGN` and `RECOVER` both land in `ASSIGNED` and stay separate events because they are caused by
  different facts: a new `assign-sector` command to another drone, and the original drone's return.
  Which drone holds a sector lives in the durable store, not in this machine.
- This record adds no `sectorId` level to the topic grammar of
  [ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md), no sector geometry, no priority
  ordering, and no coverage model. `LIMITATIONS.md` bounds those claims and this record does not widen
  them.
- The module is pure and carries no sector record type: the state and event enumerations, a total
  `transition` over the table above, and a terminal-state predicate, in the same shape as the mission
  machine of [ADR-0072](0072-mission-lifecycle-states.md).

## Consequences

- The reassignment step of the release scenario becomes an executable unit test rather than prose:
  assign, imperil, reassign, sweep is a fold this machine accepts, and the fifteen pairs outside the
  table are refused.
- The five events over four states give twenty pairs, five accepted and fifteen refused, and one test
  enumerates all twenty against the table, so no row can be dropped, added, or retargeted silently.
- Unlike the mission machine this one is deliberately cyclic: a sector may be imperilled, reassigned,
  and imperilled again as many times as the fleet loses drones over it. Progress is therefore not a
  property that can be asserted, and the invariant that replaces it is that only `SEARCHED` absorbs.
- Negative: `DEGRADED` does not imperil a sector, only `OFFLINE` does. A sector held by a struggling
  drone still reads `ASSIGNED`, so an operator who wants to see that has to read the drone's
  connectivity separately. This follows ADR-0039's hysteresis rather than fighting it.
- Negative: there is no edge back to `UNASSIGNED`. A sector whose drone is lost waits in `AT_RISK` for
  a `REASSIGN` or a `RECOVER`, and an operator who wants to withdraw a sector from the plan has no
  event. Adding one is a new record.
- Negative: because the machine does not carry the holding drone, a `REASSIGN` naming the same drone
  is indistinguishable here from a `RECOVER`. The command gateway is what refuses that, not this
  table, so the refusal lives in a different layer from the state.
- Negative: there is no mission-abort edge. A sector belonging to an aborted mission keeps whatever
  state it held, because the mission machine owns the ending and duplicating it here would put the
  same fact in two homes.
- Adding a state or an event is a new record together with a table row and its tests, because the
  table gates which sector operations the gateway may act on.

## Alternatives considered

- **A `SEARCHING` state between `ASSIGNED` and `SEARCHED`.** Rejected: the `assign-sector` command is
  what starts the sweep, so an assigned sector and one being swept have the same entry condition. The
  second state would be unreachable by any event the fleet produces, and an unreachable state cannot
  be given a failing test.
- **One `RESUME` event instead of `REASSIGN` and `RECOVER`.** Rejected: they are caused by different
  facts, and collapsing them loses the distinction the audit trail needs between a sector another
  drone took over and one whose original drone came back.
- **`IMPERIL` on `DEGRADED` as well as `OFFLINE`.** Rejected: `DEGRADED` exists to absorb a marginal
  link without flapping, so imperilling on it would reassign sectors on transient loss, which is the
  flapping ADR-0039 chose hysteresis to prevent.
- **An edge from `AT_RISK` back to `UNASSIGNED` for a sector nobody can take.** Rejected: it discards
  the fact that the sector was assigned and partly swept, which the audit trail needs, and it invites
  a second `ASSIGN` path with no way to tell the two apart.
- **A `Sector` record carrying the geometry and the holding drone.** Rejected: there is one consumer,
  and the geometry model `LIMITATIONS.md` bounds is not this machine's concern.
- **Adding a `sectorId` level to the topic grammar in this record.** Rejected: the grammar belongs to
  ADR-0036, and a new identifier level is a contracts change carrying schemas, golden fixtures, and a
  manifest entry, none of which this machine needs.
- **Deriving the sector state from the holding drone's connectivity state.** Rejected: it makes the
  sector unable to distinguish a lost drone from a swept sector, and it couples a per-sector fact to a
  per-drone one so that a single mutated comparison would move both.
