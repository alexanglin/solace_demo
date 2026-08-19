# ADR-0004: Split Python runtimes for application services and Agent Mesh

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

Application services target the newest stable Python. Agent Mesh 1.28.7 declares `>=3.10.16,<3.14`, so it cannot run on 3.14. Forcing a single interpreter would mean either holding all project code back to 3.13 or waiting for an upstream release that supports 3.14.

## Decision

Run two isolated environments, both managed by `uv` with separate `pyproject.toml` files and separate committed lockfiles:

- Application services on **Python 3.14.7** in the root `.venv`.
- Agent Mesh 1.28.7, its two pinned Event Mesh plugins, and any owned Agent Mesh extension on **Python 3.13.15** in `agent-mesh/.venv`.

Any shared Python package consumed by both must declare the intersecting compatibility range and be tested on both interpreters. The two environments' site-packages and lockfiles are never combined, and neither is installed globally.

## Consequences

- Each side runs its newest supported interpreter without blocking the other.
- Shared packages carry a real cost: a doubled test matrix and a compatibility range narrower than either side alone.
- Tooling configuration doubles — two lockfiles, two virtual environments, and two sets of type-checking and lint invocations that CI and the git hooks must both cover.
- The boundary is enforced by process separation, which suits the architecture: Agent Mesh components already run as separate native processes.
- If a shared package proves painful, the fallback is to duplicate a small amount of code rather than widen the compatibility range, consistent with the rule against premature abstraction.

## Alternatives considered

- **Single interpreter at 3.13.15.** Rejected: holds application code back for the lifetime of the upstream constraint, with no benefit to the application side.
- **Waiting for upstream 3.14 support.** Rejected: makes the project's start date depend on an external release schedule.
- **Running Agent Mesh in a container to hide the version difference.** Rejected: the supported Apple Silicon path is native, and an emulated `linux/amd64` image is explicitly not a dependency.
