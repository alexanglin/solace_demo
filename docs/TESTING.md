# Test-driven development and quality gates

> **Authority:** this document is the single home for executable-test structure, coverage tiers, test classes, and the test toolchain. `docs/IMPLEMENTATION_PLAN.md` and
> `AGENTS.md` reference it and must not restate it ([ADR-0016](adr/0016-documentation-set-split.md)).
> Where this document and an `Accepted` ADR disagree, the ADR governs.
>
> **Related:** [ADR-0015](adr/0015-tiered-quality-gates.md) (risk tiers), [ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md) (mutation tool, score, tier assignment), [ADR-0011](adr/0011-no-exception-lint-typecheck-and-complexity-budgets.md) (lint, typecheck, complexity), [ADR-0012](adr/0012-git-hooks-with-ci-as-authority.md) (hook stages, CI as authority), [ADR-0010](adr/0010-uv-workspace-and-toolchain.md) (per-member gates), [ADR-0018](adr/0018-enforced-arrange-act-assert.md) (mandatory AAA structure), and [ADR-0023](adr/0023-executable-deep-quality-gates.md) (executable complexity, duplication, and mutation gates).

## The TDD workflow

The red-green-refactor loop, the two human-approval gates, and the prohibition on weakening a test to
conceal a defect are **process rules** and live in [`AGENTS.md`](../AGENTS.md) section 5. They are not
restated here ([ADR-0016](adr/0016-documentation-set-split.md)).

This document defines *what* must be tested and to what standard. `AGENTS.md` defines *how* to work.

## Mandatory Arrange-Act-Assert structure

Every project-owned executable test case must contain exactly one explicit, ordered
**Arrange-Act-Assert (AAA)** sequence. This applies to every supported language, framework, test class,
and risk tier, including unit, property-based, contract, integration, end-to-end, Playwright, security,
failure-injection, compatibility, and replay tests.

- **Arrange** constructs the system under test, inputs, collaborators, and preconditions.
- **Act** exercises one observable behavior. Multiple low-level statements are allowed when they form one
  named scenario operation; whether they do is a review obligation because a syntax checker cannot infer
  domain intent.
- **Assert** verifies the externally observable result and, where applicable, persisted state, emitted
  events or messages, side effects, error behavior, and safety invariants.

Python tests use these exact, case-sensitive, standalone comments at the direct test-body indentation:

```python
def test_example() -> None:
    # Arrange
    expected = 42

    # Act
    result = calculate()

    # Assert
    assert result == expected
```

JavaScript and TypeScript tests use these exact, case-sensitive, standalone comments in an inline Vitest
or Playwright callback block:

```typescript
test("calculates", () => {
  // Arrange
  const expected = 42;

  // Act
  const result = calculate();

  // Assert
  expect(result).toBe(expected);
});
```

The gate enforces all of the following:

- Exactly one marker for each phase, in Arrange, Act, Assert order.
- Every phase contains executable code; `pass`, an ellipsis, and empty statements do not count.
- Only an optional Python test docstring may precede Arrange.
- Assertions occur only in Assert, and Assert contains at least one recognized outcome assertion.
- Markers are direct children of the test body, never nested in a conditional, callback, multiline
  expression, block comment, or string.
- Python `test*` functions and methods, normal Hypothesis tests, and Hypothesis `@rule` and `@invariant`
  methods are covered. Dynamically generated test callables are forbidden.
- Vitest and Playwright test imports and aliases, inline callbacks, and parameterized `.each` tests are
  covered. Named callbacks, expression-bodied callbacks, `test.todo`, global test registration, computed
  test factories, and unrecognized test dialects fail closed.
- Exception and warning outcomes are captured during Act and inspected during Assert. A fused construct is
  not sufficient by itself as the Assert-phase outcome oracle.
- There is no inline ignore, path exclusion, marker alias, Given-When-Then substitute, or per-test waiver.

Parameterized and generated inputs inherit the AAA structure of their authored executable test body.
Fixtures, lifecycle hooks, helper functions, strategies, declarative evaluation datasets, and golden
fixtures are support artifacts rather than executable test cases; the project-owned tests that consume
them remain subject to AAA. Unmodified installed, vendored, or generated upstream tests are not
project-owned, but every project-owned black-box wrapper around them is covered.

The checker scans every tracked or unignored Python, JavaScript, and TypeScript source file, not just
conventional test directories. A new language, framework, registration dialect, or dynamic test mechanism
cannot land until checker support and conformance cases land first. Parse failures and checker dependency
failures are blocking. The checker's own positive and negative conformance suite runs before every scan;
the gate has no success-on-missing-tool path.

This is structural enforcement, not a claim that comments make a test meaningful. Review, behavior-focused
assertions, coverage, property testing, failure injection, and mutation testing remain responsible for
semantic quality. AAA enforcement also cannot prove that the test was authored and observed red before
production code; the red-green-refactor evidence required by `AGENTS.md` remains a separate process gate.

## Coverage

Coverage is enforced per language and per package, not as one global total, and is tiered by risk. See
[ADR-0015](adr/0015-tiered-quality-gates.md); [ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md)
carries the per-package tier assignment, the mutation tool, and the mutation score. The current numeric
limits and their instruments live only in [operating-parameters.md](operating-parameters.md#code-quality-gates).

- **Python:** statement coverage and branch coverage are measured independently per `uv` workspace
  member at its declared risk tier. `coverage.py` reports no function-coverage metric, and statements and
  lines are the same measurement, so those are not separate Python dimensions.
- **TypeScript:** Vitest measures statements, branches, functions, and lines independently per
  production package. `tools/typescript_coverage_gate.py` treats its JSON summary as untrusted,
  recomputes the aggregate with integer arithmetic, and requires an exact match with every
  hand-written production source enumerated by the wrapper. Missing, empty, skipped, malformed,
  duplicate-key, out-of-inventory, or coverage-ignored evidence fails closed
  ([ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md)).
- **Dashboard Tier 1 coverage:** the canonical bootstrap decoder, Ajv schema registry, canonical
  digest, ordered reducer, and mutation client are an exact five-file inventory. Each file independently
  requires complete statement and branch coverage from the same full Vitest pass. Missing modules,
  inventory entries, report entries, and measurable statements fail closed
  ([ADR-0130](adr/0130-enforce-dashboard-tier-one-coverage-per-file.md)). Source-session and runtime
  transport orchestration plus UI and presentation modules remain under the package-wide four-dimensional
  gate; they are not silently classified as Tier 1.
- **Safety-critical core:** approval authorization, command-gateway dispatch, domain state machines,
  idempotency and sequence rules, evidence scoring, and `packages/contracts` carry the Tier 1 coverage,
  property-based, failure-injection, and mutation obligations.
- **Configuration and glue:** Tier 3 uses the smoke-and-failure-path inventory rather than borrowing the
  Tier 2 percentage. Until that inventory gate exists, a Tier 3 member fails closed.

Installed Agent Mesh, plugin, generated, and vendored code is excluded from owned-code coverage but remains subject to black-box compatibility, contract, integration, evaluation, and security tests. Configuration, owned adapters, error branches, and UI state logic are not exempt. Coverage is a gate, not a substitute for behavior-focused assertions.

An active workspace member with no measurable production statements fails rather than passing vacuously.
A scaffolded member — a manifest, docstring-only modules, a `py.typed` marker, and no `tests/`
directory — is reported as `SCAFFOLD` by the coverage gate and skipped by the mutation gate, and it
becomes an active member at its first executable statement or test file
([ADR-0053](adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md)).
Coverage results are compared with integer arithmetic so display rounding cannot turn a value below a
threshold into a pass ([ADR-0019](adr/0019-fail-closed-quality-gates.md)).

## Maintainability and mutation gates

Ruff enforces the cyclomatic, function-size, branch, return, local-variable, parameter, nesting, and
Boolean-expression budgets over the complete owned Python tree. Complexipy independently enforces
cognitive complexity. jscpd performs one multi-language strict duplication scan across authored source,
tests, and scripts; generated dashboard contract types are outside it, because one module per schema is
fixed by [ADR-0058](adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) and their
freshness gate rewrites and byte-compares that directory
([ADR-0110](adr/0110-scope-the-duplication-gate-to-authored-source.md)). Their values and instruments are in
[operating-parameters.md](operating-parameters.md#code-quality-gates); `scripts/hooks/` contains the
canonical fail-closed entry points. Static analyzers may parse both Python trees from the root tool
environment because they do not import or execute the Agent Mesh project.

Mutation runs independently from each Tier 1 workspace member. A Tier 1 member must contain its own
`tests/` directory because mutmut executes from that member's working directory; root `tests/` remains
the home for cross-component contract, integration, end-to-end, and acceptance coverage but cannot
substitute for member-local mutation tests. Each member's configuration selects `src/`, selects its local
tests, and invalidates cached results when tests, workspace source, manifests, or the lock change.

The gate evaluates each mutated module separately. Reviewed survivors remain in the score denominator,
and `mutation-survivors.toml` must bind each review to an exact current Tier 1 member and mutant with a
reason, reviewer, review date, and expiry. Killed, missing, expired, or out-of-scope records fail. Missing
metadata, zero generated mutants, duplicate identities, unreviewed survivors, incomplete runs, timeouts,
skips, suspicious outcomes, type-check-only catches, and unknown statuses also fail.

Mutmut 3.7.0 mutates function bodies only. The project does not claim mutation evidence for module-level
declarations or other non-function constructs; coverage, property tests, failure injection, contracts,
and review remain mandatory for those behaviors.

## Test classes

- **Offline Agent Mesh owned-code tests:** In the isolated Python 3.13 environment, the exact pinned
  upstream configuration models, distribution-bound symbols, and Solace AI Connector merge primitive
  validate includes, environment references against `.env.example`, secret indirection, broker fields
  and transport, model policy, gateway settlement and routing policy, cross-file app-name uniqueness,
  and Event Mesh Tool topic authority without starting a process or client. The same suite verifies the
  owned Direct gateway-output extension against the pinned SDK seam, including bounded backpressure,
  receipt truthfulness, refusal, and shutdown behavior. The validator and owned extension are held at
  100% statement and branch coverage by the Agent Mesh test stage
  ([operating-parameters.md](operating-parameters.md)). The gate is inert before the first owned
  configuration and fails closed afterward.
- **Unit tests:** Pure domain rules, state machines, retry logic, validation, prompt-result parsing, reducers, and UI components.
- **Property-based tests:** Event ordering, idempotency, coordinate ranges, schema round trips, and state-machine invariants using Hypothesis.
- **Contract tests:** Python and TypeScript validate the same JSON Schemas, topic rules, CloudEvents, OpenAPI schema, and golden fixtures.
- **Broker integration tests:** The PubSub+ software event broker container from `deploy/compose.yaml`
  ([ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md)) covering direct and persistent
  delivery, queues, reconnects, acknowledgement, and ACL denial. At revision `db2b640`, the selected
  local authorization suite passed 16 of 16 cases in 0.57 seconds against the shared broker
  ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
  That selector is not complete ACL, queue, TLS-downgrade, or Solace Cloud evidence. Blocking continuous
  integration is selected by [ADR-0147](adr/0147-admit-pubsub-integration-to-blocking-ci.md): the job owns
  an ephemeral broker, PostgreSQL database, generated CI-only credentials, exact reviewed live-file
  allowlist, and unconditional project-scoped cleanup. Local execution of the identical harness is
  evidence for the script, not a claim that the unpushed hosted job has run.
  Offline broker tests separately prove the pinned SEMP queue contract: literal parent depth selection,
  aligned message counts, count-only per-queue transmit-flow reads, bounded stable fan-out, malformed
  count refusal, and delete-before-readback denial. Only an authorized live positive can prove that the
  running broker exposes those fields ([ADR-0190](adr/0190-count-active-queue-binds-through-transmit-flow-aggregates.md)).
- **Application-data-plane live test:** `tests/integration/test_application_data_plane_live.py` joins the
  authenticated scenario and fleet runtimes, Direct telemetry, Agent Mesh structured response, canonical
  proposal, evidence decision, exact approval, one logical command effect/result, recorder audit, and
  dashboard projection. Its broker-restart controller may restart only the resolved PubSub+ container;
  the test then proves readiness degradation, durable spooling, consumer rebind, outbox drain, recovery,
  zero missing critical identities, and zero duplicate command effects. It runs serially with the nine
  pre-existing authorized live files and never broadens selection through a resource marker.
- **Durable-store integration tests:** The PostgreSQL container from `deploy/compose.yaml`, against a
  database the run creates and drops
  ([ADR-0086](adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)). They are the only
  evidence for isolation, constraints, transaction visibility, Alembic behaviour, restart durability,
  pool cancellation, and concurrent races; the store's own member suite never opens a connection and
  establishes none of them. The migration cases walk the ten-revision history through revision 0010 in
  both directions and exercise prepared-before-start persistence, exact-byte start/reset replay,
  same-run pending recovery, predecessor retention, broker deduplication, application inbox/outbox,
  proposals, evidence, command progress, durable receipts, dashboard command/decision idempotency, and
  snapshot reads. The complete schema contains 25 SQLAlchemy-owned tables. At revision `db2b640`, the
  exact selector passed 43 of 43 cases in 14.24 seconds: 41 PostgreSQL cases each created and dropped
  its own disposable database, while two local cases exercised target-name and refusal behavior
  ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
  Those cases do not claim a killed-process recovery test. They carry `integration` and `docker`, and
  never `broker`.
- **Provider integration tests:** Local Ollama, the pinned Agent Mesh runtime, A2A discovery and
  delegation, and both pinned Event Mesh plugins. A test asserting transport, schema, or error handling
  uses a deterministic stub at the model boundary; only a test asserting model capability calls a real
  model, and those are the model-dependent class excluded from the blocking safety gate.
- **Dashboard integration tests:** Dedicated `*.integration.test.ts` and
  `*.integration.test.tsx` specifications exercise composition across production browser modules. The A1
  case joins the production HTML host to the real application entry point. A4 carries a manifest-owned
  replay through canonical decoding, Ajv and Pydantic validation, and the production Python and
  TypeScript reducers to compare every checkpoint across ten independent runs. A5 extends the same class
  through all three source adapters, including disposal, stale callbacks, last-valid-state retention, and
  exactly one overload resnapshot; later cases carry serialized boundary input through mutation and render
  composition. The production-asset case builds the actual minified browser output, measures every
  JavaScript chunk and CSS asset through the same owner as Vite, and proves that an over-budget output
  blocks. The separate non-empty suite blocks at pre-push and in continuous integration, and the
  complete Vitest coverage run includes it.
- **End-to-end tests:** Mission start through evidence, connectivity failure, replan, approval, and completion.
- **User acceptance tests:** The manifest-owned Playwright inventory exercises the selected operator
  slice through serialized fixture inputs, including live/replay labeling and the explicit absence of
  deferred approval, command, evidence, model, rescue, and escalation controls. At revision `db2b640`,
  all 64 fixture cases passed in 42.0 seconds with no failure, skip, or retry
  ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md));
  the manifest count and discovery command remain the inventory authority.
- **Dashboard production end-to-end tests:** The production driver contains eight serial cases: four
  operator/replay workflows and four resilience workflows. Fixture selection, request interception,
  production test globals, and production control routes are forbidden. The resilience cases restart
  the dashboard API before the first accepted snapshot to prove stale-runtime lockout, stop the recorder
  to prove typed `503` readiness blockers and `200` recovery, and stop Caddy beyond the browser outage
  bound before restoring offline-to-recovered behavior on the same API runtime. The overload case pauses
  Caddy without pausing the API, targets a retained `EXHAUSTED` predecessor with the bounded pressure
  sources in [operating-parameters.md](operating-parameters.md#dashboard-event-stream), and proves that
  the current `PLANNED` successor and its audit ordinal do not change. It also holds API process identity,
  requires the terminal frame, and permits exactly one browser resnapshot under
  [ADR-0141](adr/0141-exhaust-deployed-sse-buffers-with-two-bounded-producers.md). The retained-history
  semantics are fixed by
  [ADR-0142](adr/0142-retain-dashboard-pressure-history-in-the-shared-runtime.md). The live mission case
  observes fleet publication independently from best-effort recorder receipt; a receipt count is not a
  telemetry-completeness guarantee. Replay is the final serial case so isolated mode cannot contaminate
  an operational workflow. At revision `db2b640`, all eight cases passed against the shared
  `aerial-rescue-mesh` closure in 1.6 minutes with no failure, skip, or retry
  ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
  The prepared production mission reached 14 ticks and 280 successful fleet publications. A separate
  post-soak mission readback recorded 280 best-effort telemetry receipts and 328 audit events. Receipt
  equality is an observation from that run, not a telemetry-completeness guarantee. The dedicated
  continuous-integration job will also run the resourceful
  broker, store, recorder, replay, HTTP, SSE, and packaging integration class. Once admitted, missing
  runtime evidence will fail rather than falling back to fixture acceptance
  ([ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md)).
- **Agent evaluations:** Curated datasets validate delegation, tool selection, structured outputs, refusal of unsafe requests, and approval behavior.
- **Failure-injection tests:** Broker loss, Agent Mesh container loss, Ollama loss, duplicate and out-of-order events, malformed input, model timeout, invalid output, and recovery.
- **Performance tests:** The full fleet at the telemetry rate, dashboard update latency, queue-backlog
  recovery, and the separate thirty-minute Playwright soak. Its 61 browser/process samples fail on
  transport or readiness loss, process replacement, remote requests, or RSS/file-descriptor growth over
  the accepted envelope without adding a production probe endpoint. At revision `db2b640`, the soak
  passed its single case and all 61 samples in 30.3 minutes: API container and PID remained stable; RSS
  growth stayed at most 64 MiB; file-descriptor growth stayed at most 8; and every browser sample remained
  READY and CONNECTED with the map visible, zero alerts, and zero remote requests. The retained record
  does not contain the numeric baseline or maximum values; its separate post-soak point sample was
  114,425,856 bytes RSS and 12 file descriptors, which is neither a baseline nor a maximum
  ([wilderness-dashboard-production-first-run.md](../release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).
  The soak measures only the dashboard API process after mission exhaustion, not browser, broker,
  PostgreSQL, other-service, or whole-stack resources. Every target and instrument is in
  [operating-parameters.md](operating-parameters.md).
- **Security tests:** Secret scanning, dependency scanning, container-image and deploy misconfiguration
  scanning, workflow auditing, CodeQL static analysis, authorization-negative cases, schema fuzzing,
  and prompt-injection cases.
- **Black-box compatibility tests:** The exact Agent Mesh and plugin wheels — configuration startup, agent-card discovery, A2A delegation, Event Mesh Gateway transformation and settlement, Event Mesh Tool request/reply, and ACL denial. The acceptance-evidence column in [ARCHITECTURE.md](ARCHITECTURE.md) is this class's case list.
- **Mutation tests:** Run against safety gates, state transitions, idempotency, and evidence-score logic.
- **Replay tests:** Replaying a committed fixture must produce the same ordered domain outcome and dashboard state.

A green offline Agent Mesh configuration result satisfies only the semantic gate in
[ADR-0032](adr/0032-agent-mesh-semantic-configuration-validator.md). It does not satisfy broker
integration, provider integration, or black-box compatibility. Live PubSub+ and Ollama messaging is
the next Phase 0 evidence for A2A behaviour, plugin delivery and settlement, ACL denial, and
structured model output.

## Which tests run at which stage

[ADR-0012](adr/0012-git-hooks-with-ci-as-authority.md) splits execution by cost: the commit stage runs
the tests the change affects, and the push stage runs all of them. Both halves are enforced, and
`tools/quality_gate_tests/selection/` fails if either is removed.

**Commit stage — affected tests, in all three toolchains.** `tools/affected_tests.py` builds an import
graph over the owned tree, resolves each staged Python path to a module, and selects the test files
that transitively import it
([ADR-0066](adr/0066-select-commit-stage-tests-from-an-import-graph.md)). The root tree and the Agent
Mesh tree are selected separately, because the Agent Mesh domain carries its own `tools` package and
its tests run on their own 3.13 interpreter
([ADR-0029](adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)). The dashboard uses
`vitest related` on staged `.ts` and `.tsx` files. Pre-commit serializes any filename partitions before
starting Vitest because Vitest already schedules the selected files internally; this prevents multiple
jsdom pools from competing until valid five-second waits fail
([ADR-0144](adr/0144-serialize-the-related-dashboard-test-hook.md)).

The selector fails safe rather than guessing. A staged path that is not a Python file in the graph, is
a `conftest.py`, has an ambiguous module name, or does not parse, widens the run to the whole
deterministic suite. That is what makes a change to a hook script, a workflow, a manifest, or a
committed registry run the tests that read it, none of which any import names.

**Push stage — every unit, deterministic dashboard integration, and dashboard acceptance test,
unconditionally.** `pytest-full.sh` runs the
root suite with the per-member coverage gates, `agent-mesh-test-full.sh` runs the Agent Mesh suite on its
own interpreter, `dashboard-test-full.sh` runs the complete dashboard unit/component/integration suite
and independently adjudicates all four coverage dimensions, and `dashboard-integration-full.sh`
separately refuses an empty dedicated integration inventory. `dashboard-playwright-full.sh` verifies
discovery against the manifest-owned inventory and runs every Playwright acceptance case against the
package-pinned Chromium. None selects a subset, and conformance tests hold every entry point to that
whole-suite contract.

Narrowing the commit stage is only safe while the push stage stays whole. Selection is fast feedback;
`pre-push` and continuous integration remain the authority.

## Tooling

Python quality gates use pytest, pytest-asyncio, pytest-cov, Hypothesis, Ruff, strict mypy, Complexipy
7.0.1, Bandit, pip-audit, and mutmut 3.7.0 with per-module scoring over the Tier 1 core
([ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md),
[ADR-0023](adr/0023-executable-deep-quality-gates.md)). Root-workspace strict mypy loads the official
plugin shipped by the pinned Pydantic dependency so service-owned model constructors remain typed while
the global explicit-`Any` prohibition stays enabled; a configuration conformance test holds the policy
selected by [ADR-0109](adr/0109-enable-the-pydantic-mypy-plugin-with-typed-constructors.md). The separate
Agent Mesh environment does not load that plugin. Bandit ignores inline suppression comments
and blocks medium-or-higher findings at medium-or-higher confidence; dependency auditing operates on
hashed exports of every active uv lock. A reported advisory is adjudicated against
`dependency-waivers.toml`, which binds each review to an exact domain, package, version, and
advisory with a reason, reachability statement, compensating control, reviewer, review date, and
expiry ([ADR-0026](adr/0026-expiring-dependency-waivers.md)). An unwaived advisory, an expired or
out-of-window waiver, and a waiver matching no reported advisory each fail. The same gate adjudicates
Trivy 0.74.0 reports: `trivy config` over `deploy/` at pre-push and `trivy image` over every pulled and
built stack image in continuous integration. A `deploy-config` misconfiguration at HIGH or CRITICAL
blocks unless an expiring waiver covers it; an advisory inside an image is printed as information and
never blocks, because the project's only lever on a pinned third-party image is the digest it names.
That lever has its own gate: `tools/image_pin_gate.py` fails when a pinned digest is no longer the
newest its tag carries ([ADR-0048](adr/0048-scan-images-and-deploy-configuration-with-trivy.md),
[ADR-0055](adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md)). The same image inventory
also drives one CycloneDX 1.6 SBOM per image. `tools/sbom_gate.py` binds each document to its exact
image and the pinned Trivy producer before CI accepts it; CI keeps the files temporary unless external
publication is separately authorized ([ADR-0162](adr/0162-generate-and-validate-per-image-cyclonedx-sboms.md)).
zizmor 1.29.0 audits the
workflow and Dependabot files offline at the commit stage, and any finding fails
([ADR-0049](adr/0049-audit-workflows-with-zizmor-at-the-commit-stage.md)); CodeQL analyses the Python
tree in continuous integration only
([ADR-0050](adr/0050-scan-python-with-codeql-in-continuous-integration-only.md));
`.github/workflows/security.yml` repeats the dependency audit, the configuration audit, and the image
scans daily, and Dependabot raises pinned-update pull requests under a seven-day cooldown
([ADR-0051](adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md),
[ADR-0052](adr/0052-hold-dependabot-to-a-seven-day-cooldown.md)). TypeScript quality gates use Vitest,
Testing Library, Playwright, `tsc --noEmit` over the whole project, and typescript-eslint's type-aware
presets at zero tolerated warnings; the compiler options, the required package scripts, and the coverage
thresholds are held by
`tools/typescript_policy_gate.py` at both blocking stages
([ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md)). The full Vitest wrapper
writes a temporary JSON summary, passes the tracked-or-unignored source inventory to
`tools/typescript_coverage_gate.py`, enforces its exact per-file Tier 1 statement and branch inventory,
and removes the report after adjudication; a dedicated integration
configuration has its own fail-closed wrapper. Playwright
specifications live under `apps/dashboard/tests/e2e/`, because anything outside `apps/dashboard`
escapes the type check, the linter, the formatter, and the duplication scan together. Vitest must
not register its globals: the AAA gate resolves test identifiers from `vitest` and
`@playwright/test` imports, so global registration fails every dashboard test closed. The blocking
Playwright wrapper accepts only the manifest-pinned Node and pnpm runtimes, verifies the discovered test
count against `config.playwrightExpectedTests`, checks that Chromium revision 1234 is already cached
instead of downloading from a local hook, and scans retained `test-results/` and `playwright-report/`
files for the synthetic bearer sentinel after both passing and failing browser runs.
Continuous integration performs the explicit Chromium-only installation before invoking the identical
pre-push hook. Playwright execution coverage is not merged into the package result, and fixture
acceptance is not described as production end-to-end evidence
([ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md)). jscpd 5.0.14
provides the multi-language duplication scan. The cross-language AAA gate uses Python's `ast` and `tokenize`
modules plus pinned `tree-sitter` 0.26.0 and `tree-sitter-typescript` 0.23.2 parsers. Repository-level
checks include the AAA conformance scan, domain import contracts, secret scanning, pushed-range commit
message validation, pushed-range `git diff --check`, and the directory fan-out gate, which bounds how
many files one directory holds as immediate children and adjudicates the structural exemptions in
`directory-fanout.toml` ([ADR-0033](adr/0033-bound-directory-fan-out.md)).
Contract artifacts are inventoried through `schemas/contract-manifest.toml` and validated against an
offline in-memory Draft 2020-12 registry at both blocking stages ([ADR-0021](adr/0021-contract-artifact-manifest.md)).
The compose policy gate, `tools/compose_policy_gate.py`, holds every compose file and Dockerfile under
`deploy/` to the stack policy at both blocking stages without running Docker; it is inert until the
first such file exists and fails closed afterwards ([ADR-0045](adr/0045-fail-closed-compose-policy-gate.md)).

Ruff's subprocess boundary has no global or broad test-glob waiver. `S603` is permitted only for the four
reviewed subprocess owners named by [ADR-0025](adr/0025-narrow-ruff-subprocess-waivers.md), and `S607` is
never waived. Git callers resolve an absolute executable and fail closed when it is absent; a policy test
parses the Ruff configuration and rejects any expansion of that exact scope.
