# Repository Tooling Instructions

## 1. Scope and authority

These instructions apply to every file under `tools/`. Read the repository-root
[`AGENTS.md`](../AGENTS.md) first; its TDD, security, documentation, and version-control rules still
apply. This directory is the root Python project's verification package, not part of the isolated
Agent Mesh project. Use the root `.venv` and root `uv.lock` for all work here.

Use the canonical source for each policy rather than restating its values in code comments or this
file:

| Concern | Canonical source |
| --- | --- |
| Hook stages and continuous integration authority | [ADR-0012](../docs/adr/0012-git-hooks-with-ci-as-authority.md) |
| Fail-closed activation and evidence | [ADR-0019](../docs/adr/0019-fail-closed-quality-gates.md) |
| Mandatory test structure and test taxonomy | [ADR-0018](../docs/adr/0018-enforced-arrange-act-assert.md), [`TESTING.md`](../docs/TESTING.md) |
| Complexity, duplication, and mutation execution | [ADR-0023](../docs/adr/0023-executable-deep-quality-gates.md) |
| Subprocess ownership and executable resolution | [ADR-0025](../docs/adr/0025-narrow-ruff-subprocess-waivers.md) |
| Directory decomposition | [ADR-0033](../docs/adr/0033-bound-directory-fan-out.md) |
| Scaffold reporting | [ADR-0053](../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Terminal-safe verdict reporting | [ADR-0059](../docs/adr/0059-keep-the-verification-authority-able-to-report.md) |
| Numeric limits and their instruments | [`operating-parameters.md`](../docs/operating-parameters.md) |

Also read the accepted ADR named by the gate's module docstring and tests. A change to a gate must
preserve both the cross-cutting rules above and the policy that gate makes executable.

## 2. Directory map

| Path | Responsibility |
| --- | --- |
| Top-level `*_gate.py` modules | Typed policy loading, adjudication, stable diagnostics, and command-line status |
| `image_inventory.py`, `member_scaffold.py` | Shared policy facts consumed by more than one real gate |
| `executable_resolution.py` | The reviewed absolute-executable boundary for permitted process owners |
| `aaa_checker/` | Cross-language AAA discovery, syntax analysis, self-tests, and the whole-tree entry point |
| `quality_gate_tests/analysis/` | Complexity, mutation, waiver, subprocess, and policy-regression tests |
| `quality_gate_tests/contracts/` | Contract artifacts, import boundaries, environment templates, and TypeScript policy tests |
| `quality_gate_tests/coverage/` | Per-member coverage and scaffold behavior |
| `quality_gate_tests/deploy/` | Image inventory, image pins, broker secrets, and deployment wiring |
| `quality_gate_tests/hooks/` | Hook activation, registration, range, terminal, and missing-tool behavior |
| `quality_gate_tests/support.py` | Hermetic repositories, minimal process environments, and shared fixture writers |

Put a new conformance test in the concern-named subdirectory. Do not flatten
`quality_gate_tests/` again or move a path named by an Accepted ADR without a superseding decision.

## 3. Gate design rules

- A green gate is evidence that its advertised check ran. Once a component is active, missing or
  unreadable input, a missing manifest, lock, report, executable, or test suite, an unknown field or
  status, and incomplete evidence must produce a blocking result.
- Preserve the distinction between `PASS`, `FAIL`, explicit informational output, and any
  ADR-defined non-pass state such as `SCAFFOLD`. Never turn unavailable evidence into a pass or an
  unreported skip.
- Separate exact-shape loading, typed policy evaluation, and command-line I/O. Prefer pure functions
  that receive listings, reports, registries, clocks, and paths explicitly; keep repository discovery
  and process execution at a reviewed boundary.
- Treat JSON, TOML, YAML, coverage data, scanner reports, mutation metadata, and command output as
  untrusted. Validate container shape, scalar types, required and unknown fields, duplicates, and
  domain membership before adjudication. Remember that `bool` is a subclass of `int` in Python.
- Registry-backed gates enforce both directions: an uncovered current finding fails, and a stale,
  expired, dead, duplicate, future-dated, or out-of-scope review record also fails.
- Keep diagnostics deterministic, deduplicated, value-redacted where inputs may be sensitive, and
  sorted independently of filesystem, mapping, or tool output order. Preserve documented prefixes,
  streams, and exit codes as public command behavior.
- Do not copy a gating number into another document or parallel implementation. A changed threshold,
  timeout, version pin, risk tier, or waiver rule must update its canonical source, instrument, tests,
  and governing ADR when the root policy requires one.
- Do not weaken a policy because a new component or input shape cannot yet be proved. Add the missing
  representation and tests, or keep the gate closed.

## 4. Process and environment boundaries

- The exact Python subprocess-owner allowlist is fixed by ADR-0025 and enforced by a policy test. Do
  not introduce process execution outside it, use a partial executable path, set `shell=True`, or add
  an inline Ruff waiver. A new owner requires an ADR, a narrow `pyproject.toml` entry, absolute
  executable resolution, and success and failure tests; a new call in an existing owner still requires
  review of its fixed command shape.
- Prefer the shell wrapper under [`scripts/hooks/`](../scripts/hooks/) for repository enumeration and
  external tool orchestration when Python can adjudicate explicit input instead. Do not duplicate the
  policy itself in shell.
- Tests that run Git or hook scripts must use `QualityGateTestCase` with a temporary repository,
  deterministic identity, cleared inherited Git context, a minimal `PATH`, and bounded cleanup. Never
  target the contributor's checkout, global Git configuration, credentials, network, or installed tool
  state as the test fixture.
- Use the pseudo-terminal harness only for behavior that changes on a terminal. A process timeout must
  terminate the child session so a pager or descendant cannot outlive the test.
- Keep tests deterministic by injecting dates, reports, path listings, and executable outcomes. Do not
  make a policy verdict depend on the wall clock, directory iteration order, ambient environment, or a
  warm cache below the command-line boundary.

## 5. Tests for a gate

Follow TDD and the repository's mandatory AAA structure. For each new or changed rule, cover the
applicable cases below:

- the smallest valid input and the exact success result;
- every boundary value and every rejected scalar or container type;
- absent, malformed, unknown, duplicate, incomplete, and contradictory inputs;
- stable ordering, diagnostic prefix, output stream, redaction, and exit status;
- missing executables, manifests, locks, reports, tests, and generated evidence after activation;
- both sides of any waiver, exemption, registry, inventory, or scaffold relationship;
- the shell wrapper's inactive and active states, argument forwarding, working directory, and cleanup;
- hook registration at every required stage and parity with continuous integration;
- the specific false-green regression that motivated the rule, including a terminal-backed case when
  pipe and terminal behavior can differ.

Test helpers arrange controlled inputs; they do not hide the behavior or outcome assertion from the
executable test. Extend `quality_gate_tests/support.py` only when at least two real test modules need the
fixture.

## 6. Coordinate cross-tree changes

A gate rarely changes in isolation. Inspect and update every applicable owner in the same focused
change:

- the Python implementation and its conformance tests under `tools/`;
- the canonical wrapper under [`scripts/hooks/`](../scripts/hooks/);
- [`.pre-commit-config.yaml`](../.pre-commit-config.yaml), using the same entry point at local and CI
  stages;
- [`.github/workflows/`](../.github/workflows/) only when workflow wiring, prerequisites, or schedules
  change;
- [`justfile`](../justfile) when contributors need a human-facing entry point;
- [`pyproject.toml`](../pyproject.toml) and `uv.lock` for dependency, package, lint, type, or risk-tier
  changes;
- the registry, manifest, schema, or configuration the gate adjudicates;
- the canonical ADR, [`TESTING.md`](../docs/TESTING.md),
  [`operating-parameters.md`](../docs/operating-parameters.md), and
  [`CONTRIBUTING.md`](../CONTRIBUTING.md) when their owned facts change.

Do not create a second command that approximates an existing gate. Hooks and continuous integration
must invoke the same project-owned entry point with the same fail-closed semantics.

## 7. Required verification

From the repository root, run the focused tooling checks:

```sh
uv run --frozen pytest tools -q
uv run --frozen mypy --strict tools
uv run --frozen ruff format --check tools
uv run --frozen ruff check tools
pre-commit run test-aaa --all-files --hook-stage pre-commit
```

When a gate or wrapper changes, run that entry point directly against success and failure fixtures, then
run the affected canonical full wrappers under `scripts/hooks/`. Finish with the repository-wide commit
and push stages required by the root `AGENTS.md`. Report every environment-dependent check that was not
run; a skip is not a pass.
