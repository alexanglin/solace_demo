# Operating parameters

> **Authority:** this document is the single home for every numeric parameter and service-level target, and the instrument that measures each one. `docs/IMPLEMENTATION_PLAN.md` and
> `AGENTS.md` reference it and must not restate it ([ADR-0016](adr/0016-documentation-set-split.md)).
> Where this document and an `Accepted` ADR disagree, the ADR governs.
>
> **Related:** [ADR-0015](adr/0015-tiered-quality-gates.md), [ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md), and [ADR-0023](adr/0023-executable-deep-quality-gates.md) (coverage, maintainability, and mutation thresholds). `scripts/hooks/check-docs-strict.sh` rejects a threshold stated without a number, and this document is the home it names.

A parameter that gates safety behaviour may not be changed without an ADR ([adr/README.md](adr/README.md)). A value still to be determined carries the marker `(provisional -- confirm in Phase 0)` so the strict documentation check can distinguish an open question from an omission.

## Code-quality gates

These limits apply to project-owned source, tests, fixtures, and scripts. The tools run through the
blocking hook and CI entry points documented in [TESTING.md](TESTING.md). Percentages are compared with
integer arithmetic; display rounding never changes a verdict.

| Parameter | Limit | Instrument |
| --- | --- | --- |
| Python Tier 1 statement coverage | 100% per workspace member | `coverage.py` JSON evaluated by `tools/coverage_gate.py` |
| Python Tier 1 branch coverage | 100% per workspace member | `coverage.py` JSON evaluated by `tools/coverage_gate.py` |
| Python Tier 2 statement coverage | 95% per workspace member | `coverage.py` JSON evaluated by `tools/coverage_gate.py` |
| Python Tier 2 branch coverage | 95% per workspace member | `coverage.py` JSON evaluated by `tools/coverage_gate.py` |
| Agent Mesh owned-tooling coverage | 100% statements and 100% branches for `agent-mesh/tools/agent_mesh_config_validator.py` | `pytest-cov` with `--cov-fail-under=100` in `scripts/hooks/agent-mesh-test-full.sh` |
| Python Tier 3 test inventory | At least 1 smoke test and 1 failure-path test per module | Tier inventory gate; Tier 3 fails until this inventory is executable |
| TypeScript coverage | 95% each for statements, branches, functions, and lines per production package | Vitest thresholds carried on the `test:coverage` script, held there by `tools/typescript_policy_gate.py` |
| TypeScript type errors | Zero, whole project | `tsc --noEmit` through `scripts/hooks/dashboard/dashboard-typecheck-full.sh` |
| TypeScript lint findings | Zero at any severity | `eslint --max-warnings 0` through `scripts/hooks/dashboard/dashboard-quality-full.sh` |
| TypeScript compiler options | Every option [ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md) names, at the value it names | `tools/typescript_policy_gate.py` |
| Cyclomatic complexity | At most 8 per function | Ruff `C901` |
| Cognitive complexity | At most 15 per function | Complexipy 7.0.1 |
| Function arguments | At most 5 | Ruff `PLR0913` and `PLR0917` |
| Function branches | At most 10 | Ruff `PLR0912` |
| Function returns | At most 6 | Ruff `PLR0911` |
| Function statements | At most 30 | Ruff `PLR0915` |
| Function locals | At most 12 | Ruff `PLR0914` |
| Nested blocks | At most 3 | Ruff `PLR1702` |
| Boolean expressions | At most 4 operands | Ruff `PLR0916` |
| Duplicated source | At most 3% repository-wide; clone minimum 8 lines and 50 tokens; strict mode | jscpd 5.0.14 |
| Mutation score | At least 90% killed per mutated Tier 1 Python module | `tools/mutation_gate.py` over mutmut 3.7.0 metadata |
| Mutation concurrency | At most 4 child processes | `mutmut run --max-children 4` |
| Survivor review lifetime | More than 0 and at most 30 calendar days | `mutation-survivors.toml` validation |
| Survivor reason | At least 20 Unicode characters | `mutation-survivors.toml` validation |
| Dependency waiver lifetime | More than 0 and at most 30 calendar days | `dependency-waivers.toml` validation |
| Dependency waiver reason | At least 20 Unicode characters | `dependency-waivers.toml` validation |
| Unwaived known advisory | Zero permitted in any audited dependency domain | `tools/dependency_waiver_gate.py` over pip-audit JSON (`--source pip-audit`) and Trivy JSON (`--source trivy`) |
| Image advisory | Reported at every severity, enforced at none; printed as `INFO:` on stdout ([ADR-0055](adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md)) | `tools/dependency_waiver_gate.py --source trivy --domain image:<repository>` over `trivy image` JSON |
| Image pin freshness | Every pulled image's pinned digest equals the digest its tag carries now; zero stale pins permitted | `tools/image_pin_gate.py` over the report `scripts/security/check-image-pins.sh` resolves |
| Blocking deploy misconfiguration | HIGH or CRITICAL check in `FAIL` status, unwaived; zero permitted | `scripts/hooks/deploy/trivy-config-full.sh` over `trivy config deploy` JSON in the `deploy-config` domain |
| Image scan timeout | 20 minutes per image | `trivy image --timeout 20m` in `scripts/security/scan-images.sh` |
| Workflow audit findings | Zero at any severity, offline | zizmor 1.29.0 pre-commit hook over `.github/workflows/` and `.github/dependabot.yml` |
| Continuous-integration job budget | At most 20 minutes per job, every job bounded. Measured 2026-08-20: the complete pre-push stage 2m01s, the image scan 2m58s, CodeQL 1m13s, the commit stage 1m15s | `tools/quality_gate_tests/hooks/test_hook_semantics.py` over every `.github/workflows/*.yml` job |
| Security re-scan cadence | Daily at 06:17 UTC, plus dispatch, every push to `main`, and pull requests touching the audited inputs | `.github/workflows/security.yml` |
| Dependabot updates | Daily at 06:00 UTC; at most 5 open pull requests per ecosystem; a 7-day cooldown | `.github/dependabot.yml`, held by `tools/quality_gate_tests/hooks/test_hook_repairs.py` |
| CodeQL coverage | Python, build mode `none`, on a Linux x64 runner | the `codeql` job of `.github/workflows/security.yml` |
| Directory fan-out | At most 20 files per directory, counted as immediate children only | `tools/directory_fanout_gate.py` over `git ls-files --cached --others --exclude-standard` |
| Fan-out exemption reason | At least 20 Unicode characters | `directory-fanout.toml` validation |
| Compose policy | Every pulled image `name:tag@sha256:` with a 64-hex digest; every published port on `127.0.0.1`; secrets by file or indirection; a healthcheck on every service; the broker's `shm_size`, `nofile`, certificate path, and TLS port; `SOLACE_DEV_MODE=false` on Agent Mesh | `tools/compose_policy_gate.py` over `deploy/` compose files and Dockerfiles |

## Canonical serialization bounds

The bounds a digest-covered payload must satisfy. The rules that use them are in
[CONTRACTS.md](CONTRACTS.md#canonical-serialization) and the decision is
[ADR-0027](adr/0027-integer-only-canonical-serialization.md). Every row is enforced twice: by the
`packages/contracts` canonicalizer, which rejects a violating value, and by the versioned JSON Schemas,
which the offline contract-artifact gate validates against positive and negative golden fixtures.

| Parameter | Limit | Instrument |
| --- | --- | --- |
| Admissible numeric type | Integer only; no floating-point value is representable | `packages/contracts` canonicalizer |
| Integer magnitude | At most 9007199254740991, that is 2^53 - 1, so a TypeScript `number` is exact | `packages/contracts` canonicalizer |
| Latitude | -90000000 to 90000000 microdegrees | JSON Schema plus canonicalizer |
| Longitude | -180000000 to 180000000 microdegrees | JSON Schema plus canonicalizer |
| Coordinate resolution | 1 microdegree, which is 0.111 m at the equator | Fixed by the microdegree unit |
| Evidence score | 0 to 100 hundredths | JSON Schema plus canonicalizer |
| Instant precision | Exactly 3 fractional-second digits, UTC, literal `Z` | JSON Schema plus canonicalizer |
| Object key form | `^[a-z][a-zA-Z0-9]*$`, 1 to 64 characters | `packages/contracts` canonicalizer |
| String value length | At most 4096 bytes when UTF-8 encoded | `packages/contracts` canonicalizer |
| Canonicalization version | Integer 1, carried inside the hashed bytes | `packages/contracts` digest function |
| Digest | SHA-256 rendered as 64 lowercase hexadecimal characters | `packages/contracts` digest function |

## Topic and envelope bounds

The grammar that uses these bounds is in [CONTRACTS.md](CONTRACTS.md#topic-taxonomy) and
[CONTRACTS.md](CONTRACTS.md#event-envelope); the decisions are
[ADR-0036](adr/0036-ascii-topic-grammar-bound-to-event-type.md) and
[ADR-0037](adr/0037-cloudevents-envelope-profile.md).

| Parameter | Limit | Instrument |
| --- | --- | --- |
| Published topic length | At most 250 bytes of UTF-8, the Solace SMF limit; the grammar's longest output is 232 bytes | `packages/contracts` topic parser, plus a formatting proof test |
| Topic levels | At most 8, against Solace's limit of 128 | Fixed by the family templates |
| Identifier length | 1 to 64 characters | `packages/contracts` topic grammar plus JSON Schema |
| Kind length | 1 to 32 characters | `packages/contracts` topic grammar plus JSON Schema |
| Agent name length | 1 to 64 characters | `packages/contracts` topic grammar plus JSON Schema |
| Producer identifier in `source` | 1 to 64 characters | `packages/contracts` envelope validator plus JSON Schema |
| Sequence | Exactly 15 decimal digits, zero-padded; the maximum is below 2^53 - 1 | `packages/contracts` envelope validator plus JSON Schema |
| Trace state | 1 to 512 printable ASCII characters | `packages/contracts` envelope validator plus JSON Schema |

## Telemetry payload bounds

The drone telemetry payload, `schemas/v1/payload/drone-telemetry.schema.json`, carries integers only and
the coordinate rows above apply to it unchanged. Widening any row is a schema change and therefore a new
major version ([CONTRACTS.md](CONTRACTS.md#event-envelope)).

| Parameter | Limit | Instrument |
| --- | --- | --- |
| Battery | 0 to 100 percent | JSON Schema plus the ingress validator |
| Altitude | -500 to 20000 metres above mean sea level | JSON Schema plus the ingress validator |
| Heading | 0 to 359 degrees clockwise from true north | JSON Schema plus the ingress validator |
| Ground speed | 0 to 10000 centimetres per second | JSON Schema plus the ingress validator |

## Workload and service-level profile

Use a versioned acceptance workload so performance and delivery claims are reproducible. These are initial release targets; changing them requires measured evidence and a recorded decision.

| Measure | Initial target |
| --- | --- |
| Fleet telemetry | 23 drones at 1 Hz; each telemetry event at most 2 KiB |
| Dashboard freshness | Broker receipt to rendered state p95 at most 1 second |
| Connected command path | Gateway acceptance/result p95 at most 2 seconds, excluding model inference |
| Offline detection | State changes to `OFFLINE` within 6 seconds of the configured heartbeat loss |
| Agent replan | Warm-model recommendation or explicit abstention within 30 seconds |
| Disconnect fault | 60-second edge disconnect with zero missing critical IDs and zero duplicate side effects within the declared storage/spool envelope |
| Backlog recovery | 500 critical messages drain within 10 seconds after reconnect |
| Restart recovery | RPO 0 for approvals and critical commands; readiness restored within 30 seconds, excluding model warm-up |
| Replay determinism | Identical hash of the canonical reduced dashboard state across 10 runs. Raw event streams are not compared: event IDs and timestamps legitimately differ between runs ([ADR-0009](adr/0009-isolated-side-effect-free-replay.md)) |
| Safety | Zero authorized actions across all approval-bypass attempts |
| Soak | 30 minutes with no unbounded process, queue, or SSE-client memory growth |

## Dashboard event stream

The normalized dashboard event and the reduced state it folds into are defined by
[ADR-0067](adr/0067-normalized-dashboard-events-and-reduced-state.md); the shapes live in
[CONTRACTS.md](CONTRACTS.md#dashboard-event-stream). A dashboard event carries no transport member,
so these bounds are about back-pressure, not about the envelope.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Per-client buffer | 256 dashboard events | `MAX_BUFFERED_EVENTS` in `packages/contracts`, asserted by its unit tests |
| Droppable classes | `TELEMETRY` only | `DROPPABLE_CLASSES` in `packages/contracts`; every other class is never dropped |
| Buffer overflow behaviour | Discard droppable events oldest-first; if the buffer is still full, close the stream with a typed reason and let the client re-synchronize from a state snapshot | Failure-injection test against a client slower than the telemetry rate |
| Reduced-state digest | SHA-256 over the canonical state document under the `replay-state` context | `digest.Context.REPLAY_STATE` in `packages/contracts` |

## Connectivity detection

The heartbeat is a dedicated liveness signal, not an inference from the telemetry stream: routine telemetry
uses direct delivery and may be dropped under congestion, so absence of telemetry is not evidence of
absence of the drone. Transitions are counted in consecutive missed heartbeat intervals rather than as a
wall-clock gap, which gives hysteresis and makes the behaviour reproducible under a deterministic clock. The
healthy state is `CONNECTED` and every drone starts there; one recovery count returns both impaired states
to it ([ADR-0039](adr/0039-drone-connectivity-states-and-recovery.md)).

| Parameter | Value | Notes |
| --- | --- | --- |
| Heartbeat interval | 1 s (provisional -- confirm in Phase 0) | Matches the telemetry rate so no extra timer is needed |
| Consecutive misses to enter `DEGRADED` | 3 (provisional -- confirm in Phase 0) | |
| Consecutive misses to enter `OFFLINE` | 6 (provisional -- confirm in Phase 0) | Consistent with the 6-second offline-detection target above |
| Consecutive heartbeats to return from `DEGRADED` or `OFFLINE` to `CONNECTED` | 2 (provisional -- confirm in Phase 0) | Prevents flapping on a marginal link |

## Local stack

The runtime layout is in [ARCHITECTURE.md](ARCHITECTURE.md#deployment-layout); the decisions are
[ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md), [ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), and [ADR-0046](adr/0046-generated-local-certificate-authority.md). Every image is pinned by its multi-architecture index digest, verified
against Docker Hub on 2026-08-20, and the compose policy gate refuses a pull that is not. Every image
below, and the two images the Dockerfiles build, is scanned by Trivy on each daily run
([ADR-0048](adr/0048-scan-images-and-deploy-configuration-with-trivy.md)).

| Parameter | Value | Instrument |
| --- | --- | --- |
| Broker image | `solace/solace-pubsub-standard:10.26.0.8799` at `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f` | `deploy/compose.yaml`, held by the compose policy gate |
| Postgres image | `postgres:18.6-trixie` at `sha256:06cad38a5d9f5d24b4d83d86def30795d5e4b757fedbf5281172b576dedcd941` | `deploy/compose.yaml`, held by the compose policy gate |
| Postgres data directory | `/var/lib/postgresql/18/docker`, inside the `/var/lib/postgresql` volume the image declares ([ADR-0060](adr/0060-postgresql-18-and-its-data-directory-layout.md)) | `show data_directory` on the running cluster; the named volume holds `18/docker/PG_VERSION` |
| Agent Mesh base image | `solace/solace-agent-mesh:1.28.7` at `sha256:25dc09b55e8a718e5a690e4abba039cbd032872cd6d4c402b7c69d1dead70255` | `deploy/agent-mesh/Dockerfile` |
| Application base image | `python:3.14.7-slim-trixie` at `sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4` | `deploy/application/Dockerfile` |
| Event Management Agent image | `solace/event-management-agent:1.9.9` at `sha256:c5f3d9bf711dd051c14b162f10ecdbd7f3f7a85306d16c438c92229719123c5b`, `linux/amd64` | `deploy/compose.yaml` |
| Broker shared memory | 1 GiB | `shm_size`, from Solace's single-node template |
| Broker open-file limit | 2448 soft, 1048576 hard | `ulimits.nofile`, from Solace's single-node template |
| Broker connection scaling | 100 connections, the same ceiling as the Developer-class showcase service | `system_scaling_maxconnectioncount` |
| Broker start period | 90 s before the first healthcheck counts, then 30 probes at 10 s. Measured 2026-08-21: both services healthy 40.75 s after `up --wait`, including both image pulls | `healthcheck` in `deploy/compose.yaml`; [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Published ports, all on `127.0.0.1` | 55443 SMF over TLS, 1943 SEMP over TLS, 5432 Postgres, 8000 Agent Mesh Web UI, 8080 dashboard API, 8180 Event Management Agent | compose policy gate refuses any other binding |
| Certificate validity | 365 days for the authority and the broker certificate | `scripts/broker-secrets.sh`; `just rotate-secrets` renews |
| Generated secret length | 32 random bytes, rendered as 64 hexadecimal characters | `scripts/broker-secrets.sh` |
| Docker Desktop memory allocation | 7.652 GiB on the reference workstation, measured 2026-08-21 | [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Default profile memory at rest | 1.58 GiB: broker 1.543 GiB, Postgres 35.71 MiB | [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Docker Desktop memory for the default and `mesh` profiles | 2.16 GiB: broker 1.575 GiB, Agent Mesh 556.8 MiB, Postgres 31.47 MiB, measured 2026-08-21 | [`release-evidence/phase-0/mesh-first-run.md`](../release-evidence/phase-0/mesh-first-run.md). The `services` and `event-portal` profiles are still unmeasured |
| Broker connections opened by the four Agent Mesh apps | 9, all on one client username, measured 2026-08-21 against a Message VPN ceiling of 100 | [`release-evidence/phase-0/mesh-first-run.md`](../release-evidence/phase-0/mesh-first-run.md) |
| Fleet connection count against the Developer-class limit of 100 | (provisional -- confirm in Phase 0) | Phase 0 measurement on the showcase service. The row above is the first datum: connections exceed identities by a large factor |

## Agent Mesh runtime

The A2A namespace is [ADR-0064](adr/0064-fix-the-agent-mesh-a2a-namespace.md), the Web UI's admission
to the configuration validator is [ADR-0065](adr/0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md),
and the local-model lock is [ADR-0063](adr/0063-lock-local-models-by-manifest-digest.md). This section
carries only the values.

| Parameter | Value | Instrument |
| --- | --- | --- |
| A2A namespace | `aerial-rescue-mesh`, one level, no trailing slash | `NAMESPACE` in `.env.example`; a gate test holds it equal to the subscription `a2a_subscription()` renders |
| A2A topic exceptions after the grant | 47, up from 41 while the namespace was blank | the provisioner's own report, recorded in [`release-evidence/phase-0/mesh-first-run.md`](../release-evidence/phase-0/mesh-first-run.md) |
| Management server port and readiness path | 8080 and `/readyz`, declared in exactly one configuration file | the `agent-mesh` healthcheck in `deploy/compose.yaml`; a test asserts the single declaration |
| Agent card publishing interval | 30 s per agent | `agent_card_publishing.interval_seconds` in each agent configuration |
| Web UI allowed browser origins | loopback only; a wildcard, an empty list, or any other host is refused | the `WEBUI_EXPOSURE` rule in the configuration validator |
| Local model digest form | `sha256:` and 64 lowercase hexadecimal characters, the Ollama manifest digest | the `MODEL_LOCK` rule offline; `GET /api/tags` at readiness, which is still owed |
| Orchestration model for the Phase 0 spike | `ollama_chat/qwen3:4b`, 2.50 GB resident, reporting `completion`, `tools`, `thinking` | `agent-mesh/model-lock.toml`. Provisional: the roles are pinned by the Phase 4 model selection |

## Broker authorization

The roles, their grants, and the deny-by-default rule are
[ADR-0061](adr/0061-least-privilege-broker-principals-and-topic-authorization.md). The matrix itself
lives in `packages/domain/src/aerial_rescue_domain/principals.py`, where a test asserts it is total
over the roles and names every family's publisher set; this section carries only the numbers.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Authorization roles | 9, one ACL profile each | `Principal` in `packages/domain`; a test asserts the nine names |
| Role name bound | at most 32 characters and inside the topic kind form, because the name is also the ACL profile name | `MAX_KIND_LENGTH` and `KIND_PATTERN` from `packages/contracts`, asserted per role |
| ACL profile default actions | `disallow` for publish topic, subscribe topic, and subscribe share name; `allow` for client connect | `packages/broker/src/aerial_rescue_broker/provisioning.py`, asserted per profile |
| Topic exceptions written | 16 publish and 31 subscribe across the nine profiles, the A2A grant included | asserted by the apply test in `packages/broker/tests/test_provisioning.py` |
| SEMP request timeout | 10 s per call | `REQUEST_TIMEOUT_SECONDS` in `packages/broker/src/aerial_rescue_broker/semp.py` |
| SEMP retry count | 0. A topic-exception `POST` is not idempotent, so re-running the whole convergent apply is the retry | `RETRY_COUNT` in the same module, asserted by a transport test |

## Model spend budget

Enforced before each paid call, not reconciled afterwards. A persisted ledger in the durable store records
every call by `(provider, model, role, tranche)`. Unit prices are committed as versioned data with their
retrieval date and source, because a rate from a third-party aggregator is not a first-party fact. See
[ADR-0002](adr/0002-paid-orchestration-under-enforced-budget-cap.md).

| Parameter | Value | Notes |
| --- | --- | --- |
| Total cap, all providers | USD $50.00 | Hard ceiling. Raising it is a gating-parameter change and needs an ADR |
| Phase 0 model evaluation tranche | USD $10.00 | Runs on the batch API at 50% of standard cost |
| Development and debugging tranche | USD $20.00 | |
| Acceptance and release tranche | USD $15.00 | |
| Reserve tranche | USD $5.00 | Released only by an explicit recorded decision |
| Warning threshold | 80% of each tranche | Surfaces on the dashboard health indicators |
| Per-run cap | (provisional -- confirm in Phase 0) | Bounds a single run, so a retry loop cannot drain a tranche |
| Minimum input-cache hit rate | (provisional -- confirm in Phase 0) | Asserted per provider; a near-zero rate means paying full price for a prefix that should have been cached |

On exhaustion, readiness refuses to start a paid-mode run and model-dependent work abstains. Exhaustion
never substitutes recorded evidence and never affects the approval boundary.

## Local operator credential

The dashboard API generates this bearer from the operating system's cryptographically secure random
source at each API process start. It exists only for that process lifetime and is never persisted, reused,
or logged. The routes and header rules are defined by
[ADR-0024](adr/0024-local-operator-api-boundary.md) and [CONTRACTS.md](CONTRACTS.md#local-http-api).

| Parameter | Value | Notes |
| --- | --- | --- |
| Bearer credential entropy | At least 256 bits | Fresh independent randomness on every API process start |

## Approval timing

| Parameter | Value | Instrument |
| --- | --- | --- |
| Approval time-to-live | 60 s, measured from the operator's decision reading to the gateway's consumption on both clocks ([ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md), [ADR-0042](adr/0042-approval-time-to-live.md)) | `packages/domain` takes it as an injected parameter with no default; the composition root supplies `timedelta(seconds=60)` |

The window is derived from the service-level rows above rather than measured: a 30 s restart
recovery, a 10 s backlog drain, and a 2 s connected command path give a documented worst case of
42 s and leave 18 s of margin. Moving it is a superseding record, because the parameter gates safety
behaviour.

## Parameters still to be set

Each of these is required by a claim made elsewhere and has no value yet. Every row is a gap, not a
preference, and must carry a number before the release run.

| Parameter | Required by | Status |
| --- | --- | --- |
| Per-drone outbox maximum records and bytes | The bounded-outbox claim in [CONTRACTS.md](CONTRACTS.md) | open |
| Outbox overflow behaviour | A critical-record overflow must refuse the write and emit a continuity-breach audit record; a critical record is never silently dropped | decided, unquantified |
| Queue maximum spool, maximum redelivery, message TTL, dead-message-queue target | The no-loss claim's fault envelope | open |
| Command send budget: how many times the gateway may put one command on the wire | The bounded retry policy in [CONTRACTS.md](CONTRACTS.md) and the `ABANDONED` state of [ADR-0074](adr/0074-command-dispatch-lifecycle.md) | open |
| Evidence band boundaries: the lower bound in hundredths of the weak, supported, and corroborated bands | The band-keyed escalation eligibility in [LIMITATIONS.md](LIMITATIONS.md) and [ADR-0076](adr/0076-evidence-score-bands.md). The two-source corroboration floor is structural rather than numeric and is not open | open |
| Ollama `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` | Warm-model residency across missions | open |
| Instrument definition per service-level row: start point, end point, clock, sample count, statistic, warm-up discarded, machine-state precondition | Every row of the table above | open |
