# Phase 3 evidence: a started mission reaches the durable store

- **Recorded:** 2026-08-29, 17:00–17:45 local (America/Toronto; container timestamps UTC). This record
  follows `dashboard-projection-recovery.md`, which left the deployed `dashboard-api` healthy but noted
  that telemetry arrived at roughly one event per ten seconds and that the dashboard timeline was
  structurally empty.
- **Governing documents:** the `Fleet runtime` and `Dashboard runtime` rows of
  `docs/IMPLEMENTATION_PLAN.md`; the producer is the operator's Compose composition through the
  `CONTRIBUTING.md` runbook, not a test class of `docs/TESTING.md`; the claim ceiling is
  `docs/LIMITATIONS.md`'s: live on one local host, not release evidence.
- **Revision:** `fix/deployed-telemetry-path` at `4c7aba5`; tree clean. The four commits under test are
  `b399d8f` (ADR-0140 producer scoping), `9b3f0c1` (permanent-refusal handling), `b1abf44` (ADR-0207
  fan-in drain), and `4c7aba5` (fleet lifecycle publication).
- **Host and versions:** Apple Silicon, macOS 26.6.2 (Darwin 25.6.0); the owned
  `aerial-rescue/application` image rebuilt during this record.
- **Scope:** one started mission observed to the durable store. It does **not** cover the browser, the
  mission-lifecycle event (the mission still reads `PLANNED`), the approval flow, the Agent Mesh chain,
  or replay.

Redaction: no credential, password, private key, bearer, or tenant identifier appears here.

## What was wrong

The fleet was never at fault. Its own control surface reported a complete sweep while the store held
nothing for that mission:

```text
GET /internal/v1/runs/{runId}
{"completedTickCount":14,"state":"EXHAUSTED","telemetryPublicationCount":280}

select count(*) from audit_record where mission_id = 'mission-6dabe4dc…';   ->  0
```

Three deployed-composition defects, each absent from its parallel twin:

1. `FleetExecutor._publish_readings` omitted `producer_source`, so telemetry carried the stable
   `urn:aerial-rescue:drone:{droneId}` that ADR-0140 replaced on 2026-08-26. `dashboard_broker_source`
   held 300 such sources at high-water 13, and a restarted fleet replays sequence 0. ADR-0140's own
   regression test imported `service._publish` — the composition the container cannot reach — so the
   violation passed every gate.
2. The refusal that followed ended the recorder process. `service.py:120` calls `process_next()` with
   no handler:

   ```text
   DashboardEventError: the broker event sequence is behind its producer high-water mark: 0
   aerial-rescue-mesh-recorder-1   restarts=1
   ```

   ADR-0206 predicted a badly-behaved producer would surface once provenance linking enforced the
   sequence rule; it surfaced as a crash.
3. `RecorderBrokerReceiver.receive()` spent the full 1,000 ms window on each of ten channels, so one
   revolution of the fan-in admitted at most one message. The broker's own client statistics show the
   traffic was always delivered: `aerial-rescue-recorder … topicMsgsDelivered: 280`.

A fourth defect kept the sweep invisible even when telemetry flowed: `_publish_lifecycle` existed only
in the parallel `service.py`, so the deployed executor advanced its sweep in memory and published no
sector or connectivity event at all.

## What was run

```sh
just mission-control-up            # exit 0, every container healthy
delete from dashboard_current_run; # authorized, one row, backed up first
# start through the browser boundary: bearer from the bootstrap document,
# Origin http://127.0.0.1:8080, UUIDv4 idempotency key, canonical body
POST /api/v1/scenarios/wilderness-missing-person/start -> 202
  missionId mission-4bb0e9da68c547f0b64ca8461bd00b1c
  runId     run-a15a9234c85343dda71f8a6e1468f1da
```

## Readback

```text
kind                                                count
aerial-rescue.v1.drone.telemetry                      280
aerial-rescue.v1.sector.event.lifecycle                42
aerial-rescue.v1.drone.event.connectivity-changed       3

sector states reached      ASSIGNED 21 · SEARCHED 20 · AT_RISK 1
connectivity states        CONNECTED 1 · DEGRADED 1 · OFFLINE 1

provenance rows linked     325
watermark join             325
recorder                   restarts=0, healthy
fleet-simulator            restarts=0, healthy
```

An earlier run of the same build, before the fleet lifecycle commit, isolated the first three fixes:
280 audit rows, 280 provenance rows, watermark 280, and **20 distinct producers** of the form
`urn:aerial-rescue:drone-run:<sha256>` — ADR-0140's shape, one per simulated drone, where the store
had previously held only stable sources.

## What this proves

- Every telemetry event the fleet publishes is recorded, linked to its broker provenance, and visible
  to the watermark the dashboard's snapshot reconstruction joins against.
- The scenario's designed narrative is present for the first time: twenty sectors assigned and searched,
  one sector put `AT_RISK` when its holder went offline, and that drone's `DEGRADED → OFFLINE →
  CONNECTED` transitions.
- A restarted fleet no longer collides with durable history, and a producer that does misbehave is
  refused without ending the recorder.

## What this does not prove, and what remains

1. **The browser was not opened.** This record observes the durable store only. The dashboard's fold is
   inferred from the watermark advancing, not seen.
2. **The mission still reads `PLANNED`.** Nothing publishes `Family.MISSION_EVENT`; only the dashboard
   API is granted it, and it stages none. The fleet reaches `EXHAUSTED` on its own control surface and
   that fact never reaches the operator.
3. **Replay is still unexercised on this stack.**
4. **The host ran out of disk during this record.** PostgreSQL crash-looped on
   `could not write to file "pg_logical/replorigin_checkpoint.tmp": No space left on device`, and the
   broker's SEMP management plane stopped answering until it was restarted. Roughly 19 GB of unrelated
   images were removed with explicit authorization, taking the volume from 98% to 57%. Nothing in this
   record was measured while the disk was full.
