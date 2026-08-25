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
| TypeScript coverage | 95% each for statements, branches, functions, and lines per production package | Vitest V8 JSON summary plus exact source inventory, recomputed with integer arithmetic by `tools/typescript_coverage_gate.py`; manifest thresholds held by `tools/typescript_policy_gate.py` |
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
| Command-gateway reply metadata | At most 4096 bytes of UTF-8 | `MAX_REPLY_METADATA_BYTES` in `services/command_gateway`, checked before the value is parsed |

The reply-metadata bound is derived rather than measured. The value is Solace AI Connector's own
correlation stack, one entry per requestor in a delegation chain, and an entry is a `request_id` of
at most 64 characters and a `response_topic` of at most 250 — under 340 bytes with its punctuation.
The bound therefore admits a chain of at least ten requestors, which is far beyond the two the
architecture has, and it is checked before the value is parsed so an oversized string is refused
rather than decoded. It is not a safety parameter: exceeding it costs one answer, never a command
([ADR-0070](adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)).

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
| Backlog recovery | 500 critical messages drain within 10 seconds after reconnect. **Measured 2026-08-23: 7.141 s at worst over three samples** ([backlog-recovery-first-run.md](../release-evidence/phase-2/backlog-recovery-first-run.md)) |
| Restart recovery | RPO 0 for approvals and critical commands; readiness restored within 30 seconds, excluding model warm-up |
| Replay determinism | Identical hash of the canonical reduced dashboard state across 10 runs. Raw event streams are not compared: event IDs and timestamps legitimately differ between runs ([ADR-0009](adr/0009-isolated-side-effect-free-replay.md)) |
| Safety | Zero authorized actions across all approval-bypass attempts |
| Soak | 30 minutes with no unbounded process, queue, or SSE-client memory growth |

The prepared wilderness dashboard workload adds two Phase 3 acceptance targets. They are planned, not
measured evidence; R8 must update their status only after the committed scenario and fleet runtime produce
the result.

| Prepared dashboard measure | Acceptance target | Instrument and current status |
| --- | --- | --- |
| Scenario execution | Exactly 14 ticks | The R8 fleet-control integration test reads the completed-tick count for the committed scenario; not yet implemented or measured |
| Fleet telemetry publication | Exactly 280 publications: 20 simulated members over 14 ticks | The R8 fleet publisher counter is asserted independently from best-effort recorder receipt; not yet implemented or measured |

The fleet-telemetry rate is the one row with a partial instrument. The fleet simulator's tick loop
keeps the interval its scenario declares, measured from the start of each tick, and tallies every
tick that could not finish inside it as `OVERRAN` in `ServeReport.pacing`
([ADR-0083](adr/0083-pace-the-tick-loop-at-a-fixed-rate.md)). That makes the rate falsifiable by a
run report rather than unmeasurable. The rest of this row's instrument -- sample count, statistic,
warm-up, and machine-state precondition -- stays open below.

The backlog-recovery row has a complete instrument, and it is the only row that does:
[ADR-0084](adr/0084-give-backlog-recovery-an-instrument.md) fixes its start point, end point, clock,
workload, fleet size, sample count, statistic, discarded warm-up, and machine-state precondition,
and `tests/integration/test_backlog_recovery_live.py` implements it. The record defines the
measurement; it does not move the target.

## Scenario catalog files

The file contract is fixed by
[ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) and described in
[CONTRACTS.md](CONTRACTS.md#scenario-catalog-files). Schema-expressible bounds are held by the scenario
schemas and root contract tests. Byte, nesting, path, and raw-decoder bounds become executable at the R2
loader boundary and are not yet runtime evidence.

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Production scenario artifacts | One catalog at `scenarios/catalog.v1.json` and one revision-one definition at `scenarios/v1/wilderness-missing-person.r1.json` | R2 loader inventory and digest tests; production files are not yet committed |
| Catalog and definition size | At most 256 KiB each | R2 raw-byte loader boundary test; not yet implemented |
| Document nesting | At most 16 nested containers | R2 raw-document depth test before model construction; not yet implemented |
| Catalog entries | At most 20 | `catalog.schema.json` and root schema contract tests |
| Declared members per definition | At most 64 | `definition.schema.json` and root schema contract tests |
| Heartbeat-loss ordinals per definition | At most 4,096 | `definition.schema.json` and root schema contract tests |
| Definition integrity | SHA-256 of the selected definition bytes, rendered as 64 lowercase hexadecimal characters | R2 catalog digest and file-replacement tests; not yet implemented |
| Prepared declared roster | Exactly 23 members: 20 simulated and 3 declared-only | R2 committed-definition and lossless-projection tests; not yet implemented. The generic schema separately caps definitions at 64 members |
| Prepared sector geometry | Exactly 20 sector polygons; each polygon has at most 256 integer-microdegree vertices | R2 committed-definition tests; not yet implemented. The generic schema separately caps definitions at 20 sectors and each polygon at 256 vertices |
| Prepared tick interval | 1,000 ms | Committed definition in R2 and fleet-control projection test; not yet runtime evidence |
| Prepared sweep requirement | 12 ticks | Committed definition in R2 and fleet-control projection test; not yet runtime evidence |
| Prepared heartbeat loss | `drone-sim-07` is absent on tick ordinals 2 through 7 inclusive | Committed definition in R2 plus loader and R8 transition tests; not yet runtime evidence |

## Private run control

The two authenticated private HTTP hops and their refusal order are fixed by
[ADR-0107](adr/0107-authenticate-private-scenario-and-fleet-run-control.md) and described in
[CONTRACTS.md](CONTRACTS.md#private-run-control-http). The schemas instrument representation now; R8
must add the server/client timing, authentication, reconciliation, and cancellation evidence.

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Private request-body size | At most 256 KiB before decoding | R8 scenario/fleet HTTP boundary tests; not yet implemented |
| Private-hop bearer entropy | 256 independent random bits per hop | R9 secret-generation and Compose-policy tests; not yet implemented |
| Connection establishment timeout | 1 s per private call | R8 injected HTTPX timeout and boundary tests; not yet implemented |
| Start or status response timeout | 5 s per call | R8 typed-client timeout tests; not yet implemented |
| Reset cancellation budget | One shared 15 s monotonic budget from the dashboard operation through the scenario-to-fleet call | R5/R8 fake-clock and process-integration tests; not yet implemented |
| Private listener host publications | Zero for ports 8081 and 8082 | R9 Compose-policy and exact-service-closure tests; not yet implemented |

## Dashboard event stream

The ordered dashboard event and the reduced state it folds into are defined by
[ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md); the shapes live in
[CONTRACTS.md](CONTRACTS.md#dashboard-event-stream). A dashboard event carries no transport member,
so these bounds are about back-pressure, not about the envelope.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Per-client buffer | 256 dashboard events | `MAX_BUFFERED_EVENTS` in `packages/contracts`, asserted by its unit tests |
| Droppable classes | `TELEMETRY` only | `DROPPABLE_CLASSES` in `packages/contracts`; every other class is never dropped |
| Buffer overflow behaviour | Discard droppable events oldest-first; if the buffer is still full, close the stream with a typed reason and let the client re-synchronize from a state snapshot | Failure-injection test against a client slower than the telemetry rate |
| Reduced-state digest | SHA-256 over the canonical state document under the `replay-state` context | `digest.Context.REPLAY_STATE` in `packages/contracts` |
| Readiness reasons | At most 20 | `readiness.schema.json` plus Python/Ajv contract tests |
| Snapshot non-telemetry timeline | At most 256 ordered events | `dashboard-snapshot.schema.json` plus Python/Ajv contract tests |
| Validated replay bundle | At most 512 ordered events | `replay-bundle.schema.json` plus replay-validator and browser contract tests |
| Search or sector polygon | At most 256 vertices | `scenario-catalog.schema.json` plus scenario-loader contract tests |

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
| Agent Mesh start period | 60 s before the first healthcheck counts, then 20 probes at 15 s. Measured 2026-08-24: healthy 12 s after the final `up --wait` phase began, on a built image ([`default-profile-with-agent-mesh.md`](../release-evidence/phase-0/default-profile-with-agent-mesh.md)) | `healthcheck` in `deploy/compose.yaml` |
| Broker start period | 90 s before the first healthcheck counts, then 30 probes at 10 s. Measured 2026-08-21: both services healthy 40.75 s after `up --wait`, including both image pulls | `healthcheck` in `deploy/compose.yaml`; [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Published ports, all on `127.0.0.1` | 55443 SMF over TLS, 1943 SEMP over TLS, 5432 Postgres, 8000 Agent Mesh Web UI, 8080 dashboard API, 8180 Event Management Agent | compose policy gate refuses any other binding |
| Certificate validity | 365 days for the authority and the broker certificate | `scripts/broker-secrets.sh`; `just rotate-secrets` renews |
| Generated secret length | 32 random bytes, rendered as 64 hexadecimal characters | `scripts/broker-secrets.sh` |
| Docker Desktop memory allocation | 7.652 GiB on the reference workstation, measured 2026-08-21 | [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Broker and Postgres memory at rest | 1.58 GiB: broker 1.543 GiB, Postgres 35.71 MiB. This was the whole default profile until the Agent Mesh joined it ([ADR-0102](adr/0102-start-the-agent-mesh-with-the-default-profile.md)) | [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Default profile memory at rest | 2.16 GiB: broker 1.575 GiB, Agent Mesh 556.8 MiB, Postgres 31.47 MiB, measured 2026-08-21 | [`release-evidence/phase-0/mesh-first-run.md`](../release-evidence/phase-0/mesh-first-run.md). The `services` and `event-portal` profiles are still unmeasured |
| Broker connections opened by the Agent Mesh apps | 9, all on one client username, measured 2026-08-21 against a Message VPN ceiling of 100 | [`release-evidence/phase-0/mesh-first-run.md`](../release-evidence/phase-0/mesh-first-run.md) |
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
| Agent request timeout | 60 s per A2A request | `inter_agent_communication.request_timeout_seconds` in each agent configuration |
| Event Mesh Gateway acknowledgement timeout | 180 s, the window a handler has to complete before the gateway settles the message | `acknowledgment_policy.timeout_seconds` in `agent-mesh/configs/event-mesh-gateway.yaml`; a test asserts the committed value |

The gateway acknowledgement timeout is derived from the row above it rather than measured: a salient event reaches the workflow, whose node delegates to a peer agent, so two 60 s agent requests can run in series, and 60 s of margin covers a cold model load. It settles on completion and nacks with outcome `rejected` on failure, which [CONTRACTS.md](CONTRACTS.md) fixes and the configuration validator enforces. It is not a safety parameter: losing the window costs an agent's opinion, never a command ([ADR-0071](adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)).

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
| SEMP collection page size | 100 rows asked for per read; the broker pages at ten unless asked for more | `PAGE_SIZE` in the same module, asserted by a transport test |
| SEMP collection page bound | 20 pages per read, so at most 2,000 rows; a cursor still running at the bound is refused as `PAGING` rather than truncated | `MAX_PAGES` in the same module, asserted by a transport test |

## Broker data plane

The delivery semantics are [CONTRACTS.md](CONTRACTS.md#delivery-and-failure-semantics) and the typed
facade over the pinned client is [ADR-0028](adr/0028-untyped-solace-client-boundary.md). Which
guarantee each topic family is owed is a total table in `packages/contracts`
([ADR-0079](adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md)); routine telemetry is
direct and supersedable, and the endpoints that carry the guaranteed families are the section below.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Guaranteed publication timeout | 10 s per publication | `PUBLISH_TIMEOUT_MILLISECONDS` in `packages/broker/src/aerial_rescue_broker/messaging.py`, asserted by a publisher test |
| Direct publisher buffer capacity | 0, so a publication is refused when the transport is full rather than queued or buffered without bound | `DIRECT_BUFFER_CAPACITY` in the same module, asserted by a publisher test |
| Client connection retries and reconnection attempts | 0 each, so an absent or refusing broker fails the caller instead of retrying without a log line | `CONNECTION_RETRIES` and `RECONNECTION_ATTEMPTS` in the same module, asserted by a properties test |

## Guaranteed-delivery endpoints

One durable queue per consuming role and guaranteed family, plus one command queue per simulated
drone and one dead-message queue
([ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)). Every value below is
written explicitly into each queue rather than inherited, because each corresponding broker default
is wrong for this system: redelivery retries forever, expiry is ignored, the per-queue spool exceeds
the whole message VPN's, and the default dead-message target names a queue that does not exist.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Queue maximum spool | 10 MB per queue | `MAX_SPOOL_MEGABYTES` in `packages/broker/src/aerial_rescue_broker/queues.py`, written into every queue and asserted by a provisioning test |
| Queue maximum redelivery | 3 redeliveries after the first delivery, so at most 4 arrivals, then the dead-message queue | `MAX_REDELIVERY_COUNT` in the same module; the live probe reads `MAX_REDELIVERY_COUNT + 1` arrivals before the dead-message queue |
| Queue message expiry | 300 s, with expiry respected rather than the factory `false` | `MAX_TTL_SECONDS` in the same module, written together with `respectTtlEnabled` |
| Dead-message-queue target | `#DEAD_MSG_QUEUE`, provisioned, owned by nobody and consumed by nothing | `DEAD_MESSAGE_QUEUE` in the same module; its depth over SEMP is what an acceptance run reads, through `message_count` |
| Consumer flows per queue | 1, exclusive | `MAX_BIND_COUNT` in the same module, asserted per queue |
| Queue permission for every identity but the owner | `no-access` | asserted per queue; the owner is the consuming role's client username |
| Discard notification | `always`, so a discard is negatively acknowledged to the publisher even when the endpoint is administratively disabled | asserted per queue |
| Endpoints the reference fleet needs | 44: 20 family queues, 23 per-drone command queues, and the dead-message queue | derived from the subscribe grants; the message VPN's measured ceilings are 1000 endpoints and 1500 MB of spool, read over SEMP on 2026-08-23 |

The four rows ADR-0061 left open are derived from the service-level rows above rather than measured,
in the same position as the gateway acknowledgement timeout. The **spool** follows from the two rows
that bound a backlog: an event is at most 2 KiB and 500 critical messages must drain within 10 s, so
a queue must hold at least 1 MB; 10 MB is 5,000 messages at that bound, ten times the drain envelope,
and 44 queues reserve 440 MB against the VPN's measured 1500 MB. **Expiry** follows from the worst
declared fault: a 60 s edge disconnect, a 30 s restart recovery, and a 10 s drain give a 100 s worst
case, and 300 s is three times that. It is deliberately longer than the 60 s approval time-to-live,
so a queue's expiry never stands in for the approval protocol's own two-clock consumption
([ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)); a shorter value would
have made a queue depth quietly do safety work an approval record owns. **Redelivery** is bounded so
that a message a consumer cannot settle leaves an exclusive queue instead of holding the 10 s drain
behind it, and it is a different fact from the command send budget, which counts the times the gateway
put a command on the wire ([ADR-0074](adr/0074-command-dispatch-lifecycle.md)).

None of these gates safety: exceeding any one costs a delivery and leaves a counted dead-message
entry, never a command published without an approval. The backlog-recovery row itself remains
unmeasured. The consumer it waited on now exists -- the fleet simulator drains its own drones'
queues -- so what is left is the measurement rather than a component to measure.

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

## Command dispatch

How long one dispatched command waits for its acknowledgement, how the waits grow, and how many times the
gateway may put it on the wire before `ABANDONED`
([ADR-0074](adr/0074-command-dispatch-lifecycle.md), [ADR-0081](adr/0081-give-command-dispatch-one-interval.md)).
Command dispatch has one interval, not three: the acknowledgement timeout is also the backoff base and the
jitter bound. The domain counts sends and reads no clock, so all three durations belong to the command
gateway.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Command acknowledgement timeout | 6 s per send, from the publication to the arriving command-result. Distinct from the Event Mesh Gateway timeout above, which settles a message rather than completing a command | `ACKNOWLEDGEMENT_TIMEOUT_SECONDS` in `services/command_gateway/src/aerial_rescue_command_gateway/dispatch.py`, asserted by a member test |
| Retry backoff | The acknowledgement timeout doubled per timeout: 6 s, 12 s, 24 s, 48 s | `BACKOFF_BASE_SECONDS` in the same module; a schedule test asserts the four waits |
| Retry jitter | Added and never subtracted, uniform over 0 s to the backoff base, drawn from the injected random source | `JITTER_BOUND_SECONDS` in the same module, equal to `BACKOFF_BASE_SECONDS`; a schedule test asserts the bound and the sign |
| Command send budget | 5 sends per command identifier; the fifth acknowledgement timeout abandons it | `SendBudget` in `packages/domain/src/aerial_rescue_domain/commands.py` takes it as an injected count with no default and refuses a count below one; `MAX_COMMAND_SENDS` in the module above is what the composition root supplies |
| Worst case before `ABANDONED` | 120 s without jitter and at most 144 s with it, against the 300 s queue message expiry above | a test folds the four rows rather than restating the number |

The four rows [ADR-0074](adr/0074-command-dispatch-lifecycle.md) left unset are derived from the
service-level rows above rather than measured, in the same position as the four queue parameters and the
gateway acknowledgement timeout. The rows above pin durations and not counts, so the budget cannot be
derived on its own: five is the answer only once the acknowledgement timeout and the backoff are fixed,
which is why all four land together and why three of them are rows rather than adapter constants.

The **acknowledgement timeout** follows from two rows pulling in opposite directions. The connected
command path is a p95 of at most 2 seconds, so a 2-second window would time out on its own declared tail.
Offline detection puts a drone in `OFFLINE` within 6 seconds of heartbeat loss, which is the instant at
which silence stops meaning slow and starts meaning gone. Six seconds is the shortest window that cannot
fire while the system still calls the drone `CONNECTED`, and it is three times the p95 it must not trip
over.

The **backoff base and the jitter bound are that same 6 seconds**, because neither has a row of its own.
The jitter is added rather than centred, so the schedule without it is an exact floor on the instant a
command is abandoned and the arithmetic below holds for every draw.

The **budget** then follows from the worst declared fault. A command issued as its drone drops must
survive a 60-second edge disconnect, a 30-second restart recovery, and a 10-second backlog drain, and its
acknowledgement must still cross the 2-second command path: 102 seconds, the construction the queue expiry
uses with the command path added. A budget of N abandons at 6N + 6(2^(N-1) - 1) seconds, so 36 seconds at
three sends, 66 at four, 120 at five, and 222 at six. Five is the smallest budget whose abandon instant
clears 102 seconds, and 144 seconds at the largest jitter draws still leaves every published copy inside
the 300-second queue expiry, so the gateway never waits on a command the broker has already sent to the
dead-message queue. Six clears it too, at twice the fault envelope, for no row that asks.

The budget is a different fact from the queue's redelivery bound, which counts what the broker did with
one published copy. Five sends against three redeliveries means one command identifier may reach one drone
as many as twenty times, and nineteen of those are answered from the prior persisted result by the
known-command rule in [CONTRACTS.md](CONTRACTS.md#delivery-and-failure-semantics).

None of these gates safety. Abandonment is a verdict the gateway records, not a cancellation: copies
already published stay on the drone's own durable queue until they expire, and one may still be delivered
and executed after the gateway has stopped waiting. What keeps an escalation from executing without an
approval is the atomic single-use consumption before the first publication
([ADR-0006](adr/0006-proposal-bound-single-use-approvals.md),
[ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)), which happens once
whatever the budget is. Exceeding any row here costs a command's completion report, never its
authorization.

## Command intake

How much command work one tick may do. The fleet simulator drains each drone's own durable
command queue after folding a tick, and the bound is what decides whether the backlog-recovery
target above is reachable at all
([ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)).

| Parameter | Value | Instrument |
| --- | --- | --- |
| Commands taken per drone per tick | 3 | `IntakeBounds` in `services/fleet_simulator/src/aerial_rescue_fleet_simulator/service.py`, injected with no default and refusing a bound below one; a member test asserts the refusal and the cap |
| Command receive window | 0 ms, a non-blocking poll | `_POLL_MILLISECONDS` in the same module; a member test asserts the window every receive is given |

Both rows are derived from the service-level rows above rather than measured. The **per-drone
cap** follows from the backlog-recovery target: 500 critical messages must drain within 10
seconds, and 23 drones at 1 Hz give 230 opportunities in that window, so a cap of at least
500 / 230, which is 2.18, is needed. That derivation assumes a loop that runs at 1 Hz, which it
did not do when the cap was set; [ADR-0083](adr/0083-pace-the-tick-loop-at-a-fixed-rate.md) paces
it, so the premise now holds. One command per drone per tick gives 23 a second and a
21.7-second drain, which fails the target outright; three gives 69 a second and 7.2 seconds,
which clears it. Four would clear it too, at no benefit any row asks for.

The **receive window** is zero because intake must not become the tick loop's pacer. A blocking
window would make the tick rate depend on how much command traffic arrived -- fast under load and
slow when idle, which is backwards -- and the loop has no pacing of its own to fall back on. A
poll adds no wall clock to a tick, and the per-drone cap is what bounds the work instead.

Neither gates safety, and neither is the measurement. The backlog-recovery row stays unmeasured;
what changed is that a consumer now exists to measure it against, which is the obligation
ADR-0080 recorded when it provisioned the endpoints.

## Durable store

Every wait the PostgreSQL adapter is allowed to make ([ADR-0090](adr/0090-bound-the-lock-wait-below-the-statement-time.md), which supersedes [ADR-0085](adr/0085-bound-every-durable-store-wait.md)).
Measured on the pinned cluster on 2026-08-23, `statement_timeout`, `lock_timeout`, and
`idle_in_transaction_session_timeout` are all `0`, which is not a conservative default but no bound at
all, so every row below replaces an unbounded wait rather than tightening a loose one. Each value is
derived from a number elsewhere in this document; none is measured under load, because nothing
connects yet.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Pool size | 5 sessions per process | `POOL_SIZE` in `packages/store/src/aerial_rescue_store/bounds.py`; `EngineBounds` refuses a size below one |
| Pool overflow | 0, so exhaustion is a bounded refusal rather than an unbounded queue | `POOL_OVERFLOW` in the same module, which refuses a negative count and accepts zero |
| Connection checkout timeout | 2 s, the connected command path's own p95 target | `CHECKOUT_TIMEOUT_SECONDS` in the same module |
| Connect timeout | 5 s | `CONNECT_TIMEOUT_SECONDS` in the same module |
| Connect retries | 0, matching the broker adapter's connection and reconnection attempts | `CONNECT_RETRIES` in the same module |
| Statement timeout | 5 s, applied server-side per session so a cancelled caller does not leave the work running | `STATEMENT_TIMEOUT_MILLISECONDS` in the same module |
| Lock timeout | 2 s, applied server-side. Strictly above the cluster's 1 s deadlock detection, so a deadlock and a contended wait stay distinguishable, and strictly below the statement timeout, so a contended wait and a stuck statement stay distinguishable. It is the connected command path's p95 target, the row the checkout timeout also derives from | `LOCK_TIMEOUT_MILLISECONDS` in the same module; `EngineBounds` refuses a value at or below `SERVER_DEADLOCK_TIMEOUT_MILLISECONDS` and a value at or above `STATEMENT_TIMEOUT_MILLISECONDS` |
| Idle-in-transaction timeout | 15 s, applied server-side. Contains one lock wait plus one statement, which is the longest legal transaction and is now 7 s | `IDLE_IN_TRANSACTION_TIMEOUT_MILLISECONDS` in the same module; `EngineBounds` refuses a value below the sum of the two |
| Shutdown grace | 15 s, equal to the idle-in-transaction bound so a shutdown never outlives the longest transaction the server tolerates | `SHUTDOWN_GRACE_SECONDS` in the same module |
| Migration wait | 90 s, containing the cluster's healthcheck envelope of a 10 s start period then twelve probes at 5 s | `MIGRATION_WAIT_SECONDS` in the same module, asserted against that envelope by a member test |
| Cluster deadlock detection | 1000 ms, the server's own `deadlock_timeout`, read from the running cluster rather than assumed | `SERVER_DEADLOCK_TIMEOUT_MILLISECONDS` in the same module |
| Cluster connection ceiling | 100 total, 3 reserved for superusers, 0 otherwise reserved, so 97 available | `pg_settings` on the pinned cluster; five services holding a pool of 5 with no overflow need 25 |

Only the lock timeout gates safety: a refusal there is the difference between a denied approval
consumption and an indefinite hold on the approval row. Exceeding any other row produces a failed
request, never an unsafe one. That refusal is reachable only because the lock wait is strictly below
the statement timeout: measured on 2026-08-24 with the two set equal, a contended row was reported
as `canceling statement due to statement timeout` and the lock bound never fired
([ADR-0090](adr/0090-bound-the-lock-wait-below-the-statement-time.md)).

Three of these are settings this member applies to the server, per session: the statement, lock, and
idle-in-transaction bounds reach the connection through `connect_args["server_settings"]` in
`packages/store/src/aerial_rescue_store/engine.py`. Two more are read from the cluster rather than set
on it. **All three applied bounds have now been read back from a live session** and report `5s`, `2s`,
and `15s`, and a statement past the first is cancelled by the server rather than left running
(`tests/integration/test_durable_store_live.py`, [durable-transaction-first-run.md](../release-evidence/phase-3/durable-transaction-first-run.md)).
The first of the two read rather than set, the cluster's own `deadlock_timeout`, reports `1s` from
that same session, so the relation `EngineBounds` enforces against it rests on a reading of this
cluster rather than an assumption about it. ADR-0085's consequence that "nothing applies them
yet" was true when that record landed one increment ahead of its adapter.

## Command outbox

What the central command outbox may hold before staging refuses, and what a refusal does
([ADR-0093](adr/0093-stage-the-command-outbox-under-a-counted-bound.md)). This is the gateway's own
outbox; the per-drone edge outbox is a separate, still-open row below.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Central outbox maximum unconfirmed records | 500, the workload [ADR-0084](adr/0084-give-backlog-recovery-an-instrument.md)'s instrument uses and [backlog-recovery-first-run.md](../release-evidence/phase-2/backlog-recovery-first-run.md) measured draining in 7.141 s | `MAXIMUM_UNCONFIRMED_RECORDS` in `packages/store/src/aerial_rescue_store/outbox.py`, evaluated inside the staging statement |
| Central outbox overshoot under concurrency | At most one record per concurrently staging session, which the pool bounds at 5 per process. The effective ceiling is 504 | A consequence of `READ COMMITTED` recorded in ADR-0093, not a configured value |
| Central outbox byte ceiling | None, deliberately: a staged record is one command envelope, and every member of one is already bounded by the topic and envelope rows above | ADR-0093 records the reasoning; there is nothing to measure |
| Central outbox overflow behaviour | Staging writes no row and refuses. The continuity-breach audit record is appended by the caller in its own transaction, because adding it to the staging transaction would enlarge [ADR-0006](adr/0006-proposal-bound-single-use-approvals.md)'s atomic set and would roll back with the refusal it records | `OutboxRefusal.AT_CAPACITY` in the module above |

## Parameters still to be set

Each of these is required by a claim made elsewhere and has no value yet. Every row is a gap, not a
preference, and must carry a number before the release run.

| Parameter | Required by | Status |
| --- | --- | --- |
| Per-drone **edge** outbox maximum records and bytes | The bounded-outbox claim in [CONTRACTS.md](CONTRACTS.md). The central command outbox is settled above; an edge outbox holds telemetry backlogs rather than command envelopes, so its byte ceiling does not follow from the envelope rows | open, and Phase 6's |
| Per-drone **edge** outbox overflow behaviour | A critical-record overflow must refuse the write and emit a continuity-breach audit record; a critical record is never silently dropped. The central outbox's version of this is settled above | decided, unquantified |
| Evidence band boundaries: the lower bound in hundredths of the weak, supported, and corroborated bands | The band-keyed escalation eligibility in [LIMITATIONS.md](LIMITATIONS.md) and [ADR-0076](adr/0076-evidence-score-bands.md). The two-source corroboration floor is structural rather than numeric and is not open | open |
| Ollama `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` | Warm-model residency across missions | open |
| Instrument definition per service-level row: start point, end point, clock, sample count, statistic, warm-up discarded, machine-state precondition | Every row of the table above | open |
