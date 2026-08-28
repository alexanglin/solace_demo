# ADR-0194: Gate broker health on guaranteed messaging

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0161, as to which endpoint the broker's health check probes

## Context

[ADR-0161](0161-give-the-broker-a-twenty-minute-clean-stop.md) gives the PubSub+ container a
Compose health check on `http://localhost:5550/health-check/direct-active`, which the image answers
as soon as Direct messaging is up. Every dependant waits on that check: `just up` provisions the
broker immediately after it, and every application service, the Agent Mesh, and the event monitor
start behind `service_healthy`.

The merged runtime's first composition (2026-08-28,
`release-evidence/phase-3/merged-runtime-first-run.md`) recreated the broker and ran the provisioner
21 s after the container started. Its first queue `PUT` was refused with SEMP `code=412 "message
spool data not available"`: the broker's event log shows the message spool passing `AD-Disabled →
AD-Standby → AD-Activating → AD-Active` after Direct readiness. Provisioning, every Guaranteed
publication, and every queue bind depend on the spool, not on Direct messaging. The pinned image also
serves `/health-check/guaranteed-active`, which answers healthy only once the spool is active.

## Decision

The broker's Compose health check probes `http://localhost:5550/health-check/guaranteed-active`.
Nothing else in ADR-0161 changes: the twenty-minute clean stop, the interval, timeout, retries, and
start period stay as they are. A conformance test pins the command.

## Consequences

- `service_healthy` now means what every dependant needs: Guaranteed messaging is active.
- Health arrives later in the boot by the spool's activation time; the start period and retries
  already cover it.
- Rejected: retrying the provisioner on `code=412`, which would keep every other dependant racing the
  spool; and a second health check, which Compose cannot express for one service.
