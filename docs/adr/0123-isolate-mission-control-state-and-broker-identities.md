# ADR-0123: Isolate mission-control state and broker identities

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0117 selected an exact nine-service closure and required unique projects for production E2E, while
ADR-0120 selected a two-queue mission-control broker projection. The supported recipes still inherited
the Compose file's default `aerial-rescue-mesh` project, however. Broker provisioning converges objects
inside the desired set but deliberately does not delete queues that disappear from a later projection.
Running normal startup before mission-control startup against that shared broker volume could therefore
leave all global queues present and make the two-endpoint claim false.

The mission-control provisioner also created ACL profiles, usernames, and credentials for all ten broker
roles even though only fleet simulator, scenario service, and recorder connect to the broker in the
selected closure. Those unused identities added authority and secret dependencies without a process
that could exercise them.

## Decision

All four supported mission-control recipes select one dedicated Compose project through
`AERIAL_RESCUE_MISSION_CONTROL_PROJECT`, defaulting to `aerial-rescue-mission-control`. Production E2E
sets the same variable to a unique disposable value. Normal `just up` continues to use the ordinary
project and volumes. Mission-control cleanup and inspection use only the selected dedicated project.

The mission-control broker projection creates exactly three ACL profiles and client usernames:

1. `fleet-simulator`;
2. `scenario-service`; and
3. `recorder`.

It reads only those three credentials. The global projection remains total over all ten recorded roles.
The dedicated broker volume begins empty, so the existing convergent provisioner can establish exactly
the combined recorder lifecycle queue and dead-message queue without destructive deletion of unrelated
global runtime state.

## Consequences

- Normal and mission-control broker/PostgreSQL volumes cannot contaminate each other's queue or schema
  inventory.
- The mission-control stack has no inactive broker identity, ACL profile, username, or credential
  requirement.
- Operators must use the matching mission-control status, logs, and stop recipes so every command selects
  the same project.
- A separately named project consumes its own broker and PostgreSQL storage; this is the deliberate cost
  of non-destructive isolation.
- The mission-control projection is not a cleanup mechanism. An already-contaminated dedicated project
  must be replaced explicitly rather than silently deleting broker objects.

## Alternatives considered

- **Delete queues absent from the selected projection.** Rejected because the normal broker may contain
  durable work owned by another runtime, and projection switching is not authority to destroy it.
- **Keep the shared project and inspect queue names after startup.** Rejected because detection does not
  establish isolation and makes the supported result depend on invocation history.
- **Create all ten identities with only three processes.** Rejected because unused credentials and ACL
  objects expand the authority surface without providing a runtime behavior.
- **Add another Compose file.** Rejected because ADR-0044 retains one definition; project naming already
  provides the required state boundary.
