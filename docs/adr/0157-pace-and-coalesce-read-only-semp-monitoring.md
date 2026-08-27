# ADR-0157: Pace and coalesce read-only SEMP monitoring

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0154

## Context

ADR-0154 correctly moved queue depth reads from message enumeration to the queue collection's aligned
`msgs.count` aggregate, but it stopped at a transport primitive. It did not provide the continuously
callable monitor, did not pace individual pages in a paged response, and claimed a separate management
identity without an executable identity boundary or proven provisioning surface.

Solace states that the event broker supports at most ten SEMP polling requests per second across all
SEMP connections, and that SEMP has no API-provided rate limiter. It also requires clients to follow
pagination because even a page with fewer rows than requested can carry a next cursor. The official
30-second minimum applies specifically to periodic Solace Cache monitoring; this project voluntarily
applies that more conservative interval to routine queue monitoring as well. See
[Monitoring Events](https://docs.solace.com/Monitoring/Monitoring-Events.htm) and
[SEMP Features](https://docs.solace.com/Admin/SEMP/SEMP-Features.htm).

Solace's least-privilege example for a user that monitors one Message VPN is global access `none` and
Message VPN access `read-only`. A management user receives those scopes for SEMP as it does for the CLI.
See [User Authentication and Authorization](https://docs.solace.com/Admin/Mgmt-User-Authen-Auth-Overview.htm),
[SEMP Authentication and Authorization](https://docs.solace.com/Admin/SEMP/SEMP-Security.htm), and
[Configuring Internal CLI User Accounts](https://docs.solace.com/Admin/Configuring-Internal-CLI-User-Accounts.htm).
The software broker can create a CLI user at startup, but the documented container keys expose a global
access level and not the required per-VPN exception. See
[Configuration Keys](https://docs.solace.com/Software-Broker/Configuration-Keys-Reference.htm).

The pinned 10.26.0.8799 broker's SEMP v2 configuration specification is the authority for an automated
management-user write. A TLS-validated, credential-redacted read on 2026-08-25 found `/about/user` and
Message VPN messaging `clientUsernames`, but no internal management-user create/update path or schema.
It therefore cannot prove all internal-user, global-none, VPN-read-only, password, and readback fields.
Inventing an endpoint or silently granting global read-only access would turn an unsupported automation
step into a wider principal.

## Decision

Carry forward ADR-0154's isolated DMQ, explicit queue-bound, narrow aggregate-read, and paired-retirement
decisions unchanged. Replace its routine-monitoring and management-identity clauses with the following
stronger behavior.

The broker package owns a `ReadOnlySempMonitor` capability. It accepts only the dedicated internal,
alphanumeric management username `aerialrescuemonitor`, exposes monitor reads but no configuration
`send`, uses the existing TLS and secret-redaction boundary, and paces every monitor page. A provisioning
administrator is refused before network I/O.

One routine monitor process may issue at most five SEMP requests per second. This reserves half of the
broker-wide ten-request ceiling for the Event Management Agent, provisioning, and bounded operator
diagnostics. Independent clients remain a broker-wide acceptance measurement; one process-local pacer
cannot make an assertion about another process. The pacer rejects invalid or backwards monotonic time and
never sleeps for more than one 200-millisecond request interval.

Routine queue attempts are coalesced for 30 seconds. Calls inside the interval return the same complete
successful snapshot or re-raise the same typed failure without another SEMP read. A failure never becomes
a healthy snapshot and never erases the last complete snapshot. Each refresh follows at most 20 pages of
100 rows, validates row/collection alignment and exact non-negative integers, rejects duplicate identities,
and produces stable totals for project-owned depth, primary backlog, nonempty DMQs, missing desired queues,
unexpected project queues, and bind mismatches. It does not call an endpoint's `/msgs` child collection.

A nonempty DMQ is failure evidence. It degrades health and remains in the snapshot; the routine monitor
has no settle, consume, purge, or delete capability. An empty unexpected queue also degrades configuration
health but is deleted only through ADR-0154's separately authorized two-step retirement readback.

Automatic creation of `aerialrescuemonitor` remains fail-closed because the pinned broker's own
`/SEMP/v2/config/spec` has no management-user write and readback surface. An operator therefore
must create the internal user through a Solace-supported out-of-band management path, assign global
`none` and only the selected VPN `read-only`, source its password from generated secret material, and prove
both a positive narrow monitor read and a negative configuration write before starting routine monitoring.
The project must not substitute `admin`, the provisioning credential, global `read-only`, a messaging
client username, or a startup password literal. This offline increment therefore proves the capability
and refusal boundary, not that the shared broker already has the principal.

System and Message VPN event monitoring remains a separate deployment obligation. Solace recommends SEMP
and Syslog and/or message-bus events, and publishes a capability-dependent minimum event catalogue; this
queue monitor does not claim to replace that alert path. See
[SEMP and Syslog Monitoring Best Practices](https://docs.solace.com/Monitoring/Monitoring-BP.htm) and
[Minimum Recommended Events for Monitoring](https://docs.solace.com/Monitoring/Min-Events.htm).

## Consequences

- Repeated readiness or dashboard calls cannot multiply routine SEMP traffic inside the cache interval.
- A multi-page response is rate-limited per HTTP request rather than incorrectly counted as one poll.
- Queue health includes poison-message evidence, desired-state drift, consumer bind loss, and current
  backlog without reading any message payload.
- The routine object cannot mutate broker configuration even if a caller gains a reference to it.
- Negative: the five-request local reservation does not coordinate other processes. Acceptance must count
  all SEMP clients, and operators must lower independent poll rates if their sum could exceed ten.
- Negative: routine monitoring cannot start from a clean stack until the dedicated internal management
  user is provisioned and its positive/negative authorization controls pass against the pinned broker.
- Negative: queue polling does not collect the recommended system and Message VPN event catalogue; the
  production topology still needs its chosen Syslog or message-bus event integration.

## Alternatives considered

- **Use the provisioning administrator for reads.** Rejected because credential compromise would turn a
  monitor into a broker writer and because an accidental write would be authorized.
- **Use global read-only startup keys.** Rejected because Solace's one-VPN monitor example is narrower and
  the documented startup keys do not express the required VPN exception.
- **Invent a SEMP v2 management-user request from current online documentation.** Rejected because the
  pinned broker's own specification contains no such path, and a different patch release cannot govern
  this deployment.
- **Pace one high-level poll rather than each page.** Rejected because a single collection walk can make
  20 HTTP requests and exceed the broker-wide request ceiling.
- **Retry immediately after a failure.** Rejected because monitoring loss must not create a SEMP retry
  storm; the typed failure is coalesced for the same interval as a successful snapshot.
- **Drain a DMQ after observing it.** Rejected because monitoring is read-only and dead messages are
  failure evidence whose retention requires an explicit operator decision.
