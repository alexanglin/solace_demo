# ADR-0026: Expiring, reviewed waivers for known upstream advisories

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

`AGENTS.md` already requires that a known upstream dependency finding be treated as an explicit release
risk, that reachability and compensating controls be documented, and that release be blocked until there
is a safe upgrade, an upstream fix, or a time-bounded human-approved waiver. No mechanism implemented the
waiver, so the requirement was unenforceable in either direction: nothing recorded a review, and nothing
stopped a review from outliving the advisory it described.

The dependency audit ran `pip-audit --strict --require-hashes` and let its exit status decide the outcome.
That gate arms the moment `agent-mesh/pyproject.toml` exists, which is Phase 0's first activity. Agent
Mesh 1.28.7 pins Starlette 0.49.1, whose advisories are fixed only in the 1.x series, and `google-adk`
1.18.0. The audit would therefore have failed on the first Phase 0 commit and stayed failing, leaving
`git commit --no-verify` as the only way to work — the routine bypass
[ADR-0012](0012-git-hooks-with-ci-as-authority.md) exists to prevent.

Measured against real output, `pip-audit --format json` reports Starlette 0.49.1 as seven vulnerability
entries covering five distinct identifiers, because an advisory is repeated once per resolved alias. It
also exits 1 both when it reports advisories and when it rejects its own invocation, so the exit status
alone cannot distinguish a finding from a failure to run.

## Decision

Record every accepted advisory in `dependency-waivers.toml` at the repository root, and adjudicate the
audit with `tools/dependency_waiver_gate.py` instead of with pip-audit's exit status.

A waiver is keyed on the audited domain, the package, the exact pinned version, and the advisory
identifier, so any version change invalidates it and forces a new review. Each waiver must carry a reason,
a reachability statement, a compensating control, a reviewer, a review date, and an expiry. The lifetime
and reason limits are in [operating-parameters.md](../operating-parameters.md#code-quality-gates).

The gate enforces the contract in both directions. No reported advisory may lack a waiver whose review
window is open, and no waiver may match an advisory that is no longer reported. A waiver whose window is
closed covers nothing, so its advisory is additionally reported as unwaived.

The written JSON report, not the exit status, is the oracle. A missing report, an unparsable report, a
report that is not an object, or any malformed entry fails. A pip-audit status above 1 means the audit did
not start and fails immediately.

## Consequences

- The requirement in `AGENTS.md` becomes executable, so a known advisory can be accepted deliberately and
  visibly rather than by bypassing the hook.
- Every acceptance carries a named reviewer and an expiry, so it returns for review rather than becoming
  permanent silence. A waiver cannot outlive its advisory, so the registry cannot accumulate dead reviews.
- This is a real suppression. Between review and expiry the audit does not fail on a waived advisory, and
  the compensating control is a written claim that no gate verifies. The expiry and the named reviewer are
  what make that acceptable; they are not equivalent to a fix.
- Adjudication now depends on pip-audit's JSON shape. A change to that schema is a gate failure rather
  than a silent pass, which is the correct direction but is additional upstream coupling.
- The `pnpm audit` leg is unchanged and has no waiver mechanism. `apps/dashboard/` does not exist yet, so
  that branch is inert; the registry carries a `domain` field ready for it.

## Alternatives considered

- **Pass `--ignore-vuln` to pip-audit from the registry.** Rejected: pip-audit would then never report the
  advisory, so nothing could detect a waiver that has stopped matching anything, and the registry would
  rot silently. Adjudicating the full report is what makes the stale-waiver rule possible.
- **Let the audit keep deciding by exit status, and fix the advisories by upgrading.** Rejected: the
  fixes require Starlette 1.x, which the pinned Agent Mesh release does not permit. This is not a case
  where an upgrade is available and being avoided.
- **Model this on `mutation-survivors.toml` and call it equivalent.** Rejected as a justification. A
  reviewed mutation survivor stays in the score denominator, so that registry never weakens the number it
  reports. A waived advisory genuinely is suppressed. The mechanisms look alike and differ in what they
  cost, and the record should say so.
- **Allow a waiver with no expiry when upstream has no fix.** Rejected: an advisory with no available fix
  is exactly the case that must be revisited, because the fix appears upstream without notice here.
