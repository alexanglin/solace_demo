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
| Python Tier 3 test inventory | At least 1 smoke test and 1 failure-path test per module | Tier inventory gate; Tier 3 fails until this inventory is executable |
| TypeScript coverage | 95% each for statements, branches, functions, and lines per production package | Vitest coverage thresholds |
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
| Unwaived known advisory | Zero permitted in any audited dependency domain | `tools/dependency_waiver_gate.py` over pip-audit JSON |
| Directory fan-out | At most 20 files per directory, counted as immediate children only | `tools/directory_fanout_gate.py` over `git ls-files --cached --others --exclude-standard` |
| Fan-out exemption reason | At least 20 Unicode characters | `directory-fanout.toml` validation |

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

## Connectivity detection

The heartbeat is a dedicated liveness signal, not an inference from the telemetry stream: routine telemetry
uses direct delivery and may be dropped under congestion, so absence of telemetry is not evidence of
absence of the drone. Transitions are counted in consecutive missed heartbeat intervals rather than as a
wall-clock gap, which gives hysteresis and makes the behaviour reproducible under a deterministic clock.

| Parameter | Value | Notes |
| --- | --- | --- |
| Heartbeat interval | 1 s (provisional -- confirm in Phase 0) | Matches the telemetry rate so no extra timer is needed |
| Consecutive misses to enter `DEGRADED` | 3 (provisional -- confirm in Phase 0) | |
| Consecutive misses to enter `OFFLINE` | 6 (provisional -- confirm in Phase 0) | Consistent with the 6-second offline-detection target above |
| Consecutive heartbeats to leave `OFFLINE` | 2 (provisional -- confirm in Phase 0) | Prevents flapping on a marginal link |

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

## Parameters still to be set

Each of these is required by a claim made elsewhere and has no value yet. Every row is a gap, not a
preference, and must carry a number before the release run.

| Parameter | Required by | Status |
| --- | --- | --- |
| Approval time-to-live | [ADR-0006](adr/0006-proposal-bound-single-use-approvals.md), which requires an expiry window to be "chosen and justified" | open |
| Per-drone outbox maximum records and bytes | The bounded-outbox claim in [CONTRACTS.md](CONTRACTS.md) | open |
| Outbox overflow behaviour | A critical-record overflow must refuse the write and emit a continuity-breach audit record; a critical record is never silently dropped | decided, unquantified |
| Queue maximum spool, maximum redelivery, message TTL, dead-message-queue target | The no-loss claim's fault envelope | open |
| SSE per-client buffer bound and droppable-event classes | The soak target, and the rule that audit, approval, and evidence events are never dropped | open |
| Container image tags and digests for PubSub+ and Postgres | Reproducibility from a clean checkout | open |
| Ollama `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_KEEP_ALIVE` | Warm-model residency across missions | open |
| Instrument definition per service-level row: start point, end point, clock, sample count, statistic, warm-up discarded, machine-state precondition | Every row of the table above | open |
