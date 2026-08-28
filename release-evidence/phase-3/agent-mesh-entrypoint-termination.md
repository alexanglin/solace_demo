# Phase 3 evidence: the Agent Mesh entrypoint terminates, and what the salient chain then showed

- **Recorded:** 2026-08-28, 21:30–23:00 UTC (host America/Toronto; container and broker logs UTC).
  This record follows `merged-runtime-second-composition.md` on the same host and the same broker
  volume. It exists because the previous session's salient-chain run left two probes failing, and the
  investigation found that the Agent Mesh container had died mid-run and could not be restarted.
- **Governing documents:** [ADR-0177](../../docs/adr/0177-harden-the-pinned-agent-mesh-broker-runtime.md)
  for the owned lifecycle's exit contract,
  [ADR-0199](../../docs/adr/0199-terminate-the-owned-agent-mesh-entrypoint.md) for the termination
  decision this run first exercised, and
  [ADR-0200](../../docs/adr/0200-give-the-coordinator-a-tool-capable-model.md) for the model the
  coordinator runs. The producer is a local live probe under `docs/TESTING.md`; the claim ceiling is
  `docs/LIMITATIONS.md`'s: one local host, not release evidence.
- **Revision:** `feat/terminate-the-agent-mesh-entrypoint`. The termination fix ran at `2b772d8`, the
  collapsed probe at `c690fde`, and the model correction at the commit that follows this record. The
  worktree was clean at each commit.
- **Host and versions:** Apple Silicon (arm64), macOS 26.6.2, Docker Engine 29.5.3, `just` 1.58.0,
  host Python 3.14.5; Agent Mesh image `aerial-rescue/agent-mesh:1.28.7` rebuilt at 21:34 UTC, broker
  `solace/solace-pubsub-standard:10.26.0.8799`, PostgreSQL `postgres:18.6-trixie`, application image
  `aerial-rescue/application:0.0.0`. Local models served by Ollama on the host.
- **Prerequisites and pre-existing state:** the merged stack from the previous record, less
  `dashboard-api` (down since that record's finding 6, leaving `caddy` unhealthy throughout — neither
  is on this record's path). Mission `mission-113f4845ff5d49bf88497fb36a946df6`, started in the
  previous record's amendment. The store held 2 proposals and 2 evidence decisions at the start.
- **Scope:** the owned entrypoint's termination behaviour across container recreation; the collapsed
  salient-chain probe; the coordinator's model capability. It does **not** cover the dashboard, the
  browser flow, the `event-portal` profile, Solace Cloud, or any approval or command.

Redaction: no credential, password, private key, bearer, tenant identifier, prompt, or model
completion appears here. Broker readbacks were taken through the administrator SEMP monitor plane and
report only queue names and counts.

## What was run

```sh
docker compose … build agent-mesh                                   # 21:34 UTC
docker compose … up --detach --wait --no-deps --force-recreate agent-mesh
docker compose … stop fleet-simulator                               # ADR-0168: it holds the identity
AERIAL_RESCUE_DEMO_MISSION_ID=mission-… uv run --frozen pytest -v \
    tests/phase0/test_salient_chain_live.py                         # three runs, see below
docker compose … --profile services up --detach --wait --no-deps evidence-service
docker compose … up --detach --wait --no-deps fleet-simulator
```

Read-only readbacks: `docker inspect`, `docker logs`, `docker wait`, SEMP queue and endpoint monitor
GETs through the project's own client, and `psql` counts.

## What the run found

### 1. The entrypoint now terminates, and the container recovers by itself

The defect this record exists for: on 2026-08-27 the process logged `Cleanup completed` and never
exited, so Docker reported the container `Up` with `RestartCount` frozen at 7 for fourteen minutes
while no Agent Mesh recommendation was possible. `raise SystemExit(main())` joins every nondaemon
thread, and the pinned SDK's `ThreadPoolExecutor` workers are nondaemon.

With `terminate_process` in place, the same condition produced the opposite behaviour. Recreating the
container at 21:34:48 UTC put it into the endpoint-ceiling failure — 48 log occurrences of
`SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE`, the evicted sessions' temporary queues still
holding `agent-mesh-agent`'s five-endpoint ceiling (ADR-0196) — and the process exited each time.
`RestartCount` moved 0 → 6 in about 34 seconds, and the container reached `healthy` at 21:35:22 with
its five temporary endpoints bound. The second recreation, at 21:51, did the same over 5 restarts.

This is the designed consequence recorded in ADR-0199: a restart loop bounded by Docker's backoff,
converging as soon as the broker reaps the stale endpoints, in place of a silent permanent zombie.
The forced-exit path itself did not fire in either sequence — no `Agent Mesh termination forced`
line appears — so what these restarts prove is that the process exits, not that the settle bound
works. The bound is covered offline by `ProcessTerminationTests`.

### 2. An ordinary stop was still answered with SIGKILL

`docker wait` on the healthy container across the 21:51 recreation returned **137**, so SIGTERM did
not complete inside the grace period and Docker killed the process. The `agent-mesh` service declared
no `stop_grace_period`, taking Compose's 10-second default, while the pinned Connector's own stop and
cleanup already run longer than that. ADR-0177's promise that "ordinary SIGINT/SIGTERM retains
graceful cleanup and zero status" was therefore not being kept in the deployed composition, and the
15-second settle window could never have run inside a 10-second grace.

The service now declares `stop_grace_period: 45s`, and a deployment test reads
`THREAD_SETTLE_SECONDS` from the compat source so that raising either number without the other fails.
**Not yet verified live:** the corrected grace has not been exercised by a stop; the exit status of an
ordinary shutdown remains unmeasured.

### 3. The coordinator's recorded model could not run at all

The first run that actually loaded ADR-0198's configuration failed every completion:

```text
litellm.APIConnectionError: Ollama_chatException -
{"error":"registry.ollama.ai/library/llama3:8b does not support tools"} LiteLLM Retried: 3 times
```

The structured-invocation handler then reported `No model event found in session history`, exhausted
its two retries, and published `status=error` to the gateway. The local daemon states the cause
directly: `GET /api/show` reports `['completion']` for `llama3:8b` and
`['completion', 'tools', 'thinking']` for `qwen3:4b`. A coordinator that can delegate always carries a
tool. ADR-0198 recorded this model on a convergence measurement taken against the unconstrained tool
surface its own other half was written to fix; the two were never separated by experiment. ADR-0200
returns the agent to the locked tool-capable model and keeps ADR-0198's tool-surface and call-bound
decisions.

With the corrected model the coordinator published `status=success` and the command gateway
normalised a third `candidate-location` proposal bound to the published source event.

### 4. The evidence service crash-looped on a redelivered source event

During the second probe run the evidence service raised
`SourceEvidenceError … IDENTITY_CONFLICT` for the probe's event
(`01a04a5b-d909-7210-b10f-edee4dba6828`) — "the source identity or its immutable fact set was reused
differently" — from `record_source_evidence` → `_stored_fact_set`. It raised the same error on every
redelivery, exhausted its `on-failure:3` restart budget, and **exited**. The message reached
`aerial-rescue/v1/evidence-service/drone.event_dmq` (6 → 7) after its redelivery budget, and the
service started healthy again afterwards, scoring that proposal `rejected` with no band because the
provenance never committed.

This is a product defect, not a probe defect, and it is unrelated to the entrypoint or the model. What
first left the stored fact set differing from the redelivered one was not determined; it needs its own
investigation and is recorded as such rather than guessed at here.

### 5. The probe's shape held

Collapsing the four cases into one publication with four assertions worked as intended. Every run
failed at the first hop that was genuinely absent and named it, and the 300-second window bounded each
one precisely:

| Run | Model | Provenance | A2A delivery | Proposal | Decision | Wall clock |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `llama3:8b` | pass | pass | **fail** | — | 300.18 s |
| 2 | `qwen3:4b` | pass | pass | pass | **fail** | 300.43 s |
| 3 | `qwen3:4b` | pass | pass | **fail** | — | 340.78 s |

Run 1 is finding 3, run 2 is finding 4. Run 3 was the clean rerun on a fully healthy stack, and the
coordinator did not answer within the window at all: the container logged no `LiteLLM completion` for
that task while the probe waited. The coordinator is therefore **intermittent** on this host under the
constrained tool surface and the four-call bound — it answered once in two attempts. That is a
measurement of the ADR-0198 constraints, taken here for the first time against a model that can
execute them, and it is not yet enough evidence to say whether the bound, the window, or host load is
responsible.

The first two hops passed in all three runs, so the publication path and the gateway ingress are the
settled part of the chain.

## Observed state at the last readback

- Broker: 97 queues, 89 durable and 8 temporary — `agent-mesh-agent` 5, `event-mesh-gateway` 2,
  `event-mesh-tool` 1, the documented steady state.
- Store: 3 source-event provenance rows for this mission's probes, 3 proposals, 3 evidence decisions
  (one `contributing`/75/`corroborated` from the previous session, two `rejected`).
- Containers: every service healthy except `caddy`, unhealthy since the previous record's finding 6
  because `dashboard-api` is down. `agent-mesh` healthy; `evidence-service` healthy with 0 restarts
  after its restart.
- Dead-letter queues were left untouched, deliberately: they hold the observations findings 4 and the
  previous record describe, and draining them before writing this record would have destroyed the
  evidence. The `recorder` and `evidence-service` `drone.event` dead-letter queues held 6 messages
  each from the earlier bare-SDK publications, unchanged by these runs, which is itself the check that
  the provenance fix holds.

## What this proves and what it does not

Proven live on the reference host: the owned Agent Mesh entrypoint exits when its lifecycle finishes,
and Docker's restart policy recovers the container without an operator; the coordinator's recorded
model could not serve a tool-bearing agent and the locked model can; one salient event published
through the production publisher becomes stored provenance, an A2A delivery, and a normalised
`candidate-location` proposal bound to its source event.

Not proven: that the forced-exit path fires correctly in a container (it did not trigger; only the
offline tests cover it); that an ordinary stop now completes inside the corrected grace; that the
coordinator answers reliably, which run 3 contradicts; that the chain reaches a `corroborated`
decision under the corrected model; anything about the dashboard, the browser flow, approval, or
Solace Cloud. Nothing here is release evidence for Phase 3's acceptance criteria.

The chain therefore ends this session with two open defects that are neither the entrypoint nor the
model: the evidence service's crash on a redelivered source event (finding 4), and the coordinator's
intermittent silence (finding 5). Both were found by the probe rather than hidden by it, which is the
change the collapsed shape was meant to make.
