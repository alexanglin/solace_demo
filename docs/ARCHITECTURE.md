# Architecture

> **Authority:** this document is the single home for component responsibilities, the Solace-first policy, the runtime layout, and the operating modes. `docs/IMPLEMENTATION_PLAN.md` and
> `AGENTS.md` reference it and must not restate it ([ADR-0016](adr/0016-documentation-set-split.md)).
> Where this document and an `Accepted` ADR disagree, the ADR governs.
>
> **Related:** [ADR-0007](adr/0007-solace-first-implementation-policy.md) (Solace-first), [ADR-0001](adr/0001-self-hosted-open-source-agent-mesh.md) (self-hosted Agent Mesh), [ADR-0003](adr/0003-postgres-durable-mission-store.md) (durable store), [ADR-0004](adr/0004-split-python-runtimes.md) (split runtimes), [ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md) (broker substrate and showcase), [ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) (Compose runtime), [ADR-0046](adr/0046-generated-local-certificate-authority.md) (local TLS), [ADR-0008](adr/0008-abstention-over-recorded-substitution.md) (degraded behaviour), [ADR-0009](adr/0009-isolated-side-effect-free-replay.md) (replay isolation). Interfaces are in [CONTRACTS.md](CONTRACTS.md); numbers in [operating-parameters.md](operating-parameters.md).

## Solace-first implementation policy

Use the largest practical set of supported open-source Solace building blocks before writing equivalent infrastructure. The initial architecture deliberately exercises:

- Agent Mesh A2A Agent Hosts, agent cards, discovery, delegation, Orchestrator, YAML workflows, artifact handling, HTTP/SSE gateway, and evaluation tooling.
- The official Event Mesh Gateway and Event Mesh Tool plugins, including Solace AI Connector expressions, transformations, structured invocation, correlation forwarding, request/reply, and explicit message settlement.
- The Solace PubSub+ Messaging API for Python for application-side direct messaging, guaranteed messaging, queue consumers, publisher confirmation, request/reply, reconnect handling, and trace-context propagation.
- The PubSub+ software event broker container for the shared A2A control plane and application data plane, isolated through namespaces, client identities, ACLs, queues, and subscriptions; the Developer-class Solace Cloud service is a non-gating showcase profile that carries the same traffic when selected by environment ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)).

Project-owned code is reserved for the SAR scenario and simulator, strict domain validation, deterministic evidence scoring, mission state, the command/approval policy boundary, recording/replay isolation, and the operator dashboard. A new custom transport, agent runtime, gateway, connector, or broker abstraction requires a documented capability gap and a focused test proving why the official Solace component is insufficient.

| Solace component | Initial-release responsibility | Acceptance evidence |
| --- | --- | --- |
| Agent Mesh Agent Hosts and A2A | Agent discovery, task lifecycle, streaming updates, peer delegation | Web UI Agents/Activity plus correlated A2A broker traffic |
| Agent Mesh Orchestrator | Open-ended operator questions and unexpected replanning | Delegation evaluation and task trace |
| Agent Mesh YAML workflow | Deterministic mission-response sequence and typed agent steps | Structured input/output contract and repeatable workflow result |
| HTTP/SSE gateway | Upstream engineering task UI | Loopback health and streamed task result |
| Event Mesh Gateway 1.1.0 | Allowlisted salient-event ingress, transforms, structured invocation, response routing | **Topic-to-task contract, structured invocation, and both routes proven live** on its own `event-mesh-gateway` identity ([event-mesh-gateway-first-run.md](../release-evidence/phase-0/event-mesh-gateway-first-run.md)). Deferred settlement is configured and its failure path observed; redelivery is untestable while the data-plane queue is temporary ([ADR-0071](adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)) |
| Event Mesh Tool 0.1.1 | Read-only status and action-proposal request/reply | Correlation, timeout, malformed reply, and ACL-denial tests. Request/reply and ACL denial are proven live in `tests/phase0/test_event_mesh_tool_live.py` ([ADR-0070](adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)); timeout and malformed-reply behaviour are still owed |
| PubSub+ Messaging API for Python | Application telemetry, guaranteed consumers, publisher confirmation, reconnect, request/reply | Real-broker integration and failure-injection suites |
| PubSub+ software event broker container | Shared broker, topic routing, queues, spool, ACL enforcement | Broker Manager clients/subscriptions/rates and queue-depth transition |
| Solace Cloud service (showcase) | The same identities, queues, and traffic on a Developer-class service, by environment only | Redacted Cluster Manager, Broker Manager, and Event Portal evidence ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)) |
| Solace distributed-tracing integration | W3C trace propagation across publish/receive boundaries | One trace linked to A2A task, CloudEvent, proposal, and command IDs |

![Aerial Rescue Mesh open-source Solace-first architecture](architecture/aerial-rescue-mesh-overview.png)

Editable source: [`docs/architecture/aerial-rescue-mesh-overview.dot`](architecture/aerial-rescue-mesh-overview.dot).

## Self-hosted Solace Agent Mesh

- **The PubSub+ broker container** carries application telemetry, mission events, evidence, commands, command results, and operator decisions.
- **Agent Mesh runtime 1.28.7** runs locally from a pinned package and uses the broker for A2A discovery, task requests, status updates, and responses.
- **Agent Mesh Orchestrator** discovers agents from their published agent cards and delegates mission-level work.
- **Mission Response workflow** defines the repeatable mission-start-to-proposal path in versioned Agent Mesh YAML. It invokes specialized agents through structured inputs and outputs while leaving all executable effects to the deterministic command gateway.
- **Mission Coordinator agent** translates operator intent and salient fleet events into search assignments and replanning decisions.
- **Evidence Fusion agent** correlates structured visual, thermal, positional, and contextual evidence.
- **Three edge agents** run as separately deployable Agent Mesh processes with distinct agent cards, Ollama models, capabilities, queues, and broker identities.
- **HTTP/SSE Web UI gateway** provides the upstream Agent Mesh chat/task surface on loopback for direct inspection of discovered agents and streamed task results.
- **Event Mesh Gateway 1.1.0** is the pinned official ingress plugin, configured in `agent-mesh/configs/event-mesh-gateway.yaml` and running as a fifth app on the `event-mesh-gateway` identity. Its data-plane queue is temporary and not configurable, so ingress into the mesh is at-least-once only while it is connected ([ADR-0071](adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)). It allowlists salient application topics, uses structured invocation and JSON Schemas where supported, translates events into A2A tasks, forwards correlation context, and routes success and failure outputs to the direct, non-authoritative Agent Response integration topic. The closed result schema is committed; configuring and proving that structured egress remains implementation work. Routine telemetry never enters this gateway.
- **Event Mesh Tool 0.1.1** is the pinned official request/reply plugin for read-only status queries and action proposals. Its broker identity can publish only to command-gateway request topics and cannot publish executable or authorized command topics.
- **The `general` and `planning` configurations** are supplied through LiteLLM-compatible model settings by either a paid provider (Anthropic or OpenAI) or local Ollama. Paid mode is the default for acceptance runs; local-only is a first-class, tested configuration so the system runs with no API key. Exactly one provider is active per run and readiness reports which. See [ADR-0002](adr/0002-paid-orchestration-under-enforced-budget-cap.md).

SAC YAML, agent cards, prompts, inline Ollama model dictionaries, gateway/tool configuration, and plugin declarations under `agent-mesh/` are the reproducible source of truth. Do not configure `model_provider`; in Agent Mesh 1.28.7 it takes precedence over the inline `model` dictionary and would move model authority into the local Platform database. The pinned `sam` CLI initializes, runs, and exercises these files; record exact validation commands only after confirming them from `sam --help` for 1.28.7. Secrets are injected only through ignored environment files or an approved secret store.

The owned runtime keeps the Connector's management readiness false until every Agent Mesh component
future has completed asynchronous initialization, including tool-created request/reply sessions. One
global 60-second barrier fails startup on the first component exception or on timeout; the existing
bounded cleanup and nonzero termination path then lets Compose retry without admitting dependent work
to a partially initialized mesh ([ADR-0201](adr/0201-gate-agent-mesh-readiness-on-asynchronous-initialization.md)).

### Offline Agent Mesh configuration validation

![Offline Agent Mesh validation flow](architecture/agent-mesh-validation-flow.png)

[Editable Graphviz source](architecture/agent-mesh-validation-flow.dot)

The semantic-configuration gate [ADR-0032](adr/0032-agent-mesh-semantic-configuration-validator.md) requires is repository verification tooling, not a runtime component. It runs inside the isolated Python 3.13 environment and delegates what upstream already defines to the exact pinned wheels: include expansion, parsing, and multi-file merge to Solace AI Connector 3.3.12; agent and workflow `app_config` validation to Agent Mesh 1.28.7's own configuration models; and gateway `app_config` validation to the Event Mesh Gateway 1.1.0 schema. Every imported symbol is bound to its installed distribution record, so a substituted module fails closed. On top of that it enforces the owned rules: includes stay inside the repository; every credential and broker field is an environment reference declared in `.env.example`; broker URLs are `tcps`, or WSS on port 443, with no userinfo; `model_provider` is absent and model identifiers are exact; gateway handlers settle with the policy stated in [CONTRACTS.md](CONTRACTS.md) and route only to declared outputs; and the Event Mesh Tool is the pinned `sam_event_mesh_tool.tools:EventMeshTool`, using request/reply over JSON and publishing only to `aerial-rescue/v1/{{ missionId }}/gateway/request/{{ operation }}` with wildcard-free identifiers. Every selected file must be valid on its own; a multi-file run also merges them with the pinned merge primitive and fails on a conflict such as a duplicated app name. The gate starts no Agent Mesh process, broker client, application, Ollama request, or model call, and runs the upstream imports inside a temporary home directory so their import-time artifacts never touch the working tree. It is inert until an owned configuration exists, then fails closed on a missing prerequisite or an unreadable file. Where it refuses more than ADR-0032 states, [ADR-0035](adr/0035-refuse-unprovable-agent-mesh-configuration.md) records why.

A green result is configuration evidence only. The recorded Phase 0 runs separately prove broker identity
and ACL enforcement, A2A discovery and delegation, Event Mesh Gateway transformation, and Event Mesh Tool
request/reply for the then-current configuration. Those live records also cover the bounded settlement,
redelivery, and structured-model-output paths named by their evidence. The newly structured Agent Response
and complete application data plane still require the adoption shared-stack run; offline configuration
validation cannot prove their settlement, redelivery, reconnect, or model behavior.

## Local Python components

- **Fleet simulator:** Adapts the deterministic scenario to broker and clock ports, drives the pure Tier 1
  domain state machines in `packages/domain` — mission, sector, command, drone connectivity, and evidence
  lifecycles ([ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md)) — injects failures, publishes
  telemetry, and consumes commands. Its authenticated private listener preserves the accepted twenty-member
  workload, fixed-rate tick loop, mission-scoped producer epochs, and idempotent start/status/cancel path.
  Direct telemetry, guaranteed lifecycle publication, and the drone-observable half of command dispatch have
  live evidence
  ([fleet-simulator-first-run.md](../release-evidence/phase-3/fleet-simulator-first-run.md),
  [command-dispatch-first-run.md](../release-evidence/phase-3/command-dispatch-first-run.md),
  [wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
  The adopted runtime additionally binds every provisioned per-drone queue and processes commands through
  durable receipts and a bounded critical-result outbox: effects and exact results commit before settlement,
  and broker recovery pauses then resumes the exact active operation. Deterministic tests cover that stronger
  composition; PostgreSQL receipt recovery and a broker-container reconnect remain shared-stack acceptance
  work.
- **Command gateway:** Owns deterministic mission-command policy, direct Agent Response normalization,
  canonical proposal persistence/publication, idempotency, proposal-and-evidence-bound approval checks,
  typed authorization audit, command progress, outbox state, and executable command publication. Its
  long-running composition opens one mixed least-privilege broker session and the SQLAlchemy transaction
  adapters, recovers bounded outboxes before readiness, and handles Direct and Guaranteed inputs. The
  request/reply half has earlier live evidence; application dispatch, crash recovery, and reconnect have
  deterministic offline evidence and still require the shared-stack acceptance run. Agent credentials
  cannot bypass it.
- **Durable mission store:** PostgreSQL, run as a Docker Compose service, is the authoritative durable
  store for mission state, broker inbox/outbox records, proposals, approvals, idempotency results,
  evidence provenance and decisions, command progress and receipts, and audit records. Access is through
  async SQLAlchemy 2.x with `asyncpg`, and every durable table and constraint is introduced through an
  append-only Alembic revision. The eleven-revision history preserves immutable dashboard revision 0005 and
  appends application-data-plane revisions 0006 through 0011. Its 25 SQLAlchemy-owned tables include audit,
  approval, idempotency,
  command-outbox, broker inbox/refusal, general application outbox, proposal, evidence, source-event,
  command-progress, durable receipt, pending-invocation, and dashboard-idempotency state. Typed
  SQLAlchemy Core repositories and service-specific units of work own those transactions; production
  code does not create durable tables from metadata. Broker acknowledgement occurs only after the
  related durable transaction commits. An append-only audit table with a monotonic ordinal is the
  ordering authority for the mission timeline. Revisions 0001 through 0005 have live PostgreSQL evidence;
  applying and exercising revisions 0006 through 0011 remains part of the adoption shared-stack run. See
  [ADR-0003](adr/0003-postgres-durable-mission-store.md) and
  [ADR-0146](adr/0146-define-durable-application-processing.md) and
  [ADR-0189](adr/0189-reconcile-dashboard-runtime-with-the-solace-data-plane.md).
- **Broker adapter:** Wraps the pinned Solace PubSub+ Messaging API for Python in the Python 3.14
  application environment and isolates connection, publishing, subscription, acknowledgement, retry,
  and shutdown behavior. The typed router derives delivery from validated topic and representation
  identity before broker I/O. Direct, Guaranteed, request/reply, receiver-only, and mixed sessions expose
  only the capabilities their principals need. Bounded reconnect and per-receiver activation drive
  readiness; terminal exhaustion remains terminal. These lifecycle paths are proven with the pinned SDK
  and deterministic tests, while a real container restart and application rebind remain live acceptance.
  Agent Mesh keeps the separate PubSub+ client version resolved by its own lockfile.
- **Scenario service:** Validates the versioned scenario catalog and definitions, preserves their
  explicit roster, geometry, and heartbeat-loss schedule, losslessly projects only simulated members
  into the fleet input, and exposes lifecycle operations. The input carries no seed or random source.
  ADR-0158 keeps this process brokerless: private catalog discovery, start, status, cancel, and lost-run
  recovery use authenticated bounded HTTP from one console composition (ADR-0197), and the dashboard owns durable mission-event staging and publication while the fleet owns
  telemetry, critical drone events, and command effects.
  The exact twenty-plus-three production catalog, confined digest-validating loader, process-epoch
  lifecycle coordination, HTTP client/server, health/readiness, internal listener, console entry point,
  and brokerless Compose wiring are implemented. Durable mission binding, generated OpenAPI, and live
  process/container-network evidence remain.
- **Evidence service:** Validates model observations, attaches provenance and hashes, delegates score
  calculation to pure Tier 1 domain logic, and transactionally persists and stages the versioned
  evidence decision plus typed audit. The closed schemas and 25/50/75 bands with 40/35 source weights
  are implemented. Its Guaranteed consumers verify source provenance, commit inbox, evidence, decision,
  audit, and outbox state atomically through the store, then settle. A bounded outbox worker publishes
  evidence-decision and audit events through one broker session, and the concrete console is wired in
  Compose. Deterministic tests prove the transaction and recovery orchestration; live settlement,
  reconnect, and shared-stack qualification remain. Model failure produces an explicit abstention or
  manual-review outcome; recorded evidence is never substituted.
- **Recorder/replayer:** Is the receiver-only path from validated broker application sources into durable
  audit order, committing guaranteed input before acknowledgement, and writes sanitized CloudEvents to
  NDJSON for the isolated replay path. It classifies the complete applicable subset of the fifteen topic
  families — twelve notifications, two reserved request/reply families, and one direct Agent Response
  integration — while excluding raw RPC replies and A2A control traffic. The recorder combines mission,
  sector, and connectivity transitions on one ordered lifecycle queue, commits Guaranteed input before
  settlement, and treats Direct telemetry and Agent Response as their explicit lossy classes. Its
  receiver-only broker composition, inbox deduplication, commit-before-settlement capture,
  bounded ordered export ports, and structurally isolated replay graph are implemented and Compose-wired.
  The live database export reader and recording codec remain uncomposed, and shared-stack capture,
  redelivery, reconnect, and replay evidence remain pending.
- **Dashboard API:** Owns scenario control, health, readiness, replay, the packaged browser/bootstrap, broker-backed
  normalized mission state, server-sent events, canonical operator commands, and exact proposal
  decisions. The concrete FastAPI application implements the closed Host, Origin, bearer, body, and
  idempotency boundary. Its deployed composition preserves the accepted `OperationCoordinator` start/reset
  transaction and exact-response recovery, then layers the Solace supervisor, SQLAlchemy application
  transactions, mixed broker session, audit recovery, normalized projection, bounded SSE, command/decision
  mutations, and a capability-isolated replay graph, and listens only on the private Unix socket behind
  Caddy. Offline tests prove composition and
  refusal ordering. Unix-socket reachability and end-to-end broker/store behavior remain shared-stack
  acceptance work; generated OpenAPI remains absent. The start/reset path retains its accepted live
  qualification, while the added broker/store/command/decision paths still require the adoption
  shared-stack run.

All Python work runs in an isolated project virtual environment managed by `uv`:

- Application services use Python 3.14.7, the newest stable Python release, in the root `.venv` with the root `pyproject.toml` and `uv.lock`.
- Agent Mesh 1.28.7, the two official Event Mesh plugins, and any owned Agent Mesh extension use Python 3.13.15 in `agent-mesh/.venv` with `agent-mesh/pyproject.toml` and `agent-mesh/uv.lock`, because upstream declares `>=3.10.16,<3.14`.
- Shared Python packages consumed by both environments must declare and test the intersecting compatibility range. Never install either environment's dependencies globally or combine their lockfiles.

Use Debian/glibc-based Python containers rather than Alpine images for ARM compatibility with the Solace client.

The Solace Python library must not be hosted through Python's `multiprocessing` module. Independent Agent Mesh components and edge agents run as separate native processes on the supported Apple Silicon path. Host-run components access Ollama through `http://127.0.0.1:11434`; containerized application services use `http://host.docker.internal:11434`.

### Deployment layout

Every component except Ollama runs under Docker Compose from `deploy/compose.yaml`, and the compose policy gate holds that file to its policy on every commit ([ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), [ADR-0045](adr/0045-fail-closed-compose-policy-gate.md)). Images are pinned by tag and index digest, every published port binds to `127.0.0.1`, and secrets are files under the ignored `deploy/secrets/` mounted at `/run/secrets/`. Broker, PostgreSQL, and Caddy each use a distinct single-member, non-masquerading loopback-publisher bridge; their actual application edges remain on need-to-know internal networks or Caddy's private Unix socket ([ADR-0131](adr/0131-isolate-loopback-publishers-and-forward-startup-flags.md)). Every long-running service declares a healthcheck; only the migration and replay-validator one-shot jobs use successful completion as their dependency condition. Dashboard startup reuses the broker and PostgreSQL already running in the `aerial-rescue-mesh` project rather than creating a parallel stateful stack ([ADR-0117](adr/0117-select-the-exact-mission-control-service-closure.md), [ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)).

| Profile | Services | State |
| --- | --- | --- |
| default | `broker` (PubSub+ Standard 10.26.0), `postgres` (PostgreSQL 18.6), and `agent-mesh`, built on the official `solace/solace-agent-mesh:1.28.7` image with the two pinned Event Mesh wheels installed by hash | Runnable. The mesh carries five apps: the Orchestrator, the MissionCoordinator agent, the MissionResponse workflow, the HTTP/SSE Web UI, and the Event Mesh Gateway. It joined the default profile when `agent-mesh/configs/` landed, which is the condition [ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) set and [ADR-0102](adr/0102-start-the-agent-mesh-with-the-default-profile.md) executes. It still needs a local model, so `just up` refuses to start it unless the locked model is served |
| `services` | migration, scenario, fleet, command gateway, evidence, recorder, dashboard API, and Caddy, built from the Python 3.14.7 application image carrying the frozen Vite dashboard | All application services have real bounded entrypoints, health/readiness behavior, dependency ordering, private control listeners, and the Unix-socket relay. The accepted mission-control slice has live evidence; the combined application data plane has deterministic evidence and still awaits its shared-stack reconnect and soak qualification |
| `mission-control` | migration, fleet simulator, scenario service, recorder, isolated replay validator, dashboard API, and Caddy; the broker and PostgreSQL are shared base services, not profile-owned targets | Qualified through `just mission-control-up`, which preserves the base broker/PostgreSQL container identities and volumes. The accepted run applied immutable migration 0005 and exercised the prepared wilderness mission. The adoption extends the same project and topology through revision 0010; it does not create a second stateful stack ([ADR-0117](adr/0117-select-the-exact-mission-control-service-closure.md), [ADR-0120](adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md), [ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)) |
| `event-portal` | the Event Management Agent for Event Portal runtime discovery, an amd64-only image run under emulation | Non-gating showcase support ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)) |
| `semp-monitor` | the continuous aggregate queue-health process, with only its generated monitor password and public trust store | Opt-in and fail-closed. It combines one bounded parent depth inventory with sequential count-only active-flow reads for observed desired queues. An operator must first prove global `none`, VPN default `none`, exactly one selected-VPN `read-only` exception, a positive aggregate read, and a negative configuration write ([ADR-0181](adr/0181-gate-continuous-semp-monitoring-on-vpn-scoped-operator-provisioning.md), [ADR-0190](adr/0190-count-active-queue-binds-through-transmit-flow-aggregates.md)) |

Normal `just up` owns the complete runtime and the broker/PostgreSQL lifecycle. Dashboard stop and test
cleanup target only fleet simulator, scenario service, recorder, dashboard API, and Caddy; they do not
run Compose `down`, remove networks or volumes, stop a shared stateful service, or delete dashboard
history. Production browser and soak guards compare the shared base container IDs at test start and test
completion. Broker acceptance asserts the mission-control queues and grants as a required subset of
the shared inventory, never as its exclusive contents.

The reconciled Compose graph has eight explicitly isolated networks and five retained or handoff volumes.
The adoption reuses that graph, its migration service, replay validator, recorder-readiness lease, non-root
Unix-socket ownership, and hardened Caddy relay; no service may broaden an established network edge to
avoid an explicit dependency.
Verification stays native: `agent-mesh/.venv` on Python 3.13.15 runs the configuration validator and the compatibility probes ([ADR-0029](adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)), while the container, which carries upstream's Python 3.13.11, is the runtime. The plugin-compatibility probe is run inside the built image by `scripts/probes/agent-mesh-image-probe.sh`, which is what lets the mesh be called supported; it passed on the image's CPython 3.13.11 on 2026-08-21. Ollama stays on the host; containers reach it as `http://host.docker.internal:11434`. The showcase profile is the same stack pointed at the Solace Cloud service through an ignored `.env.showcase`; no gate, hook, or release criterion depends on it.

`scripts/broker-secrets.sh` generates the per-checkout certificate authority, the broker's server certificate with subject alternative names for `localhost`, `broker`, and `127.0.0.1`, and the stack's passwords; `deploy/certs/` is the trust-store directory every client mounts, and `tcps` validation is never relaxed ([ADR-0046](adr/0046-generated-local-certificate-authority.md)).

The reference workstation, self-hosted Agent Mesh processes, and one Ollama daemon form a common failure and resource domain. Startup must warm models sequentially, inference concurrency must be bounded, and Phase 0 must record idle/peak CPU, unified memory, and model-load time. Resource exhaustion or model eviction yields an explicit degraded state and abstention; it must never cause an approval bypass or silently substitute recorded evidence.

## Local edge models

| Drone | Model | Responsibility |
| --- | --- | --- |
| `drone-vision-01` | `qwen3-vl:8b` | Analyze prepared wilderness images and return structured observations |
| `drone-navigation-02` | `qwen3:4b` | Assess local route, terrain, battery, and sector-replanning options |
| `drone-comms-03` | `llama3:8b` | Summarize evidence and create concise degraded-link reports |

Model tags, Ollama version, prompt version, generation parameters, and resolved model digests must be recorded. Model responses are untrusted input and must pass Pydantic validation before becoming domain events.

The Agent Mesh `general` and `planning` configurations are selected in Phase 0 rather than assuming an edge-role model is adequate for orchestration. Candidates — at least one paid model per permitted provider and one local model — must pass fixed evaluations for agent discovery, delegation, tool calling, JSON-Schema-constrained output, latency, memory use, and cost per run. Phase 0 measures the paid and local-only configurations both, so the capability gap between them is recorded evidence rather than an assumption. The selected aliases then pin the exact model identifier and, for a local model, the resolved Ollama digest, plus context window, token cap, temperature, seed where supported, and timeout; no floating `latest` tag is allowed.

## Dashboard

The current UI-first wilderness slice is specified as a map-first command center with:

- local MapLibre search geometry, sectors, simulated-drone markers and trails;
- scenario context and guarded start/reset controls;
- a fleet rail and semantic table that distinguish twenty simulated members from three declared-only
  external descriptors;
- a non-telemetry mission timeline ordered by audit ordinal; and
- isolated-replay playback controls and an unmistakable degraded-live or replay badge.

The React and TypeScript application preserves the accepted map, scenario start/reset, synchronized fleet
table, audit-ordered timeline, reset dialog, replay controls, compact layout, reduced-motion path, and
validated bootstrap/snapshot/SSE sources. It now also folds broker-backed proposal and evidence state,
retains the last validated state during interruption, exposes retry/recovered status, and provides an
accessible, double-submission-resistant exact proposal approval/rejection flow. Replay renders recorded
facts without constructing action controls. Caddy serves the packaged Vite bundle and relays same-origin
API traffic to the Unix-socket FastAPI process.

Commit `db2b640` and the committed Phase 3 record qualify the established wilderness dashboard slice:
the fixture inventory, eight production workflows, selected API replacement and transport recovery, and
the bounded post-mission soak on the shared `aerial-rescue-mesh` project
([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
That result is bounded to the accepted mission-control slice and measured dashboard API process. It does
not qualify the newly added proposal/evidence/decision stream, full command flow, Agent Response path,
application reconnect, or whole-stack resource behavior; those remain adoption live acceptance work.

## Solace operational surfaces

The project deliberately exercises and exposes both Solace layers:

- **Open-source Agent Mesh Web UI (`localhost:8000` by default):** use the per-request Activity viewer and `Agent Mesh > Agents` registry/topology to inspect tasks, discovered agents, delegation, streamed responses, and artifacts. This is an engineering surface; it is not the authoritative mission dashboard and cannot approve or dispatch an action.
- **Solace PubSub+ Broker Manager** (`https://localhost:1943` on the container; the browser warns until the per-checkout authority is trusted)**:** show separately named Agent Mesh, gateway, command, simulator, edge-agent, recorder, and dashboard clients; inspect A2A and application topic subscriptions; observe ingress/egress rates; inspect guaranteed queue state; and use Try Me with the scoped A2A namespace during diagnostics.
- **Aerial Rescue Mesh dashboard:** show normalized wilderness mission, sector, fleet, connectivity,
  proposal, evidence, command, approval, audit, replay, and recovery state. The current application
  preserves the qualified map, mission/fleet/timeline, start/reset, mode, and replay surfaces and layers
  broker-backed proposal/evidence state, exact proposal decisions, and recovery status onto them. General
  command controls and the remaining prepared-artifact/model presentation are not yet in the browser.

The disconnect/reconnect acceptance flow must make Solace's role visible in Broker Manager: an offline drone's durable command queue changes from depth `0` to `1`, then returns to `0` only after reconnect, durable processing, and acknowledgement. Separately, Agent Mesh agent cards and task traffic prove dynamic discovery and A2A delegation over the broker. Screenshots may document these checks only after tenant-specific values and credentials are redacted.

Reserve and document loopback-only development ports to avoid collisions: Agent Mesh initialization/configuration UI `5002`, Agent Mesh runtime Web UI `8000`, dashboard Vite development server `5173`, Caddy's production dashboard origin `8080`, and Ollama `11434`. The initialization UI is a setup surface, not an operational monitoring surface. Caddy is the sole `127.0.0.1:8080` publisher and relays without buffering to `/run/aerial-rescue/dashboard-api.sock`; the API publishes no IP port and Caddy receives no application credential ([ADR-0096](adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md)). Scenario and fleet listen only on dedicated internal networks at 8081 and 8082. The stack also publishes, on `127.0.0.1` only, 55443 and 1943 for the broker, 5432 for Postgres, 8000 for the Agent Mesh Web UI, and 8180 for the Event Management Agent; the broker's own 8080 and 8000 are never published ([ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)).

## Observability and operating modes

Every service exposes liveness and readiness. Agent Mesh readiness is composite: asynchronous component
and tool initialization has completed, the expected processes are running, the Web UI gateway is healthy,
all required agent cards are discovered, the Event Mesh Gateway data-plane subscription is active, Ollama
reports the pinned models, the broker is connected, and a bounded black-box A2A probe succeeds. A Web UI
HTTP 200 alone is not readiness. Logs are structured and correlated. Metrics must cover event counts,
publish/consume failures, retry counts, queue delay, model duration, validation failures,
abstention/degraded-mode activation, SSE clients, and mission state.

Supported modes are:

- **Live simulation:** the PubSub+ container, Agent Mesh from its official image, live Python simulators, and local Ollama execute the synthetic scenario; the showcase profile runs the same mode against the Solace Cloud service ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)).
- **Degraded live simulation:** Telemetry, the dashboard, and bounded operator control remain available when Agent Mesh or Ollama is unavailable; model-dependent work abstains or awaits manual review, and no recorded positive evidence enters the result.
- **Replay:** The implemented replay composition accepts only a validated local ordered source and drives
  an isolated, side-effect-free observer while constructing no broker, writer, model, or command
  capability. The sanitized NDJSON format, live export reader/codec, concrete browser replay adapter, and
  end-to-end repetition evidence remain incomplete. Isolation is structural rather than
  credential-scoped, and the UI makes no claim that agents are executing
  ([ADR-0009](adr/0009-isolated-side-effect-free-replay.md)).

The dashboard must always display the current mode.
