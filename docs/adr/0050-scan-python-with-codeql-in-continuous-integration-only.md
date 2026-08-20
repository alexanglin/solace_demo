# ADR-0050: Scan Python with CodeQL in continuous integration only

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Bandit is the project's static security analyser for Python, blocking medium-or-higher findings at
medium-or-higher confidence ([ADR-0019](0019-fail-closed-quality-gates.md)). It is a pattern matcher
over single files. It cannot follow a value from an HTTP request body through a validator into a
command, which is the shape of the defects the approval boundary and the operator API exist to
prevent. CodeQL performs that data-flow analysis, is free for public repositories, and runs through
`github/codeql-action`, whose `v4` line is the current commit `ff2f1c621b7f889edc0d3c761ac2e6a3f8cdb0dd`.

Three facts bound how it can run. It is a continuous-integration service with no offline equivalent,
so it cannot be a hook. A workflow triggered by a Dependabot pull request receives a read-only token
and cannot upload results, and a guard on the actor is itself a finding under the workflow audit
([ADR-0049](0049-audit-workflows-with-zizmor-at-the-commit-stage.md)). And CodeQL fails an analysis
for a language with no source, so TypeScript cannot be enabled until `apps/dashboard/` exists.

## Decision

**CodeQL analyses the repository's Python in continuous integration, and nowhere else.** A `codeql`
job in `.github/workflows/security.yml` runs `github/codeql-action/init` and `analyze` pinned to the
commit above, with `languages: python`, `build-mode: none`, and the default query suite, under job
permissions of `contents: read` and `security-events: write`. It runs on every push to `main`, on the
daily schedule, and on manual dispatch — not on pull requests. `javascript-typescript` joins the
language list in the commit that creates the dashboard. Code-scanning "default setup" stays off in the
repository settings, because it conflicts with an advanced-setup workflow.

## Consequences

- Data-flow defects in the Python services are reported as code-scanning alerts with the path that
  reaches them, which Bandit cannot produce.
- **An alert does not fail a run.** Reading the alerts is a human obligation, carried on the same
  review date `TECH_DEBT.md` already keeps for the dependency waivers.
- The job runs on an x64 runner; CodeQL's arm64 support is newer than the rest of the pipeline and
  is not worth the first-run risk.
- Pull requests are not analysed, so a defect is seen after it reaches `main`, not before. That is the
  cost of keeping Dependabot's pull requests green and the workflow audit clean.
- A handful of minutes of continuous-integration time per run, free for a public repository.

## Alternatives considered

- **CodeQL on every pull request.** Rejected: Dependabot's read-only token makes those runs fail, and
  the actor guard that avoids it is a workflow-audit finding.
- **Semgrep.** Rejected: a second analyser with a large overlap and no data-flow advantage for
  Python over CodeQL.
- **Bandit only.** Rejected: it cannot follow data flow, which is the class that matters here.
- **CodeQL as a local hook.** Rejected: there is no offline CodeQL, and the commit stage must not
  depend on a service.
