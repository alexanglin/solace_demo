# Phase 0 evidence: the first live run of the `mesh` profile

- **Recorded:** 2026-08-21
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Scope:** the **default and `mesh` profiles** — the PubSub+ broker container, Postgres, and the
  Agent Mesh 1.28.7 runtime carrying four apps. This does **not** cover the `services` or
  `event-portal` profiles, durable queues, the Event Mesh Gateway or Tool, or the Solace Cloud
  showcase service. None of those was exercised.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Model prompts and completions are not
reproduced; only topic names, message counts, and identifiers are.

## Why this record exists

`docs/IMPLEMENTATION_PLAN.md` Phase 0 is a feasibility gate with a written kill criterion: *"Stop and
revise the architecture if the selected Ollama model cannot provide reliable structured output/tool
use, the container cannot carry the required A2A traffic, or the pinned plugins cannot enforce the
domain boundary."* Every earlier increment assumed an Agent Mesh runtime that had never started. This
is the run that answers it.

## What was run

```sh
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh
docker compose --env-file .env --env-file deploy/secrets/.env.roles \
  -f deploy/compose.yaml --profile mesh up --detach --wait
uv run --frozen pytest tests/phase0/test_agent_mesh_live.py -m phase0
```

`just` is not installed on this workstation, so the recipes were run as the commands they wrap.

## Result

All three containers reached `healthy`. The A2A grant landed as predicted: the provisioner reported
**47 topic exceptions**, up from the **41** recorded in
[`broker-authorization.md`](broker-authorization.md), which stated the difference exactly — *"Forty-one
exceptions rather than forty-seven because `NAMESPACE` is still blank"*. The six withheld A2A
exceptions are now written.

| Measurement | Value |
| --- | --- |
| Agent Mesh container memory at rest | 556.8 MiB |
| Full stack memory at rest | 2.16 GiB: broker 1.575 GiB, Agent Mesh 556.8 MiB, Postgres 31.47 MiB |
| Broker connections opened by four apps | 9, all on the `agent-mesh-agent` client username |
| Message VPN connection ceiling | 100 |
| Web UI on `127.0.0.1:8000` | HTTP 200 |
| Topic exceptions after the A2A grant | 47 |

## What the run proves

**Agent-card discovery.** `GET /api/v1/agentCards` reports exactly the three configured agents —
`Orchestrator`, `MissionCoordinator`, `MissionResponse` — each carrying the skill its configuration
declares. The same cards travel on `aerial-rescue-mesh/a2a/v1/discovery/agentcards`, and the Web UI
registers separately as `aerial-rescue-web-ui` on `.../discovery/gatewaycards`. The namespace
[ADR-0064](../../docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md) fixed is the namespace the runtime
actually uses.

**Structured workflow invocation.** A task submitted to `MissionResponse` produced one request on
`aerial-rescue-mesh/a2a/v1/agent/request/MissionResponse` and a node status on
`.../agent/status/MissionResponse/wf_<task>_assess_sectors_<id>`, matching the single node the
workflow declares.

**One A2A delegation, in both forms.** The workflow's node dispatched to its named peer on
`aerial-rescue-mesh/a2a/v1/agent/request/MissionCoordinator`. Separately, and more importantly for the
kill criterion, a task submitted to the **Orchestrator** produced a request on that same
MissionCoordinator topic — a delegation the model had to choose, because the Orchestrator reaches a
peer only through a tool call. `qwen3:4b` made it.

**The model.** `qwen3:4b`, manifest digest `sha256:359d7dd4…4fae7`, reports capabilities
`completion`, `tools`, `thinking`. `llama3:8b`, the only model previously pulled, reports `completion`
alone and has no `.Tools` section in its chat template, so it could not have produced any delegation;
the kill criterion would have fired on the model rather than on the mesh.

**The container's Python.** The image carries Python 3.13.11 against the 3.13.15 verification runs on,
and all four apps started, connected, and exchanged A2A traffic on it.

## Three defects this run found, none of them in the mesh

1. **Nothing produced the eighteen per-role Compose variables.** `--profile mesh config` resolved the
   Agent Mesh service to `SOLACE_BROKER_USERNAME: ""`. Fixed by generating
   `deploy/secrets/.env.roles`.
2. **A configuration naming its role identity could not resolve it.** `deploy/compose.yaml` mapped the
   role-named variables onto generic `SOLACE_BROKER_*` names, so `${SOLACE_AGENT_MESH_AGENT_USERNAME}`
   inside a config expanded to empty. The broker refused the empty username as the shutdown factory
   `default`, and the client retried forever with no error in its own log — the failure was visible
   only in the broker's event log, as `Forbidden: Client Username Is Shutdown`. Compose now passes the
   role-named pair into the container as well. **The validator checks environment references against
   the host-scope `.env.example` while the runtime resolves them in container scope; a name can be
   declared in one and absent in the other, and nothing catches it.**
3. **A memory artifact service is per-app even inside one connector process.** A workflow node hands
   its input to a peer agent by artifact reference, and the peer could not load it:
   `Failed to load referenced artifact … version 0 not found or has no data`. The node then retried
   without bound, emitting thousands of status messages for one task. All four configs now share a
   filesystem artifact store and the handoff succeeds.

## What this run does not settle

- **No durable queue exists**, so nothing here is evidence about guaranteed delivery, redelivery, or
  the no-loss claim. The four queue parameters remain unset.
- **The Event Mesh Gateway and Tool were not configured or run.** The plan's next Phase 0 bullet, and
  the boundary [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) depends on, is
  untested live.
- **No application CloudEvent was published.** The eleven application topic families carried no
  traffic; only the A2A namespace did.
- **The workflow's end-to-end output was not asserted.** The probes assert the delegation request
  reaches the peer, not that the workflow returns a value matching its `outputSchema`.
- **The artifact store is `/tmp` inside the container**, so it does not survive a restart and is not
  the durable store [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) decided.
- **`qwen3:4b` is provisional.** It was chosen for tool capability at 2.50 GB, not by the measured
  capability-per-dollar comparison the Phase 0 evaluation still owes, and Phase 4 pins the roles.
- **Model quality was not assessed at all.** That a delegation happened is not evidence that the
  reasoning was good; no output was scored.
- **The showcase service was not touched**, so the connection count above is the container's, not the
  Developer-class service's. Four apps opening nine connections against a ceiling of 100 is the first
  real datum for that estimate, and it suggests the fleet's connection count will exceed its identity
  count by a large factor.
- **Bind-mounted configuration changes do not restart the container.** `up --wait` reports the old
  container healthy; `--force-recreate` is required. Nothing in the repository says so yet.
