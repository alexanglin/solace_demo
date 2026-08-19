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
- **TypeScript:** statements, branches, functions, and lines are enforced independently per production
  package through Vitest.
- **Safety-critical core:** approval authorization, command-gateway dispatch, domain state machines,
  idempotency and sequence rules, evidence scoring, and `packages/contracts` carry the Tier 1 coverage,
  property-based, failure-injection, and mutation obligations.
- **Configuration and glue:** Tier 3 uses the smoke-and-failure-path inventory rather than borrowing the
  Tier 2 percentage. Until that inventory gate exists, a Tier 3 member fails closed.

Installed Agent Mesh, plugin, generated, and vendored code is excluded from owned-code coverage but remains subject to black-box compatibility, contract, integration, evaluation, and security tests. Configuration, owned adapters, error branches, and UI state logic are not exempt. Coverage is a gate, not a substitute for behavior-focused assertions.

An active workspace member with no measurable production statements fails rather than passing vacuously.
Coverage results are compared with integer arithmetic so display rounding cannot turn a value below a
threshold into a pass ([ADR-0019](adr/0019-fail-closed-quality-gates.md)).

## Maintainability and mutation gates

Ruff enforces the cyclomatic, function-size, branch, return, local-variable, parameter, nesting, and
Boolean-expression budgets over the complete owned Python tree. Complexipy independently enforces
cognitive complexity. jscpd performs one multi-language strict duplication scan across owned source,
tests, and scripts. Their values and instruments are in
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

- **Unit tests:** Pure domain rules, state machines, retry logic, validation, prompt-result parsing, reducers, and UI components.
- **Property-based tests:** Event ordering, idempotency, coordinate ranges, schema round trips, and state-machine invariants using Hypothesis.
- **Contract tests:** Python and TypeScript validate the same JSON Schemas, topic rules, CloudEvents, OpenAPI schema, and golden fixtures.
- **Broker integration tests:** Real ARM64 Solace PubSub+ container covering direct and persistent delivery, queues, reconnects, acknowledgement, and ACL denial.
- **Provider integration tests:** Local Ollama, the pinned Agent Mesh runtime, A2A discovery and
  delegation, and both pinned Event Mesh plugins. A test asserting transport, schema, or error handling
  uses a deterministic stub at the model boundary; only a test asserting model capability calls a real
  model, and those are the model-dependent class excluded from the blocking safety gate.
- **End-to-end tests:** Mission start through evidence, connectivity failure, replan, approval, and completion.
- **User acceptance tests:** Playwright exercises the complete operator workflow, including live/replay labeling and approval blocking.
- **Agent evaluations:** Curated datasets validate delegation, tool selection, structured outputs, refusal of unsafe requests, and approval behavior.
- **Failure-injection tests:** Solace Cloud broker loss, local Agent Mesh process loss, Ollama loss, duplicate and out-of-order events, malformed input, model timeout, invalid output, and recovery.
- **Performance tests:** The full fleet at the telemetry rate, dashboard update latency, queue-backlog
  recovery, and the soak run. Every value, and the instrument that measures it, is in
  [operating-parameters.md](operating-parameters.md).
- **Security tests:** Secret scanning, dependency scanning, static analysis, authorization-negative cases, schema fuzzing, and prompt-injection cases.
- **Black-box compatibility tests:** The exact Agent Mesh and plugin wheels — configuration startup, agent-card discovery, A2A delegation, Event Mesh Gateway transformation and settlement, Event Mesh Tool request/reply, and ACL denial. The acceptance-evidence column in [ARCHITECTURE.md](ARCHITECTURE.md) is this class's case list.
- **Mutation tests:** Run against safety gates, state transitions, idempotency, and evidence-score logic.
- **Replay tests:** Replaying a committed fixture must produce the same ordered domain outcome and dashboard state.

## Tooling

Python quality gates use pytest, pytest-asyncio, pytest-cov, Hypothesis, Ruff, strict mypy, Complexipy
7.0.1, Bandit, pip-audit, and mutmut 3.7.0 with per-module scoring over the Tier 1 core
([ADR-0017](adr/0017-mutation-tool-score-and-risk-tiers.md),
[ADR-0023](adr/0023-executable-deep-quality-gates.md)). Bandit ignores inline suppression comments
and blocks medium-or-higher findings at medium-or-higher confidence; dependency auditing operates on
hashed exports of every active uv lock. A reported advisory is adjudicated against
`dependency-waivers.toml`, which binds each review to an exact domain, package, version, and
advisory with a reason, reachability statement, compensating control, reviewer, review date, and
expiry ([ADR-0026](adr/0026-expiring-dependency-waivers.md)). An unwaived advisory, an expired or
out-of-window waiver, and a waiver matching no reported advisory each fail. TypeScript quality gates use Vitest, Testing Library, ESLint,
TypeScript strict mode, and Playwright. jscpd 5.0.14 provides the multi-language duplication scan. The
cross-language AAA gate uses Python's `ast` and `tokenize`
modules plus pinned `tree-sitter` 0.26.0 and `tree-sitter-typescript` 0.23.2 parsers. Repository-level
checks include the AAA conformance scan, domain import contracts, secret scanning, pushed-range commit
message validation, and pushed-range `git diff --check`.
Contract artifacts are inventoried through `schemas/contract-manifest.toml` and validated against an
offline in-memory Draft 2020-12 registry at both blocking stages ([ADR-0021](adr/0021-contract-artifact-manifest.md)).

Ruff's subprocess boundary has no global or broad test-glob waiver. `S603` is permitted only for the four
reviewed subprocess owners named by [ADR-0025](adr/0025-narrow-ruff-subprocess-waivers.md), and `S607` is
never waived. Git callers resolve an absolute executable and fail closed when it is absent; a policy test
parses the Ruff configuration and rejects any expansion of that exact scope.
