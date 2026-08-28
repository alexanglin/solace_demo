# Phase 3 evidence: the merged runtime's first container composition

- **Recorded:** 2026-08-28, 09:20–10:05 local (America/Toronto; container and broker logs UTC).
- **Revision:** `feat/demo-solace-end-to-end`, starting at `b45a57d` (the merge `466df96` plus the
  commits the first live data-plane runs produced) and ending at `e120fe5`. Each attempt below names
  the commit its images were built from; the final attempt's image build and the commit `e120fe5`
  started from the same working tree within the same minute. Nothing was modified between an
  attempt and the commit it names except the record you are reading.
- **Host:** Apple Silicon, macOS 26.6.2 arm64; Docker Engine 29.5.3 with 7.65 GiB allocated to the
  virtual machine; `just` 1.58.0; the application runtime's Python 3.14.7 on the host.
- **Versions:** PubSub+ software event broker `solace/solace-pubsub-standard:10.26.0.8799` at
  `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`; PostgreSQL
  `postgres:18.6-trixie` at `sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280`
  (the merged pin, pulled by `just up --build`); the owned images `aerial-rescue/application:0.0.0`
  and `aerial-rescue/agent-mesh:1.28.7` rebuilt during the run; Solace Agent Mesh 1.28.7,
  Solace AI Connector 3.3.12, `solace-pubsubplus` 1.11.0 (read back by `just probe-image`).
- **Prerequisites and pre-existing state:** the D1 broker and PostgreSQL containers
  (`application-data-plane-first-run.md`), the broker already recreated under the merged Compose
  definition; the per-checkout CA and role credentials; the three private-control secrets
  `scripts/broker-secrets.sh` added on 2026-08-27; the shared `aerial_rescue` database at revision
  `0005`; the reference roster plus `drone-dispatch-probe` provisioned (91 durable queues); the
  D1 disposable databases (13 `aerial_rescue_app_probe_*` databases now exist) and the six `_dmq`
  residues; the pre-merge `agent-mesh` container stopped.
- **Scope:** Increment 0.3 of the demo plan: `just down`, `just up --build`, the broker
  re-provisioning that ADR-0196 required, `just probe-image`, the agent-card readback, and
  `just mission-control-up --build` (twice). It does **not** cover the `services` profile's
  `command-gateway` and `evidence-service`, the browser rehearsal, the Agent Mesh live probes,
  the `event-portal` profile, or the Solace Cloud showcase: the composition stopped at
  `dashboard-api` (finding 7) and the remaining steps wait on that decision.

Redaction: no credential, password, private key, bearer, or tenant identifier appears here. Only
identity, image, container, queue, topic, and route names, counts, exit statuses, and durations
are reproduced; identifiers and digests read from the database are elided.

## What was run

```sh
just down
just up --build                                   # 1: FAILED at provisioning (finding 1)
just up --build                                   # 2: agent-mesh restart-looped (finding 2, 3)
just up --build                                   # 3: agent-mesh restart-looped (finding 4)
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh \
    --drone drone-sim-01 ... --drone drone-sim-20 \
    --drone drone-vision-01 --drone drone-navigation-02 --drone drone-comms-03 \
    --drone drone-dispatch-probe                  # after ADR-0196: 91 queues, 24 command queues
docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml \
    up --detach --wait --wait-timeout 180 agent-mesh
just probe-image
curl -s http://127.0.0.1:8000/api/v1/agentCards
just mission-control-up --build                   # 1: recorder unhealthy (finding 5)
just mission-control-up --build                   # 2: dashboard-api exit 3 (finding 7)
```

Read-only SEMP v2 monitor and config reads over the broker container's loopback (`docker exec …
curl http://localhost:8080/SEMP/v2/…`) supplied the client, queue, flow, and profile readbacks;
`docker exec` reads of `/var/lib/solace/jail/logs/event.log` supplied the broker events. No
broker object was created or changed by hand.

## What the run found that no offline test could

### 1. The broker is "healthy" before its message spool is

`just down` removed the pre-merge containers (volumes kept) and `just up --build` recreated the
broker and PostgreSQL, waited for both health checks, and ran the provisioner 21 s after the broker
container started. The provisioner's first queue `PUT` was refused with SEMP `status=400 code=412
"Problem with PUT: message spool data not available"`. The Compose health check was
`curl http://localhost:5550/health-check/direct-active`, which turns healthy as soon as Direct
messaging is up, while the message spool passes `AD-Disabled → AD-Standby → AD-Activating →
AD-Active` afterwards. The pinned image also serves `/health-check/guaranteed-active`, healthy only
once the spool is active; every provisioning call, Guaranteed publication, and queue bind depends
on that state. The health check now probes `guaranteed-active` (ADR-0194, `1860adf`).

### 2. The owned gateway component never started inside the Connector

The second `just up --build` provisioned the reference topology, passed the Ollama preflight, built
both images, and started `agent-mesh` and `broker-event-monitor`; `agent-mesh` restart-looped with
`Could not find 'info' dictionary for component_class AerialRescueEventMeshGatewayComponent`. The
Solace AI Connector resolves `info` from the component class's module and the owned extension
defined it only in `app.py`. `component.py` now defines `info`, carrying the pinned upstream
component's configuration parameters and schemas unchanged (`6503036`).

### 3. The event monitor could not open the log it follows

`broker-event-monitor` exited 3 after four `BROKER_EVENT_SOURCE_FAILED` alerts. The pinned broker
image keeps `jail/logs` at mode 0700 and its files at 0600 under its numeric user 1000001; the
application image runs as 10001. An ad hoc read from the application image on the read-only volume
subpath was refused as 10001 and succeeded as 1000001 with the image's virtual environment still
importing. The monitor now runs as `1000001:10001` (ADR-0195, `1860adf`).

### 4. The Agent Mesh identity's endpoint ceiling was one short

The third `just up --build` (09:27) recreated the broker under the spool-gated health check,
started `broker-event-monitor` healthy for the first time, and rebuilt the Agent Mesh image with the
component's `info`; the Connector then started every app and every mesh identity connected and
bound, and the process still stopped: the MissionCoordinator's request/response app could not
create its `reply-queue/…` — `SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE` — so the agent's
asynchronous initialization failed and the app stopped the loop. A 75 s read-only SEMP poll across
one restart cycle showed the steady state: `agent-mesh-agent` owning four non-durable queues (the
`a2a` queues of MissionCoordinator, MissionResponse, and Orchestrator plus the web-ui gateway
queue), `event-mesh-gateway` two, `event-mesh-tool` one — each exactly at its ADR-0153
`maxEndpointCountPerClientUsername`. ADR-0196 raises `agent-mesh-agent` to five (`ad02fb3`); the
provisioner applied it at 09:39 and `agent-mesh` became healthy at 09:40 after 18 restarts, with
`/readyz` 200 inside the container and three agent cards (`MissionCoordinator`,
`MissionResponse`, `Orchestrator`) on `http://127.0.0.1:8000/api/v1/agentCards`.
`just probe-image` passed every check (interpreter, five pinned versions, gateway plugin, tool
import, 10 runtime symbols, 12 direct-output methods and 3 receipt properties).

The steady state after the fix holds eight non-durable queues (the seven above plus
`#P2P/QTMP/v:broker/reply-queue/…`) and 91 durable ones: **99 of the VPN's 100 effective
endpoints** with the probe drone provisioned, 97 with the reference roster alone. Every
application service that later creates a temporary endpoint competes for that last slot.

### 5. The deployed recorder could never become healthy

`just mission-control-up --build` (09:43) provisioned the reference roster (89 queues; the
provisioner leaves the probe drone's two queues in place, so 91 remain), applied revisions
`0006`–`0011` to the shared database (`alembic_version` = `0011_audit_kind`), ran the replay
validator (`validated replay ready`), and started `fleet-simulator` (healthy; 23 command-queue
binds) and `recorder`. The recorder bound its nine durable queues and both Direct subscriptions
within seconds (`CLIENT_CLIENT_BIND_SUCCESS` × 9, `CLIENT_CLIENT_SUBSCRIPTIONS_HIGH … 3`) and its
health check failed 25 times in a row (`not ready`); the recipe exited 1 after 75 s. The Compose
command is `aerial-rescue-recorder` → `aerial_rescue_recorder.console:main`, and the health check
reads the `RECORDER_READINESS_PATH` freshness lease, but only the parallel `main.py` composition
(`python -m aerial_rescue_recorder`) ever wrote that lease: `console.py` never touched it. The
console now publishes the lease from the lifecycle's readiness after every poll (`e120fe5`); the
rebuilt recorder was healthy 39 s after start with a live `recorder-readiness/v1` lease.

### 6. The event monitor refuses every line the broker writes

`broker-event-monitor` is healthy under ADR-0195 and has emitted nothing but
`BROKER_EVENT_INPUT_REFUSED` (`pipeline-degraded`) since it first read the file. The live
`event.log` (31 MB at 10:44 UTC) is the legacy text format — for example
`SYSTEM: SYSTEM_AUTHENTICATION_SESSION_OPENED: - - SEMP session ::1-5416 internal authentication
opened for user admin (admin)` — and so is the container's stdout that `just broker-events`
follows, although the merged Compose file sets `logging_event_output: all`,
`logging_event_messageformat: json`, and `logging_maxjsonmessagesize: 8192`. The broker's
`broker-storage` volume was created before those keys existed; the broker has been recreated three
times on it without the format changing. Whether the keys apply only when the storage element is
first initialized, or only to syslog forwarding, was not confirmed from Solace documentation during
the run (two documentation URLs returned 404); the read-only CLI accepted no batch `show` command.
ADR-0173 anticipated this proof obligation ("Live deployment must still prove that the pinned broker
image creates a readable `event.log`"). The monitor's contract with the file format is **not
satisfied** on this host; no change was made.

### 7. The dashboard cannot start against the scenario-service it is composed with

The second `just mission-control-up --build` (09:59) reached `dashboard-api`, which exited 3 twice:
its lifespan's `reconcile_pending()` found the one pending `dashboard_operation` row (50 rows exist
from the pre-merge dashboard runs; one is `pending`) and asked the scenario-service for its
catalog; `GET /internal/v1/scenarios` returned **404** with and without the bearer, so the client
raised `DEPENDENCY_UNAVAILABLE`. The scenario-service holds two complete HTTP compositions: the
deployed console `scenario-service` → `service:main` → `http_runtime.py` (mounts `/healthz`,
`/readyz`, run start/status/cancel; the member guide's documented surface; `8b4f6d5`), and
`main.py` → `http.py` (mounts catalog, start, status, cancel, and `recover`, with no health route;
`650822b`). The dashboard's client (`650822b`) needs the catalog and `recover` routes; the Compose
health check needs `/healthz`. Neither composition satisfies both. The same shape recurs in the
fleet simulator (`control_plane/runtime.py` deployed, `control_plane/http.py` parallel) and the
dashboard API (`delivery/production.py` deployed, `console.py` parallel). No change was made; the
reconciliation is a decision, not a defect fix.

### 8. Housekeeping the run surfaced

Thirteen `aerial_rescue_app_probe_*` databases from failed D1 attempts remain; the six `_dmq`
queues hold their D1 residue (`aerial-rescue-agent-mesh-temp_dmq` now spools 6 messages); the
probe drone's pair of queues survives every `mission-control-up` re-provisioning. Each removal
needs its own authorization.

## Observed state after the last attempt (10:05 local)

| Container | State |
| --- | --- |
| `broker` | healthy (spool-gated), 36 min |
| `broker-event-monitor` | healthy, every input line refused (finding 6) |
| `agent-mesh` | healthy, 23 min, 18 restarts before ADR-0196 |
| `postgres` | healthy; `aerial_rescue` at `0011_audit_kind` |
| `migration`, `replay-validator` | exited 0 |
| `fleet-simulator`, `scenario-service`, `recorder` | healthy |
| `dashboard-api` | exited 3 (finding 7) |
| `caddy` | created, never started |

Broker: 99 of 100 effective endpoints (91 durable, 8 non-durable); connections `agent-mesh-agent`
9/9, `event-mesh-gateway` 4/4, `event-mesh-tool` 1/1, `fleet-simulator` 1/1, `recorder` 1/1;
client profile `agent-mesh-agent` reads back `maxEndpointCountPerClientUsername: 5`.

| Finding | Commit |
| --- | --- |
| 1, 3 | `1860adf` (ADR-0194, ADR-0195) |
| 2 | `6503036` |
| 4 | `ad02fb3` (ADR-0196) |
| 5 | `e120fe5` |
| 6, 7, 8 | none — decisions pending |

## What this proves and what it does not

Proven live on the reference host: the merged Compose definition brings up the broker, PostgreSQL,
the Agent Mesh runtime under the owned entrypoint with three agent cards, the event monitor as the
log's owner, migrations `0006`–`0011`, the replay validator, the fleet simulator, the scenario
service, and the recorder, all healthy; the rebuilt images pass `just probe-image`. The full
offline suite ran green at `e120fe5` (3 744 passed, 411 subtests, 88 s, coverage gates enforced).

Not proven: the dashboard API and Caddy (finding 7), the `services` profile, any telemetry
through the composed stack (no mission was started), the event monitor's alert pipeline on real
events (finding 6), agent-mesh memory under load, and the connection counts under a running
mission. Nothing here is release evidence for Phase 3's acceptance criteria.
