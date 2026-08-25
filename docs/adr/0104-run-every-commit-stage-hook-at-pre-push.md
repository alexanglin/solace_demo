# ADR-0104: Run every commit-stage hook at pre-push as well

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** the stage tiering of [ADR-0012](0012-git-hooks-with-ci-as-authority.md), in which the
  commit stage held checks the push stage did not repeat

## Context

[ADR-0012](0012-git-hooks-with-ci-as-authority.md) tiered the hooks by cost — fast checks at
`pre-commit`, expensive ones at `pre-push` — and made CI the authority by re-running both stages. It
did not make the push stage a superset of the commit stage. Forty-eight of the repository's hooks ran
only at `pre-commit`: the hygiene checks, the secret and workflow audits, the format and lint gates,
the staged-file type checks, the affected-test selections, and the documentation gates.

Locally that leaves a gap, because a commit can reach a branch without its commit-stage hooks ever
running: `--no-verify`, a named `SKIP`, a commit written before a hook existed, a merge or rebase that
rewrote it, or a clone where `pre-commit install` was never run. The push is the moment that work
leaves the machine, and until now the stage guarding it checked less than the stage guarding each
individual commit.

CI still re-runs both stages over all files, so this closes a local gap rather than a merge gap. That
is the gap worth closing: it is the one a contributor meets before the runner does.

## Decision

`default_stages` is `[pre-commit, pre-push]`, so every hook runs at both blocking stages unless it
declares its own. Naming the two explicitly stays load-bearing — an unset `default_stages` would also
wire every hook into `commit-msg`, `post-checkout`, `post-merge`, and `pre-merge-commit`.

Exactly two commit-stage hooks declare otherwise and stay off the push stage:

- `no-commit-to-branch` refuses work committed on `main`. At `pre-push` it would refuse every push made
  from `main`, which is the branch this repository publishes.
- `gitleaks` hardcodes `--staged --pre-commit` upstream, so at `pre-push` it would scan an empty staged
  diff and report a pass it never reached. `gitleaks-history` is its push-stage counterpart and scans
  the whole history.

`tools/quality_gate_tests/hooks/test_hook_semantics.py` asserts that the set of commit-only hooks is
exactly those two, resolving `default_stages` rather than reading each declaration, so a third one
cannot appear without changing that test and this record.

## Consequences

- The push stage is slower, and some of the added work is redundant by construction:
  `pytest-unit-fast` runs beside `pytest-full`, `mypy-root` beside `mypy-full`, `eslint`, `tsc`, and
  `prettier-check` beside their `-full` counterparts, and `diagrams-fresh` beside `diagrams-fresh-all`.
  The redundancy is accepted rather than tuned away, because deciding per hook which full-tree gate
  subsumes which staged-file gate is a judgement that would have to be re-made every time either side
  changed.
- The `push-stage` job's 20-minute CI budget was set against a stage measured at 2m01s whole-tree, before
  the dashboard hooks and the 5m Chromium acceptance suite landed. That budget is now the nearest
  constraint on this decision, and it is asserted by the same test module. Re-measure it before adding
  another hook to this stage.
- A hook added with no explicit `stages` now runs at both stages. That is the safe default, but a slow
  new hook reaches the push path without anyone choosing to put it there.
- The two exceptions live in one enumerated, tested set, so the rule cannot erode quietly the way an
  unstated convention does.

## Alternatives considered

- **Keep ADR-0012's tiering.** Rejected: it leaves the local push stage weaker than the local commit
  stage, which is the wrong way round for the operation that publishes work.
- **Add only the subset the full-tree hooks do not already subsume.** Rejected: the subsumption is a
  per-hook judgement with no gate behind it, so it would drift silently as either side changed.
- **Make `pre-push` the only blocking stage.** Rejected: seconds-after-writing feedback is the whole
  point of [ADR-0012](0012-git-hooks-with-ci-as-authority.md), and a commit stage that checks nothing
  moves every defect to the end of the session.
- **Run the commit-stage hooks at `pre-push` over the whole tree rather than the pushed range.**
  Rejected: `pre-commit` already scopes the push stage to the pushed range, and the whole-tree sweep is
  what the `-full` hooks and CI's `--all-files` invocation exist to provide.
