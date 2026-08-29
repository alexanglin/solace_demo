# Phase 3 evidence: the deployed dashboard API starts, recovers, and restarts

- **Recorded:** 2026-08-29, 15:21–16:05 local (America/Toronto; container timestamps UTC). This
  record closes finding 6 of `merged-runtime-second-composition.md`, which left the deployed
  `dashboard-api` crash-looping and named it "the demo's next blocker".
- **Governing documents:** the `Dashboard runtime` row of `docs/IMPLEMENTATION_PLAN.md`; the producer
  is the operator's Compose composition through the `CONTRIBUTING.md` runbook (`just up` already
  satisfied, then `just mission-control-up --build`), not a test class of `docs/TESTING.md`; the claim
  ceiling is `docs/LIMITATIONS.md`'s: live on one local host, not release evidence.
- **Revision:** `feat/operator-approval-surface` at `0c60ad5`; tree clean at that commit. The three
  commits under test are `00c44dc` (prepared-seed regression guard), `2867c5b` (ADR-0205, the store
  adapter projects the committed envelope), and `0c60ad5` (the audit kind binds to its own envelope).
- **Host and versions:** Apple Silicon, macOS 26.6.2 (Darwin 25.6.0), Docker Engine as in the prior
  record; the owned `aerial-rescue/application` image rebuilt twice during this record.
- **Prerequisites and pre-existing state:** the stack left by `merged-runtime-second-composition.md`,
  still running 27–31 hours later. `dashboard-api` was `Exited (3)` and `caddy` `unhealthy` at the
  start of this record.
- **Scope:** the deployed `dashboard-api` reaching healthy, its readiness, the Caddy entry point, one
  started mission, and two container restarts over that mission's committed audit rows. It does
  **not** cover the browser rehearsal, a complete fleet sweep, the dashboard timeline, the approval
  flow, the Agent Mesh chain, or the `event-portal` profile. Two open observations are recorded below.

Redaction: no credential, password, private key, bearer, or tenant identifier appears here.

## The failure this record closes

Captured before any change, at 19:21:24Z:

```text
aerial-rescue-mesh-dashboard-api-1   Exited (3)   restarts=3
  started=2026-08-28T17:36:00.402501631Z  finished=2026-08-28T17:36:03.700998257Z
aerial-rescue-mesh-caddy-1           unhealthy

image aerial-rescue/application:0.0.0  created=2026-08-28T17:34:49.610548751Z
_seed_projection occurrences in that image : 0
_seed_projection occurrences in the worktree: 3
```

The image predates the `6910745` seeding fix by 71 seconds, so that fix had never executed.

## What was run

```sh
# 1. rebuild with the seeding fix already committed at 6910745
just mission-control-up --build          # dashboard-api still Exited (3)
```

The refusal moved, which is itself the evidence that seeding now works:

```text
before: durable_application.py:275 in lifespan -> activate_mission
        ProjectionError: the ordered event cannot advance the reducer checkpoint:
          'MISSION_UNPREPARED'

after:  durable_application.py:292 in lifespan  (the seeded path)
        projection.py:354 in _validated_audit_envelope
        ProjectionError: the audit columns do not bind to their canonical envelope:
          'evidence-decision'
```

The mission was prepared and the fold advanced past ordinal 1, reaching a row whose stored `kind`
disagreed with its envelope type. A readback grouped by both columns isolated it exactly:

```text
kind                                             | envelope type                                    | n
aerial-rescue.v1.agent.proposal.candidate-location | (same)                                         |   3
aerial-rescue.v1.audit.evidence-decision           | (same)                                         |   3
aerial-rescue.v1.audit.proposal-normalization      | (same)                                         |   6
aerial-rescue.v1.drone.event.salient               | (same)                                         |   7
aerial-rescue.v1.drone.telemetry                   | (same)                                         | 141
aerial-rescue.v1.evidence.decision                 | (same)                                         |   3
evidence-decision                                  | aerial-rescue.v1.audit.evidence-decision        |   3
```

Every group bound except the three the evidence service wrote. `0c60ad5` fixes that writer and the
command gateway, which carried the same defect on the path an operator approval takes.

```sh
# 2. clear the stale run pointer (authorized; one row, backed up first)
#    run-1ccae63108b4454dbc1350d8120513af -- the poisoned rows belong to a mission
#    written before 0c60ad5 and cannot be folded by any build.
delete from dashboard_current_run;       # DELETE 1

# 3. rebuild and compose
just mission-control-up --build          # exit 0
```

## Readback

Every container healthy at 19:52Z, `dashboard-api` and `caddy` included:

```text
aerial-rescue-mesh-agent-mesh-1            Up 22 hours (healthy)
aerial-rescue-mesh-broker-1                Up 29 hours (healthy)
aerial-rescue-mesh-broker-event-monitor-1  Up 35 seconds (healthy)
aerial-rescue-mesh-caddy-1                 Up 27 hours (healthy)
aerial-rescue-mesh-command-gateway-1       Up 5 hours (healthy)
aerial-rescue-mesh-dashboard-api-1         Up 13 seconds (healthy)
aerial-rescue-mesh-evidence-service-1      Up 22 hours (healthy)
aerial-rescue-mesh-fleet-simulator-1       Up 33 seconds (healthy)
aerial-rescue-mesh-postgres-1              Up 31 hours (healthy)
aerial-rescue-mesh-recorder-1              Up 33 seconds (healthy)
aerial-rescue-mesh-scenario-service-1      Up 28 seconds (healthy)
```

```text
GET /api/v1/readiness?mode=degradedLive  -> 200
  {"mode":"degradedLive","readinessVersion":"dashboard-readiness/v1","ready":true,"reasons":[]}
GET http://127.0.0.1:8080/               -> 200
```

One mission started through the browser boundary (bearer read from the bootstrap document,
`Origin: http://127.0.0.1:8080`, a UUIDv4 idempotency key, canonical body):

```text
POST /api/v1/scenarios/wilderness-missing-person/start -> 202
  missionId mission-3ce10402db954051a481dc8b00cf1e31
  runId     run-81f64fcd79b44d5787e3944a532ad7f0
```

Two container restarts over that mission's committed audit rows, each waited to a terminal health
state:

```text
restart 1: health=healthy running=true exit=0 restarts=0
restart 2: health=healthy running=true exit=0 restarts=0
           Application startup complete.
GET /api/v1/readiness?mode=degradedLive -> 200 {"ready":true}
```

`restarts=0` is the Docker restart-policy counter; it stayed zero because the process no longer exits
during startup. Before this record it read 3.

## What this proves

- The deployed `dashboard-api` composition starts against a durable store holding a prepared run,
  reaches readiness, and serves the Caddy entry point.
- It survives two restarts over an active mission's committed audit rows without a restart loop.
- Finding 6 of `merged-runtime-second-composition.md` is closed for the crash and the restart. It is
  **not** closed for the dashboard's live projection; see below.

## What this does not prove, and two open observations

1. **The dashboard's snapshot timeline is still empty, for a different reason than finding 6
   assumed.** `watermark_statement` (`packages/store/.../dashboard/events.py:177`) inner-joins
   `audit_record` to `dashboard_broker_event`, and the deployed recorder never writes that table:
   `capture.Recorder` persists inbox, source event, and audit only, while the `dashboard_broker_event`
   row is built in `_capture_material`, which belongs to the parallel `main.py` composition
   (`TECH_DEBT.md` section 3). `audit_watermark` is therefore structurally zero for every mission the
   deployed recorder records, `fold_basis_through` never reads a page, and the snapshot state and
   timeline stay empty. Readback: `dashboard_broker_event` held 10,601 rows throughout, none of them
   for the new mission, whose newest entries belong to pre-merge missions.
2. **Telemetry arrived far below the scenario's rate.** 26 `aerial-rescue.v1.drone.telemetry` rows
   over roughly four minutes, against the 280 a complete 12-tick sweep of 20 members produces.
   `broker_inbox` shows the recorder consumed 189 and `dashboard-api` 164 across all missions, so
   consumption is live; whether Start drove the fleet into a sweep is unverified here.

Neither observation is a regression from this record's changes; both predate it and are recorded so
the next increment starts from measured state rather than assumption.
