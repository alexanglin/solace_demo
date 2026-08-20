# ADR-0051: Re-scan daily and let Dependabot raise pinned-update pull requests

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Every audit in this repository ran only when somebody pushed. The dependency audit, the image and
configuration scans of [ADR-0048](0048-scan-images-and-deploy-configuration-with-trivy.md), and the
static analysis of [ADR-0050](0050-scan-python-with-codeql-in-continuous-integration-only.md) all
answer a question whose answer changes without any commit: a new advisory against an unchanged lock,
a vendor re-rating a finding, a new query in the analysis suite. The advisory that
[ADR-0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md) closes was published seventy days
after its fix shipped and was seen here only because a hook was run by hand on an unrelated branch.

`.github/workflows/checks.yml` has `push` and `pull_request` triggers and nothing else. The
`check-dependabot` schema hook has been registered since the toolchain was built and has validated
nothing, because no `.github/dependabot.yml` exists. Dependabot's configuration schema, as vendored by
that hook, accepts the `uv`, `github-actions`, `docker`, and `docker-compose` ecosystems this
repository now has, and `npm` for the dashboard that does not yet exist.

## Decision

**The scans run every day and on demand, and Dependabot raises the pull requests that clear them.**

`.github/workflows/security.yml` runs on a daily schedule (`17 6 * * *` UTC), on `workflow_dispatch`,
on every push to `main`, and on pull requests that touch the audited inputs: `deploy/`, both locks
and manifests, the waiver registry, the scanning scripts and modules, and the workflow itself. Its
jobs are the scheduled dependency audit with the Dockerfile configuration audit, the image scan of
every pulled and built image, and CodeQL. Every job has a timeout, every checkout leaves no credential
behind, dynamic values appear only in `env:`, and every action is pinned to a forty-character commit.

`.github/dependabot.yml` watches `uv` at `/` and `/agent-mesh`, `github-actions` at `/`, `docker` at
`/deploy/agent-mesh` and `/deploy/application`, and `docker-compose` at `/deploy`, daily, with at most
five open pull requests per ecosystem, a three-day cooldown, and commit prefixes that satisfy the
Conventional Commits hook. `npm` is added with the dashboard.

## Consequences

- A new advisory against an unchanged lock, a re-rated image finding, or a new analysis query is
  seen within a day instead of at the next push.
- **Up to thirty open pull requests, each running both hook stages.** The cooldown and the limit are
  what keep that from becoming the repository's main traffic.
- **A red daily run pages nobody.** The `TECH_DEBT.md` review date gains the obligation to read the
  last daily run; until a notification channel exists, that reading is the control.
- Dependabot's Docker updates move a tag and its digest together, which is what the compose policy
  gate requires; its `uv` updates regenerate the lock. Whether its bundled `uv` honours the
  `required-version` pin is unproven until its first pull request, and it cannot touch an
  `override-dependencies` entry, so the asteval override is retired by hand.
- Docker Hub's anonymous pull limit on shared runners is indistinguishable from a finding until the
  log is read; a failed pull is reported as a scan that did not complete, never as a clean result.
- pre-commit hook revisions stay a manual `just update-hooks`; Dependabot does not manage them.

## Alternatives considered

- **A weekly schedule.** Rejected: the user chose daily, and advisory databases update daily.
- **Renovate.** Rejected for now: it needs an application with write access and manages pre-commit
  revisions, which is the one gap Dependabot leaves; revisit if that gap hurts.
- **OSV-Scanner.** Rejected: it queries the same advisory database `pip-audit` already reads.
- **No pull-request automation, scans only.** Rejected: a scan that finds a fix nobody applies is a
  report, not a control.
