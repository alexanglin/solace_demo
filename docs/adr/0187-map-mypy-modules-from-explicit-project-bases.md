# ADR-0187: Map mypy modules from explicit project bases

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Extends:** ADR-0029 and ADR-0056

## Context

The root workspace contains independently owned tests below several `packages/*/tests` and
`services/*/tests` namespace-package paths. Those tests correctly reuse domain names such as
`test_ingress.py`. Once the Solace application data plane activated more workspace members, the
authoritative whole-tree mypy invocation collapsed two such files to the same top-level module name and
stopped before type checking either one.

Adding `__init__.py` only to each `tests` directory would still name each module below the same `tests`
package. Adding package markers throughout the workspace would change Python import semantics merely to
serve a static analyzer. Excluding tests or accepting the first module would weaken ADR-0029's
whole-program boundary.

## Decision

Both Python domains set mypy's `explicit_package_bases = true`. Mypy therefore derives each
namespace-package module from the project root used by its existing invocation: root workspace members
remain distinct below `packages.*` or `services.*`, and Agent Mesh remains distinct below its own
project root.

The shared type-check contract treats this switch as a required strictness flag. The root and Agent Mesh
tables must continue to agree on it. The authoritative gate still checks every project-owned Python
path; no source or test path is excluded by this decision.

## Consequences

- Reused behavior-focused test basenames no longer make whole-tree type checking ambiguous.
- Module identity now agrees with the repository-relative ownership boundary used by the two existing
  mypy invocations.
- Moving a file between workspace members changes its static module name, as its ownership changes.
- A future move away from namespace packages must explicitly revisit this setting and the conformance
  assertion rather than silently changing module discovery.

## Alternatives considered

- **Exclude tests from mypy.** Rejected because tests exercise typed production ports and are part of the
  project's strict whole-tree claim.
- **Rename every colliding test.** Rejected because it treats each new collision manually and makes
  filenames describe repository layout instead of behavior.
- **Add package markers through every workspace directory.** Rejected because it changes runtime import
  semantics to solve a static module-discovery problem.
- **Use `--explicit-package-bases` only in the full hook.** Rejected because commit-stage and direct mypy
  invocations read the manifest; command-line-only policy would make those gates disagree.
