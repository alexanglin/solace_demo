# ADR-0015: Tier quality gates by risk instead of one flat threshold

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

The current gate demands 95% across four dimensions for every project-owned production package, plus thirteen full-strength test classes, for a solo build on one workstation.

Two problems. First, it is not measurable as written: `coverage.py` produces no function-coverage metric, and statements and lines are the same measurement, so two of the four Python dimensions do not exist. Four dimensions is correct for the TypeScript side only.

Second, a uniform threshold spends equal effort on the approval gate and on configuration glue, which is the wrong allocation when the approval gate is the reason the project exists.

## Decision

Restate the Python gate as **statement and branch coverage**, keeping four dimensions for the dashboard only, and enforce it per workspace member rather than as one global total.

Then tier by risk: the safety-critical core — approval authorization, the command gateway, domain state machines, idempotency and sequence rules, evidence scoring, and the contracts package — carries the highest coverage plus mandatory property, failure-injection, and mutation testing; adapters and services carry a slightly lower bar without a mutation gate; configuration and glue carry a smoke-plus-one-failure-path bar.

Name a specific mutation tool and a specific mutation score, so that "mutation tests are run" becomes a gate that can fail.

## Consequences

- The gate becomes measurable, which the current wording is not.
- Effort concentrates where a defect would be dangerous rather than merely untidy.
- The headline "95% everywhere" claim is weakened, and the documents must say plainly which code is held to which standard rather than implying uniformity.
- Tier assignment becomes a decision that must be recorded and defended per package, and there will be arguments about placement.

## Status note

Proposed pending the outcome of the second review pass. Not yet adopted.

Accepted on 2026-08-19 after that review pass. The two deferrals in this record — naming a specific mutation tool and a specific mutation score, and assigning each package to a risk tier — are discharged by [ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) rather than by editing this record.

## Alternatives considered

- **Keep the flat gate and drop the impossible metrics.** Rejected in this proposal: still misallocates effort, though it would at least be measurable.
- **Lower the bar uniformly.** Rejected: weakens the safety-critical code, which is the one place the bar should arguably be higher than 95%.
