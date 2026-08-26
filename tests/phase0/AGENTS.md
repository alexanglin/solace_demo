# Phase 0 Test Instructions

## 1. Scope and authority

These instructions apply to every file under `tests/phase0/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`tests/AGENTS.md`](../AGENTS.md) first. The parent owns
the shared TDD, AAA, marker, live-resource authorization, TLS, secret-hygiene, and test-placement rules;
this guide adds the exact proof boundaries and operating hazards of the Phase 0 probes.

Read the owner of each system a probe touches before changing that test or its subject:

| Concern | Authority or reference |
| --- | --- |
| Test classes, marker policy, and claim ceilings | [`TESTING.md`](../../docs/TESTING.md) and [`LIMITATIONS.md`](../../docs/LIMITATIONS.md) |
| Phase 0 criteria and current delivery status | [`IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) |
| Runtime layout and acceptance-evidence boundaries | [`ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Event, topic, delivery, and request/reply semantics | [`CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety and approval boundaries | [`SAFETY.md`](../../docs/SAFETY.md) and [`docs/security/`](../../docs/security/threat-model.md) |
| Local setup, provisioning, recreation, and recovery | [`CONTRIBUTING.md`](../../CONTRIBUTING.md) and [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Agent Mesh configuration and runtime separation | [`agent-mesh/AGENTS.md`](../../agent-mesh/AGENTS.md) |
| Command-gateway behavior and host-only live evidence | [`services/command_gateway/AGENTS.md`](../../services/command_gateway/AGENTS.md) |
| Broker and contract adapter boundaries | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) and [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Dated observations and redaction | [`release-evidence/AGENTS.md`](../../release-evidence/AGENTS.md) |

The governing decisions include the split runtimes and untyped client boundary
([ADR-0004](../../docs/adr/0004-split-python-runtimes.md),
[ADR-0028](../../docs/adr/0028-untyped-solace-client-boundary.md)); the local broker, official Agent
Mesh image, and generated authority ([ADR-0043](../../docs/adr/0043-docker-broker-with-solace-cloud-showcase.md),
[ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md),
[ADR-0046](../../docs/adr/0046-generated-local-certificate-authority.md)); least-privilege roles and
the A2A namespace ([ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md),
[ADR-0064](../../docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md)); and the Event Mesh request/reply,
reply-channel, temporary-ingress, and delivery boundaries
([ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md),
[ADR-0069](../../docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md),
[ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md),
[ADR-0071](../../docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md),
[ADR-0079](../../docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md), and
[ADR-0080](../../docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)). An Accepted
ADR governs if a test, old evidence record, implementation, or this guide disagrees with it.

## 2. Five different proof boundaries

All five modules execute in the root Python 3.14 environment. The Agent Mesh container under test has
its own Python 3.13 runtime; never execute this directory from `agent-mesh/.venv`.

| File | Current markers | Bounded responsibility |
| --- | --- | --- |
| `test_solace_messaging_runtime.py` | `phase0`, `compatibility` | Offline evidence that the pinned root-environment Solace distribution loads its bundled native library and marshals selected service and message surfaces |
| `test_first_live_stack.py` | `phase0`, `docker`, `broker` | Live hostname-validated TLS handshakes to the two tested loopback endpoints and TCP acceptance on the tested PostgreSQL endpoint |
| `test_agent_mesh_live.py` | `phase0`, `docker`, `broker`, `ollama` | Live Web UI card-set, A2A discovery-topic, and workflow-to-agent-request-topic observations |
| `test_event_mesh_gateway_live.py` | `phase0`, `docker`, `broker`, `ollama` | Live salient-event publication through the Event Mesh Gateway, one observed A2A request topic, one observed application agent-response topic, and finite-window malformed-input silence |
| `test_event_mesh_tool_live.py` | `phase0`, `docker`, `broker`, `ollama` | Live reserved-channel request/reply, closed-operation refusal, model-to-tool request observation, post-reply command-topic silence, and a forbidden response-topic receiver construction that raises a vendor client error |

`phase0` and `compatibility` classify evidence; they do not exclude a test from blocking stages. The
resource markers do. The final three files carry `ollama` at module scope even though some individual
methods are model-independent, so an exact-file run has the whole file's prerequisite and authorization
set. Do not remove a conservative marker to make one method easier to select.

## 3. Safe selection stays offline

The only resource-free module here is `test_solace_messaging_runtime.py`. A safe directory-wide command
must retain the root wrapper's complete resource exclusion:

```sh
uv run --frozen pytest -q \
  -m "not broker and not ollama and not paid and not docker and not net" \
  tests/phase0
```

Never use bare `pytest tests/phase0`, `pytest tests`, or `-m phase0` as a convenience command. Those
forms select live tests that can connect, publish persistent messages, create temporary endpoints,
invoke a model, or depend on containers and a host process.

Pytest applies resource deselection after import and collection. Keep module imports, marker evaluation,
and case collection offline and deterministic: do not read generated credentials or certificates, open
a socket, inspect a daemon, start a process, or mutate state during import or collection, or outside the
execution of an explicitly selected and authorized live test.

## 4. Authorize and prepare one live file

Authorization must name one exact live file and the intended local environment. It does not implicitly
authorize generating or rotating secrets, starting or recreating containers, applying broker state,
starting the host command gateway, invoking a model, stopping services, deleting queues or volumes,
capturing evidence, or cleaning persistent messages. Follow the current runbook and request any of
those actions separately.

The current prerequisite boundaries are deliberately not interchangeable:

- `test_first_live_stack.py` needs the generated checkout-local authority and a healthy default stack.
  Its sockets do not require namespace or ACL provisioning, and it does not authenticate to PostgreSQL.
- `test_agent_mesh_live.py` additionally needs the provisioned A2A namespace and role matrix, a healthy
  default profile serving the committed configurations, and host Ollama serving the locked model. Recreate
  the Agent Mesh container after a bind-mounted configuration change; `up --wait` alone can keep the old
  process and old configuration.
- `test_event_mesh_gateway_live.py` needs the same live Agent Mesh boundary with the gateway app loaded,
  plus the provisioned identities used to publish the event and observe the A2A and response families.
  Its file-level `ollama` marker covers the response case even though event-to-request transformation is
  model-independent.
- `test_event_mesh_tool_live.py` needs provisioning **before** the Agent Mesh container is recreated so
  the reserved reply-channel ACL is present when the tool binds. It also needs the tool configuration,
  host Ollama for the model case, and the root-environment command gateway running on the host under its
  own identity. The `command-gateway` Compose service still imports and exits; it is not this live
  prerequisite and the test is not evidence about the `services` profile.

Historical records under `release-evidence/` document earlier runs; they are not current setup scripts.
Use `CONTRIBUTING.md` and the component guides for the supported sequence, then verify the actual health
and configuration of the explicitly authorized environment.

## 5. Isolate live messaging and state

Run the live messaging modules serially, without xdist, against a dedicated and quiescent authorized
local stack. Each module reuses some fixed identifiers across runs, and family subscriptions can observe
unrelated traffic. Several observers accept the first message on a family or exact topic, while several
negative cases infer from a finite silence window; concurrent traffic can therefore create either a
false positive or a false failure.

- Start a direct-message observer before the action it must see. A direct message published first is
  gone rather than waiting for a later subscription.
- For new cases, decode the observed payload and correlate explicit request, event, mission, and agent
  identifiers. Receiving one matching topic is not sufficient evidence that the triggering input caused
  it, and breaking after the first message cannot prove that exactly one was produced.
- Pair every authorization negative with a permitted positive control through the same identity and
  transport. Catch only the typed failure the boundary promises; a general outage is not an ACL denial.
- Bound every connect, acknowledgement, receive, HTTP, and model wait. Use monotonic deadlines for
  elapsed-time or silence claims, and close services, receivers, publishers, and HTTP connections in a
  `finally` block even when construction or startup fails.
- Preserve certificate-chain and hostname validation. Never retry with plaintext, disable verification,
  broaden an identity, or trust arbitrary certificate material to make a probe pass.

These probes do not own cleanup. The gateway file publishes acknowledged persistent `DRONE_EVENT`
messages; any provisioned durable consumer queue with a matching subscription can retain its own copy,
and this suite does not drain those queues. The live files can also leave transient A2A tasks, connector
artifacts, model work, application messages, CloudEvent records, and temporary gateway or reply queues.
Do not run them against shared state or claim the broker was restored merely because pytest exited.

Some current helpers construct or start a resource before entering their cleanup `try` block. Treat
complete cleanup on those failure paths as unproven and do not copy the pattern. Close the gap through
the approved TDD workflow before extending the helper.

## 6. Keep each claim below its oracle

| Green file | What the assertions establish | What they do not establish |
| --- | --- | --- |
| Solace runtime | The installed root distribution version, bundled native-library load, disconnected service construction, selected info reads, and one message-payload round trip on Python 3.14 | Broker connection, TLS, callback delivery, acknowledgement, reconnect, container compatibility, or any Agent Mesh runtime behavior |
| First live stack | Successful validated handshakes to the tested SMF and SEMP endpoints and one TCP connection to the tested Postgres port | Absence of non-loopback exposure, container or image health, broker protocol behavior, database authentication, schema, transaction, or durability |
| Agent Mesh | The exact card-name set returned by the current helper, at least one observed discovery card whose name belongs to that set, and an observed request on the MissionCoordinator topic after workflow submission | HTTP status or media-type conformance, every card publishing, payload correlation, an Ollama invocation or completion, final workflow output, model quality, structured output, or plugin settlement |
| Event Mesh Gateway | Contract-built events were acknowledged by the publisher, one matching A2A request topic and one matching application agent-response topic were observed in separate cases, and malformed bytes produced no observed request during the bounded window | Exactly one task, causation or payload correctness, answer quality, settlement, redelivery, permanent refusal, or delivery while the temporary gateway queue is disconnected |
| Event Mesh Tool | The received replies satisfy the asserted RPC fields, one unknown operation is refused by name, one gateway-request-family topic is observed after a model prompt, and receiver construction on one forbidden response topic raises a vendor client error | Reply timeout or malformed-reply behavior, concurrency, restart, durable delivery, correlation or decoding of the model-triggered request, the precise vendor denial reason, or publication of the separate CloudEvent record |

The non-actuation test starts its command-family observer only after `_ask()` has returned. Its decoded
reply establishes `actuated: false`, and its later silence window establishes that no command was
observed then; it cannot exclude a transient command published during request handling. Do not repeat a
stronger “no command while answered” claim without changing the observation boundary first.

A green local result never establishes Solace Cloud parity, fleet scale, performance, security against
every role or topic, or a current release claim. A silence window proves only non-observation during that
window, and a temporary queue is not durable merely because one message traversed it.

## 7. Construct new Phase 0 evidence carefully

Follow the parent guide's TDD workflow and exact AAA grammar. Existing tests use `unittest.TestCase`
under pytest; keep one visible scenario operation in Act and its meaningful external oracle in Assert.

- Build application events and RPC requests through `packages/contracts`, check application-envelope
  topic binding before publication, and decode replies through the committed profile. The RPC profile
  has no standalone request-topic binding checker; new cases must explicitly prove request/topic
  agreement when the claim depends on it. Hand-written JSON is appropriate only when malformed input is
  the behavior under test.
- Keep vendor objects and untyped Solace imports inside the narrow black-box helper boundary. Do not use
  a Phase 0 probe as precedent for bypassing `packages/broker` in production code.
- Subscribe before triggering direct traffic, but do not perform a live operation in a fixture, class
  setup, import, or generated test. The selected test's Act must make the authorized mutation visible.
- Prefer a unique, correlatable synthetic scenario over a shared fixed identifier. Assert destination,
  decoded body, correlation metadata, and count when the claim depends on them.
- For a negative, prove the adjacent accepted path and distinguish a typed refusal from timeout, no
  traffic, malformed input, or a dead service. Never broaden an exception catch to make a vendor error
  look like the intended security result.
- Use only synthetic public mission data. Never print or commit generated credentials, certificates,
  tenant values, broker exports, or a prompt, completion, model trace, or application payload captured
  from a live or provider run. Minimal reviewed synthetic prompts in test source remain allowed under the
  parent guide.

Never delete, skip, weaken, or change an established test without explicit human permission. If a live
assertion disagrees with the current container, first identify whether the implementation, desired-state
projection, stale broker state, prerequisite, test, contract, or ADR is defective; do not patch only the
expectation.

## 8. Coordinate changes and evidence

- A topic, envelope, delivery, request, reply, or refusal change reaches the contracts and schema owners,
  their focused tests, affected producers and consumers, canonical documentation, and a new or
  superseding ADR when the decision changes.
- An identity, grant, reply channel, endpoint, or provisioning change reaches the domain and broker
  packages, deployment projection, positive and negative authorization tests, runbook, and authorized
  live evidence where the claim depends on the broker.
- An Agent Mesh app or plugin change reaches the owned YAML, semantic validator, pinned native and image
  compatibility evidence, deployment wiring, and a recreated live container before a runtime claim.
- A command-gateway change lands with member-local unit, property, coverage, and mutation evidence before
  this cross-component probe. Host execution does not prove its Compose shell or image.

Do not edit a dated release-evidence record to match a new result. Add a new curated and redacted record
only after an explicitly authorized run and only when the user asks for evidence capture. A local run
does not update the implementation plan or establish Cloud parity by itself.

## 9. Required verification

Create the frozen root environment, then run the offline probe and deterministic Phase 0 selection:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q tests/phase0/test_solace_messaging_runtime.py
uv run --frozen pytest -q \
  -m "not broker and not ollama and not paid and not docker and not net" \
  tests/phase0
uv run --frozen ruff format --check tests/phase0
uv run --frozen ruff check tests/phase0
uv run --frozen mypy --strict tests/phase0
scripts/hooks/python/pytest-full.sh
```

For a guide-only change, pass the new files explicitly to the file-based hooks:

```sh
pre-commit run --files tests/phase0/AGENTS.md tests/phase0/CLAUDE.md \
  --hook-stage pre-commit
readlink tests/phase0/CLAUDE.md
git diff --no-index --check /dev/null tests/phase0/AGENTS.md
```

`readlink` must print `AGENTS.md`. For a new untracked guide, the no-index command normally exits `1`
because content differs; no diagnostic output means its whitespace is clean. Once staged, use
`git diff --cached --check`; for later tracked edits, use `git diff --check`. Finish with the complete
repository pre-commit and pre-push stages required by the root and parent guides.

Only after exact-file authorization and current prerequisite checks, run one intended live probe:

```sh
uv run --frozen pytest -q tests/phase0/test_first_live_stack.py
uv run --frozen pytest -q tests/phase0/test_agent_mesh_live.py
uv run --frozen pytest -q tests/phase0/test_event_mesh_gateway_live.py
uv run --frozen pytest -q tests/phase0/test_event_mesh_tool_live.py
```

Do not combine those commands into one broader run. Report every live, Docker, broker, Ollama, host
process, network, paid, or external-resource check that was not explicitly authorized; an offline pass
is not live Phase 0 evidence.
