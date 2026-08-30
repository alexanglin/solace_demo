# Phase 3 evidence: the mission's own lifecycle reaches the operator

- **Recorded:** 2026-08-29 22:00 – 2026-08-30 01:10 local (America/Toronto; container timestamps
  UTC). This record closes gap 2 of `deployed-telemetry-path.md`, which left the deployed mission
  reading `PLANNED` for its whole life and named it: "Nothing publishes `Family.MISSION_EVENT` …
  The fleet reaches `EXHAUSTED` on its own control surface and that fact never reaches the operator."
- **Governing documents:** the `Dashboard runtime` and `Fleet runtime` rows of
  `docs/IMPLEMENTATION_PLAN.md`; the producer is the operator's Compose composition through the
  `CONTRIBUTING.md` runbook, plus the packaged production Playwright inventory of `docs/TESTING.md`;
  the claim ceiling is `docs/LIMITATIONS.md`'s: live on one local host, not release evidence.
- **Revision:** `fix/deployed-telemetry-path`. Three builds were observed, at `d0057b9`, at `231716c`,
  and at the ADR-0213 commit; each is named where its run is described.
- **Host and versions:** Apple Silicon, macOS 26.6.2 (Darwin 25.6.0); the owned
  `aerial-rescue/application` image rebuilt three times during this record.
- **Scope:** one deployed mission observed from the browser and the durable store. It does **not**
  cover the approval chain, the Agent Mesh chain, the evidence flow, or the soak.

Redaction: no credential, password, private key, bearer, or tenant identifier appears here.

## What was wrong

`ADR-0189` gives the dashboard API mission-event publication. Every consumer already existed — the
envelope binding, the committed payload schema, the `missionLifecycle` projection, the `DASHBOARD_API`
publish grant, the recorder's combined lifecycle queue — and nothing produced one. A second defect sat
behind it: the transition applier that moves `dashboard_mission.lifecycle` existed only in the
parallel `service.py` composition, so the container Compose runs would have appended a mission event's
audit row and left the column where it was.

## What was run, and what each run found

**Run 1, at `d0057b9`'s parent.** Start through the browser boundary. The observer staged the
`SEARCHING` edge one second after Start; the outbox published it, the recorder applied it, and the
mission moved `PLANNED → SEARCHING`. Then it stopped. The fleet completed all fourteen ticks — 280
telemetry, 42 sector, 3 connectivity rows recorded — and the mission stayed `SEARCHING` for three
minutes while private control answered `{"state":"EXHAUSTED"}` and PostgreSQL logged no error.

```text
docker compose restart dashboard-api
22:28:50 lifecycle=SEARCHING
22:28:55 lifecycle=EXHAUSTED
```

A fresh observer published the `EXHAUST` edge within five seconds, which is what proved the logic
right and the task dead. ADR-0211 records the fix; ADR-0212 names the cause, found afterwards:
`application_outbox.stage` refuses an existing identity rather than ignoring it, so the second
observation restaged the same derived identity and raised.

**Run 2, at `231716c`'s parent.** The mission reached `EXHAUSTED` and the browser rendered it:

```text
#42  Mission · SEARCHING    2026-08-30T02:32:38.543Z
#327 Mission · EXHAUSTED    2026-08-30T02:32:51.794Z
```

The timeline had no opening entry, because no event reaches `PLANNED`. ADR-0212 records the decision
to announce it.

**Run 3, at `231716c`.** `just mission-control-up --build --force-recreate`; every container healthy.
Start driven by the packaged production Playwright inventory.

## Readback

```text
mission-8e8dc8a914b6426b99b7929d167c0b75          lifecycle EXHAUSTED

kind                                                count
aerial-rescue.v1.drone.telemetry                      280
aerial-rescue.v1.sector.event.lifecycle                42
aerial-rescue.v1.drone.event.connectivity-changed       3
aerial-rescue.v1.mission.event.lifecycle                3

application_outbox   producer dashboard-api   state confirmed
mission event source urn:aerial-rescue:mission-lifecycle:runtime-…
observer failures    0
recorder             restarts=0, healthy
fleet-simulator      restarts=0, healthy
```

Three production browser cases passed in 26.0 s, including the assertion the previous composition's
producer used to satisfy:

```text
expect(timeline).toMatch(/Mission · PLANNED.*Mission · SEARCHING.*Mission · EXHAUSTED/)
```

## What this proves

- The mission's own lifecycle now reaches the operator: the timeline opens with the mission being
  planned, moves to `SEARCHING` when the fleet begins, and reaches `EXHAUSTED` when the sweep ends.
- The deployed recorder moves the lifecycle column it owns, so the durable mission state and the
  browser's fold agree.
- The observer survives a failed observation and reports it, and a repeated observation stages
  nothing.

## What this does not prove, and what remains

1. **The full production inventory is not green.** Six of nine cases passed. Two failed and one did
   not run:
   - `stream-overload.spec.ts` fails in the recording exporter: `selected synthetic mission contains
     an invalid normalized event`. This is **pre-existing and unrelated to the mission lifecycle**.
     `exporter.py` reads `audit_record.payload` as a normalized event document and compares
     `candidate.event.kind` against `stored.kind`, but ADR-0205 made that column the committed
     envelope's own type and the payload the envelope. `store_adapter._normalized_event` is the
     projection the exporter still lacks. The replay export path has had no live producer since the
     merge, which is why it went unnoticed.
   - `runtime-recovery.spec.ts:63` timed out at 120 s starting the recorder container, on a host that
     had been running Docker and test suites for eight hours. It is not reproduced in isolation and
     is recorded as unexplained rather than diagnosed.
2. **The pointer had to be cleared by hand once.** Recreating the scenario service strands a durable
   `dashboard_current_run` whose run its new process epoch does not know, and both Start and Reset
   then refuse durably. One row was deleted with explicit authorization, backed up first; no mission,
   run, or audit history was touched. ADR-0213 removes the dead end, and that fix has its own
   verification obligation below.
3. **The approval chain, the evidence flow, the Agent Mesh chain, and the soak are untouched by this
   record.**
4. **No reset predecessor was aborted live.** ADR-0210's `ABORTED` edge is covered deterministically
   and has not been observed on this stack.
