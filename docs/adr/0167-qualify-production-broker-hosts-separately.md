# ADR-0167: Qualify production broker hosts separately

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Extends:** ADR-0043, ADR-0044, ADR-0159, and ADR-0161

## Context

The supported repository runtime is a standalone PubSub+ Software Event Broker on Docker Desktop. It
can prove application semantics, broker interoperability, persistence across container recreation, and
bounded restart recovery. It cannot prove the host properties Solace requires for a mature production
deployment or the node-failure availability of a high-availability group.

Solace's production-container guidance derives CPU, memory, shared memory, scaling keys, and storage
from the System Resource Calculator; requires accurate host time; requires XFS on dedicated SSD-backed
storage with measured latency, throughput, and IOPS; recommends a unique hostname, organization-local
DNS polling target, automatic host-boot startup, core and diagnostics handling, and a 1,200-second
broker stop; and describes a primary, backup, and monitor topology when high availability is required.
See the official
[production deployment guide](https://docs.solace.com/Software-Broker/SW-Broker-Set-Up/Containers/Deploying-Container-in-Production.htm),
[storage guidance](https://docs.solace.com/Software-Broker/Container-Tasks/Config-Container-Storage.htm),
and [DNS guidance](https://docs.solace.com/Admin/Configuring-DNS.htm).

The local Compose definition already carries the applicable container-level subset: exact image pin,
unique container hostname, persistent storage outside the writable layer, explicit shared memory and
ulimits, secret files, bridge networking, minimal host port publication, health and event output,
automatic container restart, and ADR-0161's twenty-minute stop window. A Docker named volume is not
evidence of a dedicated XFS SSD, and a host file-backed development secret is not evidence of a
production tmpfs secret injection path.

## Decision

The Docker Desktop stack remains the supported development, integration, acceptance, and reference
runtime and is explicitly **not** a qualified production broker-host profile.

A production broker-host profile is unsupported until one deployment-specific accepted ADR and its
release evidence provide all applicable rows below:

| Production concern | Required evidence |
| --- | --- |
| Capacity | Dated System Resource Calculator inputs and outputs bound to exact CPU, host memory, cgroup memory/swap, `/dev/shm`, storage, scaling keys, connection count, endpoint count, subscription count, message size, and queue-message count. |
| Storage | A dedicated non-root XFS filesystem on SSD-backed storage, capacity at or above the calculator result, measured latency/throughput/IOPS under the target workload, mount ownership for the broker UID, persistence/recreation, and restore evidence. |
| Host operation | Supported Linux and container-runtime versions, accurate synchronized time, unique host/router naming, organization-local resolvable polled DNS name, host boot ordering, automatic restart, 1,200-second broker stop, and a tested upgrade/rollback procedure. |
| Security | Only required TLS ports, production trust and certificate lifecycle, tmpfs or equivalent secret injection, no factory credentials, exact least-privilege SEMP and messaging identities, disabled unused protocols, host firewall policy, and negative authorization evidence. |
| Diagnostics and monitoring | Bounded local and remote log retention, secure event forwarding, minimum capability-dependent broker events, aggregate metrics, core-file capacity/handling, redacted gather-diagnostics procedure, alert delivery, and operator ownership. |
| Availability | If an accepted node-failure target exists, Solace primary/backup/monitor placement, Config-Sync, secure mate links, supported Docker/Podman networking, at least 300 seconds of client reconnect, and measured failover. Otherwise the profile must state that it is standalone. |
| Disaster recovery | Only after an accepted site-loss RPO/RTO: replication topology, consistent endpoints, site authority, long-lived reconnect, lag/failover/failback, duplicate handling, and replication events. |

Evidence must be captured from the actual target host and storage. A Compose schema check, Docker
Desktop measurement, or copied calculator example cannot satisfy a production row. Secrets, private
hostnames, IP addresses, tenant identifiers, and raw diagnostics remain outside public release evidence.

The production-host ADR may select a managed Solace Cloud service instead. In that case Solace owns
host sizing, storage, broker HA, and platform maintenance, while this project still owns client
identity, ACLs, queues, contracts, application recovery, and its separately authorized cloud
configuration/readback evidence.

## Consequences

- The project can claim complete applicable client, broker-object, and standalone-container practice
  without implying that Docker Desktop proves production host or HA properties.
- Every production-only Solace recommendation has a named activation boundary and evidence owner.
- A named volume and restart policy remain useful local controls but cannot be promoted into claims
  about XFS, SSD/IOPS, host boot, time synchronization, or backup.
- Negative: there is no currently supported self-managed production-host deployment artifact. Creating
  one requires a real target and deployment-specific measurements rather than another generic Compose
  overlay.

## Alternatives considered

- **Call the local stack production-ready.** Rejected because its host, storage, DNS, time, HA, secret
  injection, and operational ownership are not production evidence.
- **Encode guessed CPU, memory, or IOPS in Compose.** Rejected because Solace derives them from topology
  and workload, and incorrect hard limits can prevent the broker from starting or make it unstable.
- **Require HA in every environment.** Rejected because local deterministic integration needs one
  broker and must not claim node-failure availability; HA activates only with a named target.
- **Leave production guidance informal.** Rejected because an unchecked deployment could inherit the
  reference stack's intentionally local storage and secret assumptions while retaining a production
  label.
