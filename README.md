# Aerial Rescue Mesh

A public, production-quality reference implementation of the open-source
[Solace Agent Mesh](https://github.com/SolaceLabs/solace-agent-mesh) coordinating
independently deployed edge intelligence for wilderness search and rescue.

The question this project exists to answer is narrow and testable: **can an event-driven
agent mesh coordinate independently deployed models, with different capabilities and
unreliable connectivity, safely enough that a human still authorizes every consequential
action?**

## Status

**No operational demonstration runs yet.** This is a repository under construction. The
Agent Mesh semantic-configuration gate runs as verification tooling, but it starts no Agent
Mesh process, broker client, application, or model.

| Area | State |
| --- | --- |
| Foundation, toolchain, and quality gates | Complete |
| Contracts: canonical serialization and the proposal digest | Complete, in [`packages/contracts`](packages/contracts) |
| Agent Mesh configuration validation | Complete as an offline gate, in [`agent-mesh/tools`](agent-mesh/tools); inert until the first owned configuration |
| Docker Compose stack | Defined in [`deploy/`](deploy) — the PubSub+ broker container, Postgres, Agent Mesh from its official image, the application services, and the Event Portal discovery agent — pinned by digest and held to a policy gate on every commit; the default profile's **first live run is recorded** in [`release-evidence/phase-0/first-live-run.md`](release-evidence/phase-0/first-live-run.md) |
| Broker authorization | Nine least-privilege client usernames on deny-by-default ACL profiles, **applied to the running container**; approval-bypass cases B17, B18, and B19 pass against it ([`release-evidence/phase-0/broker-authorization.md`](release-evidence/phase-0/broker-authorization.md)) |
| Live Ollama messaging and the Agent Mesh runtime | Next Phase 0 evidence: the `mesh` profile; not yet demonstrated |
| Supply-chain and workflow scanning | Locked-dependency audit on every push; Trivy over `deploy/` at pre-push and over every stack image daily in continuous integration; zizmor on every workflow change; CodeQL for Python; Dependabot for every ecosystem the repository has |
| Solace Cloud | A non-gating showcase profile for the Cloud console; no gate depends on it |
| Everything else | Typed package manifests with no runtime behaviour |

One application module contains production code today, and one verification module checks
Agent Mesh configuration without running it. Every other package under `packages/` and
`services/` is a manifest and an empty namespace, deliberately: the verification machinery
was built first so that no behaviour could land untested. Sequenced delivery and exit
criteria are in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

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

| You need | Read |
| --- | --- |
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
