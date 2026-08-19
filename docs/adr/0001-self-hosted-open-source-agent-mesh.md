# ADR-0001: Self-hosted open-source Agent Mesh over managed Agent Mesh

- **Status:** Accepted
- **Date:** 2026-08-18
- **Supersedes:** the earlier managed-Agent-Mesh direction recorded in the initial implementation plan

## Context

The project's first plan specified managed Solace Agent Mesh through Agent Mesh Manager, described as "2.x". Verification found that no 2.x release exists — managed releases are displayed as a combined `<Agent Mesh Enterprise>-<Agent Mesh Manager Cloud>` string, and the open-source line is versioned 1.x. The managed offering is additionally entitlement-gated: it requires a Dedicated Cluster in an Agent-Mesh-enabled private region with an enterprise service class, none of which this project holds. It is also upgraded on Solace's schedule, which would move the reference implementation's substrate without warning.

The open-source distribution is Apache-2.0 and installable as a pinned package.

## Decision

Use self-hosted open-source Solace Agent Mesh, pinned to release `1.28.7` (tag has no `v` prefix) from `SolaceLabs/solace-agent-mesh`, upstream commit `6344d2b8899a6c326e8b52fce9947c4bf4b56ae2`. Install the released package into an isolated, locked subproject; do not vendor upstream source or add it as a submodule. Pin the official `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1 plugins alongside it.

Managed Agent Mesh and Agent Mesh Manager are out of scope for the initial release. A future managed deployment requires its own ADR and must not silently replace the reproducible self-hosted path.

## Consequences

- The repository becomes reproducible by any reader with no Solace entitlement beyond a broker service.
- The runtime version is frozen and upgrades become deliberate, gated events rather than external surprises.
- The project inherits upstream's dependency posture, including the Starlette 0.49.1 pin and its noted CVE exceptions, which must be assessed for reachability and either fixed or waived under a time-bounded approval before release.
- Compatibility between Agent Mesh 1.28.7 and the two independently released plugins is not attested by any single upstream artifact and must be proved in the Phase 0 gate.
- The project must run and supervise Agent Mesh processes itself, which the managed option would have handled.

## Alternatives considered

- **Managed Agent Mesh via Agent Mesh Manager.** Rejected: entitlement-gated behind a Dedicated Cluster and enterprise service class the project does not have, Early Access, and upgraded on a schedule outside the project's control.
- **Vendoring upstream source.** Rejected: creates a permanent merge burden and obscures which upstream revision is actually running.
- **Tracking an upstream branch.** Rejected: incompatible with reproducible acceptance evidence.
