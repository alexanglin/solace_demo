# ADR-0136: Bind live snapshots to accepted run identities

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0124

## Context

The public snapshot carries `currentRun`, and accepted live start/reset responses carry stable
`missionId` and `runId` values. The browser used the run mode to prevent replay-owned context from
unlocking live controls, but it did not correlate a live snapshot's identifiers with either its reduced
mission or the mutation that caused the resnapshot. Retaining those identifiers without using them left
room for a validly shaped cross-run snapshot to replace the current mission.

Removing the identifiers would prevent the browser from proving that a `202` operation outcome and a
later authoritative snapshot describe the same run. Treating the `202` response itself as mission state
would violate the reducer ownership boundary.

## Decision

Keep the accepted mutation identity in the production source session, outside reduced mission state.
Before opening the replacement live source, the production runtime records the response's exact
`missionId` and `runId`. The next live snapshot must:

- name the same mission in `currentRun.missionId` and `state.currentMission.identifier`; and
- when an accepted operation is pending confirmation, match both its `missionId` and `runId`.

A mismatch produces `RUN_IDENTITY_MISMATCH`, retains the prior immutable mission and timeline, and keeps
the expected identity until a matching validated snapshot arrives. The expectation clears only after
snapshot anchor validation succeeds. Replay session identity remains the key used to select the
read-only replay endpoint and does not enter reduced state.

The check does not promote mutation responses into mission state. Only a validated matching snapshot or
ordered event can change the mission reducer checkpoint.

## Consequences

- Every live mutation identity has a browser consumer beyond display copy.
- A stale or crossed live snapshot cannot silently confirm the wrong accepted operation.
- Initial live snapshots still validate their run mission against reduced state when no mutation is
  pending.
- The source session owns one small, process-local expected identity that disappears after successful
  confirmation and is never persisted or digested.

## Alternatives considered

- **Remove `missionId` and `runId` from snapshots.** Rejected because the browser could not correlate an
  accepted operation with later authority.
- **Apply the `202` response directly to mission state.** Rejected because acceptance is operation state,
  not evidence that the reducer has observed the run.
- **Compare only the mission identifier.** Rejected because a reset/start reconciliation can retain a
  stable mission while distinguishing attempts by run identifier.
- **Leave the fields validated but otherwise unused.** Rejected because shape validation does not prevent
  a semantically crossed run.
