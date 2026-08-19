# ADR-0023: Make complexity, duplication, and mutation gates executable

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) requires measured cognitive
complexity and duplication, and [ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) requires
independent mutation scoring for Tier 1 code. [ADR-0019](0019-fail-closed-quality-gates.md) explicitly
left all three without executable entry points. A declared control that cannot run, or that passes when
there is no code or test evidence, is not a release gate.

The repository is a `uv` workspace. A single mutation run from its root does not preserve member-local
imports, test selection, or result caches, and an aggregate result would let one strongly tested module
hide a weak one. Mutation results also include states other than killed and survived; treating an
incomplete, timed-out, skipped, or suspicious result as neutral would create another false green.

## Decision

Run three project-owned, fail-closed entry points at `pre-push` and through the identical CI stage. Their
current numeric limits and measurement commands are recorded in
[`docs/operating-parameters.md`](../operating-parameters.md), the repository's single home for gating
numbers.

1. Pin Complexipy 7.0.1 in the root lock and use it to scan every project-owned Python source and test,
   including scripts and Agent Mesh plugins. It is a repository-level static parse; it does not import
   Agent Mesh code into the application runtime. Ruff remains authoritative for cyclomatic complexity,
   function size, parameters, branches, returns, locals, nesting, and Boolean-expression limits.
2. Pin jscpd 5.0.14 in the isolated Node hook environment and scan project-owned Python, TypeScript,
   JavaScript, shell, and adjacent supported source formats in strict mode. This is a multi-language
   duplication scan; it does not claim semantic equivalence between different languages.
3. Pin mutmut 3.7.0 in the root lock and run it independently from each Tier 1 workspace member. Every
   such member owns a `tests/` directory and a `[tool.mutmut]` table selecting `src/` and those tests.
   The configuration explicitly invalidates cached verdicts when member tests, workspace source,
   manifests, or the root lock changes. Mutation exclusions, type-check filtering, `also_copy`, and
   mutation-suppression pragmas are prohibited.

Score each mutated Python module independently using killed divided by killed plus survived. Reviewed
survivors remain in that denominator and therefore never waive the required score. A survivor review
must name the exact Tier 1 member and mutant, include a substantive reason and reviewer, and contain
review and expiry dates within the limits in `docs/operating-parameters.md`. Reviews of a killed,
missing, or non-Tier 1 mutant are stale and fail. Missing metadata, zero mutants for an active Tier 1
member, duplicated identities, unreviewed survivors, and every nonterminal or unknown result fail.

Mutmut 3.7.0 mutates function bodies only. Module-level declarations and other non-function behavior
remain subject to coverage, property-based tests, failure injection, contract tests, and review; the
project must not describe those constructs as mutation-tested. The mutation run creates ignored
`mutants/` cache directories but must not rewrite tracked source or tests.

## Consequences

- The maintainability and mutation requirements now have commands that can fail locally and in CI.
- Mutation evidence cannot be diluted across members or modules, and incomplete runs cannot count as
  success.
- Tier 1 packages need co-located tests in addition to root cross-component suites.
- The pre-push stage becomes slower and creates ignored mutation caches. Cache invalidation is broader
  than mutmut's default so a sibling-domain or test change cannot reuse a stale verdict.
- The empty Tier 1 scaffolds correctly fail until they contain tested, mutation-eligible behavior. This is an
  implementation status, not a reason to weaken the gate.
- Function-only mutation leaves a known blind spot that the other mandatory test classes must cover.

## Alternatives considered

- **Run all members in one root mutmut invocation.** Rejected: member import paths, test selection, and
  caches do not have one safe root interpretation, and an aggregate score masks weak modules.
- **Use a reviewed-survivor count to increase the mutation score.** Rejected: an annotation explains a
  survivor but does not demonstrate that tests kill it.
- **Treat timeouts, skipped mutants, or type-check catches as killed.** Rejected: none proves that the
  project's behavioral tests detected the mutation.
- **Run mutation on every commit.** Rejected: the workload is unsuitable for the commit-stage time
  budget; pre-push and CI are the blocking authorities.
- **Use a regex duplication checker.** Rejected: token-aware, format-aware detection produces a more
  stable signal across the owned language set.
