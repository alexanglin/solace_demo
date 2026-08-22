# ADR-0066: Select commit-stage tests from a project-owned import graph

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0012](0012-git-hooks-with-ci-as-authority.md) decides what each stage runs. Its Decision names
"the affected unit tests" at `pre-commit` and "the full unit suite with coverage thresholds" at
`pre-push`; its Consequences call the split "running the affected subset locally and the full suite
at push time". The push half is implemented. The commit half is not.

`scripts/hooks/python/pytest-related.sh` runs the entire deterministic root suite. It is declared
`pass_filenames: true` and never reads `"$@"`, so the staged paths pre-commit hands it are discarded.
Its own comment states the reason and the precondition for changing it:

> A source-to-test dependency map does not exist yet, so path guessing would silently miss
> shared-contract and tooling consumers. The selector can become narrower only after a project-owned
> dependency map proves that every owned module maps to tests.

No such map existed. `tools/import_contract_gate.py` parses imports but keeps only the eight
forbidden roots, skips every relative import, and collapses `a.b.c` to `a`, so it retains no edges.

Counted on the reference MacBook, 2026-08-21, before this change:

| Suite | Tests |
| --- | --- |
| Root, whole deterministic suite | 942 |
| — of which `tools/` | 616 |
| — of which `packages/` | 305 |
| — of which `tests/` | 21 |
| — of which `services/` | 0 |
| Agent Mesh, whole deterministic suite | 76 |

Test counts are exact; wall clock on this workstation is not. The same root suite measured 25.1 s,
32.6 s, and 40.5 s across the session as unrelated processes came and went, so every duration in this
record is quoted from a back-to-back pair measured under one load rather than from a single run.

Two consequences follow. The commit stage runs all 942 root tests on every Python commit, against the
**≤ 60 s** budget `CONTRIBUTING.md` records, and the dominant share is `tools/quality_gate_tests`,
which a change under `packages/` or `services/` cannot affect. Separately, the hook's
`exclude: ^agent-mesh/` means a commit touching only the Agent Mesh domain runs no test at all, at
the commit stage or at any stage before push.

## Decision

Add `tools/affected_tests.py`, a pure module that builds the dependency map the script comment
requires and prints the test files a change affects.

It derives each file's importable module name the way mypy and pytest do — walk up while
`__init__.py` exists; the first directory without one is the import root — then parses every owned
Python file with `ast`, resolving absolute and relative imports against that index to first-party
files. Third-party and standard-library imports resolve to nothing and are dropped. Inverting the
edges gives dependents; the transitive closure of dependents of the changed files, restricted to test
files, is the affected set.

The module is pure: it launches no process and reads no directory. The whole-tree listing is
enumerated by the calling script with `git ls-files --cached --others --exclude-standard` and handed
over as `--paths-from`, the pattern `scripts/hooks/repo/directory-fanout.sh` already uses to keep
`subprocess` inside the four owners [ADR-0025](0025-narrow-ruff-subprocess-waivers.md) admits.

**The selector fails safe: it widens to the full suite rather than guessing.** It prints the sentinel
`:all:` when a changed path is not a Python file in the graph, is a `conftest.py`, or is absent from
the listing. A changed file that no test transitively reaches selects nothing, which is correct at
this stage — `pre-push` runs everything regardless, and CI is the authority.

Both Python trees use it. The root hook runs the selected files under the 3.14 interpreter. The Agent
Mesh hook runs them from inside `agent-mesh/` under its own 3.13 interpreter, so
[ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) still holds: selection is
text analysis over source, execution stays in the domain's own toolchain.

The `pre-push` stage is unchanged. It already runs every unit test in all three toolchains, and a
test now asserts that so the guarantee cannot be quietly removed.

Measured back-to-back, two rounds alternating whole-suite and selected runs so both saw the same
load. A one-file change under `packages/domain/src/` selects 94 of 988 tests and runs in 5.39 s and
5.34 s, against 31.28 s and 29.02 s for the whole suite. A change to `tools/coverage_gate.py` selects
25 tests and runs in 0.9 s. A change the selector cannot narrow runs the whole suite, as before.

## Consequences

- The commit stage now costs what the change costs rather than what the tree costs, which is what
  ADR-0012 decided and what its "a slow hook is a bypassed hook" rationale depends on.
- A commit touching only the Agent Mesh domain runs its affected tests. Before, it ran none.
- The trigger widens beyond `.py`: a change to a hook script, a workflow, a manifest, or a committed
  configuration reaches the tests that read it, because those paths take the `:all:` path. The
  previous `types_or: [python, pyi]` filter ran nothing for them.
- Module-name derivation now has a third implementation in play, alongside mypy's and pytest's. If
  ours drifts from theirs the gate selects the wrong tests, so the conformance tests pin it against
  real repository paths rather than invented ones.
- Negative: a dependency the AST cannot see — a plugin loaded by string name, a fixture found by
  directory convention, a test that reads a source file as data — is an edge the selector misses, and
  a missed edge means a test that should have run did not. The `:all:` fallbacks cover the cases we
  found; they cannot cover the ones we did not. `pre-push` and CI remain the authority, and this
  stage is explicitly fast feedback, not a verdict.
- Negative: the commit stage's cost is now variable, so "how long does a commit take" no longer has
  one answer, and a change that touches a widely imported module is slower than the old fixed cost
  would suggest at the low end and no worse at the high end.
- Negative: one more project-owned gate to maintain, at tier 2's 95% statement and branch coverage.

## Alternatives considered

- **`pytest-testmon`.** Rejected: it selects from a stored coverage database, so its verdict depends
  on mutable local state a clean checkout does not have, and a stale database silently under-selects.
  The failure mode is the one this stage exists to avoid.
- **`pytest --picked` or a git-diff plugin.** Rejected: it selects tests *in* changed files, not
  tests *affected by* changed files, so editing a source file with its tests elsewhere selects
  nothing — precisely the "silently miss" hazard the script comment names.
- **Select at workspace-member granularity from the manifests.** Rejected: `packages/observability`,
  `packages/store`, and every `services/*` member have `src/` and no `tests/`, so member granularity
  maps most changes to no tests at all, and the cross-cutting suites under `tests/` belong to no
  member.
- **Keep running the whole root suite and accept 25 s.** Rejected: it contradicts ADR-0012's Decision,
  and the trend is the wrong way — the suite has grown to 942 tests and the budget is 60 s.
- **Run the affected subset at `pre-push` too.** Rejected: the push stage is where the full suite
  earns its coverage thresholds, and narrowing it would leave nothing running everything before CI.
- **Drop the commit-stage test hook and rely on `pre-push`.** Rejected: it removes the fast feedback
  ADR-0012 wants, and the defect it would "fix" is a selector that can be written.
