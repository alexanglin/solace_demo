# GitHub Automation Instructions

## 1. Scope and authority

These instructions apply to every file under `.github/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first. Its safety, testing, documentation, security, and version-control
rules still apply. Files here are the repository's verification and dependency-automation control
plane; they are executable policy, not examples.

Continuous integration re-runs the repository's project-owned hook entry points and is the verification
authority. Do not duplicate hook policy in workflow shell or make the runner's verdict weaker than the
local stage. An Accepted architecture decision record (ADR) governs whenever prose, workflow comments,
or tests disagree with it.

Use the sources below according to their role instead of copying current pins or numeric values here:

| Concern | Authority or reference |
| --- | --- |
| Hook stages, local feedback, and continuous-integration authority | [ADR-0012](../docs/adr/0012-git-hooks-with-ci-as-authority.md) |
| Fail-closed activation, tool prerequisites, and runtime parity | [ADR-0019](../docs/adr/0019-fail-closed-quality-gates.md) |
| Why a verification-tool version was selected | Governing Accepted ADR |
| Executable runner version | Workflow value synchronized with the project manifest, lock, and structural tests |
| Contributor and toolchain reference | [`TESTING.md`](../docs/TESTING.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Workflow security audit and checkout credentials | [ADR-0049](../docs/adr/0049-audit-workflows-with-zizmor-at-the-commit-stage.md) |
| Image and deploy-configuration scans | [ADR-0048](../docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md), [ADR-0055](../docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md) |
| CodeQL scope, triggers, and permissions | [ADR-0050](../docs/adr/0050-scan-python-with-codeql-in-continuous-integration-only.md) |
| Scheduled scans and dependency-update automation | [ADR-0051](../docs/adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md), [ADR-0052](../docs/adr/0052-hold-dependabot-to-a-seven-day-cooldown.md) |
| Required checks and branch-protection semantics | [ADR-0054](../docs/adr/0054-enforce-the-verification-authority-with-branch-protection.md) |
| Job budgets and terminal-safe reporting | [ADR-0059](../docs/adr/0059-keep-the-verification-authority-able-to-report.md), [`operating-parameters.md`](../docs/operating-parameters.md) |
| Test classes and complete toolchain | [`TESTING.md`](../docs/TESTING.md) |

A change to a trigger, permission, action or runtime pin, required-check context, hook stage, scan policy,
dependency-update policy, or verification mechanism requires the ADR and coordinated changes specified
by the root instructions.

## 2. Directory ownership

| Path | Responsibility |
| --- | --- |
| `workflows/checks.yml` | Unconditional pull-request verification: the commit stage, push stage, and credential-absence assertion |
| `workflows/security.yml` | Scheduled and explicit security work: dependency and deploy audits, image scanning and pin checks, and CodeQL |
| `dependabot.yml` | Bounded update pull requests for every dependency ecosystem the repository actually owns |

Executable structural tests recognize the workflow inventory and selected security, runtime, timeout,
and dependency-automation shapes. Published job names and required-check contexts are contracts of
ADR-0054 and external branch-protection state; static repository tests do not prove their configuration.
Adding, renaming, splitting, or deleting a workflow or job is a policy change, not file reorganization.
Never add a second path that runs a weaker approximation of an existing gate.

## 3. Workflow supply-chain and permission rules

- Pin every third-party `uses:` step to an immutable full commit SHA. Keep a human-readable release
  comment beside the pin, and review both the release change and the resolved commit in update pull
  requests. A tag or branch is not an executable pin.
- Every checkout sets `persist-credentials: false`. A later step that genuinely needs repository
  authority requires an explicit least-privilege design and governing decision; do not leave the
  checkout token on disk for convenience.
- Keep workflow-level permissions read-only. Grant a write permission only on the job whose service
  contract requires it, and only for the resource it writes. Preserve CodeQL's isolated upload
  permission rather than widening the entire security workflow.
- Do not make broker, model-provider, Cloud, deployment, or private-tenant credentials available to
  continuous integration. Preserve the unconditional credential-absence job and extend its refusal set
  when a new runtime credential name is introduced.
- Treat pull-request source, event fields, matrix values, issue text, branch names, and action outputs as
  untrusted. Pass dynamic expressions through `env:` and quote shell expansions; never interpolate an
  expression directly into a `run:` command.
- Keep the workflow audit offline so local and runner verdicts stay identical. Do not add an inline
  suppression to actionlint, zizmor, a schema validator, or a project-owned test without the ADR and
  removal condition required by the root policy.
- Do not use `pull_request_target` or another privileged trigger to execute untrusted branch content.
  A workflow needing elevated repository authority requires a separately reviewed trust-boundary design.
- Never print an environment dump, rendered secret-bearing configuration, token, authorization header,
  or scanner input that can contain credentials. Reports and diagnostics must be redacted and useful.

## 4. Preserve hook and runner parity

- `checks.yml` invokes the same complete pre-commit and pre-push stages registered in
  [`.pre-commit-config.yaml`](../.pre-commit-config.yaml). Do not replace either stage with a
  hand-maintained list of hooks or inline copies of their commands.
- Install every system prerequisite before the stage that needs it, while leaving policy decisions in
  project-owned scripts under [`scripts/`](../scripts/). A missing runtime, lock, package manager,
  scanner, test suite, or report is a failure once its component is active.
- Keep the root and Agent Mesh Python projects on their separate locked interpreters and environments.
  Keep dashboard dependency installation rooted at its package when that manifest exists. Do not let a
  convenience install bypass a frozen lock or run dependency lifecycle scripts.
- Preserve full Git history and the exact base and head revisions needed by pushed-range commit-message,
  whitespace, and secret checks. Pass revisions through the established `QUALITY_DIFF_*` environment
  boundary; do not infer a pull-request range from the runner's current checkout.
- Keep Trivy available before the push-stage deploy audit. Docker-dependent image builds and scans belong
  in the security workflow, not the fast local or commit stage.
- Every job must declare a bounded timeout no greater than the measured repository budget. Change the
  measurement and `operating-parameters.md` before changing that budget.
- Preserve unbuffered, terminal-safe reporting for long stages. Fix a blocking command in its
  project-owned wrapper instead of masking it with workflow-only terminal or pager configuration.
- Keep concurrency groups scoped to the workflow and ref so an obsolete run is cancelled without one
  workflow cancelling the other.

When a hook changes, inspect its script, typed gate, conformance tests, registration, `justfile` alias,
runner prerequisites, and documentation together. Follow [`scripts/AGENTS.md`](../scripts/AGENTS.md) and
[`tools/AGENTS.md`](../tools/AGENTS.md) for those cross-tree edits.

## 5. Triggers, verdicts, and required checks

- The checks workflow must run all of its required jobs on every pull request without a path filter or
  job condition. Preserve the published job names because branch protection binds to their status
  contexts.
- Only unconditional jobs from the checks workflow may be branch-protection requirements. A conditional
  or path-filtered security job cannot be required: a pull request on which it does not run would remain
  pending rather than prove anything.
- Keep scheduled security scans able to run without a repository commit and keep manual dispatch for
  diagnosis. A pull-request path filter is additional feedback, not a substitute for the schedule.
- Treat the security workflow's pull-request path filter as a manually maintained dependency closure.
  When an audited manifest, lock, configuration, wrapper, gate, or other input is added or moved, update
  the filter or record why hosted feedback is deliberately deferred to push and scheduled runs. No
  current structural test proves that closure, so inspect the executed dependency graph manually.
- Preserve CodeQL's deliberate trigger and permission boundary from its governing ADR. A scanner that
  reports findings is not necessarily a blocking gate; describe its actual enforcement semantics.
- A job must fail when its required analysis did not run, its report is missing or malformed, or an
  external command timed out. Never translate an unavailable service or incomplete scan into a clean
  result.
- Workflow comments must describe current behavior and evidence. Do not claim that branch protection,
  a scheduled run, an image scan, or a service upload is active merely because YAML for it exists.

Repository settings are external state. Changing branch protection, secrets, variables, environments,
or default CodeQL setup needs explicit human authorization and readback; editing these files alone does
not authorize or prove that settings change.

## 6. Dependabot rules

- Register each real dependency ecosystem and directory exactly once, and add or remove an entry with
  the manifest, lock, Dockerfile, or Compose surface it tracks.
- Preserve the schedule, cooldown, open-pull-request bound, and Conventional Commit shape fixed by the
  governing ADRs and executable tests. Do not copy their numeric values into another policy file.
- Use commit prefixes accepted by the commit-message hook and include a scope so automated changes enter
  the same changelog and review process as human changes.
- Treat an update pull request as untrusted supply-chain input. Review changed action SHAs, image tags
  and digests, manifests, locks, generated installers, and scanner output before merging.
- A Docker update moves the reviewed tag and digest together. A Python or dashboard update regenerates
  and verifies its committed lock. Do not hand-edit a lock to match an automation request.
- Pre-commit hook revisions remain under the explicit hook-update workflow unless an Accepted ADR assigns
  them to dependency automation.

## 7. Required verification

Run the focused static checks from the repository root:

```sh
pre-commit run check-yaml --all-files --hook-stage pre-commit
pre-commit run yamllint --all-files --hook-stage pre-commit
pre-commit run actionlint --all-files --hook-stage pre-commit
pre-commit run zizmor --all-files --hook-stage pre-commit
pre-commit run check-github-workflows --all-files --hook-stage pre-commit
pre-commit run check-github-workflows-require-timeout --all-files --hook-stage pre-commit
pre-commit run check-dependabot --all-files --hook-stage pre-commit
```

Run the focused workflow-policy regression set:

```sh
uv run --frozen pytest -q \
  tools/quality_gate_tests/hooks/test_hook_repairs.py \
  tools/quality_gate_tests/hooks/test_hook_semantics.py \
  tools/quality_gate_tests/hooks/test_commit_message_gate.py \
  tools/quality_gate_tests/hooks/test_trivy_config_stage.py \
  tools/quality_gate_tests/analysis/test_uv_version_pin.py \
  tools/quality_gate_tests/selection/test_selection_stage.py
```

Run additional quality-gate tests for every script, gate, or policy the workflow change reaches. Follow
TDD and the mandatory AAA structure when authorized behavior changes require test changes. Before
staging a new guide, pass it explicitly to Markdown, spelling, facts-and-links, and strict-documentation
hooks because diff-based discovery cannot see an untracked file.

Finish with:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Static validation cannot prove GitHub-hosted permissions, repository settings, action service behavior,
runner architecture, scheduled execution, network scans, or branch-protection readback. Run the
authorized hosted check for a workflow behavior change and report exactly what remains unverified.
