# Agent Mesh Domain Instructions

## 1. Scope and authority

These instructions apply to every file under `agent-mesh/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first; its process, safety, testing, documentation, and version-control
rules still apply. This file adds the rules needed for the isolated Agent Mesh project and does not
weaken an Accepted ADR.

Use the canonical document for the fact being changed instead of copying it here:

| Concern | Canonical source |
| --- | --- |
| Interpreter, environment, and workspace separation | [ADR-0004](../docs/adr/0004-split-python-runtimes.md), [ADR-0010](../docs/adr/0010-uv-workspace-and-toolchain.md) |
| Agent Mesh test and type-check execution | [ADR-0029](../docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md), [ADR-0062](../docs/adr/0062-type-check-the-agent-mesh-domain-from-its-own-directory.md) |
| Owned configuration and its fail-closed validator | [ADR-0032](../docs/adr/0032-agent-mesh-semantic-configuration-validator.md), [ADR-0035](../docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md) |
| Local-model identity and evidence boundary | [ADR-0063](../docs/adr/0063-lock-local-models-by-manifest-digest.md) |
| Runtime layout and safety boundaries | [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`SAFETY.md`](../docs/SAFETY.md) |
| Owned Agent Mesh TLS, recovery, and lifecycle boundary | [ADR-0177](../docs/adr/0177-harden-the-pinned-agent-mesh-broker-runtime.md) |
| Event and topic contracts | [`CONTRACTS.md`](../docs/CONTRACTS.md) |
| Test classes, markers, coverage, and AAA rules | [`TESTING.md`](../docs/TESTING.md) |

## 2. What this directory owns

| Path | Responsibility |
| --- | --- |
| `.python-version`, `pyproject.toml`, `uv.lock` | The non-workspace Python verification project and its exact dependency graph |
| `model-lock.toml` | Auditable Ollama model identifiers and measured manifest digests |
| `aerial_rescue_event_mesh_gateway/` | Owned Direct-output, trusted-context, closed-response, and upstream-diagnostic-redaction extension for the pinned Event Mesh Gateway |
| `aerial_rescue_runtime_compat/` | Source-shape-attested TLS 1.3, bounded retry, asynchronous-initialization readiness, terminal recovery, and process lifecycle around the pinned Connector |
| [`configs/`](configs/AGENTS.md) | Owned Solace AI Connector definitions for agents, workflows, gateways, tools, and the local Web UI |
| `tools/` | Offline, fail-closed semantic validation of every owned configuration |
| `tests/` | Validator behavior, pinned-wheel compatibility, runtime overrides, and warning-policy evidence |

The native virtual environment is for validation and compatibility probes. The supported Agent Mesh
runtime is the container defined under [`deploy/agent-mesh/`](../deploy/agent-mesh/), as decided by
[ADR-0044](../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md).

## 3. Preserve the project boundary

- Use `agent-mesh/.venv`; never use the root `.venv`, combine the two lockfiles, or install either
  project globally.
- Run this project's Python tools from `agent-mesh/` with `uv run --frozen`. The working directory is
  load-bearing for pytest and mypy configuration and for resolving this directory's `tools` package.
- Do not run Agent Mesh pytest or mypy from the repository root with only
  `uv run --project agent-mesh ...`; `--project` selects a project but does not change the working
  directory.
- Do not add imports from the root `tools`, `packages`, or `services` trees. Shared Python code must
  use the intersecting compatibility range and be tested in both environments, as ADR-0004 requires.
- Keep the local Ruff, mypy, pytest, and warning configuration in `pyproject.toml`. Do not move a local
  rule to the root when that would make either Python domain interpret the other one's source.

From this directory, synchronize and run focused checks with:

```sh
uv sync --frozen
uv run --frozen python -m tools.agent_mesh_config_validator
uv run --frozen pytest
uv run --frozen mypy --strict .
uv run --frozen ruff format --check .
uv run --frozen ruff check .
```

## 4. Configuration and model changes

- Treat every file under `configs/` as untrusted input. It must pass the project validator both by
  itself and when merged with the other owned files. Do not bypass validation with a generic YAML
  loader or a suppression.
- Preserve the proposal-only agent boundary, deterministic command-gateway authority, anonymous
  subject representation, secret indirection, local-only Ollama access, and loopback Web UI exposure
  defined by the canonical architecture and safety documents.
- Reference credentials and broker values through environment names declared with secret-safe values
  in [`../.env.example`](../.env.example). When a container needs a new variable, wire that name
  through [`deploy/compose.yaml`](../deploy/compose.yaml); never put the value in tracked YAML, tests,
  logs, or screenshots.
- Follow ADR-0063 when changing a local model. Record a measured manifest digest in
  `model-lock.toml`; do not invent a digest, use a floating identifier, or claim that offline
  validation proves the model is installed or serving those bytes.
- A validator refusal identifies missing proof. Add the representation, test, and governing decision
  needed to prove the case; do not relax the refusal merely to admit a new configuration shape.

## 5. Tooling, dependencies, and tests

- Follow TDD and the repository's AAA structure for every owned behavior or defect. Cover the accepted
  path, each rejected boundary, deterministic redacted diagnostics, and merged-file behavior where
  configuration composition is involved.
- Keep unexpected warnings as errors. Any warning filter must remain restricted to the exact upstream
  module and condition justified by the governing ADR; never add a category-wide filter for owned
  code.
- A dependency or version decision requires an ADR. Keep exact pins and the override in
  `pyproject.toml` synchronized with `uv.lock`; use `uv lock --check` to detect drift.
- When Agent Mesh, its Connector, its SDK override, or an Event Mesh plugin pin changes, also reconcile the official image and hashed
  plugin wheels under [`deploy/agent-mesh/`](../deploy/agent-mesh/). Add or update black-box
  compatibility probes for the installed combination and for any dependency override.
- Install released upstream packages. Do not vendor Agent Mesh source or add it as a submodule.
- Distinguish evidence classes in results. Offline validation does not prove broker connectivity,
  Ollama readiness, A2A discovery, delegation, workflow execution, or container compatibility; run
  the corresponding live or container test when a change affects one of those claims.

## 6. Required verification

Run the canonical wrappers from the repository root before handoff:

```sh
scripts/hooks/agent-mesh/check-agent-mesh-configs.sh
scripts/hooks/agent-mesh/mypy-agent-mesh.sh
scripts/hooks/agent-mesh-test-full.sh
scripts/hooks/python/python-quality-full.sh
scripts/hooks/deps/check-locks.sh
```

The full Agent Mesh test wrapper is authoritative for its marker exclusions and 100% statement and
branch coverage over the validator and owned gateway extension. Finish with every repository-wide
commit- and push-stage gate required by the root
`AGENTS.md`. Report any live, container, network, broker, Ollama, or paid check that was not run; an
excluded check is not a pass.
