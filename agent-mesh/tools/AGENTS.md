# Agent Mesh Tooling Instructions

## 1. Scope and authority

These instructions apply to every file under `agent-mesh/tools/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`agent-mesh/AGENTS.md`](../AGENTS.md) first. Their
process, safety, environment, testing, and version-control rules still apply.

Use the canonical source for the policy being enforced rather than copying its values into this
guide:

| Concern | Canonical source |
| --- | --- |
| Python-domain isolation and execution context | [ADR-0004](../../docs/adr/0004-split-python-runtimes.md), [ADR-0029](../../docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md), and [ADR-0062](../../docs/adr/0062-type-check-the-agent-mesh-domain-from-its-own-directory.md) |
| Semantic validator and fail-closed refusals | [ADR-0032](../../docs/adr/0032-agent-mesh-semantic-configuration-validator.md) and [ADR-0035](../../docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md) |
| Local-model lock boundary | [ADR-0063](../../docs/adr/0063-lock-local-models-by-manifest-digest.md) |
| Web UI, gateway, and Event Mesh Tool boundaries | [ADR-0065](../../docs/adr/0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md), [ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md), [ADR-0069](../../docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md), and [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) |
| Official-image runtime and in-container evidence | [ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) and [`ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Test classes, claim ceilings, known debt, and numeric gates | [`TESTING.md`](../../docs/TESTING.md), [`TECH_DEBT.md`](../../TECH_DEBT.md), and [`operating-parameters.md`](../../docs/operating-parameters.md) |

An Accepted ADR governs if an implementation, fixture, old evidence record, or this guide disagrees
with it. Do not describe a rule more broadly than the current tested implementation: the test suite
and debt ledger expose deliberate limits that a green validator does not erase.

## 2. What this directory owns

| Path | Responsibility |
| --- | --- |
| `agent_mesh_config_validator.py` | Offline, fail-closed validation of every selected owned Agent Mesh configuration individually and, when at least two individually valid files are selected, as one merged configuration |
| `image_probe.py` | Standard-library compatibility evidence gathered inside the built Agent Mesh runtime image |
| `__init__.py` | The local `tools` package boundary used by the validator and its native tests |

`agent-mesh` is a non-workspace, non-package uv project. These tools execute directly from the tree;
they are not application services and must not become imports of the root Python domain.

## 3. Keep the two proof systems distinct

| Boundary | Configuration validator | Image compatibility probe |
| --- | --- | --- |
| Supported invocation | From `agent-mesh/`, through `uv run --frozen` or its blocking hook wrapper | From the repository root through `just probe-image` and the Docker wrapper |
| Interpreter | The isolated native `agent-mesh/.venv` | The built image's `/opt/venv/bin/python` |
| Dependencies | The frozen Agent Mesh project, including exact upstream validation primitives | The standard library plus the distributions installed in the image |
| Environment | Temporary HOME, cache, working directory, and deterministic template values; no runtime client or network | Read-only, network-disabled throwaway container supplied by the wrapper |
| Evidence | Configuration shape and project policy against the pinned native wheels | Image interpreter, three expected top-level distribution versions, plugin loading, and selected imported runtime symbols |
| Does not prove | Broker or Ollama reachability, startup, A2A, delivery, model output, or image compatibility | Configuration validity, runtime startup, messaging, settlement, delegation, or model behavior |

The wrapper is part of the image-probe evidence boundary. Running `image_probe.py` directly in the
native environment is useful diagnosis but is not in-container evidence.

## 4. Semantic validator rules

- Run the validator from `agent-mesh/`. `uv run --project agent-mesh` does not change the working
  directory, and the repository owns a second package named `tools`; running from the root can import
  or configure the wrong domain.
- Keep validation offline and side-effect bounded. It may read selected configurations, the committed
  environment template, the model lock, and installed distribution metadata. It must not open a
  socket, start Agent Mesh, contact a broker or model, depend on a warm cache, or mutate the checkout.
- Preserve the temporary runtime boundary and restore the caller's environment and working directory
  on every outcome. Host variables and user cache state must not change a verdict.
- Bind upstream modules, classes, schemas, and entry points to the exact installed distributions.
  An importable shadow module, incomplete distribution record, drifted pin, malformed upstream schema,
  or unavailable validation primitive must fail closed.
- Treat YAML, includes, environment references, model-lock data, and upstream parser results as
  untrusted. Preserve include containment, cycle and symlink-escape refusal, exact-shape checks,
  individual and merged validation, and the distinction between `bool` and integer values.
- Keep results immutable, sorted, deterministic, and value-redacted. Expected hostile-input or
  upstream-loader failures become typed rule findings; never print a secret, offending value, raw
  exception, environment, or configuration document.
- Preserve distinct success, finding, and invalid-usage outcomes. There is no per-file suppression or
  success-on-unprovable path. A refusal is lifted only with the representation, tests, documentation,
  and decision that prove the newly admitted case.
- Keep current rule scope honest. Broadening or narrowing model, topic, app, gateway, Web UI, or tool
  checks requires focused tests and review against the governing ADRs, current gaps, contracts, safety
  rules, and deployment configuration.

## 5. Image probe rules

- Keep `image_probe.py` importable with the standard library and the distributions it measures. Do
  not add pytest, a root-domain package, a native-only helper, or another dependency merely to run a
  probe inside the runtime image.
- Preserve the hardened wrapper: no network, read-only root filesystem, temporary writable storage,
  no new privileges, the image interpreter as entry point, and a read-only probe mount. A weaker
  ad-hoc Docker command is not equivalent evidence.
- Keep expected compatibility mismatches explicit and reviewable. The current command accumulates
  `ProbeError` outcomes, but unexpected import or entry-point exceptions escape and can stop later
  checks. Expanding that translation and aggregation is new behavior requiring deterministic tests;
  messages must remain free of credentials and host-specific environment values.
- A version, plugin-loading, or runtime-symbol change must be reconciled across the Agent Mesh manifest
  and lock, native compatibility tests, both tool modules, the derived image, hashed plugin
  requirements, governing ADRs, and current evidence claims.
- The image probe is intentionally non-gating because it requires Docker and a built image. A change to
  the probe or the compatibility claim requires an authorized in-container run; record new dated
  evidence rather than rewriting an earlier run. If Docker or the image is unavailable, report the
  claim as unverified rather than substituting the native test.

## 6. Tests and cross-tree coordination

Follow TDD and the repository's mandatory AAA structure for every tool behavior or defect.

- `agent-mesh/tests/test_config_validator.py` owns validator policy, hostile boundary cases,
  deterministic diagnostics, environment restoration, and command behavior. Keep its required
  validator coverage gate green.
- `agent-mesh/tests/test_pinned_plugin_compatibility.py` owns native installed-wheel compatibility;
  it does not replace the image probe. Runtime overrides and warning containment stay in their
  concern-named Agent Mesh tests.
- `image_probe.py` currently has no deterministic unit suite and is outside the validator coverage
  target. Before changing its behavior, add concern-named tests under `agent-mesh/tests/` that mock
  metadata, entry-point, and module boundaries and cover each check, expected and unexpected failures,
  output redaction, and multi-failure status. A wrapper change also needs root-side conformance tests;
  neither test class replaces an in-container run.
- Root-side tests under `tools/quality_gate_tests/hooks/` own hook activation, fail-closed prerequisites,
  command routing, and coverage wiring. Root-side deployment tests own agreement between Agent Mesh
  configuration and the 3.14 contracts, broker policy, and Compose projection.
- Cross-runtime copies of a topic, reply prefix, namespace, or policy value may be intentional because
  Python 3.13 tooling cannot import Python 3.14 packages. Do not deduplicate them with a cross-domain
  import. Hold the copies equal with tests or a committed neutral artifact and update both consumers.
- A model-policy change usually spans `.env.example`, `agent-mesh/model-lock.toml`, owned configs,
  validator tests, readiness, and operating documentation. Offline validation proves lock form and
  membership; live readiness owns digest equality.

## 7. Required verification

Start with the concern-owning test: `test_config_validator.py` for validator behavior,
`test_pinned_plugin_compatibility.py` for native pin or symbol compatibility, and the new concern-named
probe suite for image-probe logic. Then run every Agent Mesh command and repository-root wrapper in the
parent guide's required-verification sections; do not substitute a root-environment invocation.

When this guide or its alias changes, also run:

```sh
pre-commit run --files agent-mesh/tools/AGENTS.md agent-mesh/tools/CLAUDE.md \
  --hook-stage pre-commit
readlink agent-mesh/tools/CLAUDE.md
git diff --check
```

`readlink` must print `AGENTS.md`. Run `just probe-image` when the image probe, image contents,
Agent Mesh runtime-image or Event Mesh plugin pins, or compatibility claim changes; it is not required
for a guide-only change. Finish with every repository-wide commit- and push-stage gate required by
the root instructions, and report every live, container, broker, Ollama, network, or paid check that
was not run.
