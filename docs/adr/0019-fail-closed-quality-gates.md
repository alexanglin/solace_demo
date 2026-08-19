# ADR-0019: Make repository quality gates fail closed and run the same checks in CI

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The repository already declared strict quality controls, but several hooks could report success without
performing their advertised work. Missing `uv` or `pnpm` executables were treated as skips, the lockfile
checker was not wired to a hook, the full Python test command combined statement and branch coverage into
one percentage, dashboard and Bandit checks depended on the files in one pushed range, and CI did not
validate commit messages. `git diff --check HEAD` also inspected an uncommitted working tree in the context
where the defect to catch was already committed.

These are dangerous false-green states. A check that did not execute is not evidence that the property it
names holds.

## Decision

Use project-owned, test-driven shell or Python entry points for whole-tree gates. A component is inactive
only while its manifest and owned source are both absent. Once either exists, a missing manifest,
lockfile, required executable, test suite, generated report, or required package script is a failure.
Post-checkout and post-merge dependency synchronization remain nonblocking because blocking those Git
operations can strand a worktree; they must emit a warning when synchronization cannot run.

Pin the root verification environment to `pytest` 9.1.1, Ruff 0.16.3, mypy 1.19.0, Bandit 1.9.4,
`pip-audit` 2.10.1, and pre-commit 4.5.0 in `uv.lock`. Bandit ignores inline `nosec` comments and blocks
medium-or-higher findings at medium-or-higher confidence; Ruff's Bandit-compatible rules provide the fast
commit-stage signal. Dependency auditing exports hashed requirements from every active uv lock and audits
those resolutions rather than whatever happens to be installed globally.

Measure Python statement coverage and branch coverage independently, per active workspace member, using
integer comparisons. Tier 1 requires both dimensions at 100%; Tier 2 requires both at 95%; a member with
no measurable source fails. A zero-branch member has complete branch coverage for that dimension.

Run full-tree Python formatting/linting, strict type checking, deterministic tests and coverage, Bandit,
dependency auditing, dashboard coverage, and the dashboard production build at pre-push. The dashboard
scripts are invoked from `apps/dashboard`; staged dashboard filenames are translated to that working
directory with pre-commit's `hazmat cd` adapter. Domain import contracts are checked at both commit and
push so `packages/domain` cannot acquire framework, broker, persistence, or model-client dependencies.

Check the exact pushed range for whitespace and Conventional Commit messages. CI supplies the event's base
and head revisions and runs the same project entry points. GitHub Actions are pinned by full commit SHA;
CI uses Python 3.14.7, the isolated Agent Mesh environment uses Python 3.13.15, and Node uses 24.19.0.
Lockfiles remain text-diffable review artifacts.

## Consequences

- Missing tooling and incomplete scaffolds become visible failures instead of reassuring skips.
- The concurrently introduced empty package scaffolds cannot satisfy the full coverage gate until real,
  tested behavior exists. This is intentional.
- A fresh environment performs more installation before hooks can run, and the dependency audit needs
  network access to obtain current advisory data.
- Bandit's low-severity subprocess warnings do not block by themselves. This avoids blanket inline
  suppressions for fixed-argument tooling subprocesses, but makes Ruff and review responsible for those
  low-severity call sites.
- Local execution requires the exact runtimes and system tools documented in `CONTRIBUTING.md`; pre-commit
  provisions isolated hook dependencies but does not provision the dashboard's system `pnpm` command.
- This decision does not discharge ADR-0011's cognitive-complexity and duplication gates or ADR-0017's
  mutation gate. Those controls remain mandatory and require their own executable entry points.

## Alternatives considered

- **Keep direct commands in `.pre-commit-config.yaml`.** Rejected: activation, lockfile, multi-environment,
  and absent-component semantics cannot be expressed consistently there.
- **Treat missing tools as a warning at every stage.** Rejected: it turns every quality gate into an
  optional convention on the machine most likely to need the feedback.
- **Use aggregate coverage.** Rejected: strong coverage in one package or dimension can mask a weak safety
  boundary in another.
- **Audit only the current virtual environment.** Rejected: an environment can be stale or omit a locked
  platform branch; the lock resolution is the artifact under review.
- **Hide generated lockfiles as binary diffs.** Rejected: generated does not mean unreviewable, especially
  when the generated content changes the executable dependency graph.
