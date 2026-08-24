# Aerial Rescue Mesh Implementation Plan

## Document status

- **Status:** Active build plan
- **Last updated:** August 21, 2026
- **Release model:** Incremental, test-driven delivery
- **Primary audience:** Engineering contributors and search-and-rescue stakeholders
- **Repository:** Public reference implementation

This document is the implementation plan for **Aerial Rescue Mesh**, a production-quality reference implementation of the open-source Solace Agent Mesh coordinating distributed edge intelligence for search-and-rescue operations.

## 1. Objective

Build a durable, technically credible system in which Solace Agent Mesh coordinates a simulated fleet of drones searching for a missing person in a wilderness area. The system must validate how an event-driven agent mesh coordinates independently deployed edge models with different capabilities, unreliable connectivity, and strict safety constraints.

The primary operational use case is civilian wilderness search and rescue. Disaster response and military personnel recovery are documented extension scenarios. The project must not implement weapons, targeting, facial recognition, autonomous use of force, or any other offensive capability.

### Success criteria

The initial release is successful when it can:

1. Start a missing-person mission from a local operator dashboard.
2. Divide a search area into sectors and assign them across a 23-drone fleet.
3. Stream positions, battery levels, connectivity, and mission progress on a live map.
4. Operate three independently deployed, Ollama-backed edge agents alongside 20 deterministic simulated drones.
5. Detect a loss of connectivity, persist edge-created critical events in a bounded local outbox, retain inbound commands in a durable broker queue, and reassign the affected sector.
6. Analyze prepared wilderness imagery and combine visual and thermal evidence.
7. Produce an evidence-scored candidate location with a traceable explanation.
8. Require explicit human approval before rescue escalation.
9. Display an audit timeline linking commands, evidence, model decisions, and operator actions.
10. Reproduce the end-to-end scenario deterministically in replay mode if cloud connectivity is unavailable.

## 2. Confirmed decisions and boundaries

`docs/adr/` is authoritative for why each decision below was made and for its current status. Where this
plan and an `Accepted` ADR disagree, **the ADR governs and this plan is defective**. Where two documents
conflict, the stricter statement governs until an ADR resolves it. A `—` in the third column means the
decision has no ADR yet and one is owed.

| Area | Decision | Decided by |
| --- | --- | --- |
| Agent platform | Self-hosted open-source Solace Agent Mesh 1.28.7, pinned from the SolaceLabs repository | [ADR-0001](adr/0001-self-hosted-open-source-agent-mesh.md) |
| Agent integration plugins | Official `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1, pinned and locked | [ADR-0001](adr/0001-self-hosted-open-source-agent-mesh.md) |
| Event broker | The PubSub+ software event broker container, pinned by digest under `deploy/compose.yaml`, is the broker for development, integration, continuous integration, acceptance, and release; the Developer-class Solace Cloud service is a non-gating showcase profile selected by environment alone | [ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md) |
| Application event namespace | Application CloudEvents use `aerial-rescue/v1/...`, separate from the A2A namespace | [ADR-0014](adr/0014-application-events-separate-from-a2a.md) |
| Delivery semantics | Each topic family is bound to its guarantee by a total table; the guaranteed families get one durable queue per consuming role, owned by that role's client username, plus one command queue per drone and one dead-message queue | [ADR-0079](adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md), [ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md) |
| Agent models | Local Ollama for the three edge agents. The Agent Mesh `general` and `planning` roles may use a paid Anthropic or OpenAI model; provider, model, and split are selected by the Phase 0 evaluation | [ADR-0002](adr/0002-paid-orchestration-under-enforced-budget-cap.md) |
| Model budget | USD $50 total for the initial release, enforced before each call in tranches, with a persisted spend ledger. Local-only operation stays a supported, tested configuration and no release gate depends on a paid API | [ADR-0002](adr/0002-paid-orchestration-under-enforced-budget-cap.md) |
| Local environment | Apple Silicon MacBook, 64 GB RAM, Docker Desktop, and Ollama on the host; every other component runs under Docker Compose from digest-pinned images | [ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) |
| Local implementation | Python for the simulator, edge agents, broker integration, API, recording, and replay; no Go components | — |
| Python runtimes | Application services on Python 3.14.7; Agent Mesh and its plugins on Python 3.13.15, in separate `uv`-managed environments | [ADR-0004](adr/0004-split-python-runtimes.md) |
| Project layout | A `uv` workspace with per-member packages and one shared lockfile; `agent-mesh/` is a separate non-member project; local and CI tooling use uv 0.12.5 | [ADR-0010](adr/0010-uv-workspace-and-toolchain.md), [ADR-0020](adr/0020-pin-uv-version.md) |
| Durable store | PostgreSQL in Docker Compose, via async SQLAlchemy 2.x and `asyncpg`, with Alembic migrations | [ADR-0003](adr/0003-postgres-durable-mission-store.md) |
| Dashboard | React, TypeScript, Vite, MapLibre, and Playwright, built with Node 24 LTS and `pnpm` | — |
| Fleet | 23 drones by default: three model-backed agents and 20 deterministic simulations | — |
| Integration style | Agent Mesh A2A plus separate application topics over Solace; no public tunnel to the laptop | [ADR-0007](adr/0007-solace-first-implementation-policy.md) |
| Infrastructure policy | Prefer supported Solace components over project-owned equivalents; custom infrastructure requires a documented capability gap and a proving test | [ADR-0007](adr/0007-solace-first-implementation-policy.md) |
| Safety boundary | Agents may only propose. A deterministic command gateway outside model control is the sole publisher of executable commands | [ADR-0005](adr/0005-deterministic-command-gateway.md) |
| Approval semantics | Approval binds a proposal digest, is single-use, expires, and is consumed atomically; a second consumption is a hard denial | [ADR-0006](adr/0006-proposal-bound-single-use-approvals.md) |
| Local operator API | Loopback, exact Host and browser-Origin checks, plus a per-runtime bearer protect state-changing requests in the single-operator simulation | [ADR-0024](adr/0024-local-operator-api-boundary.md) |
| Degraded behaviour | Model failure yields an explicit abstention or manual review; recorded evidence is never substituted into a live run | [ADR-0008](adr/0008-abstention-over-recorded-substitution.md) |
| Continuity | Clearly labeled degraded live simulation, and replay isolated by structural deny sinks rather than by credentials alone | [ADR-0009](adr/0009-isolated-side-effect-free-replay.md) |
| Deployment boundary | The local workstation's Docker Compose stack; Solace Cloud only as the showcase profile; no AWS deployment in the initial release | [ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md) |
| Data | Search-and-rescue artifacts composited onto public-domain wilderness backgrounds; never photographs of real people | [ADR-0013](adr/0013-sar-artifact-imagery-policy.md) |
| Quality gates | Lint and typecheck everything with no escape hatches, enforce mandatory AAA test structure, fail closed when an active gate cannot run, enforce complexity, duplication, mutation, and layering budgets, validate contract artifacts offline, hold the compose stack to its policy, and tier coverage by risk | [ADR-0011](adr/0011-no-exception-lint-typecheck-and-complexity-budgets.md), [ADR-0015](adr/0015-tiered-quality-gates.md), [ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md), [ADR-0018](adr/0018-enforced-arrange-act-assert.md), [ADR-0019](adr/0019-fail-closed-quality-gates.md), [ADR-0021](adr/0021-contract-artifact-manifest.md), [ADR-0023](adr/0023-executable-deep-quality-gates.md), [ADR-0045](adr/0045-fail-closed-compose-policy-gate.md) |
| Verification authority | Staged git hooks give fast feedback; CI re-runs the identical hooks and is the authority | [ADR-0012](adr/0012-git-hooks-with-ci-as-authority.md) |
| Document precedence | Each normative fact has exactly one home; `AGENTS.md` keeps process rules and this plan keeps sequenced delivery | [ADR-0016](adr/0016-documentation-set-split.md) |
| Version control | Never commit without explicit human approval | — |
| Local TLS | A per-checkout certificate authority signs the broker's certificate; keys are never tracked and `tcps` validation is never relaxed | [ADR-0046](adr/0046-generated-local-certificate-authority.md) |
| Broker authorization | Nine authorization roles carry a total, deny-by-default publish and subscribe matrix over the eleven topic families; one client username per process binds to its role's ACL profile, and the factory `default` username is disabled | [ADR-0061](adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |

The upstream baseline is the Apache-2.0-licensed [`SolaceLabs/solace-agent-mesh`](https://github.com/SolaceLabs/solace-agent-mesh) repository. As of August 18, 2026, the pinned stable release is tag `1.28.7` (there is no `v` prefix), commit [`6344d2b8899a6c326e8b52fce9947c4bf4b56ae2`](https://github.com/SolaceLabs/solace-agent-mesh/commit/6344d2b8899a6c326e8b52fce9947c4bf4b56ae2). Install the released package in an isolated, locked subproject rather than vendoring upstream source. Record the package version and source commit in acceptance evidence, and evaluate upgrades deliberately against the full Agent Mesh integration and evaluation suites.

Managed Agent Mesh and Agent Mesh Manager are outside the initial-release architecture. A future managed deployment requires a separate architecture decision and must not silently replace the reproducible self-hosted path.

## 3. Primary operational scenario

The initial end-to-end scenario is:

1. The operator opens the dashboard and selects the prepared **Wilderness Missing Person** scenario.
2. The operator submits a mission containing the last-known position, search polygon, weather summary, and time-since-last-contact.
3. The official Event Mesh Gateway plugin validates and transforms the request, then uses structured invocation to start the versioned Mission Response workflow over A2A topics on the Solace broker.
4. The workflow invokes the Mission Coordinator and discovered specialized agents with typed inputs and outputs. The Orchestrator handles open-ended operator questions and unexpected replanning events. Agents return action proposals; the deterministic command gateway validates and persists each accepted proposal before publishing executable commands.
5. All 23 drones begin reporting telemetry. The dashboard renders the search pattern without sending high-frequency telemetry through an LLM.
6. One drone loses connectivity. Its inbound commands remain in its durable broker queue, edge-created critical events remain in its local outbox, its sector is marked at risk, and Agent Mesh coordinates reassignment.
7. A model-backed drone analyzes a prepared image while other drones report partial thermal and contextual evidence.
8. The Evidence Fusion agent correlates the reports and proposes a candidate location. The evidence
   service validates provenance, delegates the versioned score to pure Tier 1 domain logic, and publishes
   the decision-eligible candidate event.
9. The system requests operator approval. Rescue escalation remains blocked until the operator approves it.
10. The dashboard shows the completed mission and an ordered audit trail.

The scenario must run repeatably from mission creation through completion and expose enough Agent Mesh and broker observability to diagnose every important orchestration, messaging, and safety decision.

## 4. Normative documents

This plan is the home for sequenced delivery: objective, scenario, milestones with exit criteria, risks,
and release criteria. Every other class of normative fact has exactly one home elsewhere, is referenced
here, and is **not** restated ([ADR-0016](adr/0016-documentation-set-split.md)).

| Class of fact | Home | Was |
| --- | --- | --- |
| Component responsibilities, Solace-first policy, runtime layout, edge models, dashboard, operational surfaces, observability, operating modes | [ARCHITECTURE.md](ARCHITECTURE.md) | sections 4 and 10 |
| Event envelope, topic taxonomy, local HTTP API, delivery and failure semantics, canonical serialization | [CONTRACTS.md](CONTRACTS.md) | sections 5 and 6 |
| Safety invariants, approval protocol, privacy and security posture | [SAFETY.md](SAFETY.md) | section 7 |
| Coverage tiers, test classes, stages, toolchain | [TESTING.md](TESTING.md) | section 9 |
| Every numeric parameter and service-level target, with its instrument | [operating-parameters.md](operating-parameters.md) | section 6.1 |
| What is and is not modelled, for a reader from the search-and-rescue domain | [LIMITATIONS.md](LIMITATIONS.md) | — |
| Threat model | [security/threat-model.md](security/threat-model.md) | — |
| Enumerated approval-bypass attempts | [security/approval-bypass-catalogue.md](security/approval-bypass-catalogue.md) | — |
| Why a decision was made, and its current status | [adr/](adr/README.md) | — |

Process rules — how to work, TDD discipline, review, commits, security hygiene — live in `AGENTS.md`.

Where this plan and an `Accepted` ADR disagree, the ADR governs and this plan is defective. Where two
documents conflict, the stricter statement governs until an ADR resolves it.

## 5. Repository shape

The repository structure is below. Entries marked `(exists)` are already in place; entries marked
`(scaffold)` contain a manifest and typed import package only, with runtime behavior and tests still
planned. The rest are planned. Each `packages/` and `services/` member carries its own `pyproject.toml`
declaring its risk tier
([ADR-0010](adr/0010-uv-workspace-and-toolchain.md), [ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md)).

```text
AGENTS.md                      (exists)  process rules only
CLAUDE.md -> AGENTS.md         (exists)
CHANGELOG.md                   (exists)
TECH_DEBT.md                   (exists)  accepted risk, with what clears each item
CONTRIBUTING.md                (exists)
LICENSE                        (exists)
NOTICE                         (exists)
README.md                      (exists)
.env.example                   (exists)  placeholder names only; never a live value
.dockerignore                  (exists)  keeps secrets and caches out of the application image
justfile                       (exists)
.pre-commit-config.yaml        (exists)
.github/workflows/             (exists)
scripts/                       (exists)  hooks/, diagrams.sh, fix.sh, broker-secrets.sh
.python-version                (exists)  application Python 3.14.7 pin
tools/                         (scaffold) root repository-tooling package marker
pyproject.toml                 (exists)  uv workspace root, declares members
uv.lock                        (exists)  macOS arm64 and Linux aarch64 resolution
mutation-survivors.toml        (exists)  exact, expiring Tier 1 survivor reviews
dependency-waivers.toml        (exists)  expiring, reviewed upstream advisory waivers
apps/
  dashboard/
services/                      (scaffold) four typed service package shells
  dashboard_api/
  fleet_simulator/             (exists)  scenario boundary, tick fold, telemetry, composition root
    tests/                     (exists)  member-local unit and property tests
  command_gateway/
    tests/                               member-local mutation tests
  scenario_service/
  evidence_service/
  recorder/
packages/                      (exists)  four active members and one typed package shell
  broker/                      (exists)  subscriptions, queue projection, messaging, SEMP
    tests/                     (exists)  member-local unit and property tests
  contracts/                   (exists)  canonical serialization, digest, topic grammar, envelope profile
    tests/                     (exists)  member-local mutation tests
  domain/                      (exists)  connectivity, idempotency, approvals, command authority
    tests/                     (exists)  member-local mutation tests
  store/                       (exists)  the durable target, its bounds, and its engine
    src/aerial_rescue_store/migrations/  Alembic revisions, inside the member that owns them
    tests/                     (exists)  member-local unit tests
  observability/               (scaffold)
deploy/                        (exists)  held to the compose policy gate on every commit
  compose.yaml                 (exists)  broker, Postgres, Agent Mesh, services, discovery agent
  agent-mesh/Dockerfile        (exists)  official image plus the two hashed plugin wheels
  application/Dockerfile       (exists)  Python 3.14.7 workspace image
  certs/, secrets/             (ignored) written by scripts/broker-secrets.sh
agent-mesh/                    (exists)  separate non-member uv project
  .python-version              (exists)  Agent Mesh Python 3.13.15 pin
  pyproject.toml               (exists)  the three pinned wheels and this domain's toolchain
  uv.lock                      (exists)  251 packages, macOS arm64 and Linux aarch64
  tests/                       (exists)  black-box compatibility probes, run on 3.13
  tools/                       (exists)  the offline semantic-configuration validator, run on 3.13
  configs/
    agents/
    gateways/
    workflows/
  plugins/
  prompts/
  evaluations/
schemas/                       (exists)  contract-manifest.toml and the v1 JSON Schemas
fixtures/
  golden/                      (exists)  golden fixtures, one directory per schema
scenarios/
release-evidence/              (exists)  per-phase acceptance evidence, redacted
tests/
  phase0/                      (exists)  feasibility probes against the pinned runtimes
  unit/
  contract/                    (exists)  schema identity and the golden-fixture oracle
  integration/                 (exists)  the fleet simulator against the running broker
  e2e/
  performance/
  security/                    (exists)  broker authorization against the running broker
docs/
  IMPLEMENTATION_PLAN.md       (exists)  sequenced delivery, risks, release criteria
  adr/                         (exists)  authoritative decision log
  architecture/                (exists)  editable diagram sources plus generated PNGs
  ARCHITECTURE.md              (exists)  component responsibilities
  CONTRACTS.md                 (exists)  event envelope, topics, HTTP API
  SAFETY.md                    (exists)  approval protocol and safety invariants
  TESTING.md                   (exists)  test classes, stages, coverage tiers
  LIMITATIONS.md               (exists)  what is and is not modelled
  operating-parameters.md      (exists)  every number, with its instrument
  runbooks/
  security/                    (exists)  threat model, approval-bypass catalogue
```

Prefer shared packages over copied logic, but do not create abstractions until at least two real consumers require them.

## 6. Milestones

Milestones are capability-based. A phase is complete only when its tests, documentation, and quality gates pass; calendar dates do not redefine completion.

The HTTP/SSE Web UI exercised in Phase 0 is the upstream Agent Mesh engineering surface described in
[ARCHITECTURE.md](ARCHITECTURE.md#solace-operational-surfaces), not the authoritative Aerial Rescue Mesh
operator dashboard. Phases 1 and 2 establish the dashboard's quality and contract prerequisites. Beginning
with Phase 3, every capability phase delivers the corresponding operator-interface increment and its unit,
contract, and operator-flow evidence.

### Phase 0: Open-source Agent Mesh feasibility gate

- Pin `solace-agent-mesh==1.28.7` in the isolated Python 3.13.15 `agent-mesh/` project and record the upstream source revision.
- Pin and hash `sam-event-mesh-gateway==1.1.0` and `sam-event-mesh-tool==0.1.1`; prove those exact independently released wheels are compatible with Agent Mesh 1.28.7 before treating the combination as supported.
- **Done offline:** enforce the semantic-configuration gate from [ADR-0032](adr/0032-agent-mesh-semantic-configuration-validator.md) with the exact pinned parsers, configuration models, plugin symbols, include rules, model policy, topic authority, environment references, and secret hygiene. The gate starts no runtime or external client and is not live compatibility evidence.
- **Done:** the compose stack is up, including the `mesh` profile, and the pinned runtime is connected to local Ollama and to the container. All four apps run on the image's own Python 3.13.11 ([mesh-first-run.md](../release-evidence/phase-0/mesh-first-run.md)). The dedicated plugin-compatibility probe has now been run *inside* the built image too: `scripts/probes/agent-mesh-image-probe.sh` checks the three pins, the gateway entry point, the tool's module-path import, and seven runtime symbols on the image's own CPython 3.13.11 with no network, and all five pass ([event-mesh-gateway-first-run.md](../release-evidence/phase-0/event-mesh-gateway-first-run.md)).
- **Identities and ACL profiles done:** nine least-privilege client usernames and nine deny-by-default ACL profiles are provisioned on the container over SEMP, the factory `default` username is disabled, and catalogue cases B17, B18, and B19 pass against the running broker ([ADR-0061](adr/0061-least-privilege-broker-principals-and-topic-authorization.md), [broker-authorization.md](../release-evidence/phase-0/broker-authorization.md)). Still owed: reproducing the identities, ACL profiles, and queues on the Developer-class Solace Cloud service for the showcase, and the fleet's connection count against that service's limit of 100 ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)).
- Capture redacted Cloud-console evidence for the three showcase surfaces: Broker Manager and Cluster Manager, Event Portal Designer and Catalog, and Event Portal runtime discovery of the container through the Event Management Agent.
- **Done:** the built-in Orchestrator, a MissionCoordinator agent, a versioned MissionResponse workflow, and the HTTP/SSE Web UI all run under `agent-mesh/configs/`. Agent-card discovery, structured workflow invocation, and one A2A delegation are asserted by `tests/phase0/test_agent_mesh_live.py` against the running broker rather than read off Broker Manager. The delegation the kill criterion turns on is the model-chosen one: a task to the Orchestrator produced a request on the MissionCoordinator topic, which it can only reach through a tool call ([mesh-first-run.md](../release-evidence/phase-0/mesh-first-run.md)).
- **Done, both halves.** *Ingress:* one validated salient CloudEvent becomes one structured A2A task. The official Event Mesh Gateway 1.1.0 runs as a fifth app on its own `event-mesh-gateway` identity, subscribes to `aerial-rescue/v1/*/drone/*/event/salient`, and submits a structured invocation carrying the payload as a typed artifact ([event-mesh-gateway-first-run.md](../release-evidence/phase-0/event-mesh-gateway-first-run.md)). *Egress:* one Event Mesh Tool request produces one validated, non-actuating command-gateway response. The tool runs inside the MissionCoordinator on its own `event-mesh-tool` identity, and `services/command_gateway` answers it from two deny-by-default tables, replies on the reserved channel, and republishes the answer as a CloudEvent record. `tests/phase0/test_event_mesh_tool_live.py` asserts the reply, the non-actuation claim both on the wire and by observation, a refusal by name, the model actually reaching the tool, and the ACL denial that makes the reply channel a narrowing ([event-mesh-tool-first-run.md](../release-evidence/phase-0/event-mesh-tool-first-run.md), [ADR-0068](adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md), [ADR-0069](adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md), [ADR-0070](adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)). Both gateway queues are temporary and not configurable, which [ADR-0071](adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md) accepts and scopes.
- Stop and revise the architecture if the selected Ollama model cannot provide reliable structured output/tool use, the container cannot carry the required A2A traffic, or the pinned plugins cannot enforce the domain boundary.
- **Settled by waiver:** the locked dependency audit ran against the 251-package lock and reports eleven advisories across five packages that Agent Mesh 1.28.7 pins exactly; the `google-adk` override was attempted and is unsatisfiable. Each advisory is recorded with its reachability statement and compensating control as an expiring waiver in `dependency-waivers.toml`, all expiring 2026-09-18, and the accepted risk is carried in [TECH_DEBT.md](../TECH_DEBT.md) ([ADR-0031](adr/0031-reject-the-google-adk-version-override.md)).

### Phase 1: Foundation

- **Done:** the Docker Compose stack definition, the compose policy gate that holds it at both blocking stages, and the per-checkout certificate authority ([ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), [ADR-0045](adr/0045-fail-closed-compose-policy-gate.md), [ADR-0046](adr/0046-generated-local-certificate-authority.md)). The default profile's first live run is recorded in [first-live-run.md](../release-evidence/phase-0/first-live-run.md): broker and Postgres both reach healthy in 40.75s, every published port is on loopback, and TLS validates against the generated authority with hostname checking left on. The `mesh`, `services`, and `event-portal` profiles remain unstarted.
- Create project guidance, implementation plan, changelog, README skeleton, toolchain files, virtual environment, lockfiles, and CI gates.
- Enforce exactly one ordered Arrange-Act-Assert cycle in every project-owned executable test before the first production behavior lands.
- Make every active quality gate fail on a missing tool, manifest, lockfile, test, or report; run the same
  full-tree entry points in CI.
- Enforce Ruff complexity, cognitive complexity, multi-language duplication, and independent per-module
  Tier 1 mutation scoring before production behavior lands.
- Create the separate Python 3.14.7 application and Python 3.13.15 Agent Mesh environments and verify both lockfiles from a clean checkout.
- **Done:** the Agent Mesh YAML is `agent-mesh/configs/`, the agent cards are declared there and read back from the running mesh, and the model parameters are pinned by digest in `agent-mesh/model-lock.toml` ([ADR-0063](adr/0063-lock-local-models-by-manifest-digest.md)). The Event Mesh Gateway and the Event Mesh Tool are both configured there and both proven live against the broker.
- **Done:** establish and self-test the dashboard TypeScript policy and whole-tree verification stages. They
  remain inert until Phase 3 creates `apps/dashboard`, then fail closed on an incomplete configuration
  ([ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md)).

### Phase 2: Contracts and broker

- **Done:** the topic grammar, the CloudEvents envelope profile, the v1 JSON Schemas with golden fixtures, and the contract manifest ([ADR-0036](adr/0036-ascii-topic-grammar-bound-to-event-type.md), [ADR-0037](adr/0037-cloudevents-envelope-profile.md), [ADR-0038](adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md)).
- **Done:** broker identities and ACLs ([ADR-0061](adr/0061-least-privilege-broker-principals-and-topic-authorization.md)).
- **Done:** the delivery semantics and the queues that carry them. Each family is bound to its
  guarantee by a total table ([ADR-0079](adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md)),
  and the queue set is a projection of the subscribe grants intersected with it, so a queue narrows
  authority and can never widen it ([ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)).
  Twenty-two queues are live on the container, written with every value explicit because five broker
  defaults are wrong for this system. Spooling with nothing bound, fan-out to every matching queue,
  removal only on acknowledgement, rejection to the dead-message queue, the redelivery bound ending
  rather than looping, and a role holding the topic grant still being refused another role's queue
  are all asserted against the broker
  ([guaranteed-delivery-first-run.md](../release-evidence/phase-2/guaranteed-delivery-first-run.md)).
  **The backlog-recovery target is measured.** Five hundred commands spooled across the reference
  fleet drain in 7.141 seconds at worst over three samples, against a target of 10, under the
  instrument [ADR-0084](adr/0084-give-backlog-recovery-an-instrument.md) defines
  ([backlog-recovery-first-run.md](../release-evidence/phase-2/backlog-recovery-first-run.md)). The
  number confirms the command-intake cap's derivation end to end, and it is dominated by that
  configuration rather than by the broker, which the record says plainly. Still owed: message
  expiry, which is configured and unobserved, and a real reconnect -- the measurement models an
  absent consumer, not a broken session.
- Build the Python broker adapter test-first against the PubSub+ container in `deploy/compose.yaml` ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)).
- Complete the browser-consumed event and local HTTP/SSE contracts, schemas, and golden fixtures needed for
  generated TypeScript types and cross-language runtime validation. The dashboard must consume these
  artifacts rather than define a second wire contract
  ([ADR-0058](adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md)).

### Phase 3: Simulator and operator-dashboard foundation

- **Done:** the drone connectivity machine ([ADR-0039](adr/0039-drone-connectivity-states-and-recovery.md)),
  the producer-scoped sequence and known-identifier rules, the approval record with dual-clock consumption
  ([ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)), and the deny-by-default
  command-authority table ([ADR-0041](adr/0041-deny-by-default-command-authority-table.md)), and the
  mission lifecycle ([ADR-0072](adr/0072-mission-lifecycle-states.md)), and the sector lifecycle
  ([ADR-0073](adr/0073-sector-lifecycle-states.md)), the command dispatch lifecycle
  ([ADR-0074](adr/0074-command-dispatch-lifecycle.md)), and the evidence lifecycle
  ([ADR-0075](adr/0075-evidence-lifecycle-states.md)) in `packages/domain`. All five Tier 1 domain
  state machines [ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md) names now exist.
- **Done:** the evidence score, its named ordinal bands, and the corroboration floor that keeps the
  escalating band unreachable from a single model-generated observation
  ([ADR-0076](adr/0076-evidence-score-bands.md)). ADR-0017 carries evidence scoring as a Tier 1 row
  of its own, separate from the state machines. Bypass cases B31 and B32 are closed at the domain;
  the evidence service's use of them is still owed. The band boundaries are an open row in
  [operating-parameters.md](operating-parameters.md).
- **Four of five done.** The Tier 2 fleet-simulator adapter accepts a scenario as a frozen
  composition-boundary value ([ADR-0077](adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md)),
  folds one heartbeat-or-miss observation per drone per tick in ascending identifier order
  ([ADR-0078](adr/0078-one-tick-is-one-observation-per-drone.md)), and drives the mission, sector, and
  connectivity machines from it. Each tick publishes one schema-bound telemetry CloudEvent through a
  direct publisher, proven live on the least-privilege `fleet-simulator` identity with a
  `dashboard-api` reader as the positive control
  ([fleet-simulator-first-run.md](../release-evidence/phase-3/fleet-simulator-first-run.md)).
- **Done: the command dispatch lifecycle, drone side.** The drone-command and command-result
  families are bound to payload and event schemas
  ([ADR-0082](adr/0082-bind-the-drone-command-and-its-result-to-payload-schemas.md)), and the send
  budget, acknowledgement timeout, backoff, and jitter are derived rows
  ([ADR-0081](adr/0081-give-command-dispatch-one-interval.md)). Each tick is followed by a bounded
  drain of every drone's own durable queue: the simulator folds `packages/domain`'s machine over
  what arrives, publishes an acknowledgement and then a resolution, and settles only after both are
  on the wire. It is the first process in this repository to bind a durable queue in production
  ([command-dispatch-first-run.md](../release-evidence/phase-3/command-dispatch-first-run.md)).
  What the plan recorded as this capability's blocker -- the send budget -- turned out not to be
  one: every edge a drone applies is blind to it, and a property test asserts that. The blocker was
  the wire contract.
  Still owed on this lifecycle: the **gateway's half**, which needs `packages/store`, because
  `ACCEPTED` in [ADR-0074](adr/0074-command-dispatch-lifecycle.md) means validated *and persisted*.
  `SEND`, `TIME_OUT`, and `ABANDONED` are therefore unexercised and the intake claim is
  at-least-once with duplicates possible across a restart. The backlog-recovery measurement this
  consumer unblocked has since been made.
- Still owed: the **evidence lifecycle and score**, which needs the evidence band boundaries, an
  open row in [operating-parameters.md](operating-parameters.md).
- Before the first dashboard source file, record the dashboard stack and exact runtime and toolchain pins in
  an ADR. Then create `apps/dashboard`, commit its `pnpm` lockfile, and activate the strict TypeScript,
  lint, format, test, coverage, duplication, and production-build gates from
  [ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md).
- Generate and commit dashboard contract types from the versioned schemas, freshness-gate them, validate
  every HTTP/SSE input at runtime, and prove Python and TypeScript refusal parity with the shared golden
  fixtures ([ADR-0058](adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md)).
- Implement the FastAPI dashboard API and the first tested operator vertical slice: scenario selection and
  mission start and reset, a persistent run-mode and readiness region, the MapLibre search map, fleet status,
  the ordered mission timeline, and the record/replay adapter. Reduce normalized domain events into
  presentation state, and cover loading, empty, retrying, failure, and recovered states from this first slice.
  Deliver the current API process's bearer through the local startup path, and re-establish that runtime
  context after an API restart before retrying a mutation
  ([ADR-0024](adr/0024-local-operator-api-boundary.md)).

### Phase 4: Edge intelligence and evidence interface

- Pull and pin the three Ollama models.
- Implement typed edge-agent prompts, image analysis, evidence events, timeouts, abstention, and replay-only fixtures.
- Add the evidence panel and edge-agent interface states for prepared-artifact provenance, validated
  observations, evidence-score contributors, corroboration, timeouts, invalid output, abstention, manual
  review, and rejection. Make abstention visually distinct from a low evidence score
  ([ADR-0008](adr/0008-abstention-over-recorded-substitution.md)).

### Phase 5: Agent Mesh expansion and orchestration interface

- Configure and evaluate the Mission Response workflow, Mission Coordinator, Evidence Fusion, and three independently deployed edge agents using the pinned Agent Mesh evaluation tooling.
- Productionize and verify the pinned Event Mesh Gateway, Event Mesh Tool, and deterministic command-gateway boundary.
- Verify agent discovery, delegation, structured outputs, allowlists, timeouts, and failure behavior against the pinned runtime.
- Exercise the orchestration path through a validated, non-actuating proposal against the container, then run the showcase profile against the Solace Cloud service and capture redacted console evidence ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)).
- Extend the operator dashboard's timeline, proposal, and health surfaces with validated Agent Mesh responses
  and the task, correlation, causation, proposal, and command identifiers needed to trace orchestration. Keep
  the upstream Agent Mesh Web UI an engineering surface rather than an approval or dispatch surface.
- Display the active orchestration configuration (paid provider or local-only), the model-spend warning
  state, budget exhaustion, and any audited fallback to local-only operation
  ([ADR-0002](adr/0002-paid-orchestration-under-enforced-budget-cap.md),
  [operating-parameters.md](operating-parameters.md#model-spend-budget)).

### Phase 6: Resilience, safety, and approval interface

- Add connectivity loss, durable edge outboxes, guaranteed command handling, retries, proposal-bound approval enforcement, broker, Agent Mesh, and Ollama failure behaviour, and replay isolation verification.
- Implement the protected approval experience: show the proposal, digest, and action being decided; make the
  consequences explicit, expose every approval lifecycle outcome, make the control keyboard accessible and
  screen-reader labeled, disable it after submission, and visibly surface a denied repeat
  ([SAFETY.md](SAFETY.md), [security/approval-bypass-catalogue.md](security/approval-bypass-catalogue.md)).
- Make live simulation, degraded live simulation, and replay unmistakable. Render connectivity loss,
  reassignment, backlog, offline, failure, and recovered states while preserving telemetry and bounded
  operator control. Replay uses the same dashboard-facing event path but exposes no approval or escalation
  action path
  ([ADR-0008](adr/0008-abstention-over-recorded-substitution.md),
  [ADR-0009](adr/0009-isolated-side-effect-free-replay.md)).
- Exercise the complete live-simulation operator flow through approval and simulated rescue escalation with focused
  component and Playwright tests written alongside the behavior.

### Phase 7: Release qualification, security, and user acceptance

- Qualify every completed member against its declared coverage, complexity, duplication, mutation, and
  test-inventory gates.
- Complete integration, E2E, Playwright UAT, agent evaluations, performance checks, threat model, and dependency/secret scanning.
- Qualify the mode-appropriate operator workflows in live simulation, degraded live simulation, and replay
  at the reference MacBook's normal resolution without developer tools. Playwright acceptance covers the
  accessible proposal-bound approval in the live modes and an unavailable approval control in replay while
  recorded approval events remain visible; loading, empty, degraded, offline, retrying, failure, and
  recovered states; mode labeling; map attribution; and the visible server-side refusals required by
  [CONTRACTS.md](CONTRACTS.md), [SAFETY.md](SAFETY.md), and the applicable
  [approval-bypass catalogue](security/approval-bypass-catalogue.md) cases.

### Phase 8: Initial release readiness

- Serve the production dashboard bundle through the dashboard API, then verify one-command startup,
  readiness checks, live simulation, degraded live simulation, isolated replay, and the mode-appropriate
  operator workflows from a clean checkout without Vite or browser developer tools
  ([ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)).
- Complete setup, operations, recovery, and troubleshooting runbooks.
- Run the complete acceptance, performance, soak, safety, and security suites in the supported reference environment.
- Capture redacted user-interface acceptance evidence and verify asset licensing and map attribution.
- Record known limitations and create prioritized follow-on work without weakening initial-release gates.

### Phase 9: Ongoing evolution

- Add disaster-response and personnel-recovery scenarios only after the wilderness workflow is stable.
- Evaluate additional deployment targets, including an explicitly authorized AWS architecture, without changing the local reference path implicitly.
- Reassess model selection, operating cost, performance, upstream Agent Mesh releases, and a possible managed Agent Mesh deployment as dependencies and access evolve.
- Maintain backward-compatible contracts or publish explicit versioned migrations.
- Evolve the dashboard through versioned normalized events and reducers. Do not hard-code scenario business
  rules in components, and add extension-specific presentation only after its contracts are versioned.

## 7. Definition of done

The initial release is ready only when:

- The complete wilderness scenario passes in live simulation and replay modes.
- Human approval cannot be bypassed in unit, integration, or Playwright tests.
- All schemas and cross-language contracts pass.
- The whole-tree AAA conformance gate and its positive and negative self-tests pass with no exclusions or suppressions.
- Every project-owned member meets its declared risk-tier coverage and test-inventory requirements, and
  pinned upstream components pass their black-box gates.
- Ruff complexity, cognitive complexity, multi-language duplication, and per-module Tier 1 mutation gates
  pass at the limits in `docs/operating-parameters.md`.
- Linting, formatting, type checking, security scans, integration tests, E2E tests, and UAT pass without introduced warnings.
- Broker disconnect and model-failure tests demonstrate recovery or safe degradation.
- No secrets or improperly licensed assets exist in Git history or the working tree.
- Architecture and workflow documentation includes editable diagram sources and generated PNGs.
- Setup and recovery instructions work on the reference MacBook from a clean checkout.
- The mode-appropriate operator flows pass at the reference MacBook's normal resolution without developer
  tools, with an always-visible operating mode, accessible proposal-bound approval in live modes, an
  unavailable approval control but visible recorded approval events in replay, loading, empty, degraded,
  offline, retrying, failure, and recovered states, and preserved map attribution.
- Local-only operation passes the complete scenario, so no release gate depends on a paid model API.
- Total paid model spend is within the USD $50 cap, with the spend ledger committed as release evidence.
- No release gate depends on the Solace Cloud service; the showcase profile is evidence of demonstration, never of correctness ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)).

## 8. Principal risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Restricted network or cloud outage | Deterministic, clearly labeled replay through the same dashboard |
| Solace Cloud trial expiry | Ends only the showcase profile; every gated path runs on the container ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)) |
| Local Ollama contention on the reference MacBook | Bound parallel inference, warm only required models, measure memory pressure, and define deterministic timeouts/abstention |
| Agent Mesh version drift | Pin release 1.28.7 and its lockfile; upgrade only through compatibility, gateway, A2A, evaluation, and security gates |
| Event Mesh plugin drift or unsafe settlement defaults | Pin gateway 1.1.0 and tool 0.1.1, validate schemas, configure explicit acknowledgement/failure policies, and test redelivery and ACL denial |
| Known vulnerability in a pinned upstream dependency | Audit reachability, bind upstream UI to loopback, minimize enabled surfaces, track the upstream fix, and require a safe upgrade/fix or explicit expiring waiver before release |
| Split Python runtime compatibility | Isolate Python 3.14.7 and 3.13.15 environments and test any shared package on both |
| High-frequency telemetry overwhelms agents | Keep routine telemetry on the data plane and invoke agents only for salient events |
| Local-model latency or malformed output | Warm models, enforce timeouts and schemas, and produce explicit abstention/manual-review outcomes |
| Duplicate or delayed guaranteed messages | Idempotency keys, sequence checks, correlation, and replay-safe handlers |
| Unsafe autonomous action | Deterministic approval gate outside model control |
| Public-repository data concerns | Synthetic or public-domain assets with provenance and checksums |
| Uncontrolled scope growth | Stabilize the wilderness scenario first; defer AWS and additional scenarios to versioned follow-on phases |

## 9. Primary technical references

- [Solace Agent Mesh open-source repository](https://github.com/SolaceLabs/solace-agent-mesh)
- [Solace Agent Mesh releases](https://github.com/SolaceLabs/solace-agent-mesh/releases)
- [Open-source Agent Mesh documentation](https://solacelabs.github.io/solace-agent-mesh/)
- [Agent Mesh architecture](https://solacelabs.github.io/solace-agent-mesh/docs/documentation/getting-started/architecture)
- [Agent Mesh gateways](https://solacelabs.github.io/solace-agent-mesh/docs/documentation/components/gateways)
- [SAM Event Mesh Gateway 1.1.0](https://pypi.org/project/sam-event-mesh-gateway/1.1.0/)
- [SAM Event Mesh Tool 0.1.1](https://pypi.org/project/sam-event-mesh-tool/0.1.1/)
- [Solace Messaging API for Python](https://docs.solace.com/API/Messaging-APIs/Python-API/python-home.htm)
- [Supported Solace Python environments](https://docs.solace.com/API/API-Developer-Guide-Python/Python-API-supported-Environments.htm)
- [Solace Python distributed tracing](https://docs.solace.com/API/API-Developer-Guide-Python/Python-API-Distributed-Tracing.htm)
- [Python 3.14.7 release](https://www.python.org/downloads/release/python-3147/)
- [Python 3.13.15 release](https://www.python.org/downloads/release/python-31315/)
- [Ollama Qwen3-VL](https://ollama.com/library/qwen3-vl)
- [Ollama Qwen3](https://ollama.com/library/qwen3)
