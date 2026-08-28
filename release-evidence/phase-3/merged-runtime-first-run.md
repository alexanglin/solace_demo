# Phase 3 evidence: the merged runtime's first container composition

- **Recorded:** 2026-08-28, 09:12–10:06 local (America/Toronto; container and broker logs UTC).
  The first attempt's start is the PostgreSQL container's creation, 13:12:16Z; the last readback
  is the monitor line count at 10:06.
- **Revision:** `feat/demo-solace-end-to-end`, starting at `b45a57d` (the merge `466df96` plus the
  commits the first live data-plane runs produced) and ending at `e120fe5`. Attempt 1 failed at the
  provisioner before the recipe reached its image build; attempt 2 built its images from
  `b45a57d`, attempt 3 from `1860adf`, the 09:39 re-provisioning ran from
  `ad02fb3`, and the two `mission-control-up` attempts built from `ad02fb3` and `e120fe5`
  respectively; the final attempt's image build started at 09:59:33, 95 s
  after `e120fe5` (09:57:58), from the tree that commit left. Every attempt ran on the working tree
  that its named commit captured, and `git status` was empty after each commit; between attempts the
  tree changed only by the commits named below and by `9818ed7`, the D1 record's corrections.
- **Governing documents:** the criterion is the `Event broker` and `Dashboard runtime` rows of
  `docs/IMPLEMENTATION_PLAN.md`; the producer is the operator's Compose composition through the
  `CONTRIBUTING.md` runbook (`just up`, `just mission-control-up`), not a test class of
  `docs/TESTING.md` — no executable test produced these observations; the claim ceiling is
  `docs/LIMITATIONS.md`'s: live on one local host, not release evidence.
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
  definition; the per-checkout CA and role credentials; the two private-control bearers
  (`scenario-control-bearer`, `fleet-control-bearer`) `scripts/broker-secrets.sh` generated on
  2026-08-27 and the `semp-monitor-password` file; the shared `aerial_rescue` database at revision
  `0005`; the reference roster plus `drone-dispatch-probe` provisioned (91 durable queues); the
  D1 disposable databases (13 `aerial_rescue_app_probe_*` databases now exist) and dead-message
  residue on nineteen `_dmq` queues; the pre-merge `agent-mesh` container stopped.
- **Scope:** the merged Compose stack's first composition, exercising the `Event broker` and
  `Dashboard runtime` rows of `docs/IMPLEMENTATION_PLAN.md` (ADR-0043, ADR-0139) and its Phase 3
  "live qualification pending" bullets without satisfying any Phase 3 acceptance criterion: `just
  down`, `just up --build`, the broker re-provisioning that ADR-0196 required, `just probe-image`,
  the agent-card readback, and `just mission-control-up --build` (twice). (The session's working
  plan numbers this step "Increment 0.3"; that plan is not a repository document.) It does **not**
  cover the `services` profile's
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

Between attempts 2 and 3 an ad hoc read tested the log's permissions from the application image on
the read-only volume subpath as user 10001 and as 1000001 (finding 3); its exact invocation was not
retained, and it is not reproduced here.

Read-only readbacks, in the order first taken (the admin credential is read from
`deploy/secrets/broker-admin-password` inside the command and never printed):

Read-only readbacks, in the order first taken. Each SEMP request ran as `docker exec
aerial-rescue-mesh-broker-1 curl -s <basic authentication for the admin user, read from
deploy/secrets/broker-admin-password by the wrapper and never printed> "<URL>"`; only the URLs
are listed:

```sh
docker exec … "http://localhost:8080/SEMP/v2/config/msgVpns/default/clientProfiles?select=clientProfileName,maxEndpointCountPerClientUsername,maxSubscriptionCount,maxConnectionCountPerClientUsername&count=100"
docker exec … "http://localhost:8080/SEMP/v2/monitor/msgVpns/default/queues?select=queueName,durable&count=200"
docker exec … "http://localhost:8080/SEMP/v2/monitor/msgVpns/default/clients?select=clientUsername,clientName&count=100"
docker exec … "http://localhost:8080/SEMP/v2/monitor/msgVpns/default?select=maxEffectiveEndpointCount"
docker exec … "http://localhost:8080/SEMP/v2/monitor/msgVpns/default/clients/aerial-rescue-recorder/txFlows?count=50"
docker exec aerial-rescue-mesh-broker-1 sh -c 'tail -c 1200 /var/lib/solace/jail/logs/event.log'
docker exec aerial-rescue-mesh-broker-1 sh -c 'grep -n SYSTEM_SYSTEM_STARTUP_COMPLETE /var/lib/solace/jail/logs/event.log'
docker exec aerial-rescue-mesh-broker-1 /usr/sw/loads/currentload/bin/cli -A -e "show logging"
docker exec aerial-rescue-mesh-agent-mesh-1 /opt/venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=3).status)"
docker exec aerial-rescue-mesh-recorder-1 cat /run/aerial-rescue/recorder-readiness/ready.json
docker exec -i aerial-rescue-mesh-scenario-service-1 /app/.venv/bin/python -   # urllib GET /internal/v1/scenarios with Host, with and without Authorization: Bearer <file>
docker exec aerial-rescue-mesh-postgres-1 psql -U aerial_rescue -d aerial_rescue -tAc "select version_num from alembic_version;"
docker exec aerial-rescue-mesh-postgres-1 psql -U aerial_rescue -d aerial_rescue -Ac "select count(*), count(*) filter (where state='pending') from dashboard_operation;"
docker logs --tail 400 aerial-rescue-mesh-broker-event-monitor-1
docker inspect -f '{{.RestartCount}} {{.State.StartedAt}} {{.State.Health.Status}}' <container>
```

No broker object was created or changed by hand.

### Remediation between attempts

- After attempt 1 (spool refusal): none in code; the second attempt waited for the spool.
- After attempt 2 (component `info`; monitor exit 3): `6503036`, then `1860adf` (ADR-0194, ADR-0195).
- After attempt 3 (endpoint ceiling): `ad02fb3` (ADR-0196), re-provisioning, `agent-mesh` restarted.
- After `mission-control-up` 1 (recorder unhealthy): `e120fe5`, image rebuilt.
- After `mission-control-up` 2 (dashboard-api): none — finding 7 awaits a decision.

## What the run found that no offline test could

### 1. The broker is "healthy" before its message spool is

`just down` removed the pre-merge containers (volumes kept) and `just up --build` recreated the
broker and PostgreSQL, waited for both health checks, and ran the provisioner 21 s after the broker
container started (host console clock against the container's `StartedAt`; that container has
since been recreated). The provisioner's first queue `PUT` was refused with SEMP `status=400
code=412 "Problem with PUT: message spool data not available"`; it was the only call observed
refused. The Compose health check was `curl http://localhost:5550/health-check/direct-active`,
and the broker's `event.log` shows the message spool passing `AD-Disabled → AD-Standby →
AD-Activating → AD-Active` after that check had passed. The pinned image also serves
`/health-check/guaranteed-active`; ADR-0194 records the decision to gate health on it and
generalises the dependency to Guaranteed publication and queue binds (inference from the
provisioner's refusal, not measured here). The health check now probes `guaranteed-active`
(`1860adf`).

### 2. The owned gateway component never started inside the Connector

The second `just up --build` provisioned the reference topology, passed the Ollama preflight
(`ollama_chat/qwen3:4b` at the manifest digest locked in `agent-mesh/model-lock.toml`), built
both images, and started `agent-mesh` and `broker-event-monitor`; `agent-mesh` restart-looped with
`Could not find 'info' dictionary for component_class AerialRescueEventMeshGatewayComponent`. The
pinned Solace AI Connector 3.3.12 looks the `info` dictionary up on the component class's module
first (`solace_ai_connector/flow/flow.py`, lines 132–140 of the installed wheel, read 2026-08-28),
and the owned extension defined it only in `app.py` (code reading at `b45a57d`). `component.py`
now defines `info`, carrying the pinned upstream
component's configuration parameters and schemas unchanged (`6503036`).

### 3. The event monitor could not open the log it follows

`broker-event-monitor` exited 3 after four `BROKER_EVENT_SOURCE_FAILED` alerts. The pinned broker
image keeps `jail/logs` at mode 0700 and its files at 0600 under its numeric user 1000001; the
application image runs as 10001. An ad hoc read from the application image on the read-only volume
subpath was refused as 10001 and succeeded as 1000001 with the image's virtual environment still
importing. The monitor now runs as `1000001:10001` (ADR-0195, `1860adf`).

### 4. The Agent Mesh identity's endpoint ceiling was one short

The third `just up --build` (09:27) recreated the broker under the spool-gated health check,
started `broker-event-monitor` past its liveness probe for the first time, and rebuilt the Agent Mesh image with the
component's `info`; the Connector then started every app and every mesh identity connected and
bound, and the process still stopped with `SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE`
on a temporary queue create, after which the app stopped the loop. Across the 18 restarts the
container's log holds 26 such refusals: 10 for the web-ui visualization queue
`gdk/viz/aerial-rescue-web-ui` (the first refusal of all, 13:28:12Z), 8 for the request/response
`reply-queue/…`, and 8 for the gateway's `gdk/event-mesh-gw/data/…` queue. A 75 s read-only SEMP
poll across one restart cycle (sample count and cadence not retained) showed the same owner counts
in every sample: `agent-mesh-agent` owning four non-durable
queues (the `a2a` queues of MissionCoordinator, MissionResponse, and Orchestrator plus the web-ui
gateway queue `gdk/gateway/aerial-rescue-web-ui`), `event-mesh-gateway` two, `event-mesh-tool`
one — each exactly at the `maxEndpointCountPerClientUsername` its client profile is provisioned
with (the profile table in `packages/broker/src/aerial_rescue_broker/provisioning.py`, recorded in
`docs/operating-parameters.md`). The ceiling that was one short is `agent-mesh-agent`'s: its fifth
queue is the visualization queue. The reply-queue is `event-mesh-tool`'s one endpoint and the data
queue is `event-mesh-gateway`'s second; their refusals are consistent with the previous process's
temporaries still counting during a restart, not with a short ceiling (inference from the
counts, not measured). **ADR-0196 and its commit `ad02fb3` name the reply-queue as
`agent-mesh-agent`'s fifth endpoint; the live readback contradicts that attribution while
confirming the decision (five).** The provisioner applied the raised ceiling at 09:39 and
`agent-mesh` became healthy at 09:40 after 18 restarts (container `StartedAt` 13:40:34Z, the
Connector's `Health check: Startup complete` line at 13:40:39Z), with `/readyz` 200 on its
management port 8080 inside the container and three agent cards (`MissionCoordinator`,
`MissionResponse`, `Orchestrator`) on `http://127.0.0.1:8000/api/v1/agentCards`.
`just probe-image` passed every check (interpreter, five pinned versions, gateway plugin, tool
import, 10 runtime symbols, 12 direct-output methods and 3 receipt properties).

The steady state after the fix holds eight non-durable queues — `agent-mesh-agent` five (the
four above plus `gdk/viz/aerial-rescue-web-ui`), `event-mesh-gateway` two, `event-mesh-tool` one
(`#P2P/QTMP/v:broker/reply-queue/…`) — and 91 durable ones: **99 of the VPN's 100 effective
endpoints** (`maxEffectiveEndpointCount` read back over SEMP; the ceiling row in
`docs/operating-parameters.md`) with the probe drone provisioned, 97 with the reference roster
alone. Every
application service that later creates a temporary endpoint competes for that last slot.

### 5. The deployed recorder could never become healthy

`just mission-control-up --build` (09:43) provisioned the reference roster (89 queues; the
provisioner leaves the probe drone's two queues in place, so 91 remain), ran the migration
container to completion (exit 0, no output; `alembic_version` reads `0011_audit_kind` afterwards,
from `0005` before), ran the replay
validator (`validated replay ready`), and started `fleet-simulator` (alive by its `/healthz`
liveness probe; 23 `CLIENT_CLIENT_BIND_SUCCESS` events on its command queues) and `recorder`. The
recorder bound its nine durable queues and both Direct subscriptions (nine
`CLIENT_CLIENT_BIND_SUCCESS` events and `CLIENT_CLIENT_SUBSCRIPTIONS_HIGH … 3` in `event.log`,
whose current bare-text lines carry no timestamps, so the duration is not measurable from retained
artifacts) and its health check reported `not ready` on every probe (25 consecutive failures by the time it was
inspected); the recipe exited 1 after 75 s. The Compose
command is `aerial-rescue-recorder` → `aerial_rescue_recorder.console:main`, and the health check
reads the `RECORDER_READINESS_PATH` freshness lease, but (code reading at `b45a57d`) only the
parallel `main.py` composition (`python -m aerial_rescue_recorder`) ever wrote that lease:
`console.py` never referenced it. The console now publishes the lease from the lifecycle's
readiness after every poll (`e120fe5`); the rebuilt recorder's Compose probe passed within 23 s of
its container `StartedAt` (13:59:47Z; `dashboard-api`, which Compose starts only after the
recorder is healthy, wrote its first log line at 14:00:10Z) with a live `recorder-readiness/v1`
lease.

### 6. The event monitor refuses every line the broker writes

`broker-event-monitor` is "healthy" under ADR-0195 — its Compose probe is `kill -0 1`, a
liveness check — and has emitted nothing but `BROKER_EVENT_INPUT_REFUSED` (`pipeline-degraded`),
one alert per line of the file (77 465 `BROKER_EVENT_INPUT_REFUSED` lines by `docker logs … |
grep -c` against 77 465 lines by `wc -l`, both at 10:06). The live `event.log` (31 893 654
bytes at 13:44 UTC, 09:44 local) holds two text formats and no JSON: lines 1–69 084 — up to and
including the 2026-08-28T01:28Z boot's own startup events, stamped 01:28:05–01:28:32Z — are
timestamped syslog lines (`2026-08-28T01:28:32.141+00:00 <local3.info> broker event: SYSTEM: …`);
from line 69 085, that boot's `SYSTEM_SYSTEM_STARTUP_COMPLETE`, every line — twelve boots by
`grep -n SYSTEM_SYSTEM_STARTUP_COMPLETE` (lines 69 085–72 813), the first under the merged Compose
file — is bare text such as
`SYSTEM: SYSTEM_AUTHENTICATION_SESSION_OPENED: - - SEMP session ::1-5416 internal authentication
opened for user admin (admin)`. The container's stdout that `just broker-events` follows carries
the same bare text. The merged Compose file sets `logging_event_output: all`,
`logging_event_messageformat: json`, and `logging_maxjsonmessagesize: 8192`; the format changed
at the first boot that carried them (the observed boundary at line 69 085), just not to JSON —
that the keys caused the change, and which of the three, is an inference from that boundary, not
a measurement. The
`broker-storage` volume was created on 2026-08-21, before the keys existed (`8b4f6d5`,
2026-08-27). Whether the keys apply only when the storage element is
first initialized, or only to syslog forwarding, was not confirmed from Solace documentation during
the run: `https://docs.solace.com/Software-Broker/Container-Tasks/Configuration-Keys-Reference.htm`
and `…/Container-Tasks/Configuring-the-Broker-Using-Config-Keys.htm` both redirect (302) to the
site's `Not-Found.htm` page (retrieved 2026-08-28T14:36Z), and `/usr/sw/loads/currentload/bin/cli -A -e "show logging"` inside the container
printed only the product banner. ADR-0173 left the readable-file proof to live deployment; the file
is readable as its owner (finding 3), and its format is what this finding adds. The monitor's
contract with the file format is **not satisfied** on this host; no change was made.

### 7. The dashboard cannot start against the scenario-service it is composed with

The second `just mission-control-up --build` (09:59) reached `dashboard-api`, whose one container
failed startup four times (the initial start plus its three `on-failure` restarts) and exited 3:
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

Thirteen `aerial_rescue_app_probe_*` databases from failed D1 attempts remain; nineteen `_dmq`
queues hold messages (`recorder/audit_dmq` 94, `dashboard-api/audit_dmq` 94,
`recorder/operator.approval_dmq` 50, `aerial-rescue-agent-mesh-temp_dmq` 6, among them); the
probe drone's pair of queues survives every `mission-control-up` re-provisioning. No cleanup was
performed during this run; each removal needs its own authorization.

## Observed state after the last attempt (readbacks at 10:04–10:06 local)

| Container | State |
| --- | --- |
| `broker` | healthy (spool-gated `guaranteed-active` probe), `StartedAt` 13:27:28Z |
| `broker-event-monitor` | alive (`kill -0 1` probe), every input line refused (finding 6) |
| `agent-mesh` | ready (`/readyz` on port 8080), `StartedAt` 13:40:34Z, 18 restarts before ADR-0196 |
| `postgres` | healthy; `aerial_rescue` at `0011_audit_kind` |
| `migration`, `replay-validator` | exited 0 |
| `fleet-simulator`, `scenario-service` | alive (`/healthz` liveness probes; `/readyz` not probed) |
| `recorder` | ready (`recorder-readiness/v1` lease probe) |
| `dashboard-api` | exited 3 after four startup failures (finding 7) |
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
| 4 (attribution) | none — ADR-0196 names the wrong queue; decision pending |
| 6, 7, 8 | none — decisions pending |

## Corrections (2026-08-28)

The record was committed as `2da8ab9` at 10:05. Two independent read-only refutation passes
(`de9e5d7`) and a third verification pass with a completeness critic (this commit) corrected it.
Each entry names the original statement, the corrected fact, and the effect.

| Original (`2da8ab9`) | Corrected | Effect |
| --- | --- | --- |
| "Recorded: 09:20–10:05 local" | 09:12–10:06 (PostgreSQL container created 13:12:16Z; last readback 10:06) | none on conclusions |
| "Nothing was modified between an attempt and the commit it names except the record" | `9818ed7` (the D1 record's corrections) also landed between attempts | none |
| "the three private-control secrets `scripts/broker-secrets.sh` added" | two bearers generated by the script plus `semp-monitor-password` | none |
| finding 4: the reply-queue as `agent-mesh-agent`'s fifth endpoint | the fifth is `gdk/viz/aerial-rescue-web-ui`; the reply-queue is `event-mesh-tool`'s one endpoint; 26 refusals across 18 restarts (10 viz, 8 reply-queue, 8 gateway data) | ADR-0196's decision (five) stands; its context names the wrong queue |
| "each exactly at its ADR-0153 `maxEndpointCountPerClientUsername`" | the values live in the provisioning table and `docs/operating-parameters.md`; ADR-0153 sets the policy, not the numbers | none |
| "`event.log` (31 MB at 10:44 UTC)" | 13:44 UTC (09:44 local) | none |
| "is the legacy text format … recreated three times on it without the format changing" | two text formats; the boundary is the 2026-08-28T01:28Z boot's `SYSTEM_SYSTEM_STARTUP_COMPLETE` (line 69 085), twelve boots ago; attribution to the keys is an inference | finding 6 now says the keys changed the format, just not to JSON |
| "ADR-0173 anticipated this proof obligation" | ADR-0173's bullet concerns a readable file in the subpath, which finding 3 satisfied; the format is what finding 6 adds | none |
| "the six `_dmq` queues hold their D1 residue" | nineteen `_dmq` queues hold messages | finding 8 larger |
| "`dashboard-api`, which exited 3 twice" | one container, four startup failures | none |
| "`broker-event-monitor` healthy" | alive by a `kill -0 1` liveness probe | "healthy" no longer claimed for the monitor |
| "Scope: Increment 0.3 of the demo plan" | the `Event broker` and `Dashboard runtime` rows of `docs/IMPLEMENTATION_PLAN.md`; the increment numbering is the session's untracked plan | none |
| "the two documentation URLs returned 404" | 302 → `Not-Found.htm` (200), retrieved 2026-08-28T14:36Z | none |
| "The Solace AI Connector resolves `info` from the component class's module" (unsourced) | `solace_ai_connector/flow/flow.py` lines 132–140 in the pinned 3.3.12 wheel | none |
| "`/readyz` 200 inside the container" | on the management port 8080, not the agent-card port 8000 | none |
| "healthy 39 s after start", "within seconds", "21 s", "75 s poll", "36 min", "23 min" | instruments named or the value marked as not measurable from retained artifacts | none |
| `fleet-simulator`, `scenario-service` "healthy" | alive by `/healthz` liveness probes; `/readyz` not probed | the "proven live" paragraph now separates readiness from liveness |
| "every provisioning call, Guaranteed publication, and queue bind depends on that state" | only the provisioner's first `PUT` was observed; the generalisation is ADR-0194's, an inference | none |
| "Attempts 1 and 2 built their images from `b45a57d`" (`961d845`) | attempt 1 failed at the provisioner before any image build | none |
| "within 29 s of its container `StartedAt`" (`961d845`) | within 23 s (dashboard-api's first log line 14:00:10Z, not its fourth start) | none |

## Amendments (2026-08-28, same environment and revision)

- **16:56Z (12:56 local), pointer:** findings 6, 7, and 9 were resolved on a fresh broker volume and a
  later revision; that changed environment and revision have their own record,
  `merged-runtime-second-composition.md` (ADR-0197, `bbff2f5`, `66344d8`, `3566a7d`).
- **14:33Z (10:33 local):** under authorization given after the record was committed, the thirteen
  `aerial_rescue_app_probe_*` databases were dropped (`drop database` × 13 as `aerial_rescue` in
  the PostgreSQL container; `pg_database` then lists none with the prefix). The header's "13 …
  now exist", finding 8's "remain", and "No cleanup was performed during this run" describe the
  state at 10:05; this is the first cleanup.
- **14:45Z (10:45 local):** `/readyz` inside the `scenario-service` and `fleet-simulator` containers
  (`urllib` GET with the service's own `Host` header) answered `200 {"ready":true}` for both. The
  record's "alive by `/healthz` liveness probes; `/readyz` not probed" holds for the run; their
  readiness is established by this readback, not by the Compose probes.

## What this proves and what it does not

Durations, counts, and refusal codes in the findings above are the run's console and log
observations; those that no retained artifact reproduces (the first recorder attempt's 25
failures, the 39 s to a healthy recorder, the 75 s SEMP poll, the `just probe-image` output, the
documentation and CLI checks) are reported as observed, not as re-verifiable state.

Proven live on the reference host: the merged Compose definition brings up the broker, PostgreSQL,
the Agent Mesh runtime under the owned entrypoint (ready by `/readyz`, three agent cards),
migrations `0006`–`0011`, the replay validator, the fleet simulator and the scenario service (alive
by their `/healthz` liveness probes; their `/readyz` routes were not probed), and the recorder
(ready by its lease probe); the event monitor opens and reads the log as its owner
(Compose-healthy) while refusing every line of it (finding 6); the rebuilt images pass
`just probe-image`. The full offline suite, `scripts/hooks/python/pytest-full.sh` (the pre-push
authority with the coverage gates), ran green at `e120fe5` from 10:00:20 to 10:01:51 local:
3 744 passed, 100 deselected, 411 subtests, 88.51 s.

Not proven: the dashboard API and Caddy (finding 7), the `services` profile, any telemetry
through the composed stack (no mission was started), the event monitor's alert pipeline on real
events (finding 6), agent-mesh memory under load, and the connection counts under a running
mission. Nothing here is release evidence for Phase 3's acceptance criteria.
