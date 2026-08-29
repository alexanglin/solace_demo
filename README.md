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

**No complete end-to-end operational qualification has run yet.** The continuously running application
data plane is implemented and Compose-wired: typed Solace sessions connect the fleet, command gateway,
evidence service, dashboard API, and recorder; PostgreSQL owns their transactional inboxes, outboxes,
approvals, receipts, projections, and audit records. Deterministic unit, contract, integration-boundary,
and browser-fixture gates are green. The remaining release boundary is the authorized shared-stack run:
apply every additive migration, exercise the exact approval-to-command path, restart PubSub+ once, prove
rebind and outbox drain, and complete fleet-scale, latency, soak, and packaged-browser evidence. Until
that evidence exists, this repository does not claim an end-to-end operational demonstration. The
earlier fixed dashboard slice remains accepted on committed revision `db2b640`—64 fixture cases, eight
production cases, and a 61-sample bounded soak—but that evidence predates this Solace data-plane graph
and is not being presented as qualification of the merged runtime
([dashboard evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

| Area                                                            | State                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Foundation, toolchain, and quality gates                        | Complete                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Contracts: canonical serialization and application wire surface | **Implemented and enforced at runtime ingress.** Fifteen unique topic families comprise twelve notification-only families, two request/reply families, and the direct `AGENT_RESPONSE` integration-body family. ADR-0150 separates the private raw `GATEWAY_RESPONSE` reply from the direct mission-scoped `GATEWAY_RECORD` CloudEvent. The version-one manifest owns 66 schemas, including 23 dashboard schemas; typed delivery routing derives Direct, Guaranteed, or request/reply behavior from the parsed family and refuses mismatches before broker I/O ([ADR-0146](docs/adr/0146-define-durable-application-processing.md), [ADR-0148](docs/adr/0148-close-the-application-data-plane-wire-documents.md), [ADR-0150](docs/adr/0150-separate-gateway-records-from-private-replies.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Agent Mesh configuration validation                             | Complete as an offline gate, in [`agent-mesh/tools`](agent-mesh/tools); armed: five configurations under `agent-mesh/configs/`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Docker Compose stack                                            | Defined in [`deploy/`](deploy) — the PubSub+ broker container, Postgres, Agent Mesh from its official image, the application services, and the Event Portal discovery agent — pinned by digest and held to a policy gate on every commit; the Agent Mesh starts with the default profile ([ADR-0102](docs/adr/0102-start-the-agent-mesh-with-the-default-profile.md)). The broker and Postgres first live run is recorded in [`release-evidence/phase-0/first-live-run.md`](release-evidence/phase-0/first-live-run.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| Broker authorization                                            | Nine least-privilege client usernames on deny-by-default ACL profiles, **applied to the running container**; approval-bypass cases B17, B18, and B19 pass against it ([`release-evidence/phase-0/broker-authorization.md`](release-evidence/phase-0/broker-authorization.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Live Ollama messaging and the Agent Mesh runtime                | **Running, and started by default.** The stack carries the Orchestrator, a specialised agent, a versioned workflow, the HTTP/SSE Web UI, and the Event Mesh Gateway on a digest-locked local model; agent-card discovery, structured workflow invocation, and one A2A delegation from a workflow node to its peer are asserted against the running broker. The model-chosen delegation is a recorded observation, not a repeatable test ([`release-evidence/phase-0/mesh-first-run.md`](release-evidence/phase-0/mesh-first-run.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Application events and official Event Mesh plugins              | **Live slices recorded.** Fleet telemetry crosses the broker; one salient CloudEvent becomes a structured A2A task; one Event Mesh Tool request receives a validated, non-actuating command-gateway reply ([`fleet-simulator-first-run.md`](release-evidence/phase-3/fleet-simulator-first-run.md), [`event-mesh-gateway-first-run.md`](release-evidence/phase-0/event-mesh-gateway-first-run.md), [`event-mesh-tool-first-run.md`](release-evidence/phase-0/event-mesh-tool-first-run.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Guaranteed delivery and drone command consumption               | **Live slices recorded.** Durable queues, settlement, bounded redelivery, dead-message handling, backlog drain, and drone-side command consumption are exercised against the local broker; an actual broken-session reconnect and gateway-side persistent dispatch remain unproved ([`guaranteed-delivery-first-run.md`](release-evidence/phase-2/guaranteed-delivery-first-run.md), [`backlog-recovery-first-run.md`](release-evidence/phase-2/backlog-recovery-first-run.md), [`command-dispatch-first-run.md`](release-evidence/phase-3/command-dispatch-first-run.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Durable mission store                                           | **Implemented behind complete Alembic and SQLAlchemy boundaries; merged live upgrade qualification remains.** Ten append-only Alembic revisions own 26 application tables and their constraints, and async SQLAlchemy 2.x Core repositories and service-specific units of work own runtime access. Deterministic tests cover revision-by-revision upgrade/downgrade, transactions, races, conflict digests, bounds, recovery, and ordered export. The five-revision dashboard store has live evidence; revisions six through ten still require the authorized created-and-dropped probe database and shared-stack forward migration before release ([ADR-0003](docs/adr/0003-postgres-durable-mission-store.md), [ADR-0151](docs/adr/0151-require-migrated-sqlalchemy-durable-tables.md), [ADR-0189](docs/adr/0189-reconcile-dashboard-runtime-with-the-solace-data-plane.md))                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Mission dashboard                                               | **The fixed dashboard slice is accepted; the Solace-wired extension awaits packaged live requalification.** The FastAPI service runs behind hardened Caddy over a private Unix socket, enforces exact Host/Origin/bearer and bounded-body rules, consumes validated broker events, recovers persisted projection state, and exposes bounded SSE plus idempotent command and proposal-decision mutations. The React runtime preserves the accepted map, mission control, replay, and recovery behavior while adding accessible evidence-bound decisions. On 2026-08-28 the merged stack composed to a healthy state on a fresh broker volume and an operator Start moved 280 telemetry publications through the broker, which delivered them to the recorder and to the dashboard API's session; the deployed dashboard composition folded none of them and cannot restart against them ([merged-runtime evidence](release-evidence/phase-3/merged-runtime-second-composition.md), finding 6), so the dashboard's live projection, the packaged-browser rehearsal, reconnect, and complete application-data-plane acceptance in containers are pending ([FRONTEND_BUILD.md](docs/FRONTEND_BUILD.md), [dashboard evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| Supply-chain and workflow scanning                              | Locked-dependency audit on every push; Trivy over `deploy/` at pre-push and over every stack image daily in continuous integration; zizmor on every workflow change; CodeQL for Python; Dependabot for every ecosystem the repository has                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Solace Cloud                                                    | A non-gating showcase profile for the Cloud console; it has not been exercised, and no gate depends on it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| End-to-end mission experience                                   | **Assembled in production code, not yet operationally qualified.** The pending live gate must prove the complete broker/store/approval/effect/audit chain, one PubSub+ restart and recovery, fleet scale, latency, soak, and packaged-browser behavior before the status can become operational                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

Sequenced delivery and exit criteria are in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md); committed live observations are scoped in
[`release-evidence/`](release-evidence/).

## The mission scenario

A person is missing in a wilderness area. An operator opens the dashboard, submits a last
known position, a search polygon, a weather summary, and a time since last contact.

1. The Event Mesh Gateway validates the request and starts a versioned Mission Response
   workflow over A2A topics on the Solace broker.
2. The workflow invokes a Mission Coordinator and discovered specialized agents with typed
   inputs and outputs. Agents **propose**; a deterministic command gateway outside model
   control is the only component that publishes an executable command.
3. A fleet of 23 drones begins reporting telemetry — three model-backed edge agents on
   local Ollama, and 20 deterministic simulations that exist to validate fleet-scale
   behaviour. Routine telemetry never passes through a language model.
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

| You need                                                    | Read                                                           |
| ----------------------------------------------------------- | -------------------------------------------------------------- |
| Why the demo centers Solace, and what the audience must see | [`docs/SOLACE_VALUE.md`](docs/SOLACE_VALUE.md)                 |
| Why a decision was made, and whether it still stands        | [`docs/adr/`](docs/adr/README.md)                              |
| Delivery sequence, milestones, release criteria             | [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)   |
| Component responsibilities and operating modes              | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)                 |
| Event envelope, topics, HTTP API, delivery semantics        | [`docs/CONTRACTS.md`](docs/CONTRACTS.md)                       |
| Safety invariants and the approval protocol                 | [`docs/SAFETY.md`](docs/SAFETY.md)                             |
| Test classes, coverage tiers, toolchain                     | [`docs/TESTING.md`](docs/TESTING.md)                           |
| Any number, and the instrument that measures it             | [`docs/operating-parameters.md`](docs/operating-parameters.md) |
| What is and is not modelled                                 | [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md)                   |
| How to work in this repository                              | [`AGENTS.md`](AGENTS.md), [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Risks this project has measured and accepted                | [`TECH_DEBT.md`](TECH_DEBT.md)                                 |

## Getting started

There is no operational application to start yet. What you can do today is run the verification
suite. The Agent Mesh semantic-configuration gate is part of it, and
[`CONTRIBUTING.md`](CONTRIBUTING.md) shows how to run that gate on its own.

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
