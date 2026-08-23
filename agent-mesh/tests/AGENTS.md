# Agent Mesh Test Instructions

## 1. Scope and authority

These instructions apply to every file under `agent-mesh/tests/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`agent-mesh/AGENTS.md`](../AGENTS.md) first. When a
test exercises the validator or image probe, also read [`agent-mesh/tools/AGENTS.md`](../tools/AGENTS.md).
Those files retain authority over process, implementation, environment, safety, and version control.

Use the source that owns a requirement rather than making a test or fixture its policy owner:

| Concern | Canonical source |
| --- | --- |
| Executable-test structure, classification, coverage, and claim ceilings | [`TESTING.md`](../../docs/TESTING.md) and [ADR-0018](../../docs/adr/0018-enforced-arrange-act-assert.md) |
| Python-domain isolation, Agent Mesh verification, and commit-stage selection | [ADR-0004](../../docs/adr/0004-split-python-runtimes.md), [ADR-0029](../../docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md), [ADR-0062](../../docs/adr/0062-type-check-the-agent-mesh-domain-from-its-own-directory.md), and [ADR-0066](../../docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md) |
| Validator behavior and fail-closed refusals | [ADR-0032](../../docs/adr/0032-agent-mesh-semantic-configuration-validator.md), [ADR-0035](../../docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md), and [`tools/AGENTS.md`](../tools/AGENTS.md) |
| Runtime pins, warning containment, native overrides, and image evidence | [ADR-0034](../../docs/adr/0034-scope-agent-mesh-warning-filters-to-upstream-modules.md), [ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), [ADR-0047](../../docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md), and [`TECH_DEBT.md`](../../TECH_DEBT.md) |
| Local-model, Web UI, gateway, and Event Mesh Tool boundaries | [ADR-0063](../../docs/adr/0063-lock-local-models-by-manifest-digest.md), [ADR-0065](../../docs/adr/0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md), [ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md), [ADR-0069](../../docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md), and [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) |
| Event, safety, and numeric requirements | [`CONTRACTS.md`](../../docs/CONTRACTS.md), [`SAFETY.md`](../../docs/SAFETY.md), and [`operating-parameters.md`](../../docs/operating-parameters.md) |

Tests provide bounded evidence. They must independently detect drift, but a passing expectation does
not create or broaden a product, safety, compatibility, or release claim. An Accepted ADR governs if
a test, fixture, old evidence record, or this guide disagrees with it.

## 2. What this directory proves

| Path | Responsibility |
| --- | --- |
| `test_config_validator.py` | Behavioral, security-boundary, distribution-provenance, configuration-conformance, and CLI evidence for the offline semantic validator |
| `test_pinned_plugin_compatibility.py` | Native-environment evidence for exact installed Agent Mesh and Event Mesh plugin versions, entry points, base classes, and selected runtime symbols |
| `test_pinned_runtime_overrides.py` | Native-environment evidence that the approved dependency override is installed and that the selected Agent Mesh arithmetic paths still work |
| `test_warning_policy.py` | Evidence that warnings from owned code remain fatal under the local pytest policy |
| `fixtures/config_validation/` | Synthetic accepted inputs shared by validator tests |

Keep a new test in the narrowest concern-named module. Add a new module when a separate tool or proof
boundary would otherwise be hidden in an unrelated test class. In particular, deterministic tests for
`tools/image_probe.py` belong in a concern-named module here before that tool's behavior changes.

The YAML fixtures are support artifacts, not executable tests, deployed configurations, contract
goldens, release evidence, or proof of a live integration. They establish nothing unless an executable
test exercises them and asserts an observable outcome.

## 3. Preserve the execution boundary

- Follow the parent execution boundary: enter `agent-mesh/`, use `agent-mesh/.venv` on the pinned
  Python 3.13 toolchain, and never mix in the root environment. Running `uv run --project agent-mesh`
  from the root is not equivalent because it does not change the working directory.
- Run tests through pytest, not direct `unittest` discovery. Pytest's strict markers, warning filters,
  configuration, and plugins are part of the evidence boundary. Coverage evidence is added by the
  authoritative full wrapper; a focused pytest command alone does not provide it.
- The root Python 3.14 toolchain may select and statically parse these files, but it must not execute
  them. The built runtime image has a third interpreter and is verified separately by the hardened
  image probe.
- Keep module import and collection offline, deterministic, and side-effect free. Pytest applies
  marker deselection after collection, so a resource test must not connect, read a credential, or
  require a live service at import or collection time.

The current suite is deterministic and offline. `phase0` and `compatibility` classify native tests;
they do not exclude or make them live. Resource markers and the authoritative exclusion expression
live in `agent-mesh/pyproject.toml` and the hook wrappers. Before adding a broker, Ollama, network,
paid, Docker, or other resource-dependent test, coordinate its strict marker, collection behavior,
documented test class, and commit- and push-stage routing. An excluded test is not a pass.

## 4. Test construction

Follow the repository's mandatory red-green-refactor workflow and exact Arrange-Act-Assert structure.
Every executable test has one direct, non-empty `# Arrange`, `# Act`, and `# Assert` sequence; helpers,
fixtures, lifecycle methods, and declarative data remain support artifacts.

- Keep one observable behavior per test. Use the existing `unittest.TestCase` organization and focused
  pytest helpers where they make the scenario clearer; do not generate test callables dynamically.
- Name classes and tests for the boundary and outcome. Prefer a small matrix or `subTest` when the
  behavior is genuinely identical across values, while preserving useful failure identity.
- Exercise the accepted case and each distinct refused boundary. For validator rules, cover individual
  files and merged behavior when the rule can change after composition.
- Assert the exact rule identifier, location, ordering, stream, exit status, redaction, and state or
  side effect that form the contract. Do not accept a generic failure when a more precise outcome is
  observable.
- Patch at metadata, entry-point, import, environment, filesystem, socket, clock, or subprocess
  boundaries. Use deterministic values and restore the caller's working directory, environment,
  import state, and monkeypatches on every outcome.
- Use temporary directories and register cleanup immediately. Do not write generated cases, caches,
  loaded secrets, or upstream package state into the checkout.
- Use synthetic credentials and tenant values. Assert that hostile values are redacted from findings,
  standard output, standard error, and unexpected-error paths.
- Keep expected compatibility values independent of installed metadata and the manifest value being
  checked. Deriving both sides from the same source destroys the test's ability to detect drift.
- Avoid global autouse fixtures and shared mutable state. A helper may reduce setup noise, but the test
  body must still make its scenario and oracle evident.

## 5. Validator fixtures and conformance tests

Keep synthetic project tests separate from tests that deliberately inspect the real checkout.

- Build synthetic projects from the smallest files needed for the scenario. The existing factories
  emit sorted, JSON-compatible YAML so tests can change structure without indentation-sensitive text
  editing; preserve that property unless a parser-specific input is the behavior under test.
- Copy or create only the environment template and model lock needed by the synthetic project. Never
  let a host variable, home directory, cache, installed shadow package, or current directory determine
  a verdict.
- Treat symlinks, includes, environment substitutions, YAML documents, distribution records, entry
  points, and upstream schemas as hostile trust boundaries. Include containment, cycles, secret
  handling, and unavailable or malformed upstream primitives need explicit refused cases.
- Keep `fixtures/config_validation/` representative and secret-safe. Use placeholders and indirection,
  not real endpoints, credentials, tenant identifiers, or data copied from a live environment.
- Real-checkout inventory and exact-version assertions are intentional drift tripwires. They are not
  the canonical source for the number of configs, readiness owners, pins, or overrides. Update such an
  assertion only with the owning configuration or manifest and an approved behavior change; never
  weaken it in isolation to make drift pass.

A fixture edit is a non-Python change and therefore may select the whole Agent Mesh suite. Run the
selected tests rather than assuming only the apparent consumer can change.

## 6. Keep proof claims bounded

| Green evidence | What it establishes | What it does not establish |
| --- | --- | --- |
| Validator suite | Offline shape, policy, fail-closed refusal, redaction, and CLI behavior against controlled inputs and the exact native validation surfaces | Runtime startup; broker, TLS, ACL, Ollama, A2A, workflow, delivery, settlement, model-output, or image behavior |
| Native plugin compatibility suite | Exact native wheels expose the selected entry points, classes, imports, and symbols | Configuration startup, message flow, redelivery, delegation, or compatibility inside the official image |
| Native override suite | The approved override is installed in the native verification environment and selected arithmetic paths behave as expected | The image contains that override, every evaluator path is safe, or the evaluator is a general security sandbox |
| Warning-policy suite | An owned Pydantic deprecation remains an error under pytest | That every warning filter is correct or that the recorded upstream warning debt is resolved |
| Local fixtures | Reusable controlled inputs for consuming tests | Deployed configuration validity or any live, container, broker, model, or release claim |

The full wrapper's statement and branch coverage gate applies to
`tools.agent_mesh_config_validator`; it is not semantic completeness and does not cover upstream
packages or `tools/image_probe.py`. Native compatibility tests never substitute for the authorized,
network-disabled in-container probe. Refer current limitations and accepted upstream debt to
`TECH_DEBT.md` and the governing ADR rather than freezing mutable debt details into test names or this
guide.

## 7. Coordinate intentional drift

- A validator behavior change spans its implementation and focused tests. Reconcile affected owned
  configurations, contracts, safety or contributor documentation, and record a new or superseding ADR
  when the change alters a boundary or admitted shape.
- A model-policy change usually spans `.env.example`, `agent-mesh/model-lock.toml`, owned configs,
  validator tests, readiness behavior, and operating documentation. Offline tests prove lock form and
  membership, not the daemon's installed digest.
- An Agent Mesh or Event Mesh plugin pin change must reconcile `agent-mesh/pyproject.toml`,
  `agent-mesh/uv.lock`, independent native expectations, the official image and hashed plugin
  requirements, image-probe expectations, governing ADRs, debt records, and evidence claims.
- A native-only override change must reconcile the manifest, lock, independent override tests,
  governing ADR, and debt record while preserving the explicit distinction from the official image.
- A warning-policy change must remain scoped to the exact justified upstream source. Update pytest
  policy, focused regression evidence, and the debt ledger together; record a new or superseding ADR
  if the decision changes, and never rewrite an Accepted ADR or suppress the category for owned code.
- A hook or stage, selection algorithm, marker registry or routing policy, or coverage-gate change also
  needs root-side conformance tests in the concern-owning `hooks/`, `selection/`, or `coverage/`
  subtree described by
  [`tools/quality_gate_tests/AGENTS.md`](../../tools/quality_gate_tests/AGENTS.md), plus updates to the
  canonical testing documentation.
- A behavioral change to `tools/image_probe.py` first needs deterministic boundary tests here. Those
  tests still do not replace `just probe-image` when image contents or compatibility claims change.

Never delete, skip, weaken, or edit an established expectation merely to make production code or
configuration pass. A legitimate expected-behavior change requires explicit human permission and the
canonical owner changes above.

## 8. Required verification

Start from `agent-mesh/` with the narrowest concern-owning test, then run the deterministic suite:

```sh
uv sync --frozen
uv run --frozen pytest -q -m \
  "not broker and not ollama and not paid and not docker and not net" \
  tests/test_config_validator.py
uv run --frozen pytest -q -m \
  "not broker and not ollama and not paid and not docker and not net"
```

Use the corresponding concern-named path instead of `test_config_validator.py` for compatibility,
override, warning-policy, or future image-probe work. Then return to the repository root and run the
canonical wrapper list in the parent guide. The full Agent Mesh wrapper is authoritative for marker
exclusions and its validator coverage gate. Finish with all repository pre-commit and pre-push gates
required by the root instructions.

When this guide or its alias changes, also run:

```sh
pre-commit run --files agent-mesh/tests/AGENTS.md agent-mesh/tests/CLAUDE.md \
  --hook-stage pre-commit
readlink agent-mesh/tests/CLAUDE.md
git diff --no-index --check /dev/null agent-mesh/tests/AGENTS.md
```

`readlink` must print `AGENTS.md`. For a new untracked guide, the no-index command normally exits `1`
because the file has content; no diagnostic output means its whitespace is clean. Once staged, use
`git diff --cached --check`; for later tracked edits, use `git diff --check`. Run `just probe-image`
only when image-probe behavior, image contents, Agent Mesh runtime-image or Event Mesh plugin pins, or
an in-container compatibility claim changes and the Docker run is explicitly authorized. Report every
live, container, broker, Ollama, network, or paid check that was not run; an excluded check is not a
pass.
