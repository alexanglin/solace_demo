# Arrange-Act-Assert Checker Instructions

## 1. Scope and authority

These instructions apply to every file under `tools/aaa_checker/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`tools/AGENTS.md`](../AGENTS.md) first. Their TDD,
security, gate-design, documentation, and verification rules still apply.

Do not copy the executable-test grammar into this file or infer it from the implementation. Use the
canonical source for each policy:

| Concern | Canonical source |
| --- | --- |
| Exact AAA contract and supported test forms | [`docs/TESTING.md`](../../docs/TESTING.md#mandatory-arrange-act-assert-structure) |
| Why the whole-tree syntax gate and self-tests exist | [ADR-0018](../../docs/adr/0018-enforced-arrange-act-assert.md) |
| Hook stages and continuous-integration authority | [ADR-0012](../../docs/adr/0012-git-hooks-with-ci-as-authority.md) |
| Fail-closed gate activation | [ADR-0019](../../docs/adr/0019-fail-closed-quality-gates.md) |
| Git subprocess ownership and executable resolution | [ADR-0025](../../docs/adr/0025-narrow-ruff-subprocess-waivers.md) |
| Parser and test dependencies | [`pyproject.toml`](../../pyproject.toml), [`uv.lock`](../../uv.lock), and [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) |

An Accepted ADR governs if code, a test fixture, or this guide disagrees with it. A change to the
test grammar, supported language or registration dialect, dependency pin, hook stage, subprocess
boundary, or fail-closed behavior requires the decision and documentation work specified by the root
instructions.

## 2. What this directory owns

| Path | Responsibility |
| --- | --- |
| `checker.py` | Source discovery, Python and JavaScript-family syntax analysis, deterministic diagnostics, and the raw scanner command |
| `gate.py` | The authoritative self-test-first gate used by hooks and continuous integration |
| `__main__.py` | A raw scanner entry point for focused development and diagnosis |
| `tests/test_checker.py` | Positive and negative conformance cases, callable scanner behavior, and mocked gate behavior |
| `__init__.py`, `tests/__init__.py` | Stable package and test-discovery boundaries |

Keep this as a flat subpackage of the root `tools` package. The hook imports it from the repository
root in an isolated environment; moving it, adding another command that approximates it, or changing
package discovery can silently make local and hook execution exercise different code.

## 3. Keep the gate and the raw scanner distinct

- `python -m tools.aaa_checker.gate` is the authoritative gate. It runs the project-owned conformance
  suite first and must stop with a blocking result before scanning if that suite fails.
- `python -m tools.aaa_checker` invokes the raw scanner without the self-test preflight. Use it only
  for focused development or diagnosis; never wire it into a hook, continuous integration, or a
  release claim as a substitute for the gate.
- Keep the hook entry point, its isolated dependencies, and its invocation from the repository root
  synchronized with `.pre-commit-config.yaml`. The `justfile` must continue to delegate to that hook
  rather than recreating its behavior.
- Preserve the command-line contract: findings and infrastructure failures go to standard error,
  clean, finding, and gate-failure outcomes remain distinct, and diagnostics remain stable enough for
  tests and editor tooling to consume.

Use the root `.venv` and run commands from the repository root. Do not use `agent-mesh/.venv`; that
project deliberately owns a different `tools` package and Python environment.

## 4. Checker design and safety boundaries

- Keep text checking, path reading, repository discovery, and command-line I/O separate. Syntax
  rules belong in the language-specific analysis functions; filesystem and process effects stay at
  the outer boundary.
- Treat every scanned file and parser result as untrusted. Unreadable or undecodable source, parse
  errors, unsupported test registrations, missing parser capabilities, and repository-discovery
  failures must block rather than skip or pass.
- Do not add a path exclusion, inline suppression, marker alias, or success-on-unknown behavior. A
  legitimate new language, framework, assertion form, or registration dialect needs checker support
  and conformance cases before the first relying test lands.
- Whole-tree discovery must continue to use Git's view of tracked and unignored supported source.
  `checker.py` is a specifically reviewed subprocess owner under ADR-0025: resolve Git through
  `tools.executable_resolution`, keep the command shape fixed, never use a shell, and fail before
  launch when Git cannot be resolved. A new call in this reviewed owner needs fixed-command-shape
  review plus success and failure tests; expanding the owner allowlist or weakening executable
  resolution requires a new ADR.
- Keep diagnostics deterministic across filesystem and parser iteration order. Preserve precise
  paths, positions, stable codes, compiler-style rendering, and sorted output; do not echo source
  contents merely to explain a failure.
- Keep Python analysis on the standard-library syntax/token boundaries and JavaScript-family analysis
  on the pinned Tree-sitter boundary. Do not replace syntax-aware discovery with marker-only regex
  scanning or try to infer phase intent from arbitrary calls.
- The checker proves structural conformance only. Do not describe a green result as proof that a test
  is behaviorally meaningful, was observed red, covers its risk, or satisfies coverage and mutation
  requirements.

## 5. Tests and cross-tree coordination

Follow TDD for every checker behavior or defect. Add the smallest failing conformance case first, use
the raw scanner to confirm that its own test body is structurally valid, then run the focused pytest
and observe the intended red result. The authoritative gate will fail its self-test preflight during
that red step; run it after the implementation is green. The conformance tests are themselves
project-owned executable tests and must satisfy the canonical AAA contract.

Choose the evidence location deliberately:

- `tests/test_checker.py` owns the normal accepted and rejected forms, parser behavior, diagnostics,
  raw command behavior, and self-test-first gate behavior.
- [`../quality_gate_tests/analysis/test_aaa_false_green_regressions.py`](../quality_gate_tests/analysis/test_aaa_false_green_regressions.py)
  owns regressions where malformed or unsupported syntax previously risked a false green and executes
  the real raw-scanner and gate module entry points.
- [`../quality_gate_tests/analysis/test_subprocess_policy.py`](../quality_gate_tests/analysis/test_subprocess_policy.py)
  owns absolute Git resolution, discovery failure, and the exact subprocess-waiver boundary.
- [`../quality_gate_tests/hooks/test_hook_semantics.py`](../quality_gate_tests/hooks/test_hook_semantics.py)
  owns the isolated hook dependency and registration contract.
- [`../quality_gate_tests/selection/test_affected_tests.py`](../quality_gate_tests/selection/test_affected_tests.py)
  owns the root-package identity used by affected-test selection.

When behavior changes, inspect all of those consumers plus `.pre-commit-config.yaml`, `pyproject.toml`,
`uv.lock`, `justfile`, `docs/TESTING.md`, and the governing ADRs. Do not weaken a negative fixture or
reclassify unsupported syntax merely to make the checker green.

## 6. Required verification

From the repository root, run the focused suite first:

```sh
uv run --frozen pytest -q \
  tools/aaa_checker/tests/test_checker.py \
  tools/quality_gate_tests/analysis/test_aaa_false_green_regressions.py \
  tools/quality_gate_tests/analysis/test_subprocess_policy.py \
  tools/quality_gate_tests/hooks/test_hook_semantics.py \
  tools/quality_gate_tests/selection/test_affected_tests.py
uv run --frozen mypy --strict tools/aaa_checker
uv run --frozen ruff format --check tools/aaa_checker
uv run --frozen ruff check tools/aaa_checker
uv run --frozen python -m tools.aaa_checker.gate
pre-commit run test-aaa --all-files --hook-stage pre-commit
```

When this guide or its alias changes, also run:

```sh
pre-commit run --files tools/aaa_checker/AGENTS.md tools/aaa_checker/CLAUDE.md \
  --hook-stage pre-commit
readlink tools/aaa_checker/CLAUDE.md
git diff --check
```

`readlink` must print `AGENTS.md`.

Then run the parent guide's complete `tools` checks and every repository-wide commit- and push-stage
gate required by the root instructions. A raw-scanner success, an excluded file class, or an
environment-dependent check that was not run is not a passing gate.
