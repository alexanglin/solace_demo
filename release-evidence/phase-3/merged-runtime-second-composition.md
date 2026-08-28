# Phase 3 evidence: the merged runtime composed on a fresh broker volume

- **Recorded:** 2026-08-28, 10:58–12:56 local (America/Toronto; container and broker logs UTC).
  This record follows `merged-runtime-first-run.md` (same host, same session) after two things
  changed: the `broker-storage` volume was recreated (a changed environment) and the tree moved from
  `4b854c7` to `66344d8` (a changed revision). Each attempt below names its tree.
- **Governing documents:** the `Event broker` and `Dashboard runtime` rows of
  `docs/IMPLEMENTATION_PLAN.md`; the producer is the operator's Compose composition through the
  `CONTRIBUTING.md` runbook (`just up`, `just mission-control-up`, the `services` profile), not a test
  class of `docs/TESTING.md`; the claim ceiling is `docs/LIMITATIONS.md`'s: live on one local host,
  not release evidence.
- **Revision:** `feat/demo-solace-end-to-end`. The volume reset and first boot ran at `4b854c7`; the
  monitor rebuild at `bbff2f5`; the first `mission-control-up` of this record at `d75058e`; the final
  composition at `3566a7d`; the tree was clean at each commit (`git status` empty after every commit).
  The monitor image running at the last readback predates `66344d8` (the listen-port severity fix).
- **Host and versions:** as in the first record (Apple Silicon, macOS 26.6.2, Docker Engine 29.5.3,
  7.65 GiB, `just` 1.58.0, Python 3.14.7); the same broker and PostgreSQL digests; the owned images
  rebuilt at 09:28 (`agent-mesh`, `c2598a569b22`) and 12:48 (`application`, `06fd47d50e2e`) local.
- **Prerequisites and pre-existing state:** the first record's final state; the 13 probe databases
  already dropped; the `aerial_rescue` database at `0011_audit_kind` throughout.
- **Scope:** the authorized `broker-storage` reset and the default profile's first boot on it; the
  monitor's behaviour on the JSON log; the scenario-service standardization (ADR-0197) and the
  dashboard probe fix composed live; the `services` profile. It does **not** cover a started mission
  (no telemetry flowed through the composed stack yet), the browser rehearsal, the Agent Mesh live
  probes, the `event-portal` profile, or the Solace Cloud showcase.

Redaction: no credential, password, private key, bearer, or tenant identifier appears here.

## What was run

```sh
# volume reset (authorized; the deploy guide otherwise forbids removing a named volume)
docker compose … --profile mission-control --profile services stop fleet-simulator recorder \
    scenario-service dashboard-api caddy agent-mesh
docker compose … rm -sf broker broker-event-monitor
docker volume rm aerial-rescue-mesh_broker-storage
just up                                                    # 14:58:18Z–14:59:08Z, exit 0
docker compose … up --detach --wait --build broker-event-monitor   # at bbff2f5, 15:04Z
just mission-control-up --build                            # at d75058e: dashboard-api unhealthy
just mission-control-up --build                            # at 3566a7d: exit 0, 16:49:16Z
curl http://127.0.0.1:8080/api/v1/health                   # 200
curl "http://127.0.0.1:8080/api/v1/readiness?mode=degradedLive"   # {"ready":true,"reasons":[]}
docker compose … --profile services up --detach --wait --no-deps command-gateway evidence-service
```

Read-only readbacks as in the first record (SEMP monitor GETs inside the broker container, `docker
inspect`, `docker logs`, `psql`), plus in-container probes of the scenario-service catalog route.

## What the run found

### 1. The broker's configuration keys apply only when its storage element is first initialised

On the fresh volume the first boot's `event.log` was JSON from its first line (795 of 795 lines
began with `{` at 14:59Z; 2 018 of 2 018 at 16:56Z) and so was the container's stdout. The same keys
had no effect on the volume created on 2026-08-21 across fifteen boots (first record, finding 6).
The observation settles the first record's open question by experiment; no vendor document was
cited.

### 2. The monitor refused every JSON line for its severity case, then eleven for one catalog row

With the log in the expected format the monitor still refused every line: `_BrokerEventWire` is
strict and knew the lower-case syslog severities while the broker emits `INFO`, `NOTICE`, `WARNING`,
and `ERR`. Fixed test-first (`bbff2f5`); the rebuilt monitor then accepted the log, producing 664
`BROKER_EVENT_CATALOG_GAP` alerts (uncataloged SYSTEM events, chiefly the admin SEMP session-open
lines that the provisioner and these readbacks generate), 46 cataloged alerts, and 11 refusals: the
catalog paired `SYSTEM_SERVICE_LISTEN_PORT_DISABLE`/`ENABLE` at `NOTICE`/`NOTICE` while the broker
emits the enable event at `INFO`. Fixed test-first (`66344d8`); an offline replay of the 802-line
log through the corrected processor refuses nothing. The catalog-gap volume (1 593 of 2 018 lines at
16:56Z) is designed behaviour that deserves its own review; nothing was changed for it.

### 3. The scenario service standardized on the deployed composition

Finding 7 of the first record was resolved by ADR-0197: catalog projection (`6b0c5ee`), lost-run
recovery with pinned terminal states (`9b7eaa4`), the two HTTP routes with no-store responses and no
slash redirect (`d75058e`), one entry point and the deletion of the parallel composition and its
five test modules (`08dcae0`). Composed live at `d75058e`, `dashboard-api` completed startup — the
catalog call succeeded — and its Compose probe still failed (finding 4).

### 4. The dashboard probe named a Host the deployed composition refuses

The readiness probe sent `Host: localhost:8080` and the environment allowed `localhost:8080`, values
the data-plane branch wrote for its undeployed console composition; the deployed
`delivery/production.py` pins `127.0.0.1:8080`, the address Caddy publishes (ADR-0096, ADR-0117).
Every probe answered `HOST_INVALID` (36 consecutive failures by 16:37Z). The probe and both
`DASHBOARD_ALLOWED_*` values now name `127.0.0.1:8080` (`3566a7d`); the composition at that tree was
healthy end to end at 16:49:16Z.

### 5. First fully healthy composition

At 16:49:21Z every service was healthy: broker (spool-gated), broker-event-monitor, agent-mesh,
postgres, fleet-simulator, scenario-service, recorder, dashboard-api, caddy, command-gateway, and
evidence-service; `mission-control-up` and the `services` profile both exited 0. Through Caddy at
`127.0.0.1:8080`: `/api/v1/health` 200, the readiness document `{"mode":"degradedLive",
"ready":true,"reasons":[]}`, the index 200.

## Observed state at the last readback (16:56Z)

- Broker: 97 of 100 effective endpoints (89 durable, 8 non-durable); the probe drone's queues are
  gone with the volume; connections `agent-mesh-agent` 9, `event-mesh-gateway` 4, and one each for
  `event-mesh-tool`, `fleet-simulator`, `recorder`, `dashboard-api`, `command-gateway`,
  `evidence-service` — the application services create no temporary endpoint.
- `event.log`: 2 018 lines, all JSON. Monitor since 16:48Z: 1 593 `BROKER_EVENT_CATALOG_GAP`, 89
  `VPN_AD_BIND_COUNT_HIGH`, 38 `…_CLEAR`, 11 `BROKER_EVENT_INPUT_REFUSED` (the running image predates
  `66344d8`), 8 `VPN_SERVICE_LISTEN_PORT_STATE_CHANGE`, 8 `VPN_AD_CLIENT_USERNAME_ENDPOINTS_HIGH`.
- Every container: 0 restarts since its last creation; `alembic_version` `0011_audit_kind`.

| Finding | Commit |
| --- | --- |
| 1 | none — an observation; memory and this record carry it |
| 2 | `bbff2f5`, `66344d8` |
| 3 | `6b0c5ee`, `9b7eaa4`, `d75058e`, `08dcae0`, `f1738ce` (ADR-0197) |
| 4 | `3566a7d` |

## What this proves and what it does not

Proven live on the reference host: the merged Compose definition composes every default,
`mission-control`, and `services` target to a healthy state on a freshly initialised broker; the
dashboard reaches readiness against the composed scenario service; the broker writes the JSON event
log the monitor was designed for; the monitor parses it. Offline, the full pre-push suite passed at
`08dcae0` (3 713 passed, 411 subtests, coverage gates enforced).

Not proven: telemetry through the composed stack (no mission was started), the operator's browser
flow, the gateway → evidence → dashboard → command chain in containers, the Agent Mesh probes, the
monitor's alert pipeline on real incidents, and anything under the `event-portal` profile. Nothing
here is release evidence for Phase 3's acceptance criteria.
