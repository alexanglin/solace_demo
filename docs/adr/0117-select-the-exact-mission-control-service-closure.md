# ADR-0117: Select the exact mission-control service closure

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0096

## Context

ADR-0096 named a `mission-control` profile, but ADR-0102 later made Agent Mesh default-on. Enabling a
profile alone therefore cannot prove an isolated dashboard stack: Compose would also start every
profile-free default service. Production E2E needs one supported command that starts only the intended
closure while preserving ordinary `just up` behavior.

## Decision

Keep one `deploy/compose.yaml`. Add mission-control wiring but make the supported entry point an explicit
service selection. `mission-control-up` enables the profile and names exactly:

1. broker;
2. postgres;
3. migration;
4. fleet-simulator;
5. scenario-service;
6. recorder;
7. replay-validator;
8. dashboard-api; and
9. caddy.

Matching stop, status, and log recipes reuse that literal closed list. Agent Mesh, Ollama, evidence,
approval, command, rescue, edge-agent, and Event Portal services are absent from the target and dependency
closure. Normal `just up` retains ADR-0102's default Agent Mesh behavior. A bare profile invocation is not
the supported isolated entry point.

Preserve Caddy as the sole `127.0.0.1:8080` publisher and Uvicorn on the shared private Unix socket. Use
numeric UID/GID 10001 and explicit socket permissions; Caddy receives no application credential. Private
8081 and 8082 listeners publish no host ports. Dedicated networks permit only dashboard-to-scenario,
scenario-to-fleet, event-mesh, store, and Caddy-only host-ingress edges required by each service. The
shared bounded Unix-socket volume is Caddy's only edge to dashboard-api.

The application image gains a digest-pinned official `node:26.7.0-slim` builder stage, pnpm 11.23.0,
frozen dashboard installation, and the production Vite build. Resolve and commit the immutable index
digest before the image is accepted. Runtime assets contain no test hook or fixture sentinel.

Migration and replay validation are declared one-shot jobs whose successful completion can satisfy
dependency ordering without a fabricated long-running healthcheck. The validator has `network_mode:
none`, read-only root/input, bounded ephemeral output, and no secrets. All long-running services remain
non-root, read-only where applicable, `no-new-privileges`, digest-pinned, healthchecked, and scanned.

Production E2E uses a unique Compose project name. It first proves required host ports are free and never
stops a conflicting stack. Cleanup verifies every target's `com.docker.compose.project` label, then
removes only that project and its disposable volumes.

## Consequences

- Mission-control isolation is determined by an executable closed service list rather than profile
  implication.
- Normal developers still receive the default Agent Mesh stack selected by ADR-0102.
- The two one-shot jobs require explicit static-policy exceptions and completion tests.
- Frontend build reproducibility becomes part of the scanned application image.

## Alternatives considered

- **Rely on `--profile mission-control`.** Rejected because default-on Agent Mesh would also start.
- **Add a second Compose file.** Rejected because ADR-0044 and deployment policy require one definition.
- **Remove Agent Mesh from normal startup.** Rejected because it reverses ADR-0102 for an unrelated
  acceptance workflow.
- **Serve assets directly from Caddy.** Rejected because the API owns the dynamic bootstrap and exact
  same-origin caching boundary.
