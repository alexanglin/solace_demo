# ADR-0119: Parameterize disposable non-UI host ports

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0044, ADR-0117

## Context

Production dashboard acceptance must run in a uniquely named disposable Compose project and must not
stop or reuse an unrelated running stack. The reference workstation can already have the normal
Agent-Mesh project publishing broker SMF, broker SEMP, and PostgreSQL on their documented default host
ports. A second project with the same fixed mappings cannot start, even though its internal service
ports and networks are independent.

Caddy is different: `http://127.0.0.1:8080` is the product boundary the browser acceptance explicitly
tests. Moving that port would test a different delivery contract.

## Decision

Keep the existing host-port values as defaults, but permit a disposable mission-control run to
override only these non-UI mappings:

| Environment name | Default | Container port |
| --- | ---: | ---: |
| `AERIAL_RESCUE_BROKER_SMF_HOST_PORT` | 55443 | 55443 |
| `AERIAL_RESCUE_BROKER_SEMP_HOST_PORT` | 1943 | 1943 |
| `AERIAL_RESCUE_POSTGRES_HOST_PORT` | 5432 | 5432 |

Every mapping remains bound to `127.0.0.1`. Internal application URLs remain fixed to the Compose
service names and container ports, so an override changes no service-to-service contract. The
supported mission-control recipe passes the selected SEMP host port to the host-run broker
provisioner. SMF and PostgreSQL overrides exist for independent measurement and diagnostics only.

Caddy remains fixed at `127.0.0.1:8080`; the recipe must refuse through Docker's ordinary bind failure
when that product port is occupied. Acceptance resolves and records three free non-UI ports before
starting, supplies a unique `COMPOSE_PROJECT_NAME`, verifies every resulting project label, and cleans
up only that project and its volumes.

## Consequences

- Production E2E can coexist with the user's normal stack without stopping it or sharing durable
  state, credentials, broker queues, or PostgreSQL history.
- Default developer commands and documented ports do not change.
- Test and runbook commands must carry the same override values through start, inspection, and cleanup.
- Caddy port conflicts remain explicit blockers because the accepted browser URL is not configurable.

## Alternatives considered

- **Stop the existing project.** Rejected because it would mutate user-owned runtime state outside the
  disposable acceptance scope.
- **Reuse its broker and PostgreSQL.** Rejected because project-label, volume-isolation, migration, and
  cleanup evidence would no longer describe one disposable stack.
- **Randomly publish every host port.** Rejected because the host-run SEMP provisioner needs one known
  bounded target and evidence commands need reproducible coordinates.
- **Move Caddy from 8080.** Rejected because the production URL is part of the acceptance contract.
