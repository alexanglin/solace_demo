# ADR-0161: Give the broker twenty minutes to stop cleanly

- **Status:** Superseded in part by ADR-0194
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none; adds the container-lifecycle binding omitted from ADR-0159

## Context

ADR-0043 pins the PubSub+ Standard container and its single-node resource settings. ADR-0159 makes
every applicable Solace best practice a release gate, but its applicability register does not bind the
container runtime's forced-stop deadline. Docker Compose otherwise supplies its own much shorter stop
grace when it recreates or stops the broker.

Solace documents that shutdown performs material upgrade and persistence work, can take several
minutes, and should receive 1,200 seconds before a container runtime forcibly terminates the process.
The recommendation applies to a standalone broker as well as to an HA member because the local
storage group contains configuration, message spool, and delivery state. See Solace's
[production-container guidance](https://docs.solace.com/Software-Broker/SW-Broker-Set-Up/Containers/Deploying-Container-in-Production.htm#Upgrading_the_Container).

The existing reference definition already carries the other applicable single-node container controls:
an exact LTS build and digest, unique hostname, persistent `/var/lib/solace` volume, one-gigabyte
shared-memory allocation, the vendor's 100-connection scaling value and file/core ulimits, secrets by
file, bridge networking, loopback-only published TLS ports, container event logging, a healthcheck, and
automatic restart. CPU, memory-cgroup, XFS, IOPS, host time synchronization, and systemd settings belong
to a future production-host profile sized by Solace's System Resource Calculator; Docker Desktop is not
evidence for those host-level claims.

## Decision

The `broker` service in `deploy/compose.yaml` sets `stop_grace_period: 20m`. The deployment policy
tests require that exact value, and the shared-stack recreation procedure must not override it with a
shorter command-line deadline.

This control is distinct from the 15-second application/SDK endpoint shutdown grace. Application
processes bound their own transactions and SDK endpoint termination; the 20-minute value protects the
broker container's storage-group shutdown and upgrade work.

## Consequences

- Normal broker recreation and host shutdown can complete the broker's persistence work before Docker
  is permitted to force termination.
- The same value is visible and reviewable in Compose instead of depending on an operator remembering a
  `docker stop --time` option.
- Negative: a genuinely stuck broker may delay `docker compose stop`, recreation, and upgrade for as
  long as twenty minutes.
- Negative: this does not make the Docker Desktop topology production HA and does not prove production
  host CPU, memory, filesystem, IOPS, time-source, or systemd configuration.

## Alternatives considered

- **Keep the Compose default.** Rejected because it is shorter than the vendor's clean-stop guidance
  and can forcibly interrupt storage-group shutdown.
- **Use the 15-second application shutdown grace.** Rejected because an application transaction bound
  and a broker storage-group shutdown have different work and different vendor guidance.
- **Set the deadline only in the runbook.** Rejected because the supported `docker compose` path would
  still omit it and deterministic deployment evidence could not enforce it.
- **Claim the complete production-container guide is satisfied on Docker Desktop.** Rejected because
  host sizing, XFS/IOPS, time synchronization, HA, and systemd are deployment-host concerns that this
  standalone reference topology neither configures nor measures.
