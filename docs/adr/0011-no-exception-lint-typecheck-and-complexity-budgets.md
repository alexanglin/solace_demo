# ADR-0011: Lint and typecheck all code with no escape hatches, and enforce complexity budgets

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

Both planning documents required strict typing and linting for production code, but said nothing about tests, fixtures, scripts, or configuration, and nothing prevented a contributor from silencing a diagnostic in place. Separately, both documents asked for small, cohesive functions and pure typed domain functions — as prose, with no measurement, so neither could ever fail a build.

Complexity is not only a readability concern here: branch count directly determines how hard the 95% branch-coverage gate is to meet, so lowering complexity makes the coverage obligation cheaper.

## Decision

**Lint and typecheck cover everything** — source, tests, fixtures, scripts, and configuration — on both Python environments and the TypeScript dashboard. Ruff formatting and linting plus strict mypy for Python; ESLint plus `tsc --noEmit` for TypeScript.

**Escape hatches are prohibited.** Blanket `# type: ignore`, bare `# noqa`, and `eslint-disable` require a documented, reviewed waiver recorded as an ADR. Unused ignores are themselves errors, so waivers cannot silently outlive their cause.

**Complexity is measured and gated**, not asserted: cyclomatic and cognitive complexity, function length, parameter count, nesting depth, and duplication all carry enforced ceilings. Layering is enforced mechanically so that `packages/domain` cannot import Solace, FastAPI, SQLAlchemy, asyncpg, or Ollama clients — this converts the "pure typed domain functions" rule from an aspiration into a build failure.

Thresholds are set at their intended values from the first commit rather than ratcheted down from a legacy baseline, because the repository is greenfield.

## Consequences

- Test and fixture code is held to the same standard as production code, which is where a great deal of untyped drift normally accumulates.
- Silencing a diagnostic becomes a visible, reviewable act with a written justification.
- Some legitimate code will need restructuring to fit the ceilings. That is the intent, but it will occasionally be irritating, and the ceilings must be chosen to be livable rather than aspirational.
- Layering contracts make the hexagonal structure real and keep the highest-value domain code trivially unit-testable.
- Threshold values live in one place so they can be cited rather than duplicated.

## Alternatives considered

- **Strict typing on production code only.** Rejected: tests are where type errors hide, and untyped tests give false confidence in the suite that guards the safety gate.
- **Complexity as a review convention.** Rejected: unenforceable, and it decays the moment schedule pressure appears.
- **Ratcheting thresholds from a measured baseline.** Not applicable: there is no code yet, so the correct values can simply be set now.
