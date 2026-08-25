# Cross-component Test Instructions

## 1. Scope and authority

These instructions apply to every file under `tests/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its safety, TDD, documentation, security, and version-control rules
still apply. Read every more-specific guide that governs a package, schema, fixture, deployment artifact,
or evidence record a test exercises before changing that test or its subject. Tests that exercise golden
fixtures also inherit [`fixtures/AGENTS.md`](../fixtures/AGENTS.md), the schema guide, and the contracts
guide.

Use the canonical source for the fact under test instead of treating an assertion as its owner:

| Concern | Authority or reference |
| --- | --- |
| Red-green-refactor workflow and approval rules | [Root `AGENTS.md` section 5](../AGENTS.md#5-test-driven-development) |
| AAA, test classes, coverage, mutation, and stage routing | [`docs/TESTING.md`](../docs/TESTING.md) |
| Runtime layout and acceptance-evidence boundaries | [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) |
| Event, topic, HTTP, and delivery semantics | [`docs/CONTRACTS.md`](../docs/CONTRACTS.md) |
| Safety invariants and approval-bypass cases | [`docs/SAFETY.md`](../docs/SAFETY.md), [`docs/security/`](../docs/security/threat-model.md) |
| Numeric values and their measuring instruments | [`docs/operating-parameters.md`](../docs/operating-parameters.md) |
| Local setup, recovery, and hook workflow | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Live-evidence capture, redaction, and historical-record rules | [`release-evidence/AGENTS.md`](../release-evidence/AGENTS.md) |
| Split Python runtimes | [ADR-0004](../docs/adr/0004-split-python-runtimes.md) |
| Hook stages and CI authority | [ADR-0012](../docs/adr/0012-git-hooks-with-ci-as-authority.md) |
| Mandatory AAA structure | [ADR-0018](../docs/adr/0018-enforced-arrange-act-assert.md) |
| Offline contract manifest and oracles | [ADR-0021](../docs/adr/0021-contract-artifact-manifest.md) |
| Untyped Solace client containment | [ADR-0028](../docs/adr/0028-untyped-solace-client-boundary.md) |
| Agent Mesh test environment | [ADR-0029](../docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) |
| Schema identity and one-reason fixtures | [ADR-0038](../docs/adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md) |
| Ordered dashboard state and stream frames | [ADR-0101](../docs/adr/0101-order-dashboard-events-outside-the-five-field-projection.md) |
| Dashboard schema collection bounds | [ADR-0106](../docs/adr/0106-bound-dashboard-schema-strings-and-arrays-explicitly.md) |
| Local broker and non-gating Cloud showcase | [ADR-0043](../docs/adr/0043-docker-broker-with-solace-cloud-showcase.md) |
| Static Compose policy | [ADR-0045](../docs/adr/0045-fail-closed-compose-policy-gate.md) |
| Generated per-checkout certificate authority | [ADR-0046](../docs/adr/0046-generated-local-certificate-authority.md) |
| Least-privilege broker projection | [ADR-0061](../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Fixed Agent Mesh A2A namespace | [ADR-0064](../docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md) |
| Import-graph test selection | [ADR-0066](../docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md) |

An Accepted ADR governs if a test, comment, old evidence record, or implementation disagrees. Tests are
executable evidence for a bounded claim; they are not a second home for contract semantics, authorization
policy, operating parameters, or architecture. If a test exposes disagreement, report the defect instead
of changing the expected result to match the implementation.

## 2. Directory role and placement

Root `tests/` owns Python cross-component contract, integration, end-to-end, non-dashboard acceptance,
security, and black-box compatibility evidence that has no single workspace-member owner. Keep any test
whose behavior has one workspace-member owner beside that member under `packages/*/tests/` or
`services/*/tests/`, including its unit, property, and mutation cases. Root integration tests can
contribute member coverage, but cannot substitute for focused member behavior or the co-located test
directory required by mutation gates.

The current suites have these deliberately different boundaries:

| Path | Responsibility |
| --- | --- |
| `contract/test_schema_identity.py` | Offline schema inventory, identity, reference, constant, binding, and contract-gate agreement |
| `contract/test_golden_fixture_oracle.py` | Offline manifest polarity plus schema, Python, canonicalizer, topic, and refusal agreement |
| `contract/test_command_vocabulary.py` | Offline agreement between command-result schema words, dispatch states, and the fleet-simulator publishing table |
| [`phase0/`](phase0/AGENTS.md) | Offline native-client compatibility and explicitly authorized live stack, Agent Mesh, and Event Mesh feasibility probes |
| [`integration/`](integration/AGENTS.md) | Explicitly authorized live fleet telemetry, guaranteed-delivery, command-dispatch, and backlog-recovery probes |
| `security/test_broker_authorization.py` | Live positive and negative broker publish/connect authorization controls |

All files here execute in the root Python 3.14 workspace, including the client-side Agent Mesh live
probe. Tests under `agent-mesh/tests/` execute from `agent-mesh/` with its isolated Python 3.13 project.
Never collect one tree with the other interpreter. Dashboard tests remain under `apps/dashboard/`, and
quality-gate conformance tests remain under `tools/quality_gate_tests/` with their own local guidance.

Direct imports from the untyped `solace` distribution are allowed here only for deliberate native-client
compatibility and live black-box probes. They are not precedent for production code, member-local tests,
or helpers to bypass the typed `packages/broker` facade. Keep vendor objects and `Any` values contained
inside the narrow probe boundary.

## 3. Markers and test selection

Root [`pyproject.toml`](../pyproject.toml) is the marker and pytest-configuration authority. Preserve
strict marker and configuration validation, importlib import mode, strict `xfail`, warnings as errors,
and the one narrowly recorded upstream Solace warning exception.

Markers have two roles:

- Class markers such as `contract`, `compatibility`, `phase0`, and `security` describe why a test exists.
  They do not make the test nonblocking and do not prevent collection.
- Resource markers `broker`, `ollama`, `paid`, `docker`, and `net` declare external prerequisites and
  side effects. A test must carry every resource marker it needs; never omit one to make a hook select it.

The blocking root wrappers in `scripts/hooks/python/` currently exclude exactly those five resource
markers. Consequently, `phase0`, `security`, and `compatibility` tests still run at blocking stages when
they do not also declare an excluded resource. Preserve that distinction. In particular, the offline
Solace runtime probe is intentionally blocking even though it is marked `phase0`.

Do not use or recommend a bare `pytest`, `pytest tests`, `pytest tests/phase0`, or `-m phase0` as a safe
default. Those forms do not apply the resource exclusion and can connect to a live broker, publish
messages, invoke Ollama, or depend on running containers. Use the canonical wrappers or an explicit
resource-exclusion expression for deterministic work, and name one authorized live file when live
evidence is intended.

Resource deselection happens after pytest imports and collects a module. Module import, marker evaluation,
case generation, and collection must therefore remain deterministic, offline, secret-free, and free of
external mutation. Never open a socket, read generated credentials, start a container, invoke a model, or
change state at collection time. Resource work belongs inside explicit selected-test execution, with the
observable scenario operation in Act.

Adding or changing a marker, resource class, exclusion expression, hook route, or blocking-stage
admission changes verification policy. Coordinate `pyproject.toml`, `docs/TESTING.md`, hook scripts,
quality-gate conformance tests, CI, and the required decision record. Broker integration remains
nonblocking unless a new record explicitly admits it.

The commit-stage selector follows the import graph; the pre-push stage remains the full deterministic
authority. A changed `conftest.py`, non-Python input, unparsable module, or ambiguous path deliberately
widens selection to the whole deterministic suite. Do not add a global `conftest.py`, autouse fixture,
or hidden test-registration mechanism merely to reduce repetition: it broadens impact, obscures the Act,
and changes selection behavior.

## 4. Test construction and reliability

Follow the red-green-refactor workflow in the root guide and the exact AAA grammar in
`docs/TESTING.md`. Fixtures and focused helpers may construct inputs, collaborators, or low-level live
operations, but the scenario operation must remain a clearly named call in Act and its meaningful oracle
must remain visible in Assert. Do not generate tests dynamically or add a skip, weak assertion, broad
`xfail`, or exception catch to conceal a missing prerequisite or product defect.

Never delete, weaken, or change an established expectation without explicit human permission. When an
approved behavior change makes an expectation obsolete, update the canonical contract or decision and
all affected consumers in the same focused change; do not patch only the test that noticed the drift.

For all root tests:

- use deterministic values, ordering, and serialized bytes, and inject clocks and random sources where
  the subject owns them;
- keep offline tests free of sockets, containers, credentials, mutable host state, and network fallback;
- validate new or changed broker, HTTP, model, and scenario inputs at their trust boundary with Pydantic
  rather than relying on coercion; the current Agent Mesh probe manually parses broker and HTTP JSON and
  coerces card names with `str()`, so it does not satisfy the root trust-boundary rule and is not a pattern;
- bound every connection, acknowledgement, receive poll, retry, process wait, and model call;
- use a monotonic clock for elapsed-time windows and event/readback conditions instead of sleeps as
  synchronization;
- release clients, receivers, publishers, HTTP connections, and services in `finally` blocks;
- catch only the typed failure that proves the expected outcome, and let unexpected failures retain a
  useful, secret-safe traceback;
- keep helper APIs typed and narrow, and avoid mutable module-global state or order-dependent cases; and
- assert behavior, state, side effects, errors, and refusal precedence rather than implementation trivia.

An `EXPECTED_*` literal in a test is pressure against silent inventory drift, not a canonical parameter.
Change it only with the owning artifact and an explanation of why the inventory legitimately changed.
Numeric timeouts and windows need a canonical row and instrument in `docs/operating-parameters.md` before
they become safety- or release-gating values; do not treat an unowned probe literal as operational policy.

## 5. Contract-oracle changes

Contract tests must remain deterministic and completely offline. Resolve Draft 2020-12 references only
through the committed in-memory registry; never fetch a remote `$ref`. Preserve total discovery so a new
schema or fixture cannot evade the manifest merely by using an unexpected name.

Coordinate an affected contract change across the actual owners:

- `schemas/v1/` and [`schemas/AGENTS.md`](../schemas/AGENTS.md);
- `schemas/contract-manifest.toml`;
- `fixtures/golden/v1/` and the owning accepted baseline;
- `packages/contracts/` and its local tests and guide;
- `tools/contract_gate.py` when the manifest policy itself changes;
- `docs/CONTRACTS.md` and any governing ADR; and
- every Python and TypeScript boundary consumer that implements the changed contract.

Keep at least one valid and one invalid fixture for every manifest entry. Every negative fixture must
fail schema validation for exactly one reason, and review must compare it with an accepted baseline to
confirm the intended one-member delta; an error count alone cannot prove that property. Topic refusal
fixtures may be schema-valid while being semantically refused, so preserve the distinction between shape
acceptance and topic-policy acceptance.

Agreement between committed schemas, fixtures, tools, and Python can still be shared-wrong. The current
root oracles do not prove an independently correct specification or a future TypeScript consumer. Keep
claims at committed-inventory and implementation-parity scope until an independent consumer is exercised.

## 6. Live-resource authorization and hygiene

Resource markers state prerequisites; they are not authorization to create or mutate those resources.
Before a live run, obtain explicit human authorization for the exact scope and follow the current
`CONTRIBUTING.md` runbook. Starting or recreating containers, generating or rotating secrets,
provisioning ACLs, deleting volumes, contacting Solace Cloud or another network service, invoking a paid
model, and cleanup that removes state are separately authorized external actions or mutations. Never make
one an implicit collection or fixture side effect; the selected test must make the authorized action
visible through a clearly named call.

Use only the ignored, per-checkout certificate authority and role credentials generated by the approved
setup. Never print, log, snapshot, attach, or commit passwords, private keys, authorization headers,
expanded environment values, tenant identifiers, raw broker exports, prompts, completions, or model
traces captured from a live or provider run, real-person data, or real mission telemetry. Minimal,
reviewed, synthetic test input may be committed when it is the behavior under test, including a
prompt-injection or delegation case; treat it as public and keep secrets, personal data, and tenant values
out of it. Synthetic mission identifiers, public project role names, and public topic shapes are not
credentials, but still keep output to what the assertion needs.

The live broker-authorization tests connect and publish acknowledged persistent messages. Phase 0 probes
have distinct broker, model, application, temporary-endpoint, and persistent-message side effects; their
local guide owns the exact boundaries.

Confirm the exact prerequisite set for the selected file rather than applying one blanket setup:

- Files under `phase0/` use the per-file prerequisite and ordering matrix in
  [`phase0/AGENTS.md`](phase0/AGENTS.md).
- `test_broker_authorization.py` additionally needs a completed
  `just provision --namespace aerial-rescue-mesh` against that stack.

Preserve certificate-chain and hostname verification. Never switch a probe to plaintext, disable
hostname checks, trust an arbitrary certificate, or fall back from a missing local authority. Keep
timeouts bounded and cleanup best-effort without masking the primary failure. A missing live prerequisite
should fail an explicitly requested live run; it should not become a skip that CI silently counts as
evidence.

No Solace Cloud, showcase-tenant, provider, or paid-model credential belongs in a test, fixture, CI
secret, screenshot, or committed evidence. The local container does not prove Cloud parity. Record a new
authorized observation only as a dated, curated, redacted artifact under `release-evidence/`, following
that directory's guide. A green offline or local run does not automatically update release claims.

## 7. Positive controls and claim ceilings

A negative authorization result is valid only when a permitted positive control succeeds through the
same transport and configuration. Preserve persistent publish with acknowledgement where a direct
publish could be silently discarded. Do not catch a broad client or transport failure and label it an
ACL denial; when the client API conflates causes, retain positive controls and state that limitation.
The current authorization helper maps any `PubSubPlusClientError` during connect or publish to a denial;
its positive controls rule out a shared outage, not every identity-specific configuration or transport
failure. A stronger claim needs a discriminated vendor reason or broker-side denial readback.

Keep every report within what the present assertions establish:

- The contract suites establish the committed schema and fixture inventory plus schema, Python,
  canonicalizer acceptance or refusal, topic, and gate agreement. They do not prove expected canonical
  bytes, independent semantic correctness, runtime consumer behavior, or TypeScript parity that has not
  been exercised.
- The Phase 0 suites establish only the file-specific offline or live observations listed in
  [`phase0/AGENTS.md`](phase0/AGENTS.md). That guide owns their exact claim ceilings, including the Event
  Mesh Gateway and Event Mesh Tool probes; do not infer durability, causation, model quality, or complete
  authorization coverage from their class name.
- The broker-authorization suite establishes only its current allowed and denied publish/connect cases
  against the local projection. It does not establish subscription denial, every grant, A2A policy,
  queues or redelivery, stale-identity deletion, per-process identity separation, TLS-downgrade closure,
  or Cloud parity.

## 8. Required verification

Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`. For a fresh checkout, create the exact
workspace environment with:

```sh
uv sync --all-packages --frozen
```

Run deterministic focused tests from the repository root:

```sh
uv run --frozen pytest -q tests/contract
uv run --frozen pytest -q tests/phase0/test_solace_messaging_runtime.py
uv run --frozen pytest -q \
  -m "not broker and not ollama and not paid and not docker and not net" tests
pre-commit run test-aaa --all-files --hook-stage pre-commit
uv run --frozen ruff format --check tests
uv run --frozen ruff check tests
uv run --frozen mypy --strict tests
scripts/hooks/python/pytest-full.sh
```

`scripts/hooks/python/pytest-full.sh` is the authoritative root deterministic suite with per-member
coverage; a focused command is fast feedback, not a replacement. `just check-contracts` validates the
manifest artifacts but does not execute all three root contract test files, so run the focused contract
suite as well when those oracles change.

The Phase 0 guide lists its exact-file commands. Only after explicit authorization and current runbook
preparation, invoke the separate broker-authorization probe with:

```sh
uv run --frozen pytest -q tests/security/test_broker_authorization.py
```

Run only the file whose prerequisites and side effects were authorized. Do not infer permission for the
other Phase 0 or security probes, or for setup, reprovisioning, rotation, teardown, Cloud access, or paid
calls.

For a guide-only change, pass the new files explicitly to file-based hooks because Git diff discovery
does not see untracked paths:

```sh
pre-commit run markdownlint-cli2 --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run typos --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run docs-facts-and-links --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run docs-strict --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run check-symlinks --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run destroyed-symlinks --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run detect-private-key --files tests/AGENTS.md tests/CLAUDE.md \
  --hook-stage pre-commit
```

Finish with the repository-wide stages required by the root guide:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete diff, confirm that `CLAUDE.md` is a relative symlink whose literal target is
`AGENTS.md`, and verify that no generated secret, live output, live/provider prompt or completion,
model trace, sensitive synthetic input, tenant value, cache, or unrelated change is tracked. Report every
live, platform-specific, or external-resource check that was not authorized or could not run; an unrun
live probe is not a failed documentation-only change, and an offline pass is not live evidence.
