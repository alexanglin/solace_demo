# Agent Mesh Configuration Instructions

## 1. Scope and authority

These instructions apply to every file under `agent-mesh/configs/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`agent-mesh/AGENTS.md`](../AGENTS.md) first. The parent
owns the isolated Python 3.13 environment, broad configuration policy, dependency coordination, TDD,
and repository-wide verification; this guide adds the configuration composition, runtime, and proof
boundaries that are specific to these executable YAML files.

Read the canonical owner before changing a represented concern:

| Concern | Authority or reference |
| --- | --- |
| Offline semantic validation and fail-closed refusals | [ADR-0032](../../docs/adr/0032-agent-mesh-semantic-configuration-validator.md), [ADR-0035](../../docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md), and [`tools/AGENTS.md`](../tools/AGENTS.md) |
| Official-image runtime, bind mount, healthcheck, and exposure | [ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), [`deploy/AGENTS.md`](../../deploy/AGENTS.md), and [`CONTRIBUTING.md`](../../CONTRIBUTING.md) |
| Proposal-only agents and deterministic actuation | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) and [`SAFETY.md`](../../docs/SAFETY.md) |
| Broker identities, grants, and A2A namespace | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) and [ADR-0064](../../docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md) |
| Model providers, budget, local identity, and digest evidence | [ADR-0002](../../docs/adr/0002-paid-orchestration-under-enforced-budget-cap.md), [ADR-0063](../../docs/adr/0063-lock-local-models-by-manifest-digest.md), and [`model-lock.toml`](../model-lock.toml) |
| Web UI schema and loopback compensating control | [ADR-0065](../../docs/adr/0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md) and [`TECH_DEBT.md`](../../TECH_DEBT.md) |
| Event Mesh request/reply and closed operation set | [ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md), [ADR-0069](../../docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md), and [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) |
| Event Mesh settlement and delivery limits | [ADR-0071](../../docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md), [ADR-0079](../../docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md), and [`CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Milestone scope, runtime design, and live proof boundaries | [`IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md), [`ARCHITECTURE.md`](../../docs/ARCHITECTURE.md), and [`tests/phase0/AGENTS.md`](../../tests/phase0/AGENTS.md) |

An Accepted ADR governs if configuration comments, tests, historical evidence, or this guide disagree
with it. Do not turn an old measured count or Phase 0 observation into a current invariant without a
current instrument and canonical owner.

## 2. Five files, five responsibilities

The connector loads and merges all owned YAML. Each current file contains one app, except that the
Orchestrator file also owns the process-level management server and the Mission Coordinator embeds the
Event Mesh Tool.

| File | Local responsibility |
| --- | --- |
| `orchestrator.yaml` | The sole management-server declaration, the Orchestrator card and locked local model, and the only current agent allowed to delegate to `MissionCoordinator` |
| `mission-coordinator.yaml` | The proposal-only sector agent, its locked local model and card, deny-by-default outbound A2A posture, and the embedded read-only Event Mesh Tool |
| `mission-response-workflow.yaml` | The minimal versioned, typed Phase 0 workflow that invokes `MissionCoordinator`; it is not the complete later-phase mission sequence |
| `web-ui.yaml` | The local engineering HTTP/SSE surface, session-secret indirection, shared artifacts, and explicit loopback-only browser origins; it does not add the Platform service |
| `event-mesh-gateway.yaml` | Salient-event ingress, structured A2A invocation, deferred rejection, mission-context forwarding, and non-authoritative success and failure outputs |

A rename, split, merge, new app, new card, new tool, or new workflow changes runtime composition. Update
the real-checkout inventory assertions, deployment wiring, canonical architecture, and the relevant
live proof rather than treating it as a file-only edit.

## 3. Preserve the merged configuration

- Validate an edited file by itself for diagnosis, but always finish with the no-argument validator.
  Every file must be valid individually, and a multi-file selection must also merge successfully using
  the pinned Solace AI Connector primitive. App names therefore remain nonblank and globally unique.
- Keep exactly one process-level `management_server` declaration. The current conformance test fixes it
  to `orchestrator.yaml`, and the Compose healthcheck depends on its readiness endpoint. Connector merge
  semantics replace non-list values, so a second declaration is not an independent second server.
- Keep `${NAMESPACE}` consistent across every app. ADR-0064 and the committed `.env.example` own its
  fixed value; pass that exact value explicitly to broker provisioning. An offline configuration pass
  does not prove that the running container and broker use the same namespace.
- All current apps use the same filesystem artifact location because structured cross-app invocation
  cannot exchange per-app memory artifacts. Preserve agreement across the complete set. The recorded
  live run established this for its pinned revision and runtime, not for the current checkout or another
  plugin version or storage service.
- Preserve the intentional broker-identity split. The Orchestrator, coordinator, workflow, and Web UI
  use the Agent Mesh role. The Event Mesh Gateway uses its own role for both broker blocks, and the
  embedded Event Mesh Tool opens its own request/reply session with the tool role. A shared connector
  process is not permission to collapse those identities.
- Keep gateway card publication disabled unless the architecture and live card-set oracle are changed
  deliberately. The gateway is reached through its application topic and is not an agent card.

Do not factor these agreements into an include merely to remove repetition. An include introduces a
new containment, merge, review, and runtime-loading boundary; use it only when there is a demonstrated
second consumer and cover the composed result.

## 4. Keep policy separate from enforcement

Prompts, descriptions, defaults, structured-input hints, and `default_user_identity` guide model and
plugin behavior. A2A allow and deny lists enforce which agents may delegate within the upstream runtime.
Neither class is the approval or actuation boundary. Agents only propose; the deterministic command
gateway and broker authorization enforce what may be published. Never configure an agent or tool to
publish an executable command, manufacture an operator identity, approve a proposal, or claim that an
action occurred.

- Keep all broker credentials and session secrets as whole environment references declared with
  secret-safe values in [`.env.example`](../../.env.example), using placeholders for secret-bearing
  names, and passed into the container by [`deploy/compose.yaml`](../../deploy/compose.yaml). Never add
  literal secret values, URL userinfo, fallback credentials, or copied generated secrets.
- Preserve encrypted broker transport and `dev_mode: false`. The Ollama host bridge is a local model
  boundary, not precedent for exposing Ollama or weakening broker TLS.
- The Event Mesh Gateway subscribes only to the salient drone-event family. Routine telemetry must not
  enter a model. It must retain explicit completion-time settlement with rejected negative
  acknowledgement, exactly one target per handler, and references to declared output handlers.
- Gateway output remains non-authoritative agent-response traffic. It is not an agent-proposal event, a
  command, an approval, an authoritative event record, or proof that its triggering input was durably
  retained.
- The Event Mesh Tool stays the exact pinned Python symbol, uses JSON request/reply, publishes only to
  the schema-bound gateway-request topic, and receives on the reserved reply prefix. Its operation
  default is model-overridable; the deterministic gateway's closed operation table, not the YAML
  default, refuses an unknown operation.
- The Web UI's internal `0.0.0.0` listener has its host exposure bounded only by Compose's independent
  loopback publication. That is a compensating control, not proof that the upstream surface is safe.
  Keep CORS origins explicit and loopback-only. Configuration validation of CORS does not prove host
  exposure, authentication, or absence of other reachable surfaces.

Subject data remains anonymous. Do not add facial recognition, biometric identity, targeting,
weaponization, or autonomous use-of-force instructions, tools, fields, examples, or capabilities.

## 5. Models, prompts, and numeric settings

- A local Agent model uses a literal canonical identifier paired with `${OLLAMA_HOST}`. A paid-provider
  entry must use the exact evaluated identifier without a local `api_base` and follow the governing
  budget and model decisions. Never introduce `model_provider`, a floating `latest` tag, or an
  environment-indirected model name merely to make a provider easy to swap.
- A local model addition or change also updates `model-lock.toml` from a measured Ollama manifest
  digest. The offline validator proves lock form and membership only. Readiness is required to compare
  the lock with the running daemon, but that comparison remains open technical debt; do not report it as
  an existing control. An authorized model probe must establish behavior.
- A prompt or tool description is production behavior and untrusted-model context. Preserve the
  proposal-only, anonymous-subject, and no-false-actuation language. A shape-valid prompt is not proof
  of safe, useful, deterministic, or correctly structured output.
- Do not tune a timeout, expiry, publication interval, port, or model parameter only in YAML. Find its
  instrument and canonical entry in [`operating-parameters.md`](../../docs/operating-parameters.md),
  or add the missing decision and measurement plan before changing the value. Update every consumer
  and test that holds the same boundary.

## 6. Know what the validator proves

Generic `check-yaml` deliberately excludes this directory because it does not understand the connector
dialect. Use `tools.agent_mesh_config_validator`; do not substitute a generic loader or a successful
editor parse.

The semantic validator runs offline against `.env.example`, `model-lock.toml`, and the exact pinned
Python 3.13 distributions. It covers include containment, environment-reference hygiene, broker
transport, supported app modules, upstream agent/workflow/gateway/Web UI shapes, model form and lock
membership, Web UI origins, gateway settlement and route structure, and the Event Mesh Tool's symbol,
parameters, request topic, and reply prefix. It starts no connector, socket, broker client, model, or
container.

A green result does not establish:

- actual secret expansion, container environment agreement, broker role grants, namespace provisioning,
  TLS connectivity, or running-process startup;
- model digest equality, Ollama readiness, prompt behavior, tool choice, A2A delegation, card discovery,
  or workflow output;
- application-schema payload validity, input-to-output causation, settlement or redelivery on the wire,
  or durability while the gateway's upstream-created temporary queue is disconnected;
- shared-artifact behavior, host-loopback exposure, composite readiness, or compatibility inside the
  derived runtime image.

The validator intentionally has narrower checks than several architectural statements. In particular,
it rejects `model_provider` only at the top level of agent, workflow, and Web UI `app_config`; it does
not search nested structures or apply that rule to the Event Mesh Gateway arm. It also does not prove
the fixed namespace value or broker ACLs, or enforce the versioned application namespace on every
gateway subscription and output expression. Do not broaden a claim to fill those gaps. Close a gap
through the approved TDD and ADR workflow, or report it as unverified.

## 7. Coordinate cross-tree changes

| Configuration change | Reconcile at minimum |
| --- | --- |
| Environment reference or secret wiring | `.env.example`, Compose injection, semantic-validator and deployment-wiring tests, and the canonical owner of the represented value |
| Broker identity, grant, or namespace | `.env.example`, Compose injection, broker desired state and authorization tests, deployment runbook, and the governing ADRs |
| Topic, payload, RPC parameter, reply prefix, or output family | `packages/contracts`, schemas and fixtures where applicable, `CONTRACTS.md`, producers and consumers, root-side agreement tests, and a new or superseding ADR for a changed contract |
| Agent card, delegation edge, workflow node, or structured mapping | Agent Mesh validator tests, architecture and milestone documentation, and the exact authorized card/delegation/workflow live probe |
| Gateway handler, target, settlement, or delivery behavior | Gateway schema/policy tests, contract and delivery documentation, broker grants, and gateway live evidence with its temporary-queue limit stated |
| Event Mesh Tool symbol, operation, parameter, or request/reply behavior | Agent Mesh tool-policy tests, command-gateway tables and tests, broker grants, RPC contracts, and tool live evidence |
| Model, provider, or local endpoint | `model-lock.toml` for local models, environment and readiness wiring, validator tests, operating parameters, evaluation evidence, and the governing model decision |
| App or plugin module/version | `pyproject.toml`, `uv.lock`, native compatibility tests, derived image and hash-locked plugin requirements, image probe, debt records, and governing ADR |
| Management server, Web UI, or artifact service | Compose healthcheck, port or storage projection, validator and deployment-policy tests, architecture, and authorized runtime evidence |

Python 3.13 Agent Mesh tooling cannot import the root Python 3.14 contracts. Cross-runtime copies of
topic or policy values may therefore be intentional; hold them equal with static agreement tests or a
neutral committed artifact rather than a forbidden cross-domain import.

## 8. Runtime and evidence hygiene

Configuration files are mounted read-only into the container, but the running connector does not hot
reload them. A healthy `up --wait` can still be the old process with the old configuration. Follow the
current runbook and explicitly recreate the Agent Mesh service after a configuration edit. Ensure the
current desired broker state, including the reserved reply grant, is provisioned before recreation even
on a fresh stack; re-provision after any authorization-matrix change so startup does not test stale
state.

Starting or recreating containers, generating or rotating secrets, provisioning the broker, contacting
Ollama, invoking a model, and running live Phase 0 probes each require explicit authorization. Select
one exact live test file and follow [`tests/phase0/AGENTS.md`](../../tests/phase0/AGENTS.md); do not infer
permission from a YAML edit or combine live files under a broad marker.

An HTTP response or management-server readiness response is not composite Agent Mesh readiness. A live
gateway observation does not prove durable delivery, exactly-once processing, payload correlation, or
model quality. Historical files under `release-evidence/` describe dated runs and are not current setup
scripts; never rewrite them to make a new result appear established.

## 9. Required verification

Work from `agent-mesh/` in the isolated environment. During iteration, pass the edited file explicitly;
replace `EDITED_FILE.yaml` below with its actual basename. Before handoff, omit the path so every owned
file is validated individually and as a merged set:

```sh
uv sync --frozen
uv run --frozen python -m tools.agent_mesh_config_validator configs/EDITED_FILE.yaml
uv run --frozen python -m tools.agent_mesh_config_validator
uv run --frozen pytest -q -m \
  "not broker and not ollama and not paid and not docker and not net" \
  tests/test_config_validator.py
```

For a configuration behavior change, return to the repository root and run the cross-runtime agreement
tests, then run the complete wrapper list in the parent guide's required-verification section:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/deploy/test_agent_mesh_gateway_config.py \
  tools/quality_gate_tests/deploy/test_agent_mesh_tool_config.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
```

For a guide-only change, pass the new files explicitly to file-based hooks because Git diff discovery
does not see untracked paths:

```sh
pre-commit run --files agent-mesh/AGENTS.md \
  agent-mesh/configs/AGENTS.md agent-mesh/configs/CLAUDE.md \
  --hook-stage pre-commit
readlink agent-mesh/configs/CLAUDE.md
git diff --no-index --check /dev/null agent-mesh/configs/AGENTS.md
```

`readlink` must print `AGENTS.md`. The no-index command normally exits `1` for a new file because content
differs; no diagnostic output means its whitespace is clean. Finish with the complete repository
pre-commit and pre-push stages required by the root guide. Run image, container, broker, Ollama, model,
network, or paid checks only when the changed claim requires them and the user explicitly authorizes
that resource boundary; report every excluded check.
