# Aerial Rescue Mesh

A public, production-quality reference implementation of the open-source
[Solace Agent Mesh](https://github.com/SolaceLabs/solace-agent-mesh) coordinating
independently deployed edge intelligence for wilderness search and rescue.

The question this project exists to answer is narrow and testable: **can an event-driven
agent mesh coordinate independently deployed models, with different capabilities and
unreliable connectivity, safely enough that a human still authorizes every consequential
action?**

The [Solace value guide](docs/SOLACE_VALUE.md) makes the evaluation lens explicit: search and rescue is
the pressure test; agent coordination, selective event-to-agent bridging, durable delivery,
broker-enforced authority, and observable proof are the demonstration.

## Status

**No complete end-to-end operational demonstration is accepted yet.** This is a repository under
construction. Committed local slices exercise Agent Mesh discovery and delegation, both official Event
Mesh plugins, broker authorization and guaranteed delivery, fleet telemetry, and drone-side command
consumption. The dashboard feature branch implements its production runtime and has passed the complete
64-case fixture inventory, six inspected and redacted screenshot cases, an eight-case developmental
production run, and a 61-sample, 31.3-minute developmental soak. Those production results were measured
from an uncommitted revision, so A8/R7/R9 still require a clean committed rerun, release evidence,
current diagrams, and final gates. The evidence path, persistent command authorization, complete human
approval flow, and cross-system audit trace remain unassembled.

| Area | State |
| --- | --- |
| Foundation, toolchain, and quality gates | Complete |
| Contracts: canonical serialization and the proposal digest | Complete, in [`packages/contracts`](packages/contracts) |
| Agent Mesh configuration validation | Complete as an offline gate, in [`agent-mesh/tools`](agent-mesh/tools); armed: five configurations under `agent-mesh/configs/` |
| Docker Compose stack | Defined once in [`deploy/`](deploy) as the shared `aerial-rescue-mesh` project. Normal `just up` owns the broker, PostgreSQL, and default Agent Mesh lifecycle; `just mission-control-up` requires those two stateful services to be healthy, starts seven dashboard extension targets without replacing them, and post-verifies their container identities. Caddy remains the sole dashboard publisher. Pulled images are digest-pinned and the stack is held to a policy gate on every commit ([ADR-0102](docs/adr/0102-start-the-agent-mesh-with-the-default-profile.md), [ADR-0139](docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)). The broker and Postgres first live run is recorded in [`release-evidence/phase-0/first-live-run.md`](release-evidence/phase-0/first-live-run.md); complete mission-control live acceptance remains a release gate |
| Broker authorization | The current matrix defines ten least-privilege client usernames over thirteen topic families on deny-by-default ACL profiles. The original nine-role matrix is **applied to the running container** in the Phase 0 record; the expanded dashboard lifecycle controls passed the exact 16-case local authorization suite on the developmental revision and still require clean committed evidence ([`release-evidence/phase-0/broker-authorization.md`](release-evidence/phase-0/broker-authorization.md), [ADR-0111](docs/adr/0111-broker-dashboard-lifecycle-sources.md)) |
| Live Ollama messaging and the Agent Mesh runtime | **Running, and started by default.** The stack carries the Orchestrator, a specialised agent, a versioned workflow, the HTTP/SSE Web UI, and the Event Mesh Gateway on a digest-locked local model; agent-card discovery, structured workflow invocation, and one A2A delegation from a workflow node to its peer are asserted against the running broker. The model-chosen delegation is a recorded observation, not a repeatable test ([`release-evidence/phase-0/mesh-first-run.md`](release-evidence/phase-0/mesh-first-run.md)) |
| Application events and official Event Mesh plugins | **Live slices recorded.** Fleet telemetry crosses the broker; one salient CloudEvent becomes a structured A2A task; one Event Mesh Tool request receives a validated, non-actuating command-gateway reply ([`fleet-simulator-first-run.md`](release-evidence/phase-3/fleet-simulator-first-run.md), [`event-mesh-gateway-first-run.md`](release-evidence/phase-0/event-mesh-gateway-first-run.md), [`event-mesh-tool-first-run.md`](release-evidence/phase-0/event-mesh-tool-first-run.md)) |
| Guaranteed delivery and drone command consumption | **Live slices recorded.** Durable queues, settlement, bounded redelivery, dead-message handling, backlog drain, and drone-side command consumption are exercised against the local broker; an actual broken-session reconnect and gateway-side persistent dispatch remain unproved ([`guaranteed-delivery-first-run.md`](release-evidence/phase-2/guaranteed-delivery-first-run.md), [`backlog-recovery-first-run.md`](release-evidence/phase-2/backlog-recovery-first-run.md), [`command-dispatch-first-run.md`](release-evidence/phase-3/command-dispatch-first-run.md)) |
| Durable mission store | **The five-revision schema and both repository families are exercised on PostgreSQL.** Focused integration uses databases created and dropped by the test, while the dashboard production topology deliberately reuses the shared project's retained PostgreSQL container and history. Revision `0005_dashboard_runtime` adds the narrow dashboard mission/run, exact-byte operation, broker-deduplication, and bounded ordered-read paths used by the dashboard API and recorder. The live integration walks all five revisions in both directions and proves prepared-before-start persistence, exact start/reset retry, same-run pending recovery, predecessor retention, broker deduplication, and snapshot reads. Audit ordinal plus exact operation state and bytes are authority; revision 0005 stores no unused operation, mission, or run wall-clock metadata. Killed-process recovery and the paid-call ledger remain unproved ([ADR-0113](docs/adr/0113-persist-dashboard-runtime-after-the-current-store-head.md), [ADR-0139](docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)) |
| Mission dashboard | **A5-A7 and R2/R4-R6/R8/R9 are implemented; A8/R7/R9 release qualification remains open.** The validated source adapters, secure mutation client, map-first UI, strict FastAPI/Unix-socket boundary, catalog, durable orchestration, private scenario/fleet control, recorder, replay validator, and shared-project packaging are present. All 64 fixture cases and six inspected/redacted screenshots are green. A developmental uncommitted shared-stack run passed eight production cases and a 61-sample, 31.3-minute soak; it is remediation evidence, not the required clean committed release record. Final status waits on that rerun, release evidence, refreshed diagrams, and the full gates ([FRONTEND_BUILD.md](docs/FRONTEND_BUILD.md)) |
| Supply-chain and workflow scanning | Locked-dependency audit on every push; Trivy over `deploy/` at pre-push and over every stack image daily in continuous integration; zizmor on every workflow change; CodeQL for Python; Dependabot for every ecosystem the repository has |
| Solace Cloud | A non-gating showcase profile for the Cloud console; it has not been exercised, and no gate depends on it |
| End-to-end mission experience | Not yet accepted: the selected dashboard runtime is assembled and has passed developmental browser/restart/soak execution, but lacks the clean committed rerun and final evidence/gates. The broader evidence service, persistent approval-to-command path, executable edge agents, rescue path, and complete cross-system audit trace remain release work |

Sequenced delivery and exit criteria are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md); committed live observations are scoped in
[`release-evidence/`](release-evidence/).

## Target end-to-end mission scenario

The sequence below is the target demonstration, not the current dashboard acceptance claim. The
implemented dashboard slice starts a fixed degraded-live search with twenty deterministic simulations;
it lists the three external edge agents as `DECLARED ONLY — NOT EXECUTED` and accepts no telemetry or
connectivity for them. Weather, time-since-contact, evidence, approval, command, and rescue inputs stay
outside this slice until each has an implemented producer-to-consumer effect.

A person is missing in a wilderness area. An operator opens the dashboard and submits a last-known
position and search polygon.

1. The Event Mesh Gateway validates the request and starts a versioned Mission Response
   workflow over A2A topics on the Solace broker.
2. The workflow invokes a Mission Coordinator and discovered specialized agents with typed
   inputs and outputs. Agents **propose**; a deterministic command gateway outside model
   control is the only component that publishes an executable command.
3. In the future complete demonstration, 23 executable drones report telemetry — three model-backed
   edge agents on local Ollama and 20 deterministic simulations that validate fleet-scale behaviour.
   Routine telemetry never passes through a language model. That 23-executable-member claim does not
   apply to the current dashboard slice described above.
4. One drone loses connectivity. Its inbound commands wait in a durable broker queue, its
   edge-created critical events wait in a bounded local outbox, its sector is marked at
   risk, and the mesh coordinates reassignment.
5. A vision agent analyzes prepared imagery while other drones report partial thermal and
   contextual evidence. An Evidence Fusion agent correlates them into an evidence-scored
   candidate location with a traceable explanation.
6. The system requests operator approval. **Rescue escalation stays blocked until a human
   approves it.**
7. The dashboard shows the completed mission and an ordered audit trail linking every
   command, model decision, and operator action.

The same scenario must replay deterministically from a committed recording, clearly
labeled as replay, so an internet or cloud outage does not end the demonstration.

## Architecture

![Aerial Rescue Mesh architecture](docs/architecture/aerial-rescue-mesh-overview.png)

The design is deliberately Solace-first: use the supported open-source building blocks
before writing equivalent infrastructure. Project-owned code is reserved for the rescue
scenario and simulator, strict domain validation, deterministic evidence scoring, mission
state, the command and approval policy boundary, recording and replay isolation, and the
operator dashboard. New custom transport or broker abstraction requires a documented
capability gap and a test proving the official component is insufficient.

Two Python runtimes run side by side, because Agent Mesh declares `>=3.10.16,<3.14` while
application services target the newest stable release. They never share a lockfile or a
virtual environment.

Full component responsibilities are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## The safety boundary

This is a humanitarian, defensive project. It does not implement weapons, targeting,
facial recognition, autonomous use of force, or any other offensive capability, and it
never processes identifying biometric data — a rescue subject is represented anonymously.
The detection target is search-and-rescue **artifacts**: a high-visibility jacket, a tarp,
a pack, a tent, a reflective panel, disturbed ground.

Three invariants hold regardless of what any model produces:

- **Agents may only propose.** A deterministic command gateway outside model control is
  the sole publisher of executable commands.
- **Approvals bind a proposal digest, are single-use, and expire.** A second consumption
  is a hard denial, never an idempotent success, and it is recorded as a denied bypass
  attempt.
- **Degradation abstains.** Losing the mesh or the local models prevents new
  recommendations; it never disables telemetry, operator visibility, replay, or the
  approval boundary, and recorded evidence is never substituted into a live run.

See [`docs/SAFETY.md`](docs/SAFETY.md), the
[threat model](docs/security/threat-model.md), and the enumerated
[approval-bypass catalogue](docs/security/approval-bypass-catalogue.md).

## Documentation

Every normative fact has exactly one home, and the decision log governs where they
disagree.

| You need | Read |
| --- | --- |
| Why the demo centers Solace, and what the audience must see | [`docs/SOLACE_VALUE.md`](docs/SOLACE_VALUE.md) |
| Why a decision was made, and whether it still stands | [`docs/adr/`](docs/adr/README.md) |
| Delivery sequence, milestones, release criteria | [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) |
| Component responsibilities and operating modes | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Event envelope, topics, HTTP API, delivery semantics | [`docs/CONTRACTS.md`](docs/CONTRACTS.md) |
| Safety invariants and the approval protocol | [`docs/SAFETY.md`](docs/SAFETY.md) |
| Test classes, coverage tiers, toolchain | [`docs/TESTING.md`](docs/TESTING.md) |
| Any number, and the instrument that measures it | [`docs/operating-parameters.md`](docs/operating-parameters.md) |
| What is and is not modelled | [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) |
| How to work in this repository | [`AGENTS.md`](AGENTS.md), [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Risks this project has measured and accepted | [`TECH_DEBT.md`](TECH_DEBT.md) |

## Getting started

The mission-control recipe extends the existing local runtime, but it is not yet an accepted operational
demonstration: the clean committed production browser rerun, evidence, diagrams, and final gates remain.
Run the verification suite before treating a local start as evidence.
[`CONTRIBUTING.md`](CONTRIBUTING.md) lists both the verification commands and the shared-project
mission-control recipe.

Prerequisites: `uv` 0.12.5, Python 3.14.7, `pre-commit` 4.5, Graphviz, and `shellcheck`.
Agent Mesh work additionally needs Python 3.13.15. Exact versions and the rationale are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

```sh
pre-commit install --install-hooks   # six git stages, not just pre-commit
just check-commit                    # the fast tier
just check-push                      # the thorough tier
```

`just` is a convenience wrapper; the hooks and CI invoke the scripts under `scripts/`
directly, so nothing breaks without it.

## How this repository is verified

Verification is part of the artifact. Linting and type checking have no escape hatches —
there is no `# noqa`, no `# type: ignore`, and no suppression pragma anywhere in the owned
tree, and adding one requires a recorded decision. Every project-owned executable test, in
every language and test class, must carry exactly one ordered Arrange-Act-Assert sequence,
enforced by a checker that scans the whole tree with no per-test waiver. Coverage is tiered
by risk and measured per package rather than as one total, so a well-tested module cannot
mask an untested one. The safety-critical core additionally carries property-based,
failure-injection, and mutation obligations.

Gates fail closed: a component with source but no manifest, a missing tool, or an absent
report is an error, never a skip. Git hooks give fast feedback and CI re-runs the identical
configuration, so CI is the authority. A daily security workflow re-audits both dependency locks, the
deploy configuration, and every container image against the same expiring-waiver registry, so an
advisory published against an unchanged tree surfaces the next morning rather than at the next push.

## License

[Apache License 2.0](LICENSE). Third-party attribution is in [`NOTICE`](NOTICE).
