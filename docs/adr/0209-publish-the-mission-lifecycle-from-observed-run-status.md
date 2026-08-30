# ADR-0209: Publish the mission lifecycle from observed private run status

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes in part:** none

## Context

[ADR-0189](0189-reconcile-dashboard-runtime-with-the-solace-data-plane.md) assigns mission-event
publication to the dashboard API: "The dashboard API owns public authentication, durable mission
mutation, application-outbox staging, and mission-event publication; the fleet owns telemetry,
critical events, and command effects." Nothing implements it. Every consumer does:

- `envelope.BINDINGS` binds `aerial-rescue.v1.mission.event.lifecycle` to its payload schema and to a
  `mission-lifecycle` source pattern;
- `view.py` projects it to the `missionLifecycle` kind in the never-droppable `MISSION` class;
- `principals.py` grants `DASHBOARD_API` publish and `RECORDER` subscribe, and `queues.py` puts
  `MISSION_EVENT` on the recorder's combined lifecycle queue;
- the recorder applies the [ADR-0072](0072-mission-lifecycle-states.md) transition table to it and
  writes the audit row the dashboard's projection folds.

The consequence is recorded in `release-evidence/phase-3/deployed-telemetry-path.md`: a deployed run
publishes 280 telemetry events, 42 sector events, and 3 connectivity events, the fleet's own control
surface reports `{"completedTickCount":14,"state":"EXHAUSTED"}`, and the operator's mission reads
`PLANNED` from beginning to end. The mission's own story is the one thing the mission never tells.

What is missing is only the trigger. Three things could supply it:

1. The fleet publishes the edge it already computes. That reverses ADR-0189 and requires a new broker
   grant.
2. The dashboard derives exhaustion from its own sector fold — twenty sectors `SEARCHED` means the
   sweep ended. That re-derives a rule the fleet owns, in the read-side projection.
3. The dashboard reads the run state private control already reports.

The third has an authority that already exists and is already reached. `ScenarioPort.status(runId)`
returns exactly `PLANNED|SEARCHING|EXHAUSTED|ABORTED`; `scenario_service/control.py` maps the fleet's
own run state onto those four values and pins a terminal one so the fleet is never asked again. The
dashboard already calls it to reconcile an uncertain start.

## Decision

The dashboard API runs one bounded mission-lifecycle observer for the process lifetime. Each
observation reads the current run pointer, skips anything that is not a started live run, reads the
recorder-owned durable lifecycle, and — while that lifecycle is not terminal — asks private control
for the run's state. It stages a publication only when ADR-0072's transition table admits an edge
from the durable state to the observed one.

Three properties make it safe to run unattended.

**The domain table is the only authority.** A status the table cannot reach from the durable state is
refused, not published. A stale, crossed, or restarted private service cannot rewrite an operator's
mission, and no state is invented for a mission that has already ended.

**The decision and its row share one lock.** `DashboardLifecycleTransaction` reads the recorder-owned
lifecycle column `FOR UPDATE` and stages inside the same transaction, so a recorder commit cannot land
between deciding and staging. The terminality pre-check is a separate short read, because the
established rule here — stated in the store adapter itself — is to release that lock before calling
private control.

**The event identity is derived, not generated.** The application outbox's primary key is
`(producer, event_id)`, staging is `ON CONFLICT DO NOTHING`, and rows are never deleted. Deriving the
identity from the mission and the state it reached therefore makes restaging a durable no-op. The
observer needs no memory, no new table, and no fifth `idempotency_claim` kind, and it is correct
across a process restart because the guard is the row that is already there.

The producer source is `urn:aerial-rescue:mission-lifecycle:{runtimeId}`, which the binding's pattern
requires; the `dashboard-api` source that carries operator mutations is not legal for this family.
One API process is one producer epoch, so a restart cannot collide with a predecessor's recorded
high-water — the rule [ADR-0140](0140-scope-live-telemetry-producers-to-one-mission.md) established
for the fleet.

The interval is 1,000 ms, matching the prepared tick interval, so a lifecycle change is visible to the
operator within one tick of the fleet reaching it. `docs/operating-parameters.md` owns the value.

The observer stages; the serving loop publishes
([ADR-0208](0208-publish-the-dashboard-outbox-on-the-serving-cycle.md)). It holds no broker session,
so it neither competes with the supervisor's one owned session nor participates in its readiness.

## Consequences

- The mission reaches `SEARCHING` when the fleet begins and `EXHAUSTED` when the sweep ends, in the
  audit trail, in the reduced state, and in the browser. The `MISSION` event class ADR-0067 reserved
  finally has a producer.
- Reset already terminates a mission by creating a successor; publishing the predecessor's `ABORTED`
  is a further increment and is **not** delivered by this record. Until it is, a reset predecessor's
  durable lifecycle stays at whatever it last reached.
- Negative: the dashboard now polls private control while a live mission is unfinished. That is one
  loopback request per second for the run's duration, plus one durable single-row read per
  observation. A settled mission stops asking entirely, so the idle cost is the read alone.
- Negative: the terminality pre-check takes the same exclusive row lock as the deciding read, so a
  live mission takes it twice per second on a row the recorder also writes. At this rate that is not
  contention, but it is lock traffic bought to avoid an HTTP call.
- Negative: publication is not immediate. The observation interval, the serving cycle's drain, and the
  recorder's own fan-in each add a step, so the operator sees the transition within a few seconds of
  the fleet reaching it rather than at the instant it happens.
- Negative: if an unexpected exception escapes `observe_once` — which converts every expected store
  and private-control failure into a typed outcome — the task ends and the shutdown path re-raises it.
  Mission publication then stops while the API stays up and readiness stays true, and nothing surfaces
  that until shutdown. Making a stopped observer visible is separate work.
- The dashboard gains a dependency on private control being reachable for mission publication, on top
  of the one it already had for start, status, and cancel. It is not a readiness dependency: losing it
  costs the mission event, not telemetry, the timeline, or the approval boundary.

## Alternatives considered

- **Let the fleet publish the edge it computes.** Rejected: it reverses ADR-0189's division and
  ADR-0158's brokerless scenario boundary, and it needs a new publish grant on a family the fleet has
  never held. The fleet already tells the truth through a surface the dashboard can read.
- **Derive exhaustion from the dashboard's own sector fold.** Rejected: twenty `SEARCHED` sectors
  meaning "the search is over" is a mission rule the fleet owns, and re-deriving it in the read-side
  projection puts business logic where `apps/dashboard/AGENTS.md` forbids it and creates a second
  authority that can disagree with the first.
- **Stage the edges at the operation boundary instead.** Start could stage `SEARCHING` itself, since
  it knows. Rejected as the general mechanism: it covers exactly one of the three edges, leaves
  `EXHAUSTED` — the one the operator is waiting for — with no producer at all, and puts mission
  publication inside `OperationCoordinator`, which is explicitly documented as keeping accepted
  mutation state separate from recorder-owned mission lifecycle.
- **Add a fifth idempotency kind for mission publication.** Rejected: it needs a migration to change
  revision 0010's four-value constraint, and the outbox's own primary key already provides exactly the
  guarantee, permanently, for free.
- **Subscribe the dashboard to a fleet-published mission event.** Rejected: it is alternative 1 plus a
  subscribe grant, and the recorder — not the dashboard — is the mission event's consumer by design.
