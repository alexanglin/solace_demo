# ADR-0010: uv workspace with per-member packages

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

The planned repository shape shows a single root `pyproject.toml` and one `uv.lock`, while the quality gates require per-package coverage thresholds across `packages/`, `services/`, and `apps/`. A single root project cannot express per-package gates, and `--cov-fail-under` applies to one global total, which would let a well-tested domain package mask an untested adapter — the exact outcome the gates exist to prevent.

Container images are built for linux/aarch64 while development happens on macOS arm64, so a lockfile resolved only for the host platform fails at image build time.

## Decision

Structure the application side as a **uv workspace**: a root `pyproject.toml` declaring members, one shared lockfile, and a per-member `pyproject.toml` for each package and service. Resolve the lockfile for both `macosx_11_0_universal2` and `manylinux_2_17_aarch64`.

The Agent Mesh subproject under `agent-mesh/` remains a **separate, isolated project** with its own `pyproject.toml`, lockfile, and `.python-version`, not a workspace member, because it runs on a different interpreter (see [ADR-0004](0004-split-python-runtimes.md)).

Expose one canonical command entrypoint for every check so that contributors, git hooks, and CI invoke identical commands.

## Consequences

- Per-package coverage, lint, and type gates become expressible and enforceable.
- Container builds resolve dependencies that actually exist for the target platform.
- Two dependency-resolution domains must be kept healthy, and CI must exercise both.
- The repository tree gains per-member configuration files, which is more boilerplate than a single project but is the price of per-package gates.

## Alternatives considered

- **Single root project with path-based coverage reporting.** Rejected: `--cov-fail-under` is global, so a per-path gate would require bespoke scripting over coverage output with no packaging benefit.
- **Making `agent-mesh/` a workspace member.** Rejected: a uv workspace shares one resolution and one interpreter constraint, which the 3.13/3.14 split forbids.
