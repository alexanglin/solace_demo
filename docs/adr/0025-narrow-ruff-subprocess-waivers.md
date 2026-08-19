# ADR-0025: Narrow Ruff subprocess waivers and record incompatible rule choices

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0019](0019-fail-closed-quality-gates.md) makes Ruff and review responsible for low-severity
subprocess findings that Bandit does not block. The root Ruff configuration instead ignored `S603` and
`S607` globally and repeated both suppressions across broad test globs. That made Ruff unable to enforce
the boundary ADR-0019 assigned to it. The repository does have four reviewed subprocess-owning files,
but each uses a fixed executable boundary rather than an arbitrary command or shell string.

The configuration also selects rule families containing mutually exclusive docstring rules and a pytest
rule that conflicts with the repository's deliberate `unittest.TestCase` assertion style. The required
lint-waiver record must cover those choices explicitly rather than leaving unexplained ignores in TOML.

## Decision

Enable `S603` and `S607` globally and remove them from every broad test-glob waiver. Do not waive `S607`
anywhere. Resolve Git through `tools.executable_resolution.required_executable`, return its absolute
resolved path, and fail closed when it is unavailable before either repository discovery or a test Git
operation can launch a child process.

Permit `S603` through exact per-file entries for only these reviewed subprocess owners:

- `tools/aaa_checker/checker.py`, which invokes the resolved Git executable with a fixed `ls-files`
  command shape;
- `tools/coverage_gate.py`, which invokes the absolute current `sys.executable` with the fixed
  `-m coverage json` command shape;
- `tools/quality_gate_tests/support.py`, which invokes resolved Git and absolute `/bin/sh` for controlled
  hook-behavior fixtures; and
- `tools/quality_gate_tests/test_diagram_integrity.py`, which invokes absolute `/bin/sh` for the two
  diagram-integrity scripts under test.

A policy test parses the Ruff configuration, requires exactly that four-file `S603` set, rejects any
`S607` or inline subprocess waiver, and exercises successful and missing-executable resolution. Expanding
the allowlist or weakening executable resolution requires a new ADR.

Retain `D203` and `D213` as the two documented docstring convention exclusions: `D203` conflicts with
the selected `D211` class-spacing rule, and `D213` conflicts with the selected `D212` first-line summary
rule. Retain `PT009` because replacing `unittest.TestCase` assertion methods with bare assertions would
conflict with the repository's established unittest-based, syntax-enforced AAA harnesses. These are
rule-family choices, not permission for inline or file-wide ad hoc suppressions.

## Consequences

- A PATH-relative or missing Git executable now fails before subprocess launch.
- Ruff reports every partial executable path because no `S607` waiver remains.
- Only four named files may own Ruff-reviewed `S603` calls, and an executable policy test prevents broad
  suppressions from returning unnoticed.
- Per-file `S603` entries still require review of any new subprocess call added inside an allowed owner;
  the file allowlist is narrow but does not distinguish individual call sites.
- The docstring and unittest conventions remain stable without pretending their mutually incompatible
  Ruff rules can all be enabled simultaneously.

## Alternatives considered

- **Keep global `S603` and `S607` ignores.** Rejected: this contradicts ADR-0019 and hides new partial-path
  subprocess calls anywhere in owned code.
- **Keep subprocess rules on broad test globs.** Rejected: being test code does not make command
  construction safe, and the reviewed owners are already enumerable.
- **Use inline `noqa` comments.** Rejected: exact configuration entries are centrally auditable and the
  policy test can compare their complete scope without scanning syntax variants.
- **Replace Git subprocesses with a Git library.** Rejected: the checker needs Git's authoritative view of
  tracked and unignored files, while the hook fixtures intentionally exercise real repositories.
- **Enable `D203`, `D213`, and `PT009` anyway.** Rejected: each conflicts with a selected repository
  convention and would create churn without improving correctness.
