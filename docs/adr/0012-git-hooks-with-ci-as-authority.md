# ADR-0012: Staged git hooks for fast feedback, with CI as the authority

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

The project mandates a strict TDD workflow and a large battery of quality gates, but nothing ran them until a contributor remembered to. Feedback that arrives only in CI arrives too late to be cheap. At the same time, git hooks can be skipped with `--no-verify`, so they cannot be the enforcement boundary.

## Decision

Adopt the `pre-commit` framework with hooks installed across several git stages, tiered by cost:

- **pre-commit** — formatting, linting, type checking, the affected unit tests, a staged-file secret scan, and the standard hygiene checks. Held to a strict time budget, because a slow hook is a bypassed hook.
- **commit-msg** — Conventional Commits, which also gives the changelog the structure it needs.
- **pre-push** — the full unit suite with coverage thresholds, the complexity gate, contract tests, schema and fixture drift checks, dependency and static-analysis scans, a full-history secret scan, and diagram freshness.
- **post-checkout / post-merge** — dependency synchronisation when a lockfile changed, for both Python environments and the dashboard.

**CI re-runs the identical hooks.** Hooks are fast feedback; CI is the authority. `--no-verify` can skip local execution but cannot merge unverified work.

## Consequences

- Defects surface seconds after they are written rather than minutes after a push.
- Local and CI checks cannot drift apart, because they are the same configuration invoked the same way.
- The pre-commit stage must be actively kept fast as the suite grows, which means running the affected subset locally and the full suite at push time — a deliberate split that needs periodic re-tuning.
- Both Python environments and the dashboard toolchain must be present for hooks to run, raising the first-run setup cost.
- Branch protection on the default branch is enforced locally as well, consistent with the rule that work happens on a branch.

## Alternatives considered

- **CI-only enforcement.** Rejected: slow feedback, and it burns runner time on defects a two-second local check would have caught.
- **Running the full test suite at pre-commit.** Rejected: it would exceed any reasonable time budget and guarantee routine use of `--no-verify`, which is worse than not having the hook.
- **Hand-written hook scripts.** Rejected: no dependency pinning, no multi-language support, and no shared CI invocation path.
