# ADR-0056: Raise mypy to every strictness lever both trees already satisfy

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) requires strict typing over source, tests, fixtures, scripts, and configuration, with no inline escape hatches. Both Python domains satisfy it: the root table and the Agent Mesh table each declare `strict = true`, `warn_unused_ignores`, and `warn_unreachable`, and the tree carries no `# type: ignore`, no `# noqa`, and no untyped definition. `object` is used at every JSON and YAML boundary where `Any` would ordinarily appear.

`strict = true` is not the end of mypy's strictness, and the gap was never measured. mypy 1.19.0 ships thirteen error codes that are off by default, plus four `disallow_any_*` switches, `strict_equality_for_none`, and `local_partial_types`, none of which `--strict` implies. Three of those codes bear directly on invariants this repository already asserts in prose: `exhaustive-match` on the explicit state machines `packages/domain` is built from, `unused-awaitable` on the asynchronous services, and `ignore-without-code` on the suppression ban.

The gap was measured against the real tree rather than estimated. Enabling all thirteen codes together with `strict_equality_for_none`, `disallow_any_explicit`, and `local_partial_types` produces **zero errors across the root tree's 107 source files**, and two errors in the Agent Mesh tree, both in one compatibility probe that annotates a helper `Callable[..., object]` — where the `...` is an explicit `Any`.

A second gap is structural. [ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) requires two separate `[tool.mypy]` tables because one table cannot declare two `python_version` values, and it closes with the observation that the two manifests can drift and "only review catches it; no gate compares them today". The two stages checking one tree under two configurations is not hypothetical: it has already happened once in this repository and was fixed by naming the configuration file explicitly.

A third gap is in the whole-program run. `scripts/hooks/python/mypy-full.sh` builds its argument list from the hard-coded roots `tools packages services tests migrations`, and the same list appears verbatim in `python-quality-full.sh` and inside `quality_root_python_source_present`. A new top-level directory holding Python is therefore checked file by file at the commit stage and **not at all** by the pre-push run — the run whose own header records that per-file checking "gives different results than checking the project". No test asserts the list covers what the repository actually holds.

## Decision

**Both `[tool.mypy]` tables declare every strictness lever the tree already satisfies, in one identical block, and a gate holds the two tables equal.**

Each table gains `strict_equality_for_none = true`, `disallow_any_explicit = true`, `local_partial_types = true`, and an `enable_error_code` list naming all thirteen optional codes: `deprecated`, `exhaustive-match`, `explicit-override`, `ignore-without-code`, `mutable-override`, `possibly-undefined`, `redundant-expr`, `redundant-self`, `truthy-bool`, `truthy-iterable`, `unimported-reveal`, `unused-awaitable`, and `unused-ignore`. The command line in `mypy-full.sh` is unchanged, so strictness is declared in exactly one place per domain.

`disallow_any_explicit` requires one source change. `agent-mesh/tests/test_pinned_runtime_overrides.py` annotates its math-embed evaluator helper `Callable[..., object]`; it becomes a `Protocol` whose `__call__` names the two call shapes the probe actually uses. This tightens the annotation without altering an assertion.

`tools/quality_gate_tests/contracts/test_mypy_configuration_parity.py` parses both manifests and asserts the two tables are equal except for `python_version` and the `[[tool.mypy.overrides]]` module lists — the two values [ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) and [ADR-0028](0028-untyped-solace-client-boundary.md) legitimately differ on. This is the same shape as the existing gate that holds the import-contract roots and the domain Ruff ban list equal.

`quality-components.sh` gains `quality_root_python_paths`, which emits the root directories holding Python by discovery rather than by a literal list, and `mypy-full.sh` and `python-quality-full.sh` consume it. `bandit-full.sh` and `duplication-full.sh` keep their own lists, which differ deliberately: the first excludes tests, the second adds `scripts` and `apps/dashboard`.

## Consequences

- The suppression ban stops being a convention that happens to hold and becomes a check. `ignore-without-code` rejects an uncoded ignore, `unused-ignore` rejects one that has outlived its cause, and `disallow_any_explicit` rejects the annotation that would otherwise reintroduce `Any` by hand.
- `exhaustive-match` will fail a `match` over a domain state machine that gains a member without gaining a branch. That is the point, and it will occasionally be inconvenient at exactly the moment a state is added.
- The two tables can no longer drift apart silently. They can still both be wrong in the same way, which the parity gate cannot detect and review must.
- **The parity gate constrains future work in a way that is easy to resent.** A legitimate future asymmetry between the two domains now costs an edit to the gate and a recorded reason, rather than a one-line change to one manifest.
- A new top-level Python directory is type-checked by the pre-push run from the moment it holds a file, with no edit to any script. The trade is that the roots are now discovered rather than declared, so reading the script no longer tells a contributor what will be checked.
- `redundant-expr` and `truthy-bool` reject defensive code that is provably dead. Some of that code is written deliberately, and rewriting it to satisfy the checker is not always an improvement.
- Nothing about the enforcement points changes. The codes take effect at the commit stage, at pre-push, and in continuous integration, because all three already run mypy through these tables ([ADR-0012](0012-git-hooks-with-ci-as-authority.md)).

## Alternatives considered

- **`disallow_any_decorated`.** Rejected on measurement: 27 errors across 7 files, every one of them `Function is untyped after decorator transformation` on a Hypothesis `@given` property test. The cause is upstream's decorator typing, not this repository's code, and the only local remedy would be to stop using property-based testing where [ADR-0015](0015-tiered-quality-gates.md) requires it.
- **`disallow_any_unimported`.** Rejected on measurement: exactly one error, `tests/phase0/test_solace_messaging_runtime.py` returning `Any` through an unfollowed import. That import is the untyped Solace boundary [ADR-0028](0028-untyped-solace-client-boundary.md) already contains and [TECH_DEBT.md](../../TECH_DEBT.md) already carries. Enabling the switch would restate a known gap as a build failure with no new information and no available fix.
- **`disallow_any_expr`.** Rejected on measurement: 516 errors across 50 files. Every deserialized payload is an `Any` expression before it is narrowed, so this switch is incompatible with reading JSON at all.
- **One `[tool.mypy]` table shared by both domains.** Rejected for the reason [ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) gives: one table cannot declare two `python_version` values.
- **Passing the new settings on the `mypy-full.sh` command line instead.** Rejected: the commit-stage hooks take their strictness from the table alone, so command-line settings would apply at one stage and not the other. That is the precise defect [ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) was written to close.
- **A gate that fails on any `# type: ignore` at all, with an expiring waiver registry.** Rejected as a policy change wearing the clothes of an enforcement change. [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) prohibits the *blanket* form and requires a recorded waiver for the rest; `ignore-without-code` enforces exactly that boundary, and a hard-zero rule would need its own decision rather than arriving as a side effect of this one.
- **Leaving the root list literal and adding a test that it covers every directory.** Rejected: the test would encode the same list a third time, and a contributor adding a directory would face a failing gate telling them to edit two scripts rather than a gate that simply checked the new directory.
