# ADR-0072: Name the mission lifecycle states and separate an exhausted search from an aborted one

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) places the mission lifecycle in the Tier 1
core, and [ARCHITECTURE.md](../ARCHITECTURE.md) names it as one of the five pure domain state
machines the Tier 2 fleet simulator drives. Of those five only the drone connectivity machine exists
([ADR-0039](0039-drone-connectivity-states-and-recovery.md)).

Nothing in the documentation set names a mission state. What the documents do fix is the shape around
one:

- [CONTRACTS.md](../CONTRACTS.md) gives `POST /api/v1/scenarios/{scenarioId}/start` and
  `POST /api/v1/scenarios/current/reset`, so there is a start edge and a reset operation, and it makes
  the mission identifier the envelope `subject` of every application event.
- [ADR-0067](0067-normalized-dashboard-events-and-reduced-state.md) reserves `MISSION` as a
  never-droppable dashboard event class, and requires the reduced state to carry the append-only audit
  ordinal of [ADR-0003](0003-postgres-durable-mission-store.md) rather than a wall-clock instant.
- [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) sequences the release scenario from mission
  submission through sector assignment, evidence fusion, operator approval, rescue escalation, and a
  completed mission with an ordered audit trail.
- [ADR-0005](0005-deterministic-command-gateway.md), [ADR-0006](0006-proposal-bound-single-use-approvals.md),
  and [ADR-0041](0041-deny-by-default-command-authority-table.md) already own escalation authority in
  full: agents propose, a deterministic gateway publishes, and `escalate-rescue` is authorized only by
  an approval the protocol has consumed.

So the gap is exactly the state names, the legal transitions, and which of them are terminal. One
further question the documents raise but do not answer: a wilderness search that sweeps its whole area
and finds nothing is a real and common outcome, and the plan's own success criteria distinguish
completing a mission from abandoning one.

## Decision

- The mission states are `PLANNED`, `SEARCHING`, `ESCALATED`, `COMPLETED`, `EXHAUSTED`, and `ABORTED`.
  A mission starts `PLANNED`.
- The events are `START`, `ESCALATE`, `EXHAUST`, `COMPLETE`, and `ABORT`.
- The transition table is total over the seven pairs below and deny-by-default. Every other pairing of
  a state with an event is refused, including every event applied to a terminal state.

  | From | Event | To |
  | --- | --- | --- |
  | `PLANNED` | `START` | `SEARCHING` |
  | `SEARCHING` | `ESCALATE` | `ESCALATED` |
  | `SEARCHING` | `EXHAUST` | `EXHAUSTED` |
  | `ESCALATED` | `COMPLETE` | `COMPLETED` |
  | `PLANNED` | `ABORT` | `ABORTED` |
  | `SEARCHING` | `ABORT` | `ABORTED` |
  | `ESCALATED` | `ABORT` | `ABORTED` |

- `COMPLETED`, `EXHAUSTED`, and `ABORTED` are terminal. `EXHAUSTED` means the search area was swept
  without a decision-eligible candidate; `ABORTED` means a human ended the mission before that.
- Reset is not an edge. `POST /api/v1/scenarios/current/reset` terminates the current mission and
  creates a new one with a new mission identifier, so no mission ever leaves a terminal state.
- `ESCALATED` records that an `escalate-rescue` command was published. It authorizes nothing. The
  approval record and the command-authority table remain the only things that decide whether that
  command may be published, and the machine never reads an approval.
- The module is pure and carries no mission record type: it exposes the state and event enumerations,
  a total `transition` over the table above, and a terminal-state predicate. Identifiers, scenario
  bindings, and the audit ordinal live in the durable store.
- This record closes no kind set. `eventType`, `proposalType`, and `recordType` stay open in the topic
  grammar of [ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md), because no application event
  type lands here.

## Consequences

- The mission timeline gains six named states the dashboard can render and the audit trail can order,
  and the `MISSION` dashboard event class ADR-0067 reserved has something to project.
- Every refusal is reachable from a unit test. The five events over six states give thirty pairs, seven
  accepted and twenty-three refused, and one test enumerates all thirty against the table. A mutant
  that widens the table is killed by a refusal case and one that narrows it by an acceptance case, so
  no entry is an unkillable equivalent mutant.
- Terminality is expressed by the table's absence of outbound pairs rather than by a separate guard, so
  there is one rule to mutate rather than two that can disagree.
- Negative: a search that never escalates cannot reach `COMPLETED`. `COMPLETE` is reachable only from
  `ESCALATED`, so the only mission that completes is one that handed a subject to a rescue. This is the
  price of naming `EXHAUSTED`, and a reader who expects `COMPLETED` to mean "ran to its end" will find
  the naming surprising.
- Negative: `ABORTED` covers both an operator ending a search and a mission that failed for another
  reason. The machine does not carry a reason, so the reason has to be recovered from the audit record
  that accompanied the event.
- Negative: making reset a new mission rather than a transition means a reset costs a new identifier
  and a new set of durable rows, and a reader looking for the previous run has to follow the audit
  trail rather than the mission.
- A new state or event is a new record plus a table row and its tests, because the table gates which
  mission-level operations the gateway and dashboard may perform.

## Alternatives considered

- **Four states: `PLANNED`, `ACTIVE`, `COMPLETED`, `ABORTED`.** Rejected: it collapses the
  search-and-rescue distinction between a search that swept its area and one a human called off, which
  the audit timeline is meant to preserve, and it leaves the reserved `MISSION` event class with no
  mission-level escalation state to project.
- **Five states, dropping `EXHAUSTED`.** Rejected: a search that finds nothing then ends as `ABORTED`,
  which reads in the audit trail as an operator decision that was never made.
- **`COMPLETE` reachable from `SEARCHING` as well as `ESCALATED`.** Rejected: it makes `COMPLETED` mean
  two different outcomes, and the second of them is what `EXHAUSTED` already names, so the extra edge
  is duplication whose refusal case can never be written.
- **A `RESET` event returning a terminal mission to `PLANNED`.** Rejected: it rewinds a mission that the
  append-only audit ordinal of ADR-0003 has already ordered, and it makes the reduced dashboard state
  of ADR-0067 non-monotonic, which is the property its replay determinism hash rests on.
- **A `SUSPENDED` or `PAUSED` state for degraded operation.** Rejected: no document pauses a mission.
  Degraded live simulation and replay are run modes that ADR-0008 and
  [ADR-0009](0009-isolated-side-effect-free-replay.md) keep distinct from mission state, and folding
  them in here would put the same fact in two homes.
- **Carrying the escalation approval state on the mission.** Rejected: it duplicates the state machine
  in `approvals.py`, and two copies of an authorization fact can disagree, which is the failure the
  approval boundary exists to prevent.
- **A `Mission` record dataclass carrying the identifier and scenario now.** Rejected: there is one
  consumer, and the root instructions forbid abstracting before two real consumers require it.
- **Lowercase string states matching the topic kind grammar.** Rejected: the domain refuses values and
  raises structured refusals over enumerations, and the wire spelling is the contracts package's
  concern, not this machine's.
