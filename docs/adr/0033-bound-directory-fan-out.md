# ADR-0033: Bound directory fan-out and decompose by concern

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

Nothing in this repository notices a directory growing without structure. Every other maintainability
property is a fail-closed gate with a number in [operating-parameters.md](../operating-parameters.md) —
cyclomatic complexity, cognitive complexity, function length, nesting depth, duplication, mutation score.
Directory fan-out is the one dimension of structure left to review, and
[ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) already records why review is not
an acceptable enforcement mechanism for a solo build.

Measured across tracked files at the time of this decision:

| Directory | Files |
| --- | --- |
| `docs/adr/` | 34 |
| `tools/quality_gate_tests/` | 28 |
| `scripts/hooks/` | 23 |
| `.` (repository root) | 22 |
| every other directory | at most 7 |

The distribution is what makes this actionable rather than arbitrary: there is a wide empty band between
7 and 22, so a limit inside that band separates the four outliers from everything else without arguing
about borderline cases.

The four are not the same kind of problem. `tools/quality_gate_tests/` and `scripts/hooks/` are flat
because nobody grouped them, they are still growing, and
[ADR-0032](0032-agent-mesh-semantic-configuration-validator.md)'s validator adds to both. `docs/adr/` and
the repository root are large for structural reasons that no amount of tidying removes.

## Decision

**A directory may contain at most 20 files whose immediate parent is that directory.**

- The count is **not recursive**, and a subdirectory is not a file. A directory holding 20 files and any
  number of subdirectories passes.
- Files are enumerated with `git ls-files --cached --others --exclude-standard`, which is the same
  tracked-or-unignored scope the Arrange-Act-Assert checker already uses
  ([ADR-0018](0018-enforced-arrange-act-assert.md)). A file staged but not yet committed counts, because
  the gate must refuse the commit that crosses the limit rather than the one after it.
- **The enumeration lives in the hook script, not in the gate.**
  [ADR-0025](0025-narrow-ruff-subprocess-waivers.md) confines `subprocess` to four reviewed Python
  owners, and counting directory entries is not a reason to reopen that decision, so the Python gate is
  a pure function of the listing and the registry. This also makes the gate trivially testable: its
  tests feed it path lists rather than building repositories.

**Exemptions live in `directory-fanout.toml`** and bind an exact directory path to a reason of at least 20
characters, a reviewer, a review date, `structural = true`, and this ADR. The contract is enforced in both
directions, as [ADR-0026](0026-expiring-dependency-waivers.md) enforces dependency waivers: a directory
over the limit with no entry fails, **and an entry naming a directory that is under the limit fails as a
dead exemption**. A missing, malformed, or unreadable registry is a failure, never a skip, and there is no
suppression mechanism outside the registry.

Structural entries carry **no expiry**. A dependency waiver expires because an upstream fix is something
to wait for; a structural exemption has nothing to wait for, and a recurring re-review that can only ever
reach the same conclusion is paperwork rather than a control. The dead-exemption rule is what keeps the
registry honest instead.

**Two structural exemptions are granted:**

- `docs/adr/` — every document in the repository links `adr/00NN-*.md` relatively, and an accepted ADR is
  never renamed or edited except to change its status ([README.md](README.md)). Splitting the directory
  breaks roughly a hundred cross-references, and the links *inside* accepted ADRs could not be repaired
  without violating the immutability rule that gives this log its value.
- `.` (the repository root) — `pyproject.toml`, `uv.lock`, `justfile`, `LICENSE`, `.gitignore`,
  `.pre-commit-config.yaml`, `.python-version` and the rest are found by tools that look only at the root.
  Moving them breaks the tools; leaving a stub behind defeats the purpose.

**`tools/quality_gate_tests/` and `scripts/hooks/` are decomposed into concern-named subdirectories rather
than exempted.** The gate exists to cause decomposition; granting a waiver to the two directories it was
written for would be self-defeating.

**A file named by an accepted ADR keeps its path.** An ADR is immutable, so relocating a file one names
leaves an accepted record stating a path that no longer resolves. Four files are therefore left where they
are while everything around them moves: the hook scripts `agent-mesh-test-full.sh`
([ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)), `check-env-template.sh`
([ADR-0032](0032-agent-mesh-semantic-configuration-validator.md)) and `check-docs-strict.sh`
([ADR-0017](0017-mutation-tool-score-and-risk-tiers.md)), and the test
`tools/quality_gate_tests/test_diagram_integrity.py`, which is additionally one of the four exact paths
[ADR-0025](0025-narrow-ruff-subprocess-waivers.md)'s `S603` allowlist names. The rule is cheap to follow
and it means no accepted record is made stale by tidying.

The gate runs at the `pre-commit` and `pre-push` stages and in CI, per
[ADR-0012](0012-git-hooks-with-ci-as-authority.md) and [ADR-0019](0019-fail-closed-quality-gates.md).

## Consequences

- Directory growth becomes a failure instead of a review obligation, and the two directories that were
  actually growing get names that describe what is in them.
- **`directory-fanout.toml` takes the repository root from 22 files to 23.** The root exemption is
  structural rather than count-based, so the gate will not report this. Stating it here is the alternative
  to hiding it.
- **Decomposing `scripts/hooks/` is a wide mechanical change.** Twenty-one `entry:` paths in
  `.pre-commit-config.yaml`, one command in the CI workflow, and explicit path strings in four test files
  name these scripts by path. Any branch in flight will conflict. The forty `run_hook` call sites did not
  have to change, because the shared test fixture now resolves a script by basename wherever it sits; that
  is a fixture, not an executable test, and it keeps a future regrouping from touching tests at all.
- A hook script moved into a subdirectory must source `quality-components.sh` through `../`, and carry a
  matching `# shellcheck source-path` directive. A contributor adding the next script has to copy that
  relative path correctly, where before there was nothing to get wrong.
- **The limit is a judgment, not a measurement.** 20 is defensible because of the empty band between 7 and
  22, not because 20 files is a proven threshold for comprehension. A different repository would pick a
  different number.
- Counting only immediate children means a directory holding 20 files and 50 subdirectories passes. That
  is deliberate — see the rejected alternative below — but it is a real blind spot.
- A structural exemption never expires, so nothing forces a periodic re-look at whether it still holds.
  The dead-exemption rule catches the case where the directory shrinks, and nothing catches the case where
  the *reason* stops being true.

## Alternatives considered

- **Count files recursively.** Rejected: it fails a parent whose children are already well decomposed,
  which is the opposite of the behaviour wanted. `tools/` would fail *because* its tests were split up.
- **Grandfather all four directories behind expiring waivers.** Rejected: it generates a re-review every
  30 days for two directories that can simply be fixed, and lets them keep growing behind the waiver in
  the meantime. A gate whose first act is to waive the only violations it found has not been enforced.
- **Split `docs/adr/` into numbered ranges.** Rejected for the cross-reference and immutability reasons
  above. The cost is real breakage; the benefit is cosmetic.
- **Move root manifests into a `config/` directory.** Rejected: `uv`, `pre-commit`, `ruff`, `git`, and the
  license scanners locate these files at the repository root and offer no way to relocate them.
- **Add the rule to an existing linter instead of writing a gate.** Rejected: nothing in the pinned
  toolchain counts directory entries, so this would mean a new dependency carrying one rule, audited and
  version-pinned like every other, to avoid roughly a hundred lines of Python.
- **Set the limit at 15.** Rejected: it fails exactly the same four directories today while leaving each
  decomposed subdirectory much closer to its ceiling, so the next split would come sooner without having
  caught anything extra.
- **Leave it to review.** Rejected for the reason [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md)
  gives: for a solo build, an unfailable gate is a self-assessment.
