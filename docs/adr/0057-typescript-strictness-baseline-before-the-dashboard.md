# ADR-0057: Fix the dashboard's TypeScript baseline before the first dashboard file, and gate it

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) decided "ESLint plus `tsc --noEmit` for TypeScript" and prohibited escape hatches, but never fixed the strictness *level*. `AGENTS.md` §4 and `docs/TESTING.md` say the words "TypeScript strict mode", and nothing in this repository can fail because of them.

That is precisely the defect [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) opens by naming in its own Context: a rule stated "as prose, with no measurement, so neither could ever fail a build". `tsc --noEmit` holds code to whatever configuration exists. Nothing holds the configuration. ADR-0011's rule that a diagnostic may not be silenced has no equivalent one level up, where silencing is a single deleted line of JSON.

No TypeScript exists yet. No `.ts`, `.tsx`, `.js`, or `.jsx` file has ever existed in this repository's history, there is no `package.json` and no `tsconfig.json`, and `apps/` has not been created. The dashboard is Phase 3. This is the only moment at which `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` are free: they are the two options that are genuinely expensive to retrofit into a working React and MapLibre codebase, and the way that backlog gets cleared under schedule pressure is a wave of `!` and `as` assertions — the exact escape hatches ADR-0011 bans. [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) already states the governing rule: thresholds are set at their intended values from the first commit because the repository is greenfield.

Two structural gaps exist today, independent of when code lands. The pre-push tier has no whole-project type check and no whole-tree ESLint or Prettier run, while Python has both, so `just check-push` does not rehearse what continuous integration proves. And the commit-stage `tsc` hook carries `pass_filenames: false`, which makes its `files:` pattern a trigger rather than a scope — a trigger matching only `.ts` and `.tsx`, so a change to `tsconfig.json`, to `package.json`, or a Dependabot bump of an `@types` package would run no type check at all.

## Decision

**The dashboard's TypeScript baseline is fixed now, and a project-owned gate refuses a configuration that does not carry it.**

`tsc --noEmit` runs with `strict: true` and, because `strict` stops at fifteen flags, with each of the options it omits: `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitOverride`, `noPropertyAccessFromIndexSignature`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noUnusedLocals`, `noUnusedParameters`, `allowUnreachableCode: false`, `allowUnusedLabels: false`, `noUncheckedSideEffectImports`, `isolatedModules`, `verbatimModuleSyntax`, `erasableSyntaxOnly`, and `forceConsistentCasingInFileNames`.

`skipLibCheck` is `false`. Its practical value is detecting two conflicting versions of a declaration package resolving at once, which is a defect this project cannot otherwise see.

Linting is `typescript-eslint` with the `strictTypeChecked` and `stylisticTypeChecked` presets and `projectService: true`, run at `--max-warnings 0`, with `linterOptions.reportUnusedDisableDirectives` set to `"error"`.

`extends` under `apps/dashboard` must be a relative path resolving inside `apps/dashboard`. A strictness policy delivered by a mutable dependency is one a version bump can relax.

Playwright specifications live at `apps/dashboard/tests/e2e/` with `apps/dashboard/playwright.config.ts`. Anything outside `apps/dashboard` escapes `tsc`, ESLint, Prettier, the duplication scan's path list, and the dashboard activation predicate together.

`tools/typescript_policy_gate.py`, driven by `scripts/hooks/dashboard/check-typescript-policy.sh`, holds the compiler options, the required package scripts, the `--max-warnings 0` rule, the four coverage thresholds, and the exact-version rule at both blocking stages. `dashboard-typecheck-full` and `dashboard-quality-full` run the whole project and the whole tree at pre-push. All of them are inert until `apps/dashboard` holds a manifest or TypeScript source, and fail closed from that moment, on the contract [ADR-0019](0019-fail-closed-quality-gates.md) sets.

## Consequences

- The baseline stops being prose. This discharges the TypeScript half of [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) the way [ADR-0023](0023-executable-deep-quality-gates.md) discharged its complexity half.
- **`noUncheckedIndexedAccess` makes routine lookup code verbose, and the mechanical fix contributors reach for is `!`.** That is why `@typescript-eslint/no-non-null-assertion` is part of this decision rather than a separate one. Either without the other is worth nothing.
- **`skipLibCheck: false` means a defective upstream declaration file can block the build with an error nobody here can fix.** Unlike mypy, TypeScript has no per-module override: the only lever is a global flip. That flip requires an ADR carrying a compensating control, because duplicate declaration-package detection is what the setting buys.
- `noPropertyAccessFromIndexSignature` and `verbatimModuleSyntax` are friction with no runtime payoff in most files. That is accepted.
- The option list now has two homes: this record carries the reasoning, and the gate module carries the executable list. The gate is the instrument; this is the decision.
- The gate holds nothing today. It is a control that arms with its subject, like the compose policy gate and the Agent Mesh configuration validator.
- Pre-push gets two more stages once the dashboard exists, and the `CONTRIBUTING.md` budgets will need re-measuring rather than assuming.
- **The dashboard's runtime and toolchain version pins are still owed a record.** They cannot be pinned until a lockfile resolves them together in Phase 3, so this decision fixes how the dashboard is verified and not what it is built from.

## Alternatives considered

- **Adopt the Vite React and TypeScript template's configuration.** Rejected, and this is the realistic failure mode rather than a straw man: that template ships `strict` plus four options and `skipLibCheck: true`. Adopting it unchanged produces a dashboard whose type checking stops dead at the event-stream boundary.
- **`skipLibCheck: true`.** Rejected: it hides genuine errors in upstream declarations and, more importantly, hides two conflicting declaration-package versions resolving together — the defect that surfaces weeks later as an unreadable JSX error. The cost of `false` is real and is recorded above.
- **Deliver the option set through a published strictness preset.** Rejected: a policy carried by a mutable dependency can be relaxed by a version bump, and it would force the gate to resolve packages rather than read text.
- **Wrap the four pattern-gated commit hooks in unconditional backstops.** Rejected: whole-tree lint, format, and test on every commit exceeds the commit-stage budget `CONTRIBUTING.md` records, and [ADR-0012](0012-git-hooks-with-ci-as-authority.md) is explicit that a slow hook is a bypassed hook. The correct backstop is the pre-push whole-tree tier Python already has.
- **A whole-project `tsc` at the commit stage with `always_run`.** Rejected on the same budget grounds. Widening the trigger and adding the unconditional pre-push run closes the same hole within one push.
- **Defer every TypeScript decision to Phase 3.** Rejected on [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md)'s greenfield rule. The retrofit cost is the whole argument.
- **Plain JavaScript with JSDoc annotations, or Flow.** Rejected: both reverse [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) for no benefit, and the React, MapLibre, Vitest, and Playwright ecosystems all assume TypeScript.
- **An ADR with no gate.** Rejected: it would restate the defect this record exists to close.
