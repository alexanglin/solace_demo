# ADR-0188: Route root mypy through discovered source bases

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Extends:** ADR-0187

## Context

ADR-0187 makes project-relative namespace identity explicit so independently owned tests with the same
basename remain distinct. A src-layout workspace adds a second requirement: each member's `src`
directory is an import base. Without those bases, mypy sees an owned file both under its physical
`packages.member.src.package` path and under the installed `package` path, and refuses the duplicate.

A source-root list written into `pyproject.toml` would duplicate the workspace inventory and become stale
when a member is activated. Supplying the bases only to the pre-push command would make staged-file and
whole-tree checks disagree.

## Decision

One root mypy wrapper owns both the commit-stage and whole-tree invocations. Before calling the frozen,
strict mypy toolchain, it discovers every tracked or unignored non-Agent-Mesh Python file below a `src`
directory, derives the sorted unique source roots, and replaces inherited `MYPYPATH` with exactly that
colon-separated set.

The wrapper retains the existing project, lock, tool-presence, interpreter, and active-component
fail-closed checks. The Agent Mesh domain remains routed through its own project-root wrapper and
interpreter. Hermetic hook tests prove source-root discovery, strict invocation, and commit-stage wiring.

## Consequences

- Src-layout production modules keep their installed import identity while tests retain unique
  repository-relative namespace identities.
- A newly activated workspace source root enters both type-check stages automatically.
- Caller-controlled `MYPYPATH` cannot alter verification module resolution.
- The wrapper is now part of the verification authority and must remain covered by hook conformance,
  ShellCheck, and the full strict mypy gate.

## Alternatives considered

- **List every member source root in `pyproject.toml`.** Rejected because workspace membership already has
  an executable repository inventory and a second literal list would drift.
- **Pass source bases only from `mypy-full.sh`.** Rejected because the staged-file hook would resolve the
  same file differently.
- **Add `__init__.py` markers around every member test tree.** Rejected because it changes runtime import
  semantics and still does not express src-layout import bases.
- **Inherit the contributor's `MYPYPATH`.** Rejected because external environment state cannot define a
  blocking verification result.
