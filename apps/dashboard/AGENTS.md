# Dashboard Agent Instructions

The repository-root `AGENTS.md` applies here. Before changing this package, also read
`apps/dashboard/README.md`, ADR-0057, ADR-0058, and the accepted UI-slice ADRs 0094 through 0104.

## Boundaries

- Keep server state, reduced mission state, and presentation state separate.
- Feed live SSE, validated replay bundles, and deterministic test fixtures through one
  reducer-facing event-source interface. Never add fixture selection to a production URL, storage
  key, or bootstrap value.
- Validate every HTTP, bootstrap, SSE, and replay input before it becomes typed state. A refusal
  keeps the last validated mission state visible.
- Keep MapLibre geometry, styles, glyphs, fonts, and attribution local. Browser tests must fail on
  any remote request.
- Do not render approval, command, model, evidence, rescue, or escalation controls in this UI slice.

## Verification

- Keep Playwright specifications under `tests/e2e/` and import `test` and `expect` explicitly from
  `@playwright/test`.
- Keep deterministic integration specifications under `src/` with the `*.integration.test.{ts,tsx}`
  suffix. They run both as a dedicated non-empty suite and inside the complete coverage inventory.
- Do not use V8, c8, Istanbul, or Node coverage-ignore directives in hand-written production source.
  The coverage wrapper must account for every such source through the independent report gate
  selected by ADR-0103; Playwright coverage never contributes to that result.
- Every executable test callback uses one direct `// Arrange`, `// Act`, and `// Assert` sequence.
- Feed Playwright through serialized boundary inputs, never a reduced presentation fixture.
  Increment the test-source revision for every input batch and acknowledge it only after the render
  that consumed the batch commits. Clear the one-shot initial source script from the test global
  when consumed so the synthetic bearer cannot persist there.
- Prefer semantic roles and accessible names. A test ID is reserved for volatile screenshot masks or
  a third-party canvas boundary that has no semantic DOM surface.
- Run tests with the Node and pnpm versions pinned by ADR-0099 and `package.json`.
- Keep Playwright traces, videos, automatic screenshots, and HAR capture disabled. Curated
  screenshots use only synthetic data and mask runtime identifiers and presentation timestamps.
- Do not update screenshot baselines until the intended visual change has been inspected at 1440×900
  and 1280×800.

## Change hygiene

- Follow the repository red-green-refactor sequence. A failing Playwright test must first
  demonstrate a missing product behavior, not a broken server, dependency, browser, or fixture
  harness.
- Keep exact dependency pins and regenerate `pnpm-lock.yaml` with the pinned package manager.
- Update this guide and `README.md` when component ownership, test entry points, or trust boundaries
  change.
