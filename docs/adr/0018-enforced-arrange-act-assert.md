# ADR-0018: Enforce Arrange-Act-Assert structure in every project-owned executable test

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The repository mandates test-driven development, behavior-focused assertions, risk-tiered coverage, and
mutation testing. None of those controls makes the internal structure of an individual test visible or
prevents setup, action, and verification from being interleaved. The project owner has required the
Arrange-Act-Assert pattern for all tests, so relying on memory or review alone is insufficient.

A marker-only grep would count text inside strings, nested callbacks, or helper functions and would miss
dynamically registered tests. An inference-heavy checker that tries to classify every function call as
setup, behavior, or assertion would be brittle and would encode domain semantics in a lint rule. The gate
needs syntax awareness, deterministic diagnostics, and an explicit boundary between what it proves and
what remains a review concern.

## Decision

Require every project-owned executable test, in every supported language, framework, test class, and risk
tier, to satisfy the exact Arrange-Act-Assert contract in
[`docs/TESTING.md`](../TESTING.md#mandatory-arrange-act-assert-structure). There is no per-test
suppression. Parameterization changes inputs, not the authored test-body requirement. Fixtures, helpers,
lifecycle hooks, datasets, and upstream tests are not project-owned executable test cases; every
project-owned test that consumes or wraps them remains covered.

Implement a repository-owned, whole-tree checker with:

- Python `ast` and `tokenize` for Python test discovery, direct-body markers, statements, and assertion
  placement.
- [`tree-sitter` 0.26.0](https://pypi.org/project/tree-sitter/0.26.0/) and
  [`tree-sitter-typescript` 0.23.2](https://pypi.org/project/tree-sitter-typescript/0.23.2/) for
  JavaScript, TypeScript, TSX, Vitest, and Playwright syntax.
- Exact framework-import resolution, including aliases, and fail-closed rejection of unsupported or
  dynamic test registration.
- Stable compiler-style diagnostics and a project-owned positive and negative conformance suite that runs
  before each repository scan.

Run the gate over the complete tracked and unignored source tree at both `pre-commit` and `pre-push`.
CI invokes those same hook stages under [ADR-0012](0012-git-hooks-with-ci-as-authority.md). A new test
language, framework, or registration dialect must add checker support and failing-then-passing conformance
cases before its first test lands.

The structural checker enforces exact phase markers, order, direct-body placement, non-empty phases, and
basic assertion placement. It does not claim to prove that a comment truthfully describes the code beneath
it, that Act contains only one semantic behavior, that an assertion is useful, or that the test was
observed red before implementation. Code review, red-green-refactor evidence, coverage, property tests,
failure injection, and mutation testing remain mandatory for those properties.

## Consequences

- Every owned test has a uniform, quickly scannable structure across Python, Vitest, and Playwright.
- A renamed or relocated test cannot evade the check because discovery scans all supported source files.
- Dynamic test factories, bodyless tests, expression callbacks, and unrecognized test dialects are
  intentionally unavailable until the checker can verify them.
- Strict explicit markers add three comment lines and some ceremony to even very small tests.
- Framework-native fused idioms may need to capture an outcome during Act and inspect it explicitly during
  Assert.
- The checker and its parser pins become build infrastructure with their own compatibility and security
  maintenance burden.
- A structurally compliant but semantically weak test can still pass this gate, so mutation and review
  remain essential.

## Alternatives considered

- **Document AAA without a gate.** Rejected: the project requires enforcement, and review-only conventions
  drift.
- **Require Given-When-Then or accept marker aliases.** Rejected: multiple equivalent grammars weaken
  uniformity and make diagnostics less precise.
- **Use a regular-expression scanner.** Rejected: strings, comments, nesting, aliases, and dynamic test
  syntax make it unsound in both directions.
- **Infer phases entirely from syntax without explicit markers.** Rejected: a parser cannot reliably know
  whether a domain call is setup or the behavior under test.
- **Allow inline waivers.** Rejected: they create an unreviewable escape hatch and contradict the
  all-tests requirement. Extending the checker is the only supported response to a legitimate new test
  dialect.
