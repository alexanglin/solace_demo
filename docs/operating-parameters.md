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
| Agent Mesh owned-code coverage | 100% statements and 100% branches for `agent-mesh/tools/agent_mesh_config_validator.py` and `agent-mesh/aerial_rescue_event_mesh_gateway/` | `pytest-cov` with both explicit source inventories and `--cov-fail-under=100` in `scripts/hooks/agent-mesh-test-full.sh` |
| Python Tier 3 test inventory | At least 1 smoke test and 1 failure-path test per module | Tier inventory gate; Tier 3 fails until this inventory is executable |
| TypeScript coverage | 95% each for statements, branches, functions, and lines per production package | Vitest V8 JSON summary plus exact source inventory, recomputed with integer arithmetic by `tools/typescript_coverage_gate.py`; manifest thresholds held by `tools/typescript_policy_gate.py` |
| TypeScript Tier 1 statement coverage | 100% independently for each browser trust-boundary module named by [ADR-0130](adr/0130-enforce-dashboard-tier-one-coverage-per-file.md) | The same Vitest V8 JSON summary, adjudicated per file by `tools/typescript_coverage_gate.py` |
| TypeScript Tier 1 branch coverage | 100% independently for each browser trust-boundary module named by [ADR-0130](adr/0130-enforce-dashboard-tier-one-coverage-per-file.md) | The same Vitest V8 JSON summary, adjudicated per file by `tools/typescript_coverage_gate.py` |
| Fixture Playwright inventory | Exactly 64 cases | `config.playwrightExpectedTests`, `playwright test --list`, and `scripts/hooks/dashboard/dashboard-playwright-full.sh`; 64 of 64 passed in 42.0 s at revision `db2b640` ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| Production Playwright inventory | Exactly 8 serial cases: 4 operator/replay and 4 resilience | `pnpm --dir apps/dashboard run test:e2e:production` plus production Playwright discovery; 8 of 8 passed in 1.6 min at revision `db2b640` against the shared `aerial-rescue-mesh` project ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| TypeScript type errors | Zero, whole project | `tsc --noEmit` through `scripts/hooks/dashboard/dashboard-typecheck-full.sh` |
| TypeScript lint findings | Zero at any severity | `eslint --max-warnings 0` through `scripts/hooks/dashboard/dashboard-quality-full.sh` |
| TypeScript compiler options | Every option [ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md) names, at the value it names | `tools/typescript_policy_gate.py` |
| Production dashboard script and style output | At most 1,500,000 uncompressed bytes after minification, aggregated over every emitted JavaScript chunk and CSS asset | `production-asset-budget` Vite `generateBundle` plugin plus `apps/dashboard/src/production-policy/asset-budget.integration.test.ts` ([ADR-0122](adr/0122-bound-production-dashboard-script-and-style-bytes.md)) |
| Cyclomatic complexity | At most 8 per function | Ruff `C901` |
| Cognitive complexity | At most 15 per function | Complexipy 7.0.1 |
| Function arguments | At most 5 | Ruff `PLR0913` and `PLR0917` |
| Function branches | At most 10 | Ruff `PLR0912` |
| Function returns | At most 6 | Ruff `PLR0911` |
| Function statements | At most 30 | Ruff `PLR0915` |
| Function locals | At most 12 | Ruff `PLR0914` |
| Nested blocks | At most 3 | Ruff `PLR1702` |
| Boolean expressions | At most 4 operands | Ruff `PLR0916` |
| Duplicated source | At most 3% across authored source; clone minimum 8 lines and 50 tokens; strict mode; generated dashboard contract types sit outside the measurement ([ADR-0110](adr/0110-scope-the-duplication-gate-to-authored-source.md)) | jscpd 5.0.14 |
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
| Image inspection timeout | 20 minutes per image | `trivy image --timeout 20m` in `scripts/security/scan-images.sh` and `scripts/security/generate-sboms.sh` |
| Workflow audit findings | Zero at any severity, offline | zizmor 1.29.0 pre-commit hook over `.github/workflows/` and `.github/dependabot.yml` |
| Continuous-integration job budget | At most 20 minutes per job, every job bounded. Measured 2026-08-20: the complete pre-push stage 2m01s, the image scan 2m58s, CodeQL 1m13s, the commit stage 1m15s | `tools/quality_gate_tests/hooks/test_hook_semantics.py` over every `.github/workflows/*.yml` job |
| Security re-scan cadence | Daily at 06:17 UTC, plus dispatch, every push to `main`, and pull requests touching the audited inputs | `.github/workflows/security.yml` |
| Dependabot updates | Daily at 06:00 UTC; at most 5 open pull requests per ecosystem; a 7-day cooldown | `.github/dependabot.yml`, held by `tools/quality_gate_tests/hooks/test_hook_repairs.py` |
| CodeQL coverage | Python, build mode `none`, on a Linux x64 runner | the `codeql` job of `.github/workflows/security.yml` |
| Directory fan-out | At most 20 files per directory, counted as immediate children only | `tools/directory_fanout_gate.py` over `git ls-files --cached --others --exclude-standard` |
| Fan-out exemption reason | At least 20 Unicode characters | `directory-fanout.toml` validation |
| Compose policy | Every pulled image `name:tag@sha256:` with a 64-hex digest; every published port on `127.0.0.1`; secrets by file or indirection; a healthcheck on every long-running service; exactly migration and replay validation admitted as `restart: "no"` one-shot jobs; the broker's `shm_size`, `nofile`, certificate path, and TLS port; `SOLACE_DEV_MODE=false` on Agent Mesh | `tools/compose_policy_gate.py` over `deploy/` compose files and Dockerfiles |

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
| Soak | 30 minutes; 61 samples including endpoints at 30-second cadence; stable dashboard API container/PID; sampled RSS growth at most 64 MiB and open-file-descriptor growth at most 8 from the post-connect baseline; every browser sample remains `CONNECTED` and `READY`. Instrument: `pnpm --dir apps/dashboard run test:e2e:soak` plus the runner-side `/proc/1` probe selected by [ADR-0126](adr/0126-instrument-the-dashboard-soak-with-bounded-process-growth.md). At revision `db2b640`, 1 of 1 passed in 30.3 min with all 61 samples: the API container/PID and shared broker/PostgreSQL IDs remained stable; every browser sample was READY and CONNECTED with the map visible and zero alerts; and the run made zero remote-origin requests. Both growth bounds passed. The retained evidence does not contain the numeric baseline or maximum; the separate post-soak point sample was 114,425,856 bytes RSS and 12 file descriptors, neither of which is a baseline or maximum ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |

The prepared wilderness dashboard workload adds three independently interpreted Phase 3 acceptance
measures. The deterministic R8 runtime asserts the execution and publication counters. The recorder
receipt is deliberately best effort and can establish only that some telemetry crossed the adapter; it
cannot replace or weaken the fleet publication counter.

| Prepared dashboard measure | Acceptance target | Instrument and current status |
| --- | --- | --- |
| Scenario execution | Exactly 14 ticks | `services/fleet_simulator/tests/test_fleet_live_control.py` asserts the fleet-owned completed-tick count and scenario-control integration asserts the resulting `EXHAUSTED` lifecycle. The post-soak mission at revision `db2b640` reached 14 ticks and 328 total audit events ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)); the audit count is an observation, not a telemetry target |
| Fleet telemetry publication | Exactly 280 successful publications: 20 simulated members over 14 ticks | The R8 fleet publisher counter and production `collectLiveMissionEvidence` query are independent from recorder receipt. The post-soak mission at revision `db2b640` reported exactly 280 successful fleet publications ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| Recorder telemetry receipt | Best-effort acceptance observation greater than 0 and no greater than the 280 successful fleet publications | The recorder audit query in `collectLiveMissionEvidence` is evaluated separately from fleet status. The post-soak mission at revision `db2b640` observed 280 receipts, but that equality is not a telemetry-completeness guarantee for this or another run ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |

The fleet-telemetry rate is the one row with a partial instrument. The fleet simulator's tick loop
keeps the interval its scenario declares, measured from the start of each tick, and tallies every
tick that could not finish inside it as `OVERRAN` in `ServeReport.pacing`
([ADR-0083](adr/0083-pace-the-tick-loop-at-a-fixed-rate.md)). That makes the rate falsifiable by a
run report rather than unmeasurable. The rest of this row's instrument -- sample count, statistic,
warm-up, and machine-state precondition -- stays open below.

The backlog-recovery row has a complete instrument; the soak row above independently has a complete
bounded process-growth and browser-health instrument:
[ADR-0084](adr/0084-give-backlog-recovery-an-instrument.md) fixes its start point, end point, clock,
workload, fleet size, sample count, statistic, discarded warm-up, and machine-state precondition,
and `tests/integration/test_backlog_recovery_live.py` implements it. The record defines the
measurement; it does not move the target.

## Scenario catalog files

The file contract is fixed by
[ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) and described in
[CONTRACTS.md](CONTRACTS.md#scenario-catalog-files). Schema-expressible bounds are held by the scenario
schemas and root contract tests. Byte, nesting, path, and raw-decoder bounds are executable at the R2
loader boundary.

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Production scenario artifacts | One catalog at `scenarios/catalog.v1.json` and one revision-one definition at `scenarios/v1/wilderness-missing-person.r1.json` | Committed files plus R2 loader inventory and digest tests |
| Catalog and definition size | At most 256 KiB each | R2 raw-byte loader boundary test |
| Document nesting | At most 16 nested containers | R2 raw-document depth test before model construction |
| Catalog entries | At most 20 | `catalog.schema.json` and root schema contract tests |
| Declared members per definition | At most 64 | `definition.schema.json` and root schema contract tests |
| Heartbeat-loss ordinals per definition | At most 4,096 | `definition.schema.json` and root schema contract tests |
| Definition integrity | SHA-256 of the selected definition bytes, rendered as 64 lowercase hexadecimal characters | R2 catalog digest and file-replacement tests |
| Prepared declared roster | Exactly 23 members: 20 simulated and 3 declared-only | R2 committed-definition and lossless-projection tests. The generic schema separately caps definitions at 64 members |
| Prepared sector geometry | Exactly 20 sector polygons; each polygon has at most 256 integer-microdegree vertices | R2 committed-definition tests. The generic schema separately caps definitions at 20 sectors and each polygon at 256 vertices |
| Prepared tick interval | 1,000 ms | Committed definition plus fleet-control projection and R8 runtime tests |
| Prepared sweep requirement | 12 ticks | Committed definition plus fleet-control projection and R8 runtime tests |
| Prepared heartbeat loss | `drone-sim-07` is absent on tick ordinals 2 through 7 inclusive | Committed definition plus loader and R8 connectivity/sector-transition tests |

## Private run control

The two authenticated private HTTP hops and their refusal order are fixed by
[ADR-0107](adr/0107-authenticate-private-scenario-and-fleet-run-control.md) and described in
[CONTRACTS.md](CONTRACTS.md#private-run-control-http). R8 supplies deterministic server/client,
authentication, reconciliation, timeout, and cancellation evidence. The committed A8/R9 run crossed both
private hops through the packaged services; post-run inspection confirmed that neither private listener
had a host publication
([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Private request-body size | At most 256 KiB before decoding | R8 scenario/fleet HTTP boundary tests |
| Private-hop bearer entropy | 256 independent random bits per hop | R9 secret-generation and Compose-policy tests; the committed production workflows crossed both authenticated private hops without exposing either listener on the host ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| Connection establishment timeout | 1 s per private call | R8 injected HTTPX timeout and boundary tests |
| Start or status response timeout | 5 s per call | R8 typed-client timeout tests |
| Reset cancellation budget | One shared 15 s monotonic budget from the dashboard operation through the scenario-to-fleet call | R5 orchestration and R8 private-control deadline tests |
| Scenario-control run bindings per process epoch | 32 (`MAXIMUM_BINDINGS`); a further start or recovery of an unknown run is refused `INTERNAL_FAILURE` | `ScenarioCoordinator(maximum_bindings=…)` construction argument and the coordinator capacity test in `services/scenario_service/tests/test_control.py` ([ADR-0197](adr/0197-standardize-scenario-control-on-the-console-composition.md)) |
| Mission-lifecycle observation interval | 1,000 ms between observations, matching the prepared tick interval so a lifecycle change is visible within one tick. A started live run costs one status call plus a durable lifecycle read and a predecessor read pair per interval; a durably terminal mission costs the reads alone ([ADR-0209](adr/0209-publish-the-mission-lifecycle-from-observed-run-status.md), [ADR-0210](adr/0210-publish-the-ending-a-reset-gives-its-predecessor.md)) | `MISSION_LIFECYCLE_POLL_MILLISECONDS` in `services/dashboard_api/src/aerial_rescue_dashboard_api/console.py`; observation, transition-refusal, settled-mission, and watch-lifecycle tests in `services/dashboard_api/tests/messaging/test_mission_observer.py`; live qualification pending |
| Private listener host publications | Zero for ports 8081 and 8082 | R9 Compose-policy and exact-service-closure tests plus post-cleanup container inspection at revision `db2b640`, which observed empty host-port maps for scenario service and fleet simulator ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |

## Dashboard event stream

The ordered dashboard event and the reduced state it folds into are defined by
[ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md); the shapes live in
[CONTRACTS.md](CONTRACTS.md#dashboard-event-stream). A dashboard event carries no transport member,
so these bounds are about back-pressure, not about the envelope.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Per-client buffer | 256 dashboard events | `MAX_BUFFERED_EVENTS` in `packages/contracts`, asserted by its unit tests |
| Concurrent local SSE clients | 8 | Production dashboard composition constants plus refusal-before-allocation tests at client 9 |
| Idle SSE comment keepalive | Every 15 s | Production dashboard composition constant plus deterministic comment-frame tests |
| Droppable classes | `TELEMETRY` only | `DROPPABLE_CLASSES` in `packages/contracts`; every other class is never dropped |
| Buffer overflow behaviour | Discard droppable events oldest-first; if the buffer is still full, close the stream with a typed reason and let the client re-synchronize from a state snapshot | Failure-injection test against a client slower than the telemetry rate |
| Reduced-state digest | SHA-256 over the canonical state document under the `replay-state` context | `digest.Context.REPLAY_STATE` in `packages/contracts` |
| Readiness reasons | At most 20 | `readiness.schema.json` plus Python/Ajv contract tests |
| Snapshot non-telemetry timeline | At most 256 ordered events | `dashboard-snapshot.schema.json` plus Python/Ajv contract tests |
| Validated replay bundle | At most 512 ordered events | `replay-bundle.schema.json` plus replay-validator and browser contract tests |
| Normalized recording | At most 1 MiB, 512 event records, 64 KiB per canonical line, depth 16, UTF-8 with LF-only framing and a final newline | replay-validator boundary and failure-injection tests selected by ADR-0115 |
| SSE clients per API process | 8 | capacity refusal and disconnect cleanup tests selected by ADR-0116 |
| SSE data plus terminal capacity | 256 data frames plus one reserved terminal overload frame per client | pressure tests selected by ADR-0116 |
| Production SSE pressure input | 2 distinct producers × 512 acknowledged non-droppable events after pausing Caddy while the API remains running; both target the retained `EXHAUSTED` predecessor mission/run after reset, while the current `PLANNED` successor mission/run and its audit ordinal must remain unchanged | production Playwright pressure acceptance, per-producer receipt queries, predecessor recording export, successor before/after assertions, and API process-identity samples selected by ADR-0141 and ADR-0142 |
| Audit page/reconstruction bounds | 256 rows per query and 512 ordered events per reconstruction | store/API integration tests selected by ADR-0113/0116 |
| SSE polling, keepalive, and cleanup | 250 ms audit polling, 15 s keepalive comments, 1 s disconnect cleanup | fake-clock SSE integration tests selected by ADR-0116 |
| Browser transport offline transition | 6 s after the first EventSource error without an `open` callback | `DASHBOARD_TRANSPORT_OFFLINE_MILLISECONDS`, fake-clock adapter tests, and the packaged Caddy outage case selected by ADR-0125 |
| Browser overload notice | At least 1 s without delaying replacement snapshot application | `DASHBOARD_OVERLOAD_NOTICE_MILLISECONDS`, the immediate-resnapshot integration test, and production pressure acceptance selected by ADR-0135 |
| Dashboard API graceful shutdown | 5 s | process-level shutdown test selected by ADR-0116 |
| Reducer parity repetitions | 10 independent Python folds and 10 independent TypeScript folds | `reducer-parity.integration.test.ts` compares per-step state bytes, digests, witnesses, outcomes, and timeline ordinals |
| Parity support process | 5 s timeout and at most 1 MiB of captured output | `PYTHON_RUNNER_TIMEOUT_MILLISECONDS` and `PYTHON_RUNNER_OUTPUT_BYTES` in the dashboard integration test |
| Parity support input | At most 1 MiB per fixture and at most 100 requested runs | `MAX_FIXTURE_BYTES` and `MAX_PARITY_RUNS` in `reducer_parity_runner.py`; the production fixture requests 10 runs |
| Search or sector polygon | At most 256 vertices | `scenario-catalog.schema.json` plus scenario-loader contract tests |

## Public dashboard HTTP

The raw mutation boundary and refusal order are fixed by
[ADR-0160](adr/0160-bound-public-dashboard-mutation-bodies.md). This bound applies before canonical
decoding and before an injected route operation can observe a request.

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Public mutation request-body size | At most 262,144 bytes, inclusive | `MAX_MUTATION_BODY_BYTES` plus dashboard API exact-bound, one-byte-over, refusal-order, and no-effect tests |

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
[ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md), [ADR-0044](adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md), [ADR-0046](adr/0046-generated-local-certificate-authority.md), [ADR-0117](adr/0117-select-the-exact-mission-control-service-closure.md), and [ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md). Every image is pinned by its multi-architecture index digest, verified on the date its governing record accepted the pin, and the compose policy gate refuses a pull that is not. Every image
below, and the two images the Dockerfiles build, is scanned by Trivy on each daily run
([ADR-0048](adr/0048-scan-images-and-deploy-configuration-with-trivy.md)).

| Parameter | Value | Instrument |
| --- | --- | --- |
| Broker image | `solace/solace-pubsub-standard:10.26.0.8799` at `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f` | `deploy/compose.yaml`, held by the compose policy gate |
| Postgres image | `postgres:18.6-trixie` at `sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280` | `deploy/compose.yaml`, held by the compose policy gate |
| Postgres data directory | `/var/lib/postgresql/18/docker`, inside the `/var/lib/postgresql` volume the image declares ([ADR-0060](adr/0060-postgresql-18-and-its-data-directory-layout.md)) | `show data_directory` on the running cluster; the named volume holds `18/docker/PG_VERSION` |
| Agent Mesh base image | `solace/solace-agent-mesh:1.28.7` at `sha256:25dc09b55e8a718e5a690e4abba039cbd032872cd6d4c402b7c69d1dead70255` | `deploy/agent-mesh/Dockerfile` |
| Application base image | `python:3.14.7-slim-trixie` at `sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5` | `deploy/application/Dockerfile` |
| Dashboard builder image | `node:26.7.0-slim` at `sha256:5758d367d7b4f48b73a9bb3530e687e47efb289f3b43f9c0450a25225ae0db5d`, with pnpm 11.23.0 | `deploy/application/Dockerfile`; frozen install and production build are held by deployment conformance tests |
| Dashboard relay image | `caddy:2.11.4-alpine` at `sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648` | `deploy/compose.yaml`, held by the compose policy and mission-control packaging tests |
| Event Management Agent image | `solace/event-management-agent:1.9.9` at `sha256:c5f3d9bf711dd051c14b162f10ecdbd7f3f7a85306d16c438c92229719123c5b`, `linux/amd64` | `deploy/compose.yaml` |
| Broker shared memory | 1 GiB | `shm_size`, from Solace's single-node template |
| Broker open-file limit | 2448 soft, 1048576 hard | `ulimits.nofile`, from Solace's single-node template |
| Broker connection scaling | 100 connections, the same ceiling as the Developer-class showcase service | `system_scaling_maxconnectioncount` |
| Broker event JSON line | 8192 bytes of broker-native JSON; each source read is bounded to 8194 bytes including a possible CRLF | `logging_maxjsonmessagesize` in `deploy/compose.yaml`; `MAX_JSON_EVENT_BYTES`, bounded-source and oversize-continuation tests in `packages/broker` |
| Broker event monitor buffering | One line processed synchronously; no elastic queue | Sink-failure, oversize, malformed-input, continuation, and source-closure tests in `packages/broker/tests/test_event_monitor.py` |
| Retained broker event source | `/jail/logs/event.log`, from only the read-only `jail/logs` subpath of `broker-storage` | `RETAINED_EVENT_LOG`, the exact long-syntax Compose mount, and deployment isolation tests selected by [ADR-0173](adr/0173-follow-the-retained-broker-event-log-without-runtime-authority.md) |
| Retained event EOF poll | 1 s | `EVENT_LOG_POLL_SECONDS`; partial-append and exact-bound tests in `packages/broker/tests/test_event_source.py` |
| Retained event rotation gap | 30 EOF polls (30 s at the fixed poll interval), then a redacted source failure and nonzero exit | `MAXIMUM_ROTATION_GAP_POLLS`; missing, unreadable, rename-rotation, copy-truncation, and gap-exhaustion tests in `packages/broker/tests/test_event_source.py` |
| Broker event monitor restart and stop | At most 3 restarts after failure; 15 s graceful stop | `on-failure:3` and `stop_grace_period` on `broker-event-monitor`; graceful-shutdown and static deployment tests |
| Agent Mesh start period | 60 s before the first healthcheck counts, then 20 probes at 15 s. Measured 2026-08-24: healthy 12 s after the final `up --wait` phase began, on a built image ([`default-profile-with-agent-mesh.md`](../release-evidence/phase-0/default-profile-with-agent-mesh.md)) | `healthcheck` in `deploy/compose.yaml` |
| Agent Mesh forced-stop grace | 46 s, covering at most 0.5 s to observe a stop during asynchronous initialization, the pinned Connector's 30 s cleanup allowance, and the owned 15 s settle window that follows | `stop_grace_period` on the `agent-mesh` service, held against `ASYNC_INITIALIZATION_POLL_SECONDS` and `THREAD_SETTLE_SECONDS` by a deployment test ([ADR-0199](adr/0199-terminate-the-owned-agent-mesh-entrypoint.md), [ADR-0201](adr/0201-gate-agent-mesh-readiness-on-asynchronous-initialization.md)) |
| Broker start period | 90 s before the first healthcheck counts, then 30 probes at 10 s. Measured 2026-08-21: both services healthy 40.75 s after `up --wait`, including both image pulls | `healthcheck` in `deploy/compose.yaml`; [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Published ports, all on `127.0.0.1` | 55443 SMF over TLS, 1943 SEMP over TLS, 5432 Postgres, 8000 Agent Mesh Web UI, 8080 Caddy dashboard relay, 8180 Event Management Agent. Dashboard API, scenario 8081, and fleet 8082 publish none | compose policy and mission-control packaging gates refuse any other binding |
| Mission-control host-publisher bridges | 3 single-member bridges: broker, PostgreSQL, and Caddy each receive one distinct bridge with IP masquerade disabled and default host binding fixed to `127.0.0.1` ([ADR-0131](adr/0131-isolate-loopback-publishers-and-forward-startup-flags.md)) | `test_host_publishers_use_single_service_nonmasquerading_networks` asserts exact membership and both driver options; committed post-run inspection observed the selected `aerial-rescue-mesh` project labels, private scenario/fleet networks, and loopback-only broker/PostgreSQL bindings ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| Compose topology | Exactly 8 named networks and 5 named volumes | Compose configuration and deployment-conformance tests enumerate `event-mesh`, three loopback bridges, `store`, two private-control networks, `model-egress`, and the five retained/handoff volumes |
| Mission-control startup targets | 7 dashboard extensions: migration, fleet simulator, scenario service, recorder, replay validator, dashboard API, and Caddy ([ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)) | the packaging test asserts the literal target list and `--no-deps`; broker and PostgreSQL are required healthy prerequisites rather than selected targets |
| Mission-control stop targets | 5 long-running dashboard services: fleet simulator, scenario service, recorder, dashboard API, and Caddy ([ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)) | the packaging test asserts the stop list and rejects Compose `down`, volume removal, broker, and PostgreSQL |
| Shared base identity guard | 2 container IDs: broker and PostgreSQL are sampled before dashboard startup and after startup/cleanup and must remain equal ([ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)) | recipe tests assert pre/post reads; both IDs remained stable across the committed production and soak runs at revision `db2b640`; the independently sampled Agent Mesh ID also remained stable ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)) |
| Certificate validity | 365 days for the authority and the broker certificate | `scripts/broker-secrets.sh`; `just rotate-secrets` renews |
| Generated secret length | 32 random bytes, rendered as 64 hexadecimal characters; scenario-control and fleet-control secrets are generated independently | `scripts/broker-secrets.sh` and its hermetic conformance tests |
| Docker Desktop memory allocation | 7.652 GiB on the reference workstation, measured 2026-08-21 | [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Broker and Postgres memory at rest | 1.58 GiB: broker 1.543 GiB, Postgres 35.71 MiB. This was the whole default profile until the Agent Mesh joined it ([ADR-0102](adr/0102-start-the-agent-mesh-with-the-default-profile.md)) | [`release-evidence/phase-0/first-live-run.md`](../release-evidence/phase-0/first-live-run.md) |
| Default profile memory at rest | 2.16 GiB: broker 1.575 GiB, Agent Mesh 556.8 MiB, Postgres 31.47 MiB, measured 2026-08-21 | [`release-evidence/phase-0/mesh-first-run.md`](../release-evidence/phase-0/mesh-first-run.md). The complete adoption `services` profile and `event-portal` profile remain unmeasured; the existing dashboard evidence is not whole-stack resource evidence |
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
| Agent card publishing interval | 10 s per agent | `agent_card_publishing.interval_seconds` in each agent configuration. Derived from the pinned runtime's 60 s registry TTL: at 30 s a single missed publication expired a card, which a workflow node turns into a hard failure ([ADR-0219](adr/0219-publish-agent-cards-inside-the-registry-ttl.md)) |
| Workflow node timeout | 120 s per node, stated per node and as `default_node_timeout_seconds` | `agent-mesh/configs/mission-response-workflow.yaml`. A successful node is two model turns; both are stated because a malformed per-node value falls back to the default silently. Measured 2026-08-31: the two-node DAG completed in 89 s |
| Agent Mesh temporary-endpoint drain | 120 s bound, twice the observed maximum | `DRAIN_DEADLINE_SECONDS` in `packages/broker/src/aerial_rescue_broker/drain.py`. Five waits measured 2026-08-31 took 37.2, 48.1, 49.2, 48.3, and 59.0 s ([ADR-0216](adr/0216-drain-agent-mesh-temporaries-before-a-recreate-binds.md)) |
| Web UI allowed browser origins | loopback only; a wildcard, an empty list, or any other host is refused | the `WEBUI_EXPOSURE` rule in the configuration validator |
| Local model digest form | `sha256:` and 64 lowercase hexadecimal characters, the Ollama manifest digest | the `MODEL_LOCK` rule offline; `GET /api/tags` in `scripts/preflight-ollama.sh`, which refuses to start the mesh on an absent model or a moved digest |
| Orchestration model for the Orchestrator and the workflow | `ollama_chat/llama3.1:8b`, 4.92 GB on disk at 8.0B parameters (Q4_K_M), reporting `completion` and `tools` | `agent-mesh/model-lock.toml`. Provisional: the roles are pinned by the Phase 4 model selection |
| Model for the coordinator's structured answer | `ollama_chat/llama3.1:8b`, 4.92 GB, reporting `completion` and `tools` and no `thinking` | `agent-mesh/model-lock.toml` and the `model` in `agent-mesh/configs/mission-coordinator.yaml` |
| Coordinator model calls per task | 4, bounding a non-converging agent inside the gateway's acknowledgment window | `max_llm_calls_per_task` in `agent-mesh/configs/mission-coordinator.yaml`; the framework's default is 20 ([ADR-0198](adr/0198-give-the-coordinator-a-model-and-a-tool-surface-that-answer.md)) |
| Agent request timeout | 60 s per A2A request | `inter_agent_communication.request_timeout_seconds` in each agent configuration |
| Agent Mesh asynchronous-initialization barrier | 60 s shared by every component future, observed in slices of at most 0.5 s so the Connector stop signal remains prompt; the first failure ends the wait | `ASYNC_INITIALIZATION_TIMEOUT_SECONDS`, `ASYNC_INITIALIZATION_POLL_SECONDS`, and the owned `on_flow_creation` handler in `aerial_rescue_runtime_compat.lifecycle` ([ADR-0201](adr/0201-gate-agent-mesh-readiness-on-asynchronous-initialization.md)) |
| Event Mesh Gateway acknowledgement timeout | 180 s, the window a handler has to complete before the gateway settles the message | `acknowledgment_policy.timeout_seconds` in `agent-mesh/configs/event-mesh-gateway.yaml`; a test asserts the committed value |
| Salient-chain probe response window | 300 s, shared by the provenance row, the normalised proposal, and its evidence decision after one published salient event | `RESPONSE_WINDOW_SECONDS` in `tests/phase0/test_salient_chain_live.py` |
| Entrypoint thread settle bound | 15 s, how long the owned entrypoint waits for surviving nondaemon threads before it forces the process to stop with the status the lifecycle chose | `THREAD_SETTLE_SECONDS` in `agent-mesh/aerial_rescue_runtime_compat/lifecycle.py`, asserted by an offline test ([ADR-0199](adr/0199-terminate-the-owned-agent-mesh-entrypoint.md)) |

The asynchronous-initialization barrier and entrypoint thread settle bound are judgements, not
measurements. The former is one global startup window for local construction and broker session creation,
not model execution, and sits inside the `agent-mesh` healthcheck failure window; its half-second slices
bound shutdown observation rather than extending that window. The latter protects the process exit status
rather than a duration: it sits well above the pinned Connector's own bounded joins
(0.1 s per flow thread, 1.0 s per component and trace thread) and well below that same healthcheck window,
so an interpreter that has not settled inside it is stuck rather than slow. A startup timeout or forced
exit during an ordinary healthy start or SIGTERM shutdown, respectively, is evidence that the applicable
bound is wrong.

The gateway acknowledgement timeout is derived from the row above it rather than measured: a salient event reaches the workflow, whose node delegates to a peer agent, so two 60 s agent requests can run in series, and 60 s of margin covers a cold model load. It settles on completion and nacks with outcome `rejected` on failure, which [CONTRACTS.md](CONTRACTS.md) fixes and the configuration validator enforces. It is not a safety parameter: losing the window costs an agent's opinion, never a command ([ADR-0071](adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)).

The salient-chain probe's response window is derived from the acknowledgement timeout in turn. 180 s is the longest a task can run before the gateway settles its message, and the command gateway's normalisation and the evidence service's decision follow that; 300 s covers the chain with margin for both hops, and a probe waiting past it would only be watching for a task the gateway has already nacked.

## Durable application processing

[ADR-0146](adr/0146-define-durable-application-processing.md) fixes the bounds around the general
application inbox/outbox and the centrally simulated edge. Alembic revisions, SQLAlchemy repositories,
bounded workers, and service integrations implement those bounds with deterministic tests. The tests are
not PostgreSQL/PubSub+ interruption evidence; the shared-stack run remains required.

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Application-outbox drain batch | At most 50 oldest eligible `STAGED` rows per drain. Recovery drains to exhaustion once per connected epoch; the dashboard and evidence serving loops additionally drain one batch per cycle ([ADR-0208](adr/0208-publish-the-dashboard-outbox-on-the-serving-cycle.md)) | Ordered-claim, independent-outcome, crash, refusal, ambiguity, service-recovery, and idle-cycle publication tests; live drain pending |
| Per-drone critical outbox records | At most 500 unconfirmed records, independently per simulated drone | SQLAlchemy repository capacity/concurrency tests and fleet recovery tests; live reconnect drain pending |
| Per-drone critical outbox bytes | At most 2 MiB of exact canonical topic, headers, and body bytes, independently per simulated drone | SQLAlchemy repository byte-cap and rollback tests; live saturation pending |
| Per-drone critical overflow | Refuse the new critical record without eviction and append a continuity-breach audit outcome | Capacity rollback, audit append, and redelivery tests are implemented; live saturation remains pending |
| Telemetry records buffered | 0 | Fleet outbox tests prove `DRONE_TELEMETRY` never enters PostgreSQL and Direct congestion increments the drop counter; live congestion pending |

The record and byte limits apply simultaneously to staged and reconciliation-needed critical
publications. Reaching either refuses the new critical transition without eviction. The general
50-row batch is a work cap, not a bulk-transaction size: no database transaction spans broker I/O, and
one row's refusal or ambiguity cannot confirm another row.

## Evidence scoring

The score is a deterministic simulation heuristic selected by ADR-0146, not a calibrated probability.
Only `CONTRIBUTING` live evidence enters it; recorded-origin evidence is refused, and the corroborated
band still requires at least two distinct live source identifiers.

| Parameter | Value | Instrument and current status |
| --- | --- | --- |
| Weak band | Inclusive score 25 through 49 | Tier 1 domain boundary, property, and Evidence Service composition tests; shared-stack publication pending |
| Supported band | Inclusive score 50 through 74 | Tier 1 domain boundary, property, and Evidence Service composition tests; shared-stack publication pending |
| Corroborated band | Inclusive score 75 through 100, with at least two distinct live sources | Tier 1 domain, approval-bypass, and Evidence Service integration tests; live qualification pending |
| Live sensor contribution | Integer weight 40 | closed evidence-decision schema, Tier 1 score tests, and service mapping tests; live qualification pending |
| Live model contribution | Integer weight 35 | closed evidence-decision schema, Tier 1 score tests, and service mapping tests; live qualification pending |

Scores 0 through 24 are `NONE`. One sensor plus one model reaches 75, two sensors reach 80, and two
models reach only 70. Eligibility never authorizes escalation; the exact proposal/evidence approval
binding and command-gateway transaction remain separate controls.

## Broker authorization

The roles, their grants, and the deny-by-default rule are
[ADR-0061](adr/0061-least-privilege-broker-principals-and-topic-authorization.md). The matrix itself
lives in `packages/domain/src/aerial_rescue_domain/principals.py`, where a test asserts it is total
over the roles and names every family's publisher set; this section carries only the numbers.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Authorization roles | 9, one ACL profile and client profile per role | `Principal` in `packages/domain`; a totality test asserts the nine names |
| Enabled client usernames | 8; the deny-all discovery role has no connecting username | desired-state construction and the two-drone deployment report |
| Topic families | 15: 12 notification-only, 2 request/reply, and 1 Agent Response integration | `Family` in `packages/contracts`; domain grant and delivery tests are total over all fifteen |
| Role name bound | at most 32 characters and inside the topic kind form, because the name is also the ACL profile name | `MAX_KIND_LENGTH` and `KIND_PATTERN` from `packages/contracts`, asserted per role |
| ACL profile default actions | `disallow` for publish topic, subscribe topic, and subscribe share name; `allow` for client connect | `packages/broker/src/aerial_rescue_broker/provisioning.py`, asserted per profile |
| Topic exceptions written | 19 publish and 36 subscribe across the nine profiles, including A2A and the reserved reply-channel exception | asserted by the desired-state apply test in `packages/broker/tests/test_provisioning.py`; live reprovisioning remains pending |
| SEMP request timeout | 10 s per call | `REQUEST_TIMEOUT_SECONDS` in `packages/broker/src/aerial_rescue_broker/semp.py` |
| SEMP retry count | 0. A topic-exception `POST` is not idempotent, so re-running the whole convergent apply is the retry | `RETRY_COUNT` in the same module, asserted by a transport test |
| SEMP collection page size | 100 rows asked for per read; the broker pages at ten unless asked for more | `PAGE_SIZE` in the same module, asserted by a transport test |
| SEMP collection page bound | 20 pages per read, so at most 2,000 rows; a cursor still running at the bound is refused as `PAGING` rather than truncated | `MAX_PAGES` in the same module, asserted by a transport test |
| Broker-wide SEMP polling ceiling | 10 requests/s across every SEMP connection | official Solace monitoring guidance; shared-stack acceptance counts every routine, discovery, provisioning, and operator client rather than extrapolating from one process |

At revision `db2b640`, `tests/security/test_broker_authorization.py` passed 16 of 16 selected local
authorization controls in 0.57 seconds against the shared broker
([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
That result is not complete ACL, queue, TLS-downgrade, or Solace Cloud evidence.

## Broker data plane

The delivery semantics are [CONTRACTS.md](CONTRACTS.md#delivery-and-failure-semantics) and the typed
facade over the pinned client is [ADR-0028](adr/0028-untyped-solace-client-boundary.md). Which
guarantee each topic family is owed is a total table in `packages/contracts`
([ADR-0079](adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md),
[ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md)); routine telemetry is direct and
supersedable, and the endpoints that carry the guaranteed families are the section below.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Guaranteed publication timeout | 10 s per publication | `PUBLISH_TIMEOUT_MILLISECONDS` in `packages/broker/src/aerial_rescue_broker/messaging.py`, asserted by a publisher test |
| Guaranteed publication broker ACK | immediate for every individually confirmed application message | the Python 1.11 `PERSISTENT_ACK_IMMEDIATELY` message property, an exact builder test, and connected-path latency evidence ([ADR-0169](adr/0169-request-immediate-acks-for-confirmed-publications.md)) |
| Guaranteed publication DMQ eligibility | `true` on every application message, owned by the publisher adapter | the Python 1.11 `PERSISTENT_DMQ_ELIGIBLE` message property, an override-resistant builder test, and the independent queue-side override required by [ADR-0170](adr/0170-force-dmq-eligibility-at-the-publisher.md) |
| Direct publisher buffer capacity | 0, so a publication is refused when the transport is full rather than queued or buffered without bound | `DIRECT_BUFFER_CAPACITY` in the same module, asserted by a publisher test |
| Persistent publisher buffer capacity | 50 messages, reject on full | matches one generic application-outbox claim batch; exact builder and capacity-refusal tests |
| Direct telemetry receiver capacity | 1 message, drop oldest | the newest position supersedes a stale buffered position; exact builder and discard-listener tests |
| Other direct receiver capacity | 50 messages, supplied explicitly by composition | no receiver may inherit the elastic SDK default; composition and overflow-readiness tests |
| Recorder fan-in receive window | 1,000 ms, spent once a complete revolution of the Direct and durable channels has found nothing; every other poll waits zero, so a fan-in with traffic runs at the producer's rate ([ADR-0207](adr/0207-drain-the-recorder-fan-in-without-a-wait-per-channel.md)) | `RECEIVE_WINDOW_MILLISECONDS` in `services/recorder/src/aerial_rescue_recorder/console.py`; drain-shape, fair-cycle, cancellation, lifecycle-recovery, and exact-composition tests |
| Client keepalive | every 3,000 ms; fail after 3 unanswered probes | explicit SDK properties and lifecycle-listener tests; these own rather than tune the pinned SDK defaults |
| SDK endpoint termination grace | 15,000 ms | exact terminate-call and cleanup-continuation tests; aligned with the process/store shutdown grace |
| Broker container forced-stop grace | 1,200 s (`20m`) | `stop_grace_period` on the PubSub+ service plus the deployment conformance test selected by [ADR-0161](adr/0161-give-the-broker-a-twenty-minute-clean-stop.md) |
| One-shot broker-restart controller bound | 30 s independently for the request read, restart command, healthy-service wait, and result write | `OPERATION_TIMEOUT_SECONDS` in `scripts/ci/broker-restart-controller.sh`; hermetic exact-command, timeout, refusal, failure-propagation, and cleanup tests selected by [ADR-0186](adr/0186-delegate-one-broker-restart-without-project-authority.md) |
| Live restart result window | 120 s for the probe to receive the controller's one-shot result, covering both 30 s controller bounds, the broker's graceful stop, and its boot; the 30 s readiness observation that follows is the restart-recovery row above and starts when the result arrives | `RESTART_RESULT_POLLS × RECOVERY_POLL_SECONDS` in `tests/integration/test_application_data_plane_live.py`; measured 2026-08-27 on the Apple Silicon Docker Desktop reference host: about 14 s of graceful stop plus about 20 s of boot before the listen ports open |
| Connection-attempt timeout | 1,000 ms per attempt | exact Python SDK-property and composition tests; live connection-refusal timing remains part of shared-stack acceptance ([ADR-0145](adr/0145-bound-solace-recovery-and-queue-retirement.md)) |
| Initial connection retries | 2 retries after the first attempt | exact Python SDK-property and composition tests; live exhaustion timing remains part of shared-stack acceptance (ADR-0145) |
| Connection retries per host | 0 | exact Python SDK-property test; the standalone topology intentionally has one host (ADR-0145) |
| Active-session reconnection attempts | 60 | exact Python SDK-property, lifecycle-listener, endpoint-termination, and service nonzero-exit tests; the ADR-0186 restart in the live data-plane probe proves the budget covers a reference-host restart ([ADR-0192](adr/0192-cover-a-reference-host-broker-restart-with-the-reconnection-budget.md), which raised it from 30 after a measured 14 s stop plus 20 s boot exhausted the sessions) |
| Wait between active reconnection attempts | 1,000 ms | exact Python SDK-property and lifecycle-state tests; live reconnect timing remains part of shared-stack acceptance (ADR-0145) |

### PubSub+ client profiles

[ADR-0153](adr/0153-own-bounded-least-privilege-pubsub-clients.md) replaces the factory profile with a
total per-principal table. `GS` and `GR` are Guaranteed send and receive; `EC` is dynamic endpoint
creation; `C` is each of the total, SMF, and Web/WSS connection ceilings per username; `E/I` are egress
consumer and ingress publisher flows per client; `EP` is endpoints owned by the username; and `S` is
direct client subscriptions; the broker also counts the reply-inbox subscription the pinned SDK
installs on every session, so the provisioned `maxSubscriptionCount` is `S + 1` for each profile that
permits a connection and zero for `discovery`
([ADR-0191](adr/0191-reserve-one-subscription-for-the-sdk-reply-inbox.md)); the total provisioning
test and the live authorization suite are the instruments. `G1` is the explicit Guaranteed-1 minimum
message burst derived by
[ADR-0165](adr/0165-size-g1-bursts-to-the-complete-flow-set.md) from the complete allowed flow set. The
endpoint ceiling includes SEMP-created queues assigned to an owner even when that owner cannot create an
endpoint.

| Principal | GS / GR | EC / durability | C | E / I | EP | S | G1 | Instrument |
| --- | --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| fleet-simulator | yes / yes | no / non-durable guard | 1 | 23 / 1 | 23 | 0 | 23 | one shared fleet connection and 23 per-drone one-message durable flows; live high-water pending |
| command-gateway | yes / yes | no / non-durable guard | 1 | 3 / 1 | 3 | 2 | 3 | one owned long-lived connection with three one-message application flows; construction-count and live readback instruments |
| dashboard-api | yes / yes | no / non-durable guard | 1 | 6 / 1 | 6 | 3 | 6 | one owned long-lived connection with six one-message application flows; construction-count and live readback instruments |
| evidence-service | yes / yes | no / non-durable guard | 1 | 2 / 1 | 2 | 0 | 2 | one owned long-lived connection with two one-message application flows; construction-count and live readback instruments |
| recorder | no / yes | no / non-durable guard | 1 | 10 / 0 | 10 | 3 | 10 | one receiver-only connection with nine named flows (one below the flow ceiling); construction-count and live readback instruments |
| event-mesh-gateway | yes / yes | yes / non-durable | 4 | 1 / 1 | 2 | 0 | 255 | one pinned upstream default-window flow; the live steady state of 2026-08-28 held two temporaries, at the ceiling (`release-evidence/phase-3/merged-runtime-first-run.md`) |
| event-mesh-tool | yes / yes | yes / non-durable | 1 | 1 / 1 | 1 | 0 | 255 | one pinned upstream default-window flow; the live steady state of 2026-08-28 held one temporary, the request/response reply queue, at the ceiling (`release-evidence/phase-3/merged-runtime-first-run.md`) |
| agent-mesh-agent | yes / yes | yes / non-durable | 13 | 1 / 1 | 7 | 0 | 255 | six connector apps on one identity (Orchestrator, MissionCoordinator, MissionResponse, SectorPlanner, EvidenceFusion, Web UI) plus the Web UI's internal visualization app; five `a2a`/gateway queues plus the visualization queue. Both values are the measured high-water of 2026-08-31 across startup, one complete workflow execution, and one restart, provisioned exactly rather than with margin ([ADR-0217](adr/0217-raise-the-agent-mesh-identity-ceilings-for-the-phase-5-agent-set.md)) |

The retired `scenario-service` broker principal has neither a profile nor a username; the brokerless
scenario process remains active over private HTTP. The disabled `discovery` role retains deny-all ACL
and zero-capability client profiles for fail-closed totality but has no
username that can connect; its explicit G-1 minimum burst is zero. Every owned profile explicitly
disables compression, eliding, transactions, bridges, shared subscriptions, endpoint-permission
override, and TLS downgrade; the total provisioning tests and live SEMP readback are the instruments.
Transaction counts are zero. SMF minimum keepalive is enabled at 30 seconds; explicit TCP keepalive uses
5 probes, 3 seconds idle, and 1 second between probes. Project Guaranteed publishers reject a send with
no matching Guaranteed subscription; the three pinned upstream profiles retain that option disabled
until their broadcast and contract-Direct paths pass the compatibility probe.

## Guaranteed-delivery endpoints

One durable primary per consuming role and guaranteed family, with the recorder's lifecycle families
consolidated, plus one command primary per simulated drone and one isolated DMQ per application primary
([ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md),
[ADR-0120](adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md),
[ADR-0157](adr/0157-pace-and-coalesce-read-only-semp-monitoring.md)). Three bounded upstream template
DMQs coexist with those pairs. Every value below is
written explicitly into each queue rather than inherited, because each corresponding broker default
is wrong for this system: redelivery retries forever, expiry is ignored, the per-queue spool exceeds
the whole message VPN's, and the default dead-message target names a queue that does not exist.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Queue maximum spool | 10 MB per queue | `MAX_SPOOL_MEGABYTES` in `packages/broker/src/aerial_rescue_broker/queues.py`, written into every queue and asserted by a provisioning test |
| Queue maximum message size | 262,144 bytes for application primary/DMQ pairs; 10,000,000 bytes explicitly for pinned upstream temporary endpoints | the application boundary is the largest accepted service wire document; exact SEMP body/readback and oversized-publication tests |
| Delivered but unacknowledged | 1 per flow on application primaries, aligned with receiver window 1 | exact source-queue body/readback and one-at-a-time commit-before-settlement test |
| Queue maximum redelivery | 3 redeliveries after the first delivery, so at most 4 arrivals, then the dead-message queue | `MAX_REDELIVERY_COUNT` in the same module; the live probe reads `MAX_REDELIVERY_COUNT + 1` arrivals before the dead-message queue |
| Queue message expiry | 300 s, with expiry respected rather than the factory `false` | `MAX_TTL_SECONDS` in the same module, written together with `respectTtlEnabled` |
| Dead-message-queue target | one unowned, unsubscribed `<complete-primary-name>_dmq` per primary; no recursive DMQ or expiry | desired-pair derivation and exact SEMP body/readback tests; acceptance reports each pair's delta |
| Consumer flows per queue | 1, exclusive | `MAX_BIND_COUNT` in the same module, asserted per queue |
| Queue permission for every identity but the owner | `no-access` | asserted per queue; the owner is the consuming role's client username |
| Discard notification | `always`, so a discard is negatively acknowledged to the publisher even when the endpoint is administratively disabled | asserted per queue |
| Endpoints the reference fleet needs | 89 broker-managed queues: 43 application primaries (20 family and 23 per-drone), 43 paired application DMQs, and 3 upstream-template DMQs; up to 8 bounded upstream temporary primaries coexist (four, two, and one at steady state plus the coordinator's reply queue, ADR-0196) | derived from the current grants and asserted by `test_the_reference_fleet_reserves_eighty_nine_queues_and_890_megabytes`; live reprovisioning/readback is pending. The message VPN's measured ceilings are 1,000 endpoints and 1,500 MB of spool |
| Effective endpoint ceiling of the pinned broker | 100 durable and temporary queues per message VPN on the 100-connection scaling tier (`maxEffectiveEndpointCount`), whatever the VPN's configured `maxEndpointCount` states | live SEMP readback on 2026-08-27 ([ADR-0191](adr/0191-reserve-one-subscription-for-the-sdk-reply-inbox.md) records the same first live apply); the reference 89 plus the pinned upstream temporaries fit, and the CI-only 28-drone probe roster of 99 queues cannot coexist with the reference roster |
| Two-drone provisioning fixture | 47 durable queues and 24 subscriptions | desired-state apply/deployment tests; this is the exact fixture used to verify nine profiles, eight enabled usernames, and 55 ACL exceptions |
| Routine queue monitor interval | 30 s between complete attempts, successful or failed | `MONITOR_POLL_INTERVAL_SECONDS`; cache/failure-coalescing tests prove calls inside the interval perform no read |
| Routine monitor SEMP share | at most 5 requests/s with at least 200 ms between pages, reserving half of the broker-wide ceiling for other clients | `ROUTINE_MONITOR_REQUESTS_PER_SECOND` and `MONITOR_REQUEST_INTERVAL_SECONDS`; deterministic clock and page-pacing tests |
| Routine parent queue inventory bound | 20 pages of 100 rows, so at most 2,000 observed queue rows | `MAX_MONITORED_QUEUES` aligned to the SEMP transport page size and bound; duplicate, malformed, and over-bound parent inventories fail closed |
| Routine active-bind fan-out | At most 89 observed desired queues, read sequentially after the parent inventory; at most 109 requests per complete attempt | `MAX_MONITORED_BIND_COUNTS`, the exact reference-fleet queue count, plus the 20-page parent bound; tests prove stable selection, no foreign/unexpected/missing fan-out, pre-I/O refusal above the bound, and coalesced child-read failure ([ADR-0190](adr/0190-count-active-queue-binds-through-transmit-flow-aggregates.md)) |
| Continuous SEMP monitor restart and stop | At most 3 restarts after a read failure; 15 s graceful stop | `on-failure:3` and `stop_grace_period` on the opt-in `semp-monitor`; connection-close, redaction, refusal, and static isolation tests selected by [ADR-0181](adr/0181-gate-continuous-semp-monitoring-on-vpn-scoped-operator-provisioning.md) |
| Recorder poll cycle | one total blocking wait of 100 ms, followed by zero-wait round-robin drain to a maximum batch of 64 messages | recorder capture-loop unit and 280-telemetry burst tests |
| Recorder readiness lease | 2 s refresh, 10 s expiry, 256-byte maximum, canonical integer epoch | shared freshness-codec tests, recorder startup/shutdown tests, dashboard mode-readiness tests, and the Compose healthcheck |

The four rows ADR-0061 left open are derived from the service-level rows above rather than measured,
in the same position as the gateway acknowledgement timeout. The **spool** follows from the two rows
that bound a backlog: an event is at most 2 KiB and 500 critical messages must drain within 10 s, so
a queue must hold at least 1 MB; 10 MB is 5,000 messages at that bound, ten times the drain envelope,
and the reference fleet's 89 desired queues reserve a nominal 890 MB against the VPN's
measured 1500 MB. **Expiry** follows from the worst
declared fault: a 60 s edge disconnect, a 30 s restart recovery, and a 10 s drain give a 100 s worst
case, and 300 s is three times that. It is deliberately longer than the 60 s approval time-to-live,
so a queue's expiry never stands in for the approval protocol's own two-clock consumption
([ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)); a shorter value would
have made a queue depth quietly do safety work an approval record owns. **Redelivery** is bounded so
that a message a consumer cannot settle leaves an exclusive queue instead of holding the 10 s drain
behind it, and it is a different fact from the command send budget, which counts the times the gateway
put a command on the wire ([ADR-0074](adr/0074-command-dispatch-lifecycle.md)).

None of these gates safety: exceeding any one costs a delivery and leaves a counted dead-message
entry, never a command published without an approval. The backlog-recovery row is measured by the
500-message live instrument above. That measurement models an absent consumer; it does not prove the
ADR-0145 broken-session reconnect, rebind, outbox-drain, or readiness-recovery behavior.

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

Neither gates safety, and neither is the measurement. The separate live backlog-recovery instrument
measured the 500-message drain in 7.141 seconds at worst; it models an absent consumer rather than a
broken transport session or an ADR-0145 reconnect.

## Durable store

Every wait the PostgreSQL adapter is allowed to make ([ADR-0090](adr/0090-bound-the-lock-wait-below-the-statement-time.md), which supersedes [ADR-0085](adr/0085-bound-every-durable-store-wait.md)).
Measured on the pinned cluster on 2026-08-23, `statement_timeout`, `lock_timeout`, and
`idle_in_transaction_session_timeout` are all `0`, which is not a conservative default but no bound at
all, so every row below replaces an unbounded wait rather than tightening a loose one. Each value is
derived from a number elsewhere in this document; none is calibrated under load. At revision `db2b640`,
the exact disposable-PostgreSQL selector passed 43 of 43 cases in 14.24 seconds across the five-revision
history and revision-0005 repository paths
([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
That suite does not prove killed-process recovery, persistent-project restart durability, or whole-stack
resource behavior.

| Parameter | Value | Instrument |
| --- | --- | --- |
| Pool size | 5 sessions per process | `POOL_SIZE` in `packages/store/src/aerial_rescue_store/bounds.py`; `EngineBounds` refuses a size below one |
| Pool overflow | 0, so exhaustion is a bounded refusal rather than an unbounded queue | `POOL_OVERFLOW` in the same module, which refuses a negative count and accepts zero |
| Connection checkout timeout | 2 s, the connected command path's own p95 target | `CHECKOUT_TIMEOUT_SECONDS` in the same module |
| Connect timeout | 5 s | `CONNECT_TIMEOUT_SECONDS` in the same module |
| Connect retries | 0, so a store connection failure returns to the service's bounded lifecycle rather than multiplying the database driver's attempts | `CONNECT_RETRIES` in the same module |
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
command-specific outbox; ADR-0146's general application outbox and per-drone critical bounds are
separate rows under [Durable application processing](#durable-application-processing).

| Parameter | Value | Instrument |
| --- | --- | --- |
| Central outbox maximum unconfirmed records | 500, the workload [ADR-0084](adr/0084-give-backlog-recovery-an-instrument.md)'s instrument uses and [backlog-recovery-first-run.md](../release-evidence/phase-2/backlog-recovery-first-run.md) measured draining in 7.141 s | `MAXIMUM_UNCONFIRMED_RECORDS` in `packages/store/src/aerial_rescue_store/outbox.py`, evaluated inside the staging statement |
| Central outbox overshoot under concurrency | At most one record per concurrently staging session, which the pool bounds at 5 per process. The effective ceiling is 504 | A consequence of `READ COMMITTED` recorded in ADR-0093, not a configured value |
| Central outbox byte ceiling | None, deliberately: a staged record is one command envelope, and every member of one is already bounded by the topic and envelope rows above | ADR-0093 records the reasoning; there is nothing to measure |
| Central outbox overflow behaviour | Staging writes no command row and refuses. ADR-0146 enlarges an accepted command authorization to include its typed audit record in the same transaction; a refused overflow remains a separately durable refusal outcome rather than a partial accepted authorization | `OutboxRefusal.AT_CAPACITY` plus command-gateway transaction and rollback tests; live qualification is pending |

## Parameters still to be set

Each of these is required by a claim made elsewhere and has no value yet. Every row is a gap, not a
preference, and must carry a number before the release run.

| Parameter | Required by | Status |
| --- | --- | --- |
| Ollama `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` | Warm-model residency across missions | open |
| Instrument definition per service-level row: start point, end point, clock, sample count, statistic, warm-up discarded, machine-state precondition | Every row of the table above | open |
