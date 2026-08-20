# Accepted technical debt

> **Authority:** the enforcing registries are authoritative for the exact terms of each item —
> [`dependency-waivers.toml`](dependency-waivers.toml) for advisories,
> [`mutation-survivors.toml`](mutation-survivors.toml) for surviving mutants, and
> [`docs/adr/`](docs/adr/README.md) for every decision behind them. This document exists because a
> machine-readable registry does not tell a reader which items matter, what would clear them, or what
> is holding them back. Where this document and a registry disagree, the registry governs and this
> document is stale.

Every item here is a risk this project has measured and accepted, not one it has overlooked. Each
names what would clear it. Nothing here is a placeholder for work that is merely unfinished — that
lives in [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md).

**Next review date: 2026-09-18.** Every dependency waiver below expires on that date and the
pre-push audit turns red until each is re-reviewed or cleared.

## 1. Dependency advisories in the pinned Agent Mesh runtime

Eleven advisories across five packages, all in the `agent-mesh` domain. The application workspace
reports none.

The constraint that shapes all of them: **Agent Mesh 1.28.7 pins every one of these five packages
exactly**, so no transitive upgrade is available, and 1.28.7 is the latest upstream release, so
there is nothing to upgrade Agent Mesh to. Fixing any of them requires overriding a vendor pin,
which would mean the black-box compatibility suite was no longer testing the pinned wheels.

| Package | Version | Advisories | Fixed upstream in | Why it is not reachable here |
| --- | --- | --- | --- | --- |
| `google-adk` | 1.18.0 | PYSEC-2026-344 | 1.28.1 — unsatisfiable | **See below.** |
| `starlette` | 0.49.1 | PYSEC-2026-161, -248, -249, -2280 | 1.0.1 – 1.3.1 | Upstream Web UI surface only; the owned API validates `Host` itself, takes typed JSON rather than forms, and uses path operation functions |
| `starlette` | 0.49.1 | PYSEC-2026-2281 | 1.1.0 | Windows-specific; the lockfile resolves only for macOS arm64 and Linux aarch64 |
| `cryptography` | 48.0.1 | PYSEC-2026-3552, -3553, -3554 | 49.0.0, 50.0.0 | Need PKCS7 decryption, inbound chain verification, or a name-constrained private certificate authority. None is used |
| `python-multipart` | 0.0.30 | PYSEC-2026-3040 | 0.0.31 | Multipart parsing happens only on the upstream Web UI upload path |
| `setuptools` | 80.10.2 | PYSEC-2026-3447 | 83.0.0 | Packaging-time only, in a project that declares `package = false` and builds nothing |

### The one that matters most

**`google-adk` 1.18.0 / PYSEC-2026-344 is unauthenticated remote code execution** in the agent
runtime every Agent Mesh agent is built on. It is materially more serious than the other ten and it
is the one with no available fix: `google-adk` 1.28.1 requires `google-genai` at or above 1.64.0 and
`fastapi` at or above 0.124.1, while Agent Mesh pins 1.49.0 and 0.120.1 exactly. The override was
attempted and `uv` reports the requirements unsatisfiable
([ADR-0031](docs/adr/0031-reject-the-google-adk-version-override.md)).

What bounds it is the absence of a network path, not the absence of the vulnerability: every Agent
Mesh surface binds to loopback on a single workstation, the reference deployment has no public
ingress or tunnel, and agents may only propose — a deterministic command gateway outside model
control is the sole publisher of executable commands, so code execution inside an agent cannot by
itself authorize a mission action.

**Reassess immediately if any Agent Mesh surface is ever exposed beyond loopback.** That single
change invalidates the compensating control this acceptance rests on.

**Clears when:** an Agent Mesh release ships with `google-adk` at or above 1.28.1.

## 2. Suppressed upstream warnings

The repository escalates every warning to an error. Four classes from the pinned upstream are
exempted, each scoped by message or exact warning class and, in the `agent-mesh` domain, by the
upstream module or source path that emits it
([ADR-0028](docs/adr/0028-untyped-solace-client-boundary.md),
[ADR-0034](docs/adr/0034-scope-agent-mesh-warning-filters-to-upstream-modules.md)).

| Warning | Source | Why it is exempt | Clears when |
| --- | --- | --- | --- |
| `PydanticDeprecatedSince20`, 11 occurrences | Agent Mesh's own models, four modules | Upstream uses Pydantic's class-based `Config`, removed in Pydantic V3 | Agent Mesh migrates to `ConfigDict` |
| `SyntaxWarning: invalid escape sequence` | `solace-pubsubplus` docstrings | Escalated it becomes a `SyntaxError` at import, and fires only on a cold bytecode cache | The client fixes its docstrings |
| `DeprecationWarning: datetime.utcnow()` | `solace/messaging/messaging_service.py` | Evaluated as a default argument, so any import raises it | The client moves to timezone-aware datetimes |
| `RuntimeWarning: ffmpeg or avconv missing` | `pydub`, via `markitdown[all]` | Not a code defect: it made the verdict depend on unrelated system packages | Nothing here converts audio; revisit if that changes |

Two of these bound how long the current pins stay viable: Agent Mesh will not import cleanly under
Pydantic V3, and the Solace client will not compile cleanly once Python promotes the invalid-escape
warning to an error.

**These exemptions rested on the `agent-mesh` domain containing no owned production source.** That
stopped being true when the semantic-configuration validator landed under `agent-mesh/tools/`, so
[ADR-0034](docs/adr/0034-scope-agent-mesh-warning-filters-to-upstream-modules.md) narrowed each
exemption to the upstream module, or for the compile-time `SyntaxWarning` the upstream source path,
that emits it. The same warning raised from owned code under `agent-mesh/tools/` or
`agent-mesh/tests/` is an error again. The four upstream defects and their clearing conditions are
unchanged.

## 3. Lost type checking at the Solace boundary

No Solace or Agent Mesh distribution ships a `py.typed` marker and no stub package exists, so strict
mypy cannot check any call into them
([ADR-0028](docs/adr/0028-untyped-solace-client-boundary.md)). A misspelled method, a wrong argument
count, and a wrong argument type all pass type checking on the boundary that carries every command
and every telemetry event.

The compensating control is confinement: the client must stay behind a fully typed adapter in
`packages/broker`, and no other package may import `solace` directly. `packages/domain` is
additionally forbidden from importing it at all, enforced by both the import-contract gate and a
Ruff banned-api rule.

**Clears when:** upstream ships `py.typed`, or the project writes stubs for the surface the adapter
uses.

## 4. Gates that are red by design

The pre-push tier does not pass on `main`, and this is intended rather than broken. Coverage is
enforced per workspace member with no vacuous pass, so a member holding only a docstring fails
rather than reporting success. The mutation gate fails at preflight for `services/command_gateway`,
the one tier-one member that has no co-located tests yet; `packages/contracts` and `packages/domain`
pass both gates. The remaining lines clear as those members gain tested behaviour; none is a defect
in the gates.

## 5. Owed before the first Agent Mesh configuration

The semantic-configuration validator
[ADR-0032](docs/adr/0032-agent-mesh-semantic-configuration-validator.md) required exists at
`agent-mesh/tools/agent_mesh_config_validator.py` and arms with the first file under
`agent-mesh/configs/`. What is still owed is the local-model lock representation: every `ollama`
model identifier fails `MODEL_LOCK_REQUIRED` until the digest form, its home, and the check are
decided with the first live Ollama configuration
([ADR-0035](docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md)). The rules the validator
enforces more narrowly than ADR-0032 states them are listed under "Known gaps" in
[CONTRIBUTING.md](CONTRIBUTING.md).

**Clears when:** the lock representation is recorded and the validator checks it.

## 6. Instrument definitions and unset parameters

Several service-level targets and operating parameters carry no number yet, and several carry a
number with no defined instrument. They are tracked in their own home, the "Parameters still to be
set" section of [`docs/operating-parameters.md`](docs/operating-parameters.md), and are listed here
only so that a reader of this document knows they exist.
