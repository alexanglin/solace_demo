# ADR-0159: Gate every applicable Solace best practice

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0155's topology applicability clauses, which ADR-0156 superseded
  without carrying forward

## Context

Solace publishes a best-practices index rather than one universal checklist. Its entries cover topic
design, client APIs, Guaranteed messaging, monitoring, release maintenance, broker replay, DMR,
replication, bridges, microgateways, and appliance disk arrays. Several recommendations are explicitly
conditional on a feature or topology. Enabling HA retry behavior on a standalone broker, adding DMR to
one broker, or using an appliance disk-array rule in a software-container deployment would not improve
this system; it would misstate what is deployed. See Solace's
[best-practices index](https://docs.solace.com/Get-Started/best-practices.htm),
[API best practices](https://docs.solace.com/API/API-Developer-Guide/C-API-Best-Practices.htm), and
[topic-architecture guidance](https://docs.solace.com/Messaging/Topic-Architecture-Best-Practices.htm).

The review found practices already encoded in accepted decisions and executable controls, and four
gaps that cannot remain implicit:

1. the supported Python client and pinned 10.26 broker can require TLS 1.3, while the adapter still
   admitted TLS 1.2;
2. queue health monitoring did not cover Solace's capability-dependent minimum System and Message VPN
   event catalogue;
3. the removed scenario messaging identity needed a fail-closed retirement boundary; and
4. ADR-0156 superseded ADR-0155 as a whole while deciding only trace propagation, leaving HA, DR, DMR,
   partitioning, and authentication applicability without an accepted owner.

Solace recommends an LTS broker and current maintenance updates for mature deployments. Version 10.26.0
is an LTS line; the repository pins a complete build tag and image digest so an update is reviewed and
repeatable rather than silently inherited. See
[release-version management](https://docs.solace.com/Release-Notes/Release-Version-Management-Best-Practices.htm)
and [release support dates](https://solace.com/support/support-dates-for-release-versions/).

## Decision

### Closed applicability rule

A Solace recommendation is **required** when the reference topology uses the feature, exchange pattern,
or failure mode to which it applies. It is **conditional** when a named production topology would first
introduce that feature. It is **not applicable** only when the selected product or runtime cannot use the
feature. A stronger project control may replace a recommendation only when this record names the
difference and tests the stronger outcome.

No service is production-ready merely because code for a required row exists. Each required row needs:

- a canonical contract or accepted ADR;
- deterministic evidence for configuration and refusal behavior;
- live evidence wherever the claim depends on the pinned broker, SDK, operating system, or network; and
- an owner for maintenance, monitoring, and recovery.

The release gate fails when a required row lacks any applicable evidence. Conditional rows are not
silently enabled and cannot be described as current capability. Their named trigger requires a new
accepted topology decision and the evidence in the last column.

### Applicability register

| Official practice area | Class | Project binding | Required evidence or activation trigger |
| --- | --- | --- | --- |
| Topic taxonomy and event catalog | Required | ADR-0014, ADR-0036, ADR-0148, the closed schema manifest, typed topic parser, and golden fixtures own the versioned domain, families, actions, value spaces, and application/A2A separation. Routing fields live in topics; selectors, environment names, and trace identifiers do not. The established `aerial-rescue/v1/{missionId}/...` order is a documented tailoring for version-wide policy and mission-wide attraction rather than an unreviewed variant. | Total family/delivery/schema tables, round-trip and maximum-length properties, wildcard/reserved-level negatives, and application/A2A live traffic. Event Portal publication is optional showcase work and requires separately authorized cloud mutation. |
| Supported release and maintenance | Required | ADR-0043 pins the 10.26.0 LTS build and multi-architecture digest; ADR-0055 owns image scanning. Floating `latest`, `lts`, or major/minor tags are forbidden. | Compose pin, registry/readback, build, SBOM, vulnerability scan, and a dated maintenance-review record before release. A newer maintenance build is an explicit tested pin change. |
| Long-lived sessions and endpoint reuse | Required | ADR-0153 constructs one owned messaging service and reusable publishers/receivers per process composition. Per-message connection churn is forbidden. | Construction-count, reconnect, shutdown-order, client-inventory, and soak evidence. |
| TLS and authentication | Required | Project clients use `tcps`, validate expiry and hostname, trust only the generated project CA, forbid plaintext downgrade, and require TLS 1.3 on the pinned Python client/broker path. Every messaging process has one generated secret and owned client/ACL profile; a process with no messaging need has no principal. | Offline TLS-property tests, live TLS 1.3 connection and plaintext/wrong-host/expired-certificate negatives, credential wiring, and broker readback. Linux production images use system OpenSSL; bundled development OpenSSL is not production evidence. |
| Least-privilege client profiles and ACLs | Required | ADR-0061, ADR-0153, and ADR-0158 use deny-by-default topic authorization plus explicit connection, flow, endpoint, transaction, subscription, and downgrade limits. The factory username is disabled. | Total grant/profile tables, exact SEMP write/readback, positive controls, representative publish/subscribe denials, and absence of unused identities. |
| Delivery mode selection | Required | The parsed family selects typed Direct, Guaranteed, or request/reply ports before I/O. Callers cannot request a weaker delivery mode. Telemetry and structured plugin output are explicit Direct loss boundaries; critical facts use Guaranteed delivery. | Total delivery table, wrong-capability refusals, wire-mode compatibility, queue matching, and loss/recovery live probes. |
| Publisher confirmation and backpressure | Required | A bounded publisher rejects rather than grows elastically. A broker confirmation is the only success; broker rejection is definite; capacity is retryable; timeout or interruption is ambiguous and enters durable reconciliation. Direct telemetry uses zero SDK buffering. | Refusal/ambiguity unit tests, confirmation live tests, queue-full and no-route negatives, outbox recovery, and bounded-buffer metrics. Batch sending is not used on per-row confirmation paths. |
| Guaranteed receive and acknowledgment | Required | Durable receivers use client acknowledgment and settle only after the complete PostgreSQL transaction commits. One queue owner, one exclusive binding, and `maxDeliveredUnackedMsgsPerFlow=1` bound in-flight work. The Python 1.11 API exposes no assured-delivery-window setter; the endpoint bound plus synchronous one-message processing is the supported control and connected-path latency is measured. | Commit-before-settlement fault injection, broker redelivery/restart, queue readback, one-at-a-time processing, and latency evidence. |
| Duplicate and redelivered messages | Required | Broker inbox identities, canonical digests, immutable command receipts/effects, and producer sequence locks make redelivery idempotent. Same identity and bytes return the prior outcome; conflicting bytes fail closed. The SDK redelivery flag is telemetry, never the idempotency authority. | Same-byte duplicate, conflicting duplicate, lost-ack, restart, concurrent claim, and zero-duplicate-side-effect live evidence. |
| Unexpected message formats | Required, stronger | Official API guidance permits acknowledging an invalid Guaranteed message to stop a poison loop. This project first persists a bounded refusal/audit fact and then settles `REJECTED` to the source queue's isolated DMQ. It never logs the body. This also stops the loop while retaining evidence. If the refusal cannot commit, the message remains unsettled. | Malformed/oversized/schema/topic/trace negatives, commit failure, redelivery bound, DMQ movement, sanitization, and no-body logging tests. |
| TTL, redelivery, discard, spool, and flow bounds | Required | Every owned queue/template explicitly sets maximum message size, spool, bind count, unacknowledged delivery count, redelivery count, TTL, TTL enforcement, discard rejection, ingress/egress state, owner, permission, and thresholds. No broker factory default is authority. | Total desired-state body/readback tests, oversized and expired messages, queue-full rejection, resource-high-water readback, and backlog recovery. |
| Dead-message isolation | Required | ADR-0157 carries forward one derived DMQ per source, no recursive DMQ, no routine draining, and paired fail-closed retirement. A nonempty DMQ is degraded failure evidence. | Pair derivation, malformed/race/nonempty retirement refusals, max-redelivery live movement, depth delta, and operator retention evidence. |
| Reconnection and readiness | Required for standalone | ADR-0145 and ADR-0153 bound initial connection and active recovery. Disconnect removes readiness immediately; reconnect does not restore it until receivers rebind and committed outboxes drain; exhaustion exits nonzero. The reference stack claims neither broker HA nor site-loss DR. | Lifecycle listener, publisher readiness, receiver rebind, outbox drain, broker restart, exhaustion, and mount/client readback evidence. |
| Graceful shutdown and callback behavior | Required | Intake stops before receivers, publishers, and the service terminate within bounded grace periods. Owned consumers use bounded synchronous receive loops; lifecycle callbacks only change in-memory state and return, so no store/network work blocks an SDK callback. Cleanup continues after one endpoint failure. | Call-order, aggregate-error, cancellation, signal, outstanding-settlement, and process-exit tests. |
| SEMP and broker-event monitoring | Required | ADR-0157 owns paced, coalesced, read-only aggregate queue monitoring through a dedicated least-privilege management identity. The production-like profile must additionally collect the capability-relevant minimum System and Message VPN events through a Solace-supported Syslog or message-bus path; queue polling is not a substitute. | Five-request/s local share, broker-wide ten-request/s acceptance, 30-second coalescing, pagination, no `/msgs` enumeration, positive read/negative write, minimum-event catalog configuration, alert-path fault injection, and log/metric redaction. |
| Distributed tracing | Required for application events | ADR-0156 pins the official Solace/OpenTelemetry carrier. Native and envelope W3C contexts derive from one source and must share TraceID; baggage is excluded. Broker-generated spans, sampling, collector, and backend are a conditional production profile. | Carrier compatibility, injection readback, malformed/missing/mismatched context, child-span acceptance, and live cross-broker propagation. |
| HA redundancy | Conditional | A production HA profile requires the Solace primary/backup/monitor topology, synchronized clocks, virtual IP or supported host list, and at least 300 seconds of client reconnect. The standalone 30-second budget is not reused. | Trigger: an accepted broker-node-failure availability target. Requires failover timing, host-list/VIP, state synchronization, client recovery, and recommended HA event alerts. |
| Disaster-recovery replication | Conditional | A production DR profile requires paired sites, consistent queue names, replication-aware clients, site-specific authority, and an intentionally long or indefinite reconnect policy. Active/active deployments do not use a replication host list. | Trigger: an accepted site-loss RPO/RTO. Requires replication lag, failover/failback, ambiguity, duplicated/redelivered work, and recommended replication event alerts. |
| DMR, bridges, and multi-site mesh | Conditional | DMR is introduced only for multiple brokers that need dynamic subscription propagation. Secured links, cluster shape, topic/export governance, loop prevention, link capacity, and DMR events are decided with that topology. DMR and VPN bridges are never layered for the same route by default. | Trigger: a second broker/site or external application domain. Requires topology, propagation, loop, link-loss, authorization, sovereignty, and capacity evidence. |
| Partitioned queues and shared subscriptions | Conditional | Current exclusive queues preserve per-producer command order and single-owner effects. Partitioning or shared consumption requires a keyed-ordering and concurrency decision; it is not a generic scale switch. | Trigger: measured single-consumer throughput saturation. Requires partition-key stability, rebalance, ordering, duplicate effects, failure recovery, and client-profile changes. |
| Broker replay | Conditional | Operational replay is reconstructed from validated PostgreSQL audit facts and deliberately opens no broker connection. Broker replay is added only for a separate broker-spool recovery use case and never replaces the audit authority. | Trigger: an accepted broker-message replay objective and licensed/configured replay log. Requires retention, replay identity, duplicate handling, authorization, and isolation tests. |
| Compression, batch publish, and LVQ | Conditional | The local path has no measured bandwidth bottleneck; critical outbox rows need individual confirmation; PostgreSQL stores confirmation correlation. Compression, batch send, and LVQ are therefore not enabled speculatively. | Trigger: measured CPU/network/publication bottleneck that preserves exact delivery semantics. Requires benchmark, mixed-failure, ordering, memory, and recovery evidence. |
| mTLS or OAuth client authentication | Conditional | TLS plus per-role generated basic credentials is the supported local boundary. mTLS or OAuth is required only by a production identity-provider or certificate-lifecycle decision; neither may weaken ACL/profile isolation. | Trigger: accepted external identity and rotation requirements. Requires expiry/revocation, clock, issuer/audience, rotation, outage, and negative authorization evidence. |
| Microgateway and appliance disk-array guidance | Not applicable | The system uses SMF clients and a software event broker container, not REST microgateway routing or an appliance with an external disk array. | Becomes conditional only if the selected broker product or ingress protocol changes. |

The topology rules in the conditional rows are authoritative even though ADR-0156 superseded ADR-0155.
Solace's [DMR design guidance](https://docs.solace.com/Features/DMR/DMR-Best-Practices.htm) and
[replication guidance](https://docs.solace.com/API/API-Developer-Guide/Replication-BP.htm) are applied
only after those triggers. Solace's minimum event list is capability-dependent, so the event monitor
filters out HA, DMR, bridge, LDAP, and replication categories until those features exist; it must add
them in the same change that activates the feature. See
[minimum recommended monitoring events](https://docs.solace.com/Monitoring/Min-Events.htm).

## Consequences

- "Uses Solace best practices" becomes a bounded, reviewable claim with code and live evidence rather
  than a claim that every broker feature is enabled.
- The application moves to TLS 1.3 and cannot connect to an older TLS-only endpoint without a new
  accepted compatibility decision.
- Each topology expansion brings its matching client retry, authentication, monitoring, capacity, and
  failure tests in the same increment.
- A malformed Guaranteed message stops redelivering only after durable refusal evidence exists, which is
  stricter and more operationally expensive than immediate acknowledgement.
- Negative: the minimum broker-event monitor adds another operational component and alert catalogue;
  queue depth alone is no longer sufficient production-like monitoring evidence.
- Negative: exact image pins require deliberate maintenance updates. Reproducibility is preferred over a
  floating tag, but the maintenance-review obligation prevents a pin from becoming indefinite neglect.
- Negative: the register must be reviewed whenever Solace changes its best-practices index, the broker or
  SDK pin changes, or a conditional feature is activated.

## Alternatives considered

- **Enable every named Solace feature.** Rejected because several recommendations apply only to
  multi-node, multi-site, appliance, REST, or measured-performance cases and would create false
  availability and security claims in the standalone reference stack.
- **Treat official guidance as informal documentation.** Rejected because queue defaults, unbounded SDK
  buffers, stale identities, and ambiguous publication semantics are executable risks.
- **Keep TLS 1.2 for broad compatibility.** Rejected because the pinned broker and Python client support
  TLS 1.3 and this repository owns both ends of the reference connection.
- **Use queue polling as complete broker monitoring.** Rejected because Solace separately recommends
  System and Message VPN events for component, authentication, service, and resource failures.
- **Automatically delete any broker identity absent from the principal enum.** Rejected because absence
  from application desired state is not proof of ownership. Provisioning detects the exact retired
  scenario identity and fails closed without mutation; disabling or deleting it requires separate
  operator authorization and a second ownership/binding readback. Every other unexpected identity is
  reported, not mutated.
- **Adopt Event Portal as the only catalog.** Rejected because release correctness cannot depend on an
  optional cloud account or unauthorized cloud mutation. The committed schema manifest is the local
  authority; Event Portal remains a valuable governed projection when explicitly enabled.
