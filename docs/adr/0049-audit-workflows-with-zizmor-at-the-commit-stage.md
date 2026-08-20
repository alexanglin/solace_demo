# ADR-0049: Audit the GitHub Actions workflows with zizmor at the commit stage

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The workflows under `.github/workflows/` are the authority that re-runs every hook
([ADR-0012](0012-git-hooks-with-ci-as-authority.md)), and since
[ADR-0048](0048-scan-images-and-deploy-configuration-with-trivy.md) they also pull images and will soon
hold a scheduled scan. Two things already hold them: `actionlint` checks their shape, and a test
asserts that every `uses:` is pinned to a forty-character commit. Nothing checks the properties a
workflow compromise actually turns on: a checkout that leaves the repository token on disk for a later
step or artifact to read, an expression interpolated into a `run:` line, permissions broader than a
job needs, or a trigger that hands a fork's code the repository's secrets.

zizmor is a static analyser for exactly those properties. Version 1.29.0 is published on PyPI with
wheels for the arm64 macOS and Linux hosts this repository runs on and is mirrored as a pre-commit
hook at `zizmorcore/zizmor-pre-commit`, which already scopes itself to the workflow files,
`dependabot.yml`, and action manifests. It runs offline by default when no GitHub token is set; its
online audits need a token and a network and would give the local and continuous-integration stages
different verdicts. Run offline against the current `checks.yml` it reports three medium findings, all
`artipacked`: each `actions/checkout` step persists the token because `persist-credentials` is left at
its default, and no step in either stage needs it — the pushed-range scripts read local references.

## Decision

**zizmor 1.29.0 audits the workflow and Dependabot files at the commit stage, offline, and any finding
fails.** The mirror repository is pinned at `v1.29.0` in `.pre-commit-config.yaml` with `--offline`,
so a contributor with a token in the environment gets the same verdict as continuous integration,
which re-runs the commit stage and therefore the audit. The three `artipacked` findings are fixed by
setting `persist-credentials: false` on every checkout rather than suppressed; a suppression is an
inline `# zizmor: ignore[rule]` comment, which the repository's no-suppression rule
([ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md)) permits only with a record
naming the rule and the condition for its removal.

## Consequences

- Template injection, persisted credentials, over-broad permissions, and dangerous triggers become
  commit-stage failures, in the same place a syntax error already is.
- **Offline mode forgoes the online audits** — known-vulnerable actions, impostor commits, and
  reference-version mismatches. The first is covered by Dependabot's `github-actions` updates; the
  other two are mitigated by the forty-character pin that the existing test enforces.
- A new zizmor release can add an audit that reddens an unchanged workflow. The pin keeps that a
  deliberate upgrade, reviewed like any other hook revision, rather than a surprise.
- Every future checkout must set `persist-credentials: false`, or state why it needs the token.

## Alternatives considered

- **`actionlint` alone.** Rejected: it checks that a workflow is well-formed, not that it is safe.
- **zizmor as a continuous-integration action only.** Rejected: the finding would arrive after the
  push, and the commit stage is where the repository puts fast feedback.
- **Online mode with a token.** Rejected: the local and continuous-integration verdicts would differ,
  and the commit stage must not depend on a credential.
- **Semgrep's workflow rules.** Rejected: a second analyser for a narrower set of checks.
