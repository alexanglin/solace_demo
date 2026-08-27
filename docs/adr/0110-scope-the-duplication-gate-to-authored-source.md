# ADR-0110: Scope the duplication gate to authored source

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0023](0023-executable-deep-quality-gates.md) makes jscpd 5.0.14 a fail-closed pre-push and
continuous-integration gate over project-owned Python, TypeScript, JavaScript, shell, and adjacent
supported formats. Its numeric limits live in
[`operating-parameters.md`](../operating-parameters.md#code-quality-gates): at most 3 percent
duplicated lines repository-wide, a clone minimum of 8 lines and 50 tokens, strict mode.

[ADR-0058](0058-validate-dashboard-inputs-against-the-committed-schemas.md) requires one generated
TypeScript module per committed browser schema. The A2 increment committed 19 such modules plus a
schema-ID index under `apps/dashboard/src/contracts/generated/`.

Types derived from JSON Schema repeat every nested shape at each use site, because each module is
closed over one schema and shares no declarations with its siblings. The scan reports six clones
inside that directory: `dashboard-snapshot.ts` and `replay-bundle.ts` share a 99-line region;
`dashboard-event-frame.ts` shares 54-line, 49-line, and 45-line regions with three siblings;
`dashboard-reduced-state.ts` shares 42 lines with `dashboard-snapshot.ts`; and
`mutation-outcome.ts` shares 17 lines with `start-response.ts`.

Measured on this tree, the scan over the previously owned paths reports 2231 of 66524 lines
duplicated (3.35 percent), above the 3 percent limit, with TypeScript alone at 340 of 4947
(6.87 percent). The identical scan with the generated directory excluded reports 1931 of 65741
(2.94 percent), with TypeScript at 40 of 4164 (0.96 percent).

No authored change removes the reported clones. The generator emits one module per schema by
decision, and hand-editing its output is refused by the freshness gate that regenerates and compares
it.

## Decision

Measure duplication over authored source only. `scripts/hooks/repo/duplication-full.sh` passes
`--ignore 'apps/dashboard/src/contracts/generated/**'` to jscpd. Every other scanned path, the
3 percent limit, the 8-line and 50-token clone minimum, and strict mode are unchanged.

The exclusion creates no review gap. `pnpm --dir apps/dashboard run contracts:check`, wired both as
a file-triggered pre-commit hook and as an unconditional whole-tree pre-push hook, regenerates the
directory from the manifest-owned schemas and refuses any missing entry, any extra entry, and any
entry whose bytes differ. A hand-written file cannot survive in that directory, so no authored code
can escape duplication review by being placed there.

The generator itself, `apps/dashboard/scripts/generate-dashboard-contracts.ts`, stays inside the
scan.

This exemption is granted to generated output whose freshness an executable gate proves. It is not a
precedent for excluding authored code, and it names one exact path rather than a `**/generated/**`
pattern, so a future generated directory is scanned until its own record extends the exclusion.

## Consequences

- The gate measures code a contributor can refactor, and its verdict again reflects authored
  duplication rather than the arity of the committed schema set.
- Repository-wide duplication reads 2.94 percent against the 3 percent limit, which is 41 duplicated
  lines of headroom. The gate remains close to its limit, and the Python tree at 2.94 percent is
  what now dominates that figure. A future breach is a signal about authored code, which is the
  intent, but the margin is narrow enough that it can arrive from an unrelated change.
- Duplication among the generated modules is no longer reported. Repetition introduced by a change
  to the generator is visible in the generator source and in review, not in this gate's output.
- Adding a schema to the manifest no longer moves the repository-wide figure, so the gate no longer
  penalises growth of the committed contract surface.
- The hook's argument list carries a path that must be kept in step with the generator's output
  directory. A rename of that directory silently returns the generated modules to the scan, which
  fails closed rather than open.

## Alternatives considered

- **Raise the limit above 3 percent.** Rejected: it weakens the gate for the authored Python tree at
  2.94 percent and the authored shell tree at 6.49 percent in order to accommodate output that no
  contributor writes.
- **Emit shared TypeScript declarations across schemas.** Rejected:
  [ADR-0058](0058-validate-dashboard-inputs-against-the-committed-schemas.md) fixes one generated
  module per schema, and a cross-module reference graph would make each module's content depend on
  the traversal order of the whole schema set rather than on its own schema.
- **Ignore `**/generated/**` across the repository.** Rejected: it grants the exemption to any future
  directory carrying that name, none of which need have the byte-exact freshness proof this one has.
- **Stop committing the generated modules and emit them during the build.** Rejected:
  [ADR-0058](0058-validate-dashboard-inputs-against-the-committed-schemas.md) requires committed
  output precisely so it can be reviewed and checked offline; an artifact produced during the build
  is neither.
- **Record a time-bounded waiver instead of a scope decision.** Rejected: a waiver implies the
  condition expires. One generated module per schema is the standing decision, so the duplication it
  produces is permanent and belongs in the gate's scope rather than in an expiry calendar.
