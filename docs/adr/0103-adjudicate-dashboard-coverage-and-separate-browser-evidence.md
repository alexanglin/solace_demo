# ADR-0103: Adjudicate dashboard coverage and require separate browser evidence

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0057 requires independent 95 percent statement, branch, function, and line coverage for the
dashboard. The package currently delegates that decision entirely to Vitest command-line thresholds.
That is not equivalent to the fail-closed Python member gate: an empty or incomplete production-file
inventory can report a reassuring percentage, the command does not prove that every hand-written source
file appeared in its report, and display rounding is not an integer comparison.

The dashboard also begins with 64 fixture-driven Playwright acceptance cases. They exercise a real
browser surface through serialized trust-boundary inputs, but they do not start the production service
closure and therefore cannot establish that Caddy, the Unix socket, HTTP, SSE, Postgres, the broker, the
recorder, and replay validation work together. Conversely, collecting browser execution coverage would
let broad workflows hide missing focused tests without proving any of those service boundaries.

## Decision

Adjudicate dashboard coverage independently from the test runner. `dashboard-test-full.sh` runs the
complete Vitest unit, component, and integration inventory with V8 coverage, writes a JSON summary into
a temporary directory, enumerates every tracked or unignored JavaScript and TypeScript file under
`apps/dashboard/src`, and passes both artifacts to `tools/typescript_coverage_gate.py`.

The gate treats the report as untrusted. It refuses missing, malformed, duplicate-key, incomplete, or
internally inconsistent data; a source or report path outside the dashboard; a missing or unexpected
production file; a non-regular or symbolic-link source; JavaScript or JSX production; skipped counts;
and V8, c8, Istanbul, or Node coverage-ignore directives in hand-written production source. Test files,
generated contract types, and declaration files are not production inventory and are the only source
exclusions.

The gate recomputes aggregate counts from per-file integer counts and compares statements, branches,
functions, and lines independently with integer arithmetic. Each dimension requires 95 percent. A
source inventory with no measurable statements or lines fails; zero branch or function opportunities
are complete for that dimension. The runner's percentage and aggregate row are consistency evidence,
not the authority for the verdict.

Keep deterministic frontend integration specifications in files named
`*.integration.test.ts` or `*.integration.test.tsx`. A separate, non-empty integration command runs at
pre-push and in continuous integration, and the complete coverage run includes those specifications.
Contract validation through sources and reducers into rendered state belongs to this class. Missing
integration discovery is a failure, not a skip.

Keep browser evidence separate from package coverage:

- The existing 64-case Playwright inventory remains fixture-driven browser acceptance and runs in full
  at pre-push and in continuous integration.
- After the production mission-control closure exists, a production configuration re-executes four
  existing case identities against `http://127.0.0.1:8080`: the live heartbeat/recovery/exhaustion
  sequence, guarded reset, ten-fold replay digest, and zero-remote-request workflow. That path forbids
  the test source, request interception, and fixture helpers.
- Resourceful service integration and production browser end-to-end execution run in a dedicated
  continuous-integration job and through an explicit local command. They use a uniquely named Compose
  project and remove only that project. A missing browser, runtime, service, or report fails.

Do not merge Playwright coverage into the Vitest report. Coverage, deterministic integration, fixture
browser acceptance, service integration, and production end-to-end execution are separate required
claims; none substitutes for another.

## Consequences

- A source file cannot disappear from coverage by changing a runner pattern, adding an ignore comment,
  or producing an empty report.
- The dashboard gains a Python adjudicator and an additional deterministic integration invocation at
  pre-push, increasing local and continuous-integration duration.
- Test, generated, and declaration exclusions are deliberately narrow. A new source category needs a
  superseding decision rather than a convenient omit.
- The 64 authored Playwright cases remain stable, while four of them eventually run twice against
  materially different boundaries.
- Production end-to-end evidence remains blocked until the live API, replay, fleet control, and exact
  package closure exist. Fixture acceptance must not be described as that evidence in the meantime.
- Full-stack tests need containers and bounded cleanup, so they cannot run implicitly in every local
  pre-push hook.

## Alternatives considered

- **Trust Vitest's threshold exit status.** Rejected because it does not independently prove the owned
  source inventory or reject an empty measurable result.
- **Merge Playwright execution coverage into the package total.** Rejected because broad journeys can
  conceal missing focused tests and still say nothing about an unexecuted service boundary.
- **Treat the 64 fixture cases as production end-to-end tests.** Rejected because their adapters and
  intercepted requests deliberately replace the production runtime.
- **Add four new Playwright declarations for production.** Rejected because the accepted browser
  inventory is fixed at 64 and the same behavior should be compared across fixture and production
  sources.
- **Start and remove Compose automatically from every local pre-push.** Rejected because an implicit
  resource mutation could interfere with an operator's stack and would make ordinary local verification
  depend on container availability.
