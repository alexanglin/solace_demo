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
pre-push audit turns red until each is re-reviewed or cleared. On the same date, read the last daily
run of `.github/workflows/security.yml` and the repository's code-scanning alerts: a red daily run
and a CodeQL alert page nobody reads are not controls
([ADR-0050](docs/adr/0050-scan-python-with-codeql-in-continuous-integration-only.md),
[ADR-0051](docs/adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md)).

## 1. Dependency advisories in the pinned Agent Mesh runtime

Eleven advisories across five packages, all in the `agent-mesh` domain, are waived; a twelfth,
against `asteval`, is overridden rather than waived (below). The application workspace reports none.

The constraint that shapes all of them: **Agent Mesh 1.28.7 pins every one of these five packages
exactly**, so no transitive upgrade is available, and 1.28.7 is the latest upstream release, so
there is nothing to upgrade Agent Mesh to. Fixing any of them requires overriding a vendor pin.
[ADR-0031](docs/adr/0031-reject-the-google-adk-version-override.md) rejected the override that
would have replaced three pins, and
[ADR-0047](docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md) accepted the one that
replaces a single leaf package with no dependents and an unchanged import surface, so the rule is a
test each advisory is put to, not a prohibition.

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

### Overridden rather than waived: `asteval`

`asteval` 1.0.6, pinned exactly by Agent Mesh, carried CVE-2026-55244 — a sandbox escape in the
`Interpreter` that Agent Mesh feeds math embeds taken from model output. `agent-mesh/pyproject.toml`
overrides it to 1.0.9, and `agent-mesh/tests/test_pinned_runtime_overrides.py` proves the overridden
wheel against the pinned runtime on every push
([ADR-0047](docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md)). The debt is the
divergence: the verification environment's `asteval` is not the one the vendor declares, and the
official container image still carries 1.0.6 (section 6).

**Clears when:** an Agent Mesh release declares `asteval>=1.0.9`. The vendor-pin test turns red that
day, and deleting the override line turns it green.

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

## 4. What the gate modules still owe

The pre-push tier passes on `main`. It did not until 2026-08-20, when two changes closed the gap that
[ADR-0019](docs/adr/0019-fail-closed-quality-gates.md) had left open by design. Five workspace members
are scaffolds — a manifest, a docstring-only module, a `py.typed` marker — and the coverage and
mutation gates now report them as `SCAFFOLD` rather than failing them, because a member with nothing
to measure proves nothing either way
([ADR-0053](docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md)). The root
tooling member, which had sat at 85% statements and 81% branches against its Tier 2 threshold of 95%,
was characterized to 98.26% and 96.84% and passes.

What remains is one module and its two entry points:

| Item | Why it is accepted for now | Clears when |
| --- | --- | --- |
| `tools/aaa_checker/checker.py` at 92% statements and 91% branches, with `gate.py` at 74% and `__main__.py` uncovered | The checker's own conformance suite under `tools/aaa_checker/tests/` exercises the parser on positive and negative sources and runs before every tree scan, so the untested lines are diagnostic and entry-point paths rather than parsing rules | The three modules reach 100% in the per-member measurement, or a recorded decision states which paths are unreachable |

`packages/contracts` and `packages/domain` pass the coverage and mutation gates at 100% statements,
100% branches, and 882 of 882 mutants killed. A scaffold that gains its first executable statement or
test file becomes an active member immediately and is measured against its declared tier.

## 4b. Advisories inside the pinned third-party images

The first image scan, on 2026-08-20, reported **307 distinct HIGH and CRITICAL advisories with published
fixes** across the seven images: 63 in `solace/event-management-agent`, 58 in `postgres`, 48 each in
`solace/solace-agent-mesh` and the derived `aerial-rescue/agent-mesh`, 38 each in `python` and the derived
`aerial-rescue/application`, and 14 in `solace/solace-pubsub-standard`. Roughly 180 are the `util-linux`
family of Debian packages, 51 are the Go standard library inside the Solace images, and the rest are
Python packages in the vendor's own virtual environment.

None of them is waived, because none of them is actionable. Every pinned digest was the newest its tag
carried on that date, 3.14.7, 17.11, and 1.28.7 were the newest tags then (the durable store has
since moved to 18.6, [ADR-0060](docs/adr/0060-postgresql-18-and-its-data-directory-layout.md)), and the two derived images inherit
their operating-system packages wholesale from a base this project pins rather than builds. The fix for
all 307 arrives the same way: a publisher rebuilds an image, and the pin check of
[ADR-0055](docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md) turns red until the digest
is bumped.

**This is the risk of running pinned third-party images, and it is accepted knowingly rather than
signed away.** No waiver claims any of these has been reviewed for reachability; the scan prints all of
them on every run and the daily workflow keeps the list current.

**Clears when:** each publisher rebuilds. The pin check is the instrument, and it needs no human to
notice.

## 5. Owed after the first Agent Mesh configuration

The local-model lock representation that
[ADR-0035](docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md) demanded is now recorded and
enforced: [ADR-0063](docs/adr/0063-lock-local-models-by-manifest-digest.md) fixes the digest form, its
home in `agent-mesh/model-lock.toml`, and the comparison. `MODEL_LOCK_REQUIRED` no longer refuses every
local identifier; it means "not listed in the lock". What that record could not do, and what remains
owed, is below.

| Item | Why it is accepted for now | Clears when |
| --- | --- | --- |
| The digest is never compared against a running daemon | Ollama is addressable only as `name:tag`, so the offline gate can prove membership and form but not that the bytes are present. A re-pulled tag is caught by no gate today | A readiness check reads `GET /api/tags` and refuses a local-model run whose digest differs from the lock |
| `ollama_chat/qwen3:4b` is a spike input, not a measured choice | It was selected for tool capability at 2.50 GB. The `general` and `planning` roles are still an open question in [docs/adr/README.md](docs/adr/README.md) | The Phase 0 evaluation measures capability per dollar and Phase 4 pins the three models |
| The A2A namespace refusal rules exist twice | The configuration validator runs on Python 3.13 and `packages/broker` on 3.14 ([ADR-0029](docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)), so they cannot share code. Only `packages/broker` enforces them today; the validator does not check `namespace` at all | A committed data file both interpreters read, or a decision that one side alone is authoritative |
| Configuration environment references are checked in the wrong scope | The validator resolves `${...}` against the host-scope `.env.example`, while the runtime resolves them inside the container. **Half-closed 2026-08-21:** `AgentMeshContainerScopeTests` in `tools/quality_gate_tests/deploy/test_broker_identity_wiring.py` now reads every `${SOLACE_*}` the mounted configuration names and fails unless the compose service passes each one in. It caught the two the Event Mesh Gateway added. What remains is the other direction and the non-broker names: the check lives in the root gate rather than in the validator, and covers only the `SOLACE_` prefix | The validator itself checks every reference against what `deploy/compose.yaml` passes into the container |

ADR-0035's **second** refusal still stands and is unaffected: every `tool_type: python` other than the
pinned `sam_event_mesh_tool.tools:EventMeshTool`, and every `app_package`, `app_base_path`, or
alternate loader field, is refused until an owned-plugin registry exists under `agent-mesh/plugins/`.

**Clears when:** the readiness digest comparison exists and the model roles are pinned.

## 6. Container stack defined but not yet exercised

[ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) put every component
except Ollama under `deploy/compose.yaml`, and the compose policy gate proves the file's text conforms.
The default profile has now been started and recorded in
[`release-evidence/phase-0/first-live-run.md`](release-evidence/phase-0/first-live-run.md), which
cleared two rows below. What the profiles that remain unstarted leave unproven, each a design choice
rather than a measurement:

| Item | Why it is accepted for now | Clears when |
| --- | --- | --- |
| ~~The Agent Mesh container carries Python 3.13.11 while verification runs on 3.13.15~~ **Cleared 2026-08-21:** `scripts/probes/agent-mesh-image-probe.sh` runs the pinned-plugin checks inside the built image on its own CPython 3.13.11 — three pins, the gateway entry point, the tool's module-path import, and seven runtime symbols — with no network ([event-mesh-gateway-first-run.md](release-evidence/phase-0/event-mesh-gateway-first-run.md)) | -- | Cleared |
| ~~No durable queue exists, so guaranteed delivery has no endpoint~~ **Cleared 2026-08-23:** the four parameters are derived from the declared fault envelope and 22 queues are live on the container, with spooling, acknowledgement, rejection, the redelivery bound, and queue ownership all asserted against the broker ([ADR-0080](docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md), [guaranteed-delivery-first-run.md](release-evidence/phase-2/guaranteed-delivery-first-run.md)) | -- | Cleared |
| A salient event published while the Agent Mesh is down never reaches an agent, and nothing observes the loss | Event Mesh Gateway 1.1.0 names and binds its own temporary data-plane queue in `component.py`; the name, `create_queue_on_start`, and `temporary_queue` are literals absent from `app_schema`, so no configuration changes it, and [ADR-0007](docs/adr/0007-solace-first-implementation-policy.md) forbids forking a supported component without a proving test. Bounded by what does not depend on it: the application topic is the authoritative record, and no command, approval, or audit record runs through the gateway ([ADR-0071](docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)) | Upstream makes the endpoint configurable, or an owned consumer on a durable queue invokes the mesh with a proving test behind it |
| ~~The three Agent Mesh roles hold no A2A grant~~ **Cleared 2026-08-21:** [ADR-0064](docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md) fixed the namespace and the provisioner wrote the six withheld exceptions, taking the broker from 41 to 47 | -- | Cleared |
| Recreating the Agent Mesh container before the provisioner has applied a changed matrix leaves it unhealthy | The Event Mesh Tool's reply subscription is refused with `SOLCLIENT_SUBCODE_SUBSCRIPTION_ACL_DENIED`, and the reason is visible only by reading the container's log; `just up` and `just provision` are independent recipes with no declared order ([event-mesh-tool-first-run.md](release-evidence/phase-0/event-mesh-tool-first-run.md)) | A single entry point applies the matrix and then brings the profile up, or the healthcheck reports the denial |
| Command intake settles after publisher confirmation rather than after a durable commit | `packages/store` now has all three repositories [ADR-0006](docs/adr/0006-proposal-bound-single-use-approvals.md)'s atomic set needs, proven together on a database a probe creates and drops -- but **nothing calls them**: no workspace member declares the package as a dependency, nothing applies the schema to the persistent database, and a simulated drone has no durable effect for exactly-once to protect, so `FleetState` is authority for nothing durable. The receipts that recognise a known command identifier are process-local, so a restart between publishing a result and settling its command yields a redelivery the process re-answers. The claim is **at-least-once with duplicates possible across a restart** ([command-dispatch-first-run.md](release-evidence/phase-3/command-dispatch-first-run.md)) | A gateway opens the transaction these repositories are for, and intake settles after it commits |
| The fleet simulator has no process entry point, so its Compose service is still a shell | A composition root needs a scenario, and [ADR-0077](docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) leaves producing one to the scenario service, which is a scaffold with no broker identity by decision. The root is exercised by its own suite and by [fleet-simulator-first-run.md](release-evidence/phase-3/fleet-simulator-first-run.md) instead | The scenario service produces a validated scenario and the member declares a console script |
| A denied *direct* subscription is silent to the client | `SolaceReceiver` raises nothing and simply receives nothing, so a test asserting "nothing arrived" can pass for the wrong reason. The guaranteed path raises, which is why `tests/security/` uses an acknowledged publish. A refused *queue* binding is now loud: `SolacePersistentReceiver` turns it into `BIND_REFUSED` naming the queue, proven live against a role that holds the topic grant and is not the owner | The adapter surfaces the transport's subscription error, or every denial assertion binds a queue |
| The Event Management Agent runs emulated and reaches SEMP in plaintext inside the network | amd64-only image; the plaintext port is never published; the profile never gates | A Java truststore path for the per-checkout authority is proven live |
| The fleet's connection count against the showcase service is unmeasured | The `mesh` profile's cost is now measured at 2.16 GiB for the whole stack, and four apps were seen to open nine broker connections against a ceiling of 100. The showcase service itself has not been touched | The showcase measurement lands in `docs/operating-parameters.md` |
| The official Agent Mesh image's `/opt/venv` carries `asteval` 1.0.6 | The override in [ADR-0047](docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md) changes the lock, not upstream's image; Trivy reports the finding at MEDIUM, below the blocking threshold, so every daily scan prints it as information | Upstream publishes an image with `asteval` at or above 1.0.9 |
| Neither Dockerfile declares a `HEALTHCHECK`, so `trivy config` reports `DS-0026` at LOW on both | The compose policy gate requires the healthcheck in `deploy/compose.yaml`, which is where Compose reads it; the finding is informational on every pre-push run | A recorded decision settles where the healthcheck lives |

**Broker authorization landed on 2026-08-21** and is recorded in
[`release-evidence/phase-0/broker-authorization.md`](release-evidence/phase-0/broker-authorization.md).
Before it, an identity that did not exist could publish an executable rescue-escalation command; the
factory `default` client username is now disabled and every owned ACL profile denies by default. The
two rows it adds above are what it did not settle.

**Two rows cleared on 2026-08-21.** The broker image carries `/usr/bin/curl` 7.76.1 and the container
reached healthy, so the healthcheck's assumption is measured rather than argued and the `/dev/tcp`
fallback is unnecessary. `scripts/broker-secrets.sh` generated a working authority under macOS
LibreSSL 3.3.6, and the nine tests that drive it now pass on the Linux runner against OpenSSL — which
they could not do before, because the job they run in had never completed
([ADR-0059](docs/adr/0059-keep-the-verification-authority-able-to-report.md)).

**The `mesh` profile was started on 2026-08-21** and is recorded in
[`release-evidence/phase-0/mesh-first-run.md`](release-evidence/phase-0/mesh-first-run.md), which also
lists three defects the run found and what it does not settle. One new row belongs here: a
bind-mounted configuration change does not restart the container, so `up --wait` reports the old
container healthy and `--force-recreate` is required.

**Twenty-two durable queues were created on 2026-08-23** and are recorded in
[`release-evidence/phase-2/guaranteed-delivery-first-run.md`](release-evidence/phase-2/guaranteed-delivery-first-run.md).
That run cleared the queue row above and found three things no offline test could: the dead-message
queue refuses `maxRedeliveryCount` and `maxTtl`, a queue's `ingressEnabled` and `egressEnabled` both
default to `false`, and the monitor member `spooledMsgCount` is cumulative rather than a depth. What
it did not settle is carried in that record's closing section.

**Clears when:** the `services` and `event-portal` profiles are started and recorded under
`release-evidence/`.

## 7. Instrument definitions and unset parameters

Several service-level targets and operating parameters carry no number yet, and several carry a
number with no defined instrument. They are tracked in their own home, the "Parameters still to be
set" section of [`docs/operating-parameters.md`](docs/operating-parameters.md), and are listed here
only so that a reader of this document knows they exist.
