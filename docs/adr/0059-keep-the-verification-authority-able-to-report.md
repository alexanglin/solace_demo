# ADR-0059: Keep the verification authority able to report a verdict

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none. This record repairs the mechanism
  [ADR-0012](0012-git-hooks-with-ci-as-authority.md) and
  [ADR-0054](0054-enforce-the-verification-authority-with-branch-protection.md) depend on.

## Context

[ADR-0012](0012-git-hooks-with-ci-as-authority.md) makes continuous integration the authority and the
local hooks fast feedback. [ADR-0054](0054-enforce-the-verification-authority-with-branch-protection.md)
turns that into an enforceable rule by requiring three `checks.yml` jobs before the merge button
unlocks. Both records assume the runner can report a verdict.

It could not. On 2026-08-20 the `pre-push hooks` job had run eight times and completed zero times.
Every run stalled immediately after `gitleaks (full history)` and was killed at the 60-minute cap,
leaving orphan `git` and `pager` processes in the runner's cleanup log. The stage that owns whole-tree
type checking, the full test suite with its per-member coverage gates, mutation scoring, the lockfile
checks, Bandit, the dependency audit and the deploy-configuration scan had therefore never produced a
result — not a red one, not a green one.

The cause was one missing flag. pre-commit runs hooks under a pseudo-terminal so their output keeps
its colour. git therefore sees a terminal and sends `diff --check` through `core.pager`. On a runner
whose `TERM` is degraded, `less` prints `WARNING: terminal is not fully functional / Press RETURN to
continue` and waits for a keystroke that never arrives.

Three properties of the repository let that survive:

- **The gate's own tests could not observe it.** `run_script` in the quality-gate harness runs every
  hook through pipes. git took its pipe path in every test and its terminal path only on the runner,
  so the test suite was structurally blind to the one failure mode that mattered.
- **The budget hid it.** A 60-minute timeout on a stage that takes 2m01s whole-tree meant a wedge and
  a slow suite looked identical for as long as anyone was willing to watch.
- **The first diagnosis was wrong and was committed.** `PYTHONUNBUFFERED` was added on the theory that
  block buffering explained why the stage appeared to stop at an arbitrary hook. It did not; the last
  line printed was accurate and the next hook was the one that blocked.

Two claims in the document set were false as a result. [ADR-0054](0054-enforce-the-verification-authority-with-branch-protection.md)
records that "the three checks were green on the runner before this record landed"; they were not, and
one of them could not have been. `CONTRIBUTING.md` and `CHANGELOG.md` describe the protection as
applied, while `branches/main/protection` returned 404 and `rulesets` returned an empty list on the
same day — every commit on `main` had arrived by direct push.

## Decision

**A project-owned hook must run to completion with its output attached to a terminal, and every
continuous-integration job is bounded by a budget derived from its measured cost.** Three clauses,
each with an executable holder:

| Clause | Held by |
| --- | --- |
| No project-owned hook script may run a pageable git subcommand with the terminal inherited and no pager suppressed | `test_no_hook_script_lets_git_start_a_pager` over every `scripts/hooks/**/*.sh` |
| Hook behaviour that depends on a terminal is proved on one, not on a pipe | `run_script_on_terminal`, which fixes `TERM` to a degraded value rather than inheriting the contributor's, and kills the session rather than the script so a surviving pager cannot outlive the test that caught it |
| Every job in `.github/workflows/` declares `timeout-minutes` of at most 20 | `test_no_continuous_integration_job_may_outlive_its_measured_cost` over every workflow |

The budget is a detection threshold, not a performance target. It comes from measurement taken on
2026-08-20 whole-tree: the complete pre-push stage 2m01s, the image scan 2m58s, CodeQL 1m13s, the
commit stage 1m15s. Twenty minutes leaves the slowest job better than four times its measured cost.
The number and its instrument live in [operating-parameters.md](../operating-parameters.md).

## Consequences

- The runner can report a verdict, so [ADR-0054](0054-enforce-the-verification-authority-with-branch-protection.md)
  becomes applicable. It was not before: a required check that never reports holds a pull request
  pending forever, so applying the protection while the job wedged would have deadlocked the
  repository rather than protecting it.
- A wedge now costs 20 minutes rather than 60, and the budget is the instrument that finds the next
  one. The pager fix removes this defect; the budget addresses the class.
- **A job that legitimately grows past 20 minutes now fails.** That is friction by design: raising the
  budget requires a new measurement and an edit to the parameter row, rather than a silent hour. One
  number covers jobs with different profiles, which is the cost of having a rule at all.
- **The terminal harness is a blunt instrument.** It allocates a pseudo-terminal per test and kills a
  process group on timeout. Both are correct here and neither is free, so it is for hooks whose
  behaviour genuinely turns on the terminal rather than for hook tests in general.
- **The pager clause is git-specific and syntactic.** It reads shell text rather than executing it, so
  a hook that reaches a pager by some other route — a different tool, an indirection through a
  variable — is not covered. The behavioural test covers the one hook known to have done it; the
  syntactic test covers the shape.
- The claim in [ADR-0054](0054-enforce-the-verification-authority-with-branch-protection.md) about the
  checks being green stays where it is. An accepted record is not edited; this record is the
  correction, which is what the log is for.

## Alternatives considered

- **Set `GIT_PAGER=cat` or `core.pager=cat` in the workflow.** Rejected: it repairs the runner and
  leaves the hook wedging everywhere else, and it makes CI's configuration differ from the one a
  contributor runs. That divergence is precisely what
  [ADR-0012](0012-git-hooks-with-ci-as-authority.md) exists to prevent — the value of the runner is
  that it executes the identical configuration.
- **Pin `TERM` in the workflow so `less` behaves.** Rejected for the same asymmetry, and it makes
  correctness depend on one pager's behaviour under one terminal type instead of removing the pager.
- **Wrap every hook in `timeout(1)`.** Rejected: it turns a wedge into a red hook without saying why,
  it needs the `timeout`/`gtimeout` split handled on macOS, and the platform already enforces a budget
  at the job. Two timeout mechanisms disagreeing is worse than one.
- **Split the single `pre-commit/action` step into one step per hook so a wedging hook names itself.**
  Rejected: it replaces `pre-commit run --all-files --hook-stage <stage>` with a hand-maintained list,
  which breaks the identical-configuration claim and would drift the first time a hook is added.
- **Run every hook test through a pseudo-terminal.** Rejected on cost: the deep hooks take minutes
  each, and the fast stage would stop being fast to buy coverage of a property most hooks do not have.
- **Fix the pager and leave the budgets at 60.** Rejected: the fix removes this instance. Without a
  budget derived from measurement, the next hook that blocks costs another eight hours of runner time
  before anyone can tell it apart from a slow suite.
- **Raise the budget per job rather than one number for all.** Deferred rather than rejected. Every
  job measures under three minutes today, so per-job budgets would encode six numbers where one
  suffices. Revisit when a job's measured cost genuinely approaches the budget.
