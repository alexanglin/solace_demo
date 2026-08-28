# ADR-0173: Follow the retained broker event log without runtime authority

- **Status:** Superseded in part by ADR-0195
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes:** the continuous event-source ambiguity left by ADR-0159

## Context

ADR-0159 requires a continuously running, capability-relevant minimum SYSTEM and Message VPN event
monitor. The broker already directs the `event` facility to both its retained file and container stdout,
uses the broker-native JSON message format, and bounds each JSON message to 8,192 bytes. The existing
`aerial-rescue-broker-events` program validates that closed stream and emits only tenant-neutral alerts,
but its operator recipe follows Docker logs interactively. An application container cannot consume
another container's stdout without a privileged Docker API/socket capability or an external collector,
so that recipe is not a safe continuous application composition.

Solace documents that container broker logs are retained under `/var/lib/solace/jail/logs`, that the
active event facility file is `event.log`, and that local syslog files rotate to numbered archives. It
also documents `/var/lib/solace` as the container storage-group mount and, for its own Insights Agent,
the least-capability pattern of sharing only the named volume's `jail/logs` subpath read-only. See
[Configuring Container Logging](https://docs.solace.com/Software-Broker/Container-Tasks/Configuring-VMR-Container-Logging.htm),
[Monitoring Events Using Syslog](https://docs.solace.com/Monitoring/Monitoring-Events-Using-Syslog.htm),
and [Sharing the Log Files With the Insights Agent](https://docs.solace.com/Cloud/Insights/insights-ss-broker-container.htm#Sharing-the-Log-Files-With-the-Insights-Agent).

The retained log contains raw broker, tenant, VPN, and client information. A monitor with network,
broker, database, Docker, or whole-storage authority could exfiltrate more than its alerting role needs.
Mounting the whole storage group would also expose configuration, diagnostic, and message-spool state
even though the process needs only one log file.

## Decision

The default Compose topology runs one `broker-event-monitor` service after the broker becomes healthy.
It uses the project application image and the dedicated
`aerial-rescue-broker-event-monitor` entry point. The service:

- mounts `broker-storage` with long syntax, `volume.subpath: jail/logs`, read-only at `/jail/logs`;
- reads only the closed path `/jail/logs/event.log` and receives no path override;
- has `network_mode: none`, no ports, no Compose secret, no broker/database setting, no Docker socket,
  a read-only root filesystem, all Linux capabilities dropped, and `no-new-privileges`;
- emits only the existing closed, redacted alert document to stdout; and
- remains in the default topology so Agent Mesh-only and complete application runs both monitor the
  broker substrate.

The follower reads one complete line at a time under the existing 8,192-byte event bound. It holds an
incomplete append until the newline arrives, detects rename rotation by device/inode, and detects
copy-truncation when the active file becomes shorter than its current offset. It polls an unchanged EOF
once per second. A temporary missing active path during rotation is tolerated for at most 30 polls;
initial missing/unreadable material and an exhausted rotation gap emit a redacted source-failed alert
and exit nonzero. SIGINT or SIGTERM closes the descriptor without emitting a false source-closed alert.

The process gets the common 15-second application stop grace. After its own fail-closed exit Compose
restarts it at most three times (`on-failure:3`); repeated failure stays visible instead of creating an
unbounded crash loop. Its container healthcheck is liveness-only. Monitoring correctness and source
continuity are expressed by the process exit and closed alerts, not by claiming a live PID has parsed a
particular event.

The retained file is the continuous source. The stdout setting remains because it is useful to the
container runtime and the credentialless operator recipe, but no application sidecar consumes Docker
logs. The `system` facility remains unconfigured on this path because its relevant SYSTEM events are a
subset of the `event` facility and would duplicate them.

## Consequences

- Continuous broker-event monitoring no longer depends on an operator keeping a shell pipeline open.
- The monitor can read the Solace-supported retained stream without any messaging, management, host,
  or container-runtime authority.
- The official volume-subpath form prevents the monitor from reading broker configuration, diagnostics,
  and spooled messages in the rest of the storage group.
- Missing material, permission failure, an unbounded rotation gap, malformed input, catalog gaps,
  alert-delivery failure, and unexpected source closure remain visible fail-closed conditions.
- Negative: a monitor process restart begins at the active file's start and can repeat already-redacted
  alerts; the reference topology accepts duplicate observations rather than persist a writable cursor
  or silently skip events emitted before startup.
- Negative: a monitor outage lasting across enough broker rotations can miss discarded archives. The
  bounded restart and source-failure alert make that an explicit operational failure, not continuity
  evidence.
- Live deployment must still prove that the pinned broker image creates a readable `event.log` in the
  documented subpath and that the installed Compose runtime honors `volume.subpath`.

## Alternatives considered

- **Mount `/var/run/docker.sock` and follow `docker logs`.** Rejected because the Docker API is host-level
  container authority, not a read-only log capability.
- **Mount the whole `broker-storage` volume read-only.** Rejected in favor of Solace's documented
  `jail/logs` subpath pattern, which exposes less broker state.
- **Bind-mount or copy a second broker log path.** Rejected because it creates another sensitive-data
  lifecycle and can drift from the broker's retained facility.
- **Configure remote Syslog.** Supported by Solace, but rejected for this local topology because it adds
  a network listener and broker management configuration when the retained volume already supplies the
  exact stream.
- **Publish and consume `#LOG/...` message-bus events.** Supported by Solace, but rejected here because it
  adds a monitoring messaging principal, grants, and broker configuration solely to reach information
  already retained in the storage group.
- **Use only SEMP aggregate polling.** Rejected because ADR-0159 distinguishes periodic state sampling
  from event continuity.
