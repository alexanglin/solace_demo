# ADR-0054: Enforce the verification authority with branch protection on `main`

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none. This record makes enforceable a claim
  [ADR-0012](0012-git-hooks-with-ci-as-authority.md) already made.

## Context

[ADR-0012](0012-git-hooks-with-ci-as-authority.md) states that "hooks are fast feedback; CI is the
authority" and that `--no-verify` "can skip local execution but cannot merge unverified work."
[CONTRIBUTING.md](../../CONTRIBUTING.md) tells a contributor to work on a short-lived branch and open a
pull request. Until now nothing enforced either sentence: `main` carried no protection at all, so a
single `git push --no-verify origin main` would have placed unverified work on the default branch of a
public repository, and the second half of ADR-0012's sentence was aspiration rather than fact.

Three things made this concrete on 2026-08-20.

First, the branch was pushed for the first time with hooks enabled, and CI immediately found a defect
that no local run could have found. `test_a_missing_openssl_fails_closed` simulated a missing
`openssl` by setting `PATH=/bin`, which is true on macOS and false on Debian, where `/bin` is a symlink
to `/usr/bin` and `openssl` is in it. On the reference workstation the test passed and asserted a real
fail-closed path; on the Linux runner it ran the script with `openssl` present and failed. A gate that
only ever runs on one operating system cannot see a defect of that shape, which is precisely why the
authority is the runner and not the workstation.

Second, [ADR-0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md) put Dependabot to
work, and it now opens pull requests on its own. Those changes reach `main` through the merge button
rather than through a person's `git push`, so the merge button is where the verification has to be.

Third, the image scan of [ADR-0048](0048-scan-images-and-deploy-configuration-with-trivy.md) ran for
the first time and failed, as designed, on unwaived advisories. A red required check is only a
meaningful signal if something acts on it.

The repository is a public reference implementation whose stated subject is verified delivery. An
unprotected default branch is a claim the artifact does not keep.

## Decision

**Protect `main` so that every change arrives through a pull request whose checks passed, and enforce
it for administrators too.** The configuration is:

| Setting | Value | Why |
| --- | --- | --- |
| Require a pull request before merging | yes, **0** required approvals | The documented workflow. Zero approvals because a sole maintainer cannot approve their own pull request, and a rule nobody can satisfy is a rule that gets switched off |
| Require conversation resolution | yes | An unresolved review comment is unfinished work |
| Required status checks | `commit-stage hooks`, `pre-push hooks`, `no credentials in CI` | The three jobs of `checks.yml`, which runs unconditionally on every pull request |
| Require branches up to date before merging | no | See the consequences below |
| Require linear history | yes | The changelog is generated from Conventional Commits; a merge commit that carries no type would break that derivation, and the project already merges fast-forward |
| Enforce for administrators | yes | The repository's whole subject is that verification is not optional. An exemption for the one person who pushes is the entire exemption |
| Force pushes | blocked | Published history is evidence; rewriting it destroys the audit trail |
| Deletions | blocked | — |

**The required checks come from `checks.yml` only.** No job of `security.yml` is required, and this is
a correctness constraint rather than a preference. Its `image-scan` job is filtered to a set of paths
and its `codeql` job does not run on `pull_request` at all
([ADR-0050](0050-scan-python-with-codeql-in-continuous-integration-only.md)). GitHub holds a required
check that never reports as pending forever, so requiring a conditional job would deadlock every pull
request that did not happen to touch its paths. The daily run of
[ADR-0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md) remains the instrument for
those scans, and reading it is the human obligation already recorded in
[TECH_DEBT.md](../../TECH_DEBT.md).

## Consequences

- Direct pushes to `main` stop working, including the maintainer's own. Landing a change becomes:
  branch, push the branch, open a pull request, let the three checks pass, merge. This is what
  `CONTRIBUTING.md` already described.
- ADR-0012's sentence becomes true. `--no-verify` still skips the local hooks, and the same hook
  configuration then runs on the runner where skipping is not available.
- Dependabot's pull requests are gated by the same three checks as everything else, so an automated
  lockfile bump cannot land unverified.
- **Branches are not required to be up to date before merging.** Dependabot keeps up to five open pull
  requests per ecosystem across six ecosystems; under the strict setting every merge would invalidate
  every other open branch and demand a rebase, and a rule that generates that much churn is one people
  route around. The cost is a semantic conflict between two independently green branches, which the
  next push to `main` catches on the runner rather than at the merge. Revisit if that ever happens.
- The maintainer keeps administrative access to the settings, so the protection can be lifted
  deliberately. That is the intended escape hatch: visible, auditable, and not a flag on a push.
- A red required check now blocks work, which is the point and also the cost. The three checks were
  green on the runner before this record landed.

## Alternatives considered

- **A repository ruleset instead of classic branch protection.** Rulesets are GitHub's newer mechanism
  and can be exported as JSON, which suits a repository that treats its verification configuration as
  an artifact. Deferred rather than rejected: classic protection expresses exactly this policy today,
  and moving to a ruleset is a later change with no behavioural difference to record.
- **Protect against force pushes and deletion only.** Rejected: it prevents history loss but still
  lets unverified work reach `main`, which is the half that matters here.
- **Require approvals.** Rejected while the project has one maintainer: GitHub forbids approving your
  own pull request, so any non-zero count would make merging impossible and the protection would be
  turned off within the day. Raise it the moment there is a second maintainer.
- **Exempt administrators.** Rejected: it is the exemption that makes every other setting decorative.
- **Require the image scan and CodeQL.** Rejected for the deadlock reason above, not because they are
  unimportant.
