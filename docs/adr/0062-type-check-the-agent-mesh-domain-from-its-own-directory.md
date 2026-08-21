# ADR-0062: Type-check the Agent Mesh domain from its own directory, over its whole tree

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) verifies the Agent Mesh
domain with its own toolchain at its own stage, and the comment above the commit-stage type-check
hook stated the intent plainly: a setting that made the fast stage weaker than the authoritative one
would be "the disagreement ADR-0029 exists to prevent". The hook nonetheless disagreed with the
authoritative run in a way the comment did not anticipate.

`scripts/hooks/python/mypy-full.sh` changes directory into `agent-mesh/` and checks the whole tree.
The commit-stage hook ran from the repository root and passed the staged files. mypy derives a
module name by walking up from a file while `__init__.py` exists, relative to its working directory,
so from the root `agent-mesh/tools/agent_mesh_config_validator.py` is not `tools.…` — the root
tooling package owns that name. `agent-mesh/tests/test_config_validator.py` imports
`from tools import agent_mesh_config_validator`.

The failure is conditional, which is why it survived. `pre-commit run --all-files` passes every
matching file at once, so the validator module is present in the same invocation and the import
resolves; `git commit` passes only the staged files, so a commit touching the test without touching
the module fails with `import-not-found`. Two commits have touched that test, and both touched the
module in the same change. The first commit to touch it alone found the defect.

## Decision

The commit-stage hook runs `scripts/hooks/agent-mesh/mypy-agent-mesh.sh`, which changes directory
into `agent-mesh/` and runs `uv run --frozen mypy --strict .` over the whole tree, with
`pass_filenames: false`. It is inert when `agent-mesh/pyproject.toml` is absent and fails closed on a
missing lockfile or a missing `uv`, matching the other fail-closed entry points
([ADR-0019](0019-fail-closed-quality-gates.md)).

The `files: ^agent-mesh/` filter stays, so the hook still runs only when a file in that domain
changes. `pass_filenames: false` changes what is checked, not when.

Measured cost: 0.29 s warm, against the twenty-minute per-job ceiling in
[operating-parameters.md](../operating-parameters.md).

## Consequences

- The commit stage and the pre-push stage now issue the same command against the same tree, so their
  verdicts cannot differ for this domain. That is what ADR-0029 asked for and what the previous
  hook's own comment claimed.
- A commit touching only a test in this domain type-checks, which it could not before.
- The hook checks files the commit did not change. For six source files and 0.29 s that is a better
  trade than a verdict that depends on which files happen to be staged.
- Negative: the cost grows with the tree rather than with the change, so an Agent Mesh domain that
  grows substantially will make the commit stage slower and may need revisiting.
- Negative: this makes the commit stage's cost for one domain independent of the diff, which is a
  different shape from every other language hook in the file, and the inconsistency is now something
  a reader has to notice and understand rather than assume.

## Alternatives considered

- **Set `MYPYPATH=agent-mesh` and keep passing staged files.** Rejected: both `tools` packages would
  then be resolvable and which one wins depends on search order, which trades a loud failure for a
  silent one.
- **Rename `agent-mesh/tools/` so it cannot collide.** Rejected: it is the package the validator is
  imported as throughout its own suite, and renaming it to accommodate a hook's working directory is
  the wrong thing moving.
- **Add an `agent-mesh/__init__.py` so the module becomes `agent_mesh.tools.…`.** Rejected: it would
  make the non-member project importable as a package from the root, which is exactly the coupling
  [ADR-0004](0004-split-python-runtimes.md) and [ADR-0010](0010-uv-workspace-and-toolchain.md) keep
  apart.
- **Drop the commit-stage hook and rely on pre-push.** Rejected: it removes the fast feedback
  [ADR-0012](0012-git-hooks-with-ci-as-authority.md) wants, to fix a defect that costs 0.29 s to fix
  properly.
- **Leave it and stage the validator alongside its tests when needed.** Rejected: a gate whose
  verdict depends on what else is in the commit is not a gate.
