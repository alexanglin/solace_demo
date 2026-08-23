# Quality-Gate Conformance Test Instructions

## 1. Scope and authority

These instructions apply to every file under `tools/quality_gate_tests/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`tools/AGENTS.md`](../AGENTS.md) first. Their TDD,
security, tooling, and version-control rules still apply.

This tree tests the repository's verification authority. Its assertions are executable evidence for a
bounded policy; they are not a second home for the policy itself. The parent guide routes the general
gate policies. These records govern boundaries specific to this test harness:

| Concern | Canonical source |
| --- | --- |
| Test structure, stage semantics, and CI authority | [`TESTING.md`](../../docs/TESTING.md), [ADR-0012](../../docs/adr/0012-git-hooks-with-ci-as-authority.md), and [ADR-0018](../../docs/adr/0018-enforced-arrange-act-assert.md) |
| Reviewed subprocess and terminal boundaries | [ADR-0025](../../docs/adr/0025-narrow-ruff-subprocess-waivers.md) and [ADR-0059](../../docs/adr/0059-keep-the-verification-authority-able-to-report.md) |
| Concern decomposition and diagram integrity | [ADR-0033](../../docs/adr/0033-bound-directory-fan-out.md) and [ADR-0022](../../docs/adr/0022-recursive-diagram-integrity.md) |
| Import-graph selection and split Python runtimes | [ADR-0066](../../docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md) and [ADR-0029](../../docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) |

Also read the Accepted ADR named by the subject gate or test module. Never edit a test merely to make a
changed implementation pass. If established evidence and implementation disagree, report the defect;
an approved policy change must update its canonical owner and every affected consumer together.

## 2. Evidence placement

Keep each test with the concern whose claim it proves:

| Path | Responsibility |
| --- | --- |
| `analysis/` | AAA false-green regressions, complexity, duplication, mutation, dependency and structural registries, subprocess policy, and static-analysis adjudication |
| `contracts/` | Contract artifacts, import boundaries, environment templates, dashboard package and TypeScript policy |
| `coverage/` | Workspace discovery, per-member coverage verdicts, inventory, empty evidence, and scaffold classification |
| `deploy/` | Image inventory and pins, scan orchestration, broker secret generation, and static deployment/configuration agreement |
| `hooks/` | Component activation, fail-closed prerequisites, wrapper behavior, hook registration, CI parity, range handling, and terminal safety |
| `selection/` | Pure import-graph selection plus commit-stage narrowing and unconditional push-stage execution |
| `support.py` | Shared hermetic repositories, process environments, executable recorders, terminal runner, and mutation fixture writers |
| `test_diagram_integrity.py` | ADR-0022 diagram-wrapper behavior at the exact path retained by ADR-0033 and allowed to own subprocesses by ADR-0025 |

Place pure loading and adjudication tests in the corresponding concern subdirectory. Test shell
activation, argument routing, and stage wiring under `hooks/`. When a behavior crosses both boundaries,
prove both; a passing evaluator test does not prove that the hook invokes it, and a recorded invocation
does not prove its verdict.

Tests that inspect the real checkout establish repository conformance only. Keep general policy behavior
in controlled fixtures so an unrelated tree change cannot silently replace a boundary case. Do not move
an ADR-fixed file, flatten the concern directories, or add a root-level test merely to avoid choosing its
owner.

## 3. Keep each proof boundary visible

- Follow the parent guide's case matrix and the exact Arrange-Act-Assert grammar in `docs/TESTING.md`.
  Helpers may arrange inputs, but they must not hide the observable Act or its outcome assertion.
- A Python-evaluator test controls the untrusted input and asserts the returned verdict. A wrapper test
  separately proves activation, prerequisite refusal, command routing, failure propagation, registration,
  and CI parity where applicable.
- Assert public behavior: deterministic diagnostics, output stream, ordering, exit status, and side
  effects. Do not pin incidental private call structure when a stable result can prove the claim.
- Capture expected exceptions and warnings during Act and inspect them during Assert. Let unexpected
  failures retain their traceback; do not catch broadly or weaken an oracle to obtain green output.
- Keep each case deterministic and offline. Do not depend on the contributor's checkout state, global Git
  configuration, credentials, network, containers, daemons, wall clock, warm caches, or arbitrary PATH.

## 4. Use the shared harness deliberately

- Plain `unittest.TestCase` is sufficient for pure gate functions and isolated temporary-file inputs.
  Use `QualityGateTestCase` for repository or Git fixtures, project-owned scripts, process environments,
  executable recorders, or the terminal harness.
- Create Git fixtures with `temporary_repository()` and operate on them through the harness. Its
  deterministic identity and removal of inherited `GIT_*` context prevent a hook-launched test from
  writing to the contributor's repository. Never point a mutating test at `REPOSITORY_ROOT`.
- Normally run scripts through `run_script()` or `run_hook()` with the minimal deterministic
  environment. Add test-owned executable recorders when command shape is the claim; do not inherit the
  host PATH or let an installed tool accidentally satisfy a missing-tool case. The ADR-fixed
  `test_diagram_integrity.py` intentionally invokes absolute `/bin/sh` directly; preserve its equivalent
  controlled environment instead of routing that fixed owner through a second abstraction.
- Treat each hook basename as a stable test identity and keep it unique under `scripts/hooks/`.
  `hook_script()` prefers an ADR-fixed direct path and otherwise requires exactly one nested match, so
  do not rely on it to diagnose a nested duplicate of a fixed direct basename.
- Use `run_script_on_terminal()` only when behavior differs between a pipe and a terminal. Preserve its
  fixed degraded terminal, bounded deadline, replacement decoding, and whole-session kill so a pager or
  descendant cannot outlive the test.
- ADR-0025 permits subprocess ownership in this tree only in `support.py` and
  `test_diagram_integrity.py`, with absolute executable boundaries. A new owner requires a governing
  decision; a new call in either existing owner still requires review and focused success and failure
  tests. Never introduce `shell=True`, a partial executable, or an inline waiver.
- Extend `support.py` only for behavior shared by at least two real consumers. A helper must arrange a
  boundary, not hide the observable Act or the outcome assertion.

## 5. Preserve stage and selection claims

- Commit-stage selection is fast feedback. Pre-push is local whole-suite feedback, and CI is the
  authority. Never make a selected run the only evidence for a gate or narrow any push-stage suite.
- Invoke the shared selector on one isolated graph at a time, never a combined root and Agent Mesh graph.
  The root listing excludes `agent-mesh/`; the Agent Mesh invocation uses an Agent-Mesh-relative listing
  and root with the root selector, then runs the selected tests from `agent-mesh/` under that project's
  interpreter.
- Preserve fail-safe widening for inputs the graph cannot prove, including non-Python, missing,
  ambiguous, or unparsable paths and `conftest.py`. Do not replace dependency proof with filename or
  directory guesses.
- A wrapper change needs cases for inactive state, first-file activation, every required prerequisite,
  exact argument and working-directory routing, failure propagation, hook registration at each required
  stage, and CI installation or invocation parity.
- Keep terminal behavior, pushed-range behavior, and direct module execution explicit when they are part
  of the public boundary. A pipe-only or imported-only test does not prove those paths.

## 6. Coordinate changes across the tree

A conformance-test change often reveals work outside this directory. Inspect the applicable Python gate,
shell wrapper under [`scripts/hooks/`](../../scripts/hooks/), `.pre-commit-config.yaml`, workflow,
manifest or registry, canonical documentation, and governing ADR. Update only the owners whose fact or
invocation actually changed.

Do not duplicate a numeric limit, pin, allowlist, policy table, or expected inventory here as a new
authority. A deliberate equality assertion may hold two independent representations together, but its
source and rationale must remain clear.

## 7. Required verification

Use the root Python environment. In a fresh worktree, synchronize all workspace members before collecting
tests that import them. Start with the concern-owning path; `selection/` is the concrete example below:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q tools/quality_gate_tests/selection
uv run --frozen pytest -q tools/quality_gate_tests
uv run --frozen mypy --strict tools/quality_gate_tests
uv run --frozen ruff format --check tools/quality_gate_tests
uv run --frozen ruff check tools/quality_gate_tests
pre-commit run test-aaa --all-files --hook-stage pre-commit
```

Run the changed gate and wrapper directly against controlled success and failure fixtures, then run the
canonical affected full wrapper named by the parent guide. When this guide or its alias changes, also run:

```sh
pre-commit run --files tools/quality_gate_tests/AGENTS.md \
  tools/quality_gate_tests/CLAUDE.md --hook-stage pre-commit
readlink tools/quality_gate_tests/CLAUDE.md
git diff --check
```

`readlink` must print `AGENTS.md`. Finish with the repository-wide commit and push stages required by
the root instructions, inspect the complete diff, and report every environment-dependent check that was
not run; a skip is not a pass.
