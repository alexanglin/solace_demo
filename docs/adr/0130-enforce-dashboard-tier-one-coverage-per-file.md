# ADR-0130: Enforce dashboard Tier 1 coverage per file

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0105](0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md) makes the dashboard
coverage report fail closed and holds the package aggregate at 95 percent independently for statements,
branches, functions, and lines. That aggregate can still let presentation tests mask an untested branch
in code that decides whether untrusted input may become mission state or whether a mutation may carry the
in-memory bearer.

The dashboard now has five hand-written production modules on those trust boundaries:

- `src/contracts/bootstrap.ts` applies the canonical JSON profile before bootstrap schema validation;
- `src/contracts/schema-registry.ts` is the offline Ajv boundary for every browser wire document;
- `src/domain/canonical.ts` serializes and hashes reduced state and ordered events and compares digests;
- `src/domain/reducer.ts` validates anchors and ordered events before changing mission state; and
- `src/api/mutation-client.ts` owns bearer use, idempotency keys, synchronous submission exclusion,
  response validation, and stale-runtime lockout.

The source session and production runtime compose those validated results and transports, while timeline,
map, table, and component modules own presentation. Treating all of them as one high-risk aggregate would
make the tier implicit and would spend test effort on rendering branches without strengthening a trust
boundary.

## Decision

Keep one complete Vitest V8 coverage pass. After validating its report and complete hand-written source
inventory under ADR-0105, `tools/typescript_coverage_gate.py` enforces the five paths above as an exact
dashboard Tier 1 coverage inventory. Each file independently requires 100 percent statement coverage and
100 percent branch coverage. A missing Tier 1 file, an omitted inventory entry, a missing report entry,
or a Tier 1 file with no measurable statements is a blocking finding. A file with no branch opportunities
is complete for that dimension only when the validated V8 counts are exactly zero covered of zero total.

The same report continues to require 95 percent independently for aggregate statements, branches,
functions, and lines across every hand-written production file. Generated schema types, declarations,
tests, and Playwright evidence retain the exclusions or separate evidence boundaries ADR-0105 selected;
no hand-written production exclusion is added.

The authoritative dashboard coverage wrapper enables the fixed Tier 1 policy when it invokes the typed
gate. Conformance tests hold the exact five-file inventory, the single package coverage pass, wrapper
activation, missing evidence, and every below-100 statement and branch case. The command exposes no path
argument that can narrow the fixed inventory.

This decision applies the Tier 1 *coverage* standard to the named browser modules. It does not claim a
TypeScript mutation score; the repository's current mutation instrument remains scoped to the Python Tier
1 core. Adding a TypeScript mutation instrument would be a separate verification decision.

## Consequences

- A completely covered UI cannot hide one uncovered validation, digest, fold, or mutation-security
  branch.
- Adding, removing, or moving a Tier 1 browser boundary requires an explicit inventory and documentation
  change; a missing named module fails instead of silently reducing the tier.
- The exact inventory deliberately excludes transport orchestration and presentation modules. They remain
  subject to complete source accounting and the four global 95-percent dimensions.
- The stricter rule adds no second test run and no browser-derived coverage, so it preserves the evidence
  separation and duration selected by ADR-0105.

## Alternatives considered

- **Raise the entire dashboard to 100 percent in all four dimensions.** Rejected because it classifies
  presentation and transport glue as safety-critical and would make rendering coverage the price of a
  trust-boundary change.
- **Require 100 percent only in Vitest configuration.** Rejected because runner thresholds do not prove
  that each expected file appeared, and a package aggregate lets one file mask another.
- **Run a second focused coverage suite for Tier 1.** Rejected because two reports can drift in discovery
  and needlessly double execution; the complete report already contains authoritative per-file counts.
- **Include source-session and production-runtime.** Rejected because they compose validated values and
  transport state rather than defining the raw-input, mission-state, digest, or mutation authorization
  rules selected here.
- **Use coverage-ignore directives for defensive branches.** Rejected by ADR-0105; a defensive trust
  boundary that cannot be exercised is unproved, not exempt.
