# ADR-0052: Hold Dependabot to the seven-day cooldown the workflow audit requires

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** the three-day cooldown clause of
  [ADR-0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md); every other part of that
  record stands.

## Context

[ADR-0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md) gave every Dependabot update a
three-day cooldown, a number chosen to bound pull-request traffic rather than measured against
anything. Landing the configuration under the workflow audit of
[ADR-0049](0049-audit-workflows-with-zizmor-at-the-commit-stage.md) produced six high-confidence
findings from zizmor's `dependabot-cooldown` audit, one per update entry: "insufficient
`default-days` configured (less than 7)". The audit's reasoning is a supply-chain one — a compromised
release is usually withdrawn within days of publication, and a cooldown shorter than that window
installs it before it is pulled — and seven days is the audit's default, adjustable through its own
configuration file.

ADR-0049 made any finding fail the commit stage and reserved a suppression for a recorded exception.
Lowering the audit's threshold to match a number this project picked without evidence would be that
suppression under another name.

## Decision

**Every Dependabot update entry carries `cooldown: {default-days: 7}`, and the audit keeps its
default.** The test that pins the Dependabot shape asserts seven.

## Consequences

- An automatic update arrives up to a week after its release instead of three days. The daily scan
  still reports an advisory the day it is published; what waits is the pull request, and a blocking
  finding can be cleared by a bump raised by hand at any time.
- One fewer number in this repository rests on nothing but preference; this one rests on the
  audit's stated rationale.
- ADR-0051's text is now defective in one clause and this record is where the correction lives.

## Alternatives considered

- **Configure the audit to accept three days.** Rejected: it moves the audit's bar to meet an
  unmeasured preference, which ADR-0049 exists to prevent.
- **Suppress the six findings inline.** Rejected: six recorded exceptions to avoid changing one
  number.
- **Keep three days and accept a red commit stage.** Rejected: a red stage nobody can clear is the
  routine bypass [ADR-0012](0012-git-hooks-with-ci-as-authority.md) forbids.
