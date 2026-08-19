# ADR-0020: Pin uv 0.12.5 across local development and CI

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The root workspace, lock validation, dependency export, test execution, and CI bootstrap all depend on
`uv`. The repository documentation named 0.11.6 while CI and the workstation used 0.12.5, so two
contributors could validate the same manifest with different resolver behavior and both report success.
The quality policy requires the local command and CI command to be identical.

Astral released [uv 0.12.5](https://github.com/astral-sh/uv/releases/tag/0.12.5) on 2026-08-14 and
publishes an Apple Silicon artifact. uv's official settings reference documents
[`required-version`](https://docs.astral.sh/uv/reference/settings/#required-version) as the project-level
mechanism that rejects a mismatched executable.

## Decision

Pin uv exactly to 0.12.5 in the root `[tool.uv]` table with `required-version = "==0.12.5"`. Use the same
version in CI and contributor prerequisites. The lock gate relies on uv's own version check rather than
maintaining a second version parser.

## Consequences

- Local and CI resolution, locking, exporting, and execution use one uv implementation.
- A contributor with any other uv version receives a deliberate failure before a misleading lock result.
- uv upgrades require a reviewed ADR, lock verification in both Python environments, and a coordinated CI
  and documentation change.
- Exact pinning increases maintenance compared with a compatible range.

## Alternatives considered

- **Keep 0.11.6.** Rejected: it was already inconsistent with CI and the current workstation and did not
  resolve the selected newest Python patch releases in local validation.
- **Require `uv>=0.12.5`.** Rejected: future resolver changes could make local and CI results diverge.
- **Document a version without mechanical enforcement.** Rejected: that was the false-green state this
  decision repairs.
