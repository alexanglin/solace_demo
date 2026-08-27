# ADR-0153: Own bounded least-privilege PubSub+ clients

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0061 as to factory client profiles; ADR-0145 as to the still-unimplemented
  transport mechanism

## Context

The application topic and ACL tables are deny-by-default, but every client username is currently bound
to PubSub+ software broker's factory `default` client profile. That profile permits capabilities an
ordinary application role does not need, including both Guaranteed directions, dynamic endpoint
creation, and transacted sessions. It also permits TLS downgrade by default. Topic authorization alone
therefore does not bound connection, flow, endpoint, or session resources and does not prevent a
compromised role from attempting unrelated broker features.

The project-owned Python adapter has a second gap. It accepts expired broker certificates, leaves the
persistent publisher and direct receiver on elastic buffers, sets initial and active reconnect attempts
to zero, and reports every publication exception as a definite refusal. The official Solace Python API
distinguishes bounded backpressure, publish rejection, acknowledgement timeout, service interruption,
publisher readiness, and reconnection lifecycle. A timeout or disconnect can be ambiguous: the broker
may have accepted the message even though the client did not observe confirmation.

The accepted delivery table also needs to describe the actual SDK path. The project command gateway
currently uses its persistent adapter for the direct `GATEWAY_RECORD`. The pinned Solace AI Connector
constructs a persistent publisher for every output, including the contract-direct `AGENT_RESPONSE`, and
its receipt listener invokes success without inspecting the receipt exception or persisted flag. A
family table cannot enforce delivery while those paths bypass it.

Solace's guidance is authoritative for the mechanisms in this record:

- [client profiles](https://docs.solace.com/Security/Configuring-Client-Profiles.htm);
- [Python service, TLS, and reconnection](https://docs.solace.com/API/API-Developer-Guide-Python/Python-API-Messaging-Service.htm);
- [persistent publishing and backpressure](https://docs.solace.com/API/API-Developer-Guide-Python/Python-PM-Publish.htm); and
- [direct receive backpressure](https://docs.solace.com/API/API-Developer-Guide-Python/Python-DM-Receive.htm).

## Decision

### Owned profiles and endpoint templates

Provision one project-owned client profile per broker principal before creating or rebinding that
principal's username. The profile table is total over the principal enum and carries, explicitly:

- Guaranteed send, Guaranteed receive, and dynamic endpoint-create permissions;
- permitted endpoint durability;
- transacted-session, bridge, shared-subscription, endpoint-permission-override, and TLS-downgrade
  permissions, all disabled unless a later accepted record names a use;
- connection, ingress-flow, egress-flow, owned-endpoint, direct-subscription, transaction, and eliding
  limits; and
- SMF and TCP keepalive policy.

Application services cannot create endpoints. Their administratively created owned queues still count
toward their endpoint ceiling. The three pinned upstream identities may create only non-durable
endpoints, through a distinct owned queue template selected by
`apiQueueManagementCopyFromOnCreateTemplateName`. The selector, not a generated endpoint-name pattern,
is the authority because it applies to anonymous `#P2P/QTMP` queues. Templates are created before
profiles, carry an empty `queueNameFilter`, and bound access, durability, binds, spool, message size,
unacknowledged deliveries, redelivery, expiry, discard behavior, and dead-message routing.

The exact role limits and keepalive values live in
[operating-parameters.md](../operating-parameters.md#pubsub-client-profiles) with the test or SEMP
instrument that proves each. A field missing from the pinned broker's own
`GET /SEMP/v2/config/spec`, an unreadable readback, or a pinned component that cannot start within its
profile refuses the apply. Public documentation for a newer broker or Agent Mesh release is not a
substitute for the pinned specification and black-box compatibility test.

The factory `default` username is disabled before any other nontransactional mutation. The unused
`discovery` SMF username is not created: Event Management Agent uses a separately scoped SEMP identity,
not an application messaging identity. Every created username is read back bound to its exact owned
profile. Client-connect remains limited by loopback/container networking in the reference stack;
source-CIDR ACL rules require a separately decided stable network range.

`rejectMsgToSenderOnNoSubscriptionMatchEnabled` is true on a project application profile that publishes
Guaranteed families with mandatory durable consumers. It is false on the pinned Agent Mesh, Event Mesh
Gateway, and Event Mesh Tool profiles until a black-box upgrade test proves it compatible: their
broadcast traffic and the direct `AGENT_RESPONSE` would otherwise be negatively acknowledged merely
because no Guaranteed endpoint matched.

### Bounded, truthful SDK behavior

Every project-owned connection:

- rejects an expired certificate, validates the server name, requires TLS 1.2 or newer, and never
  downgrades to plaintext;
- uses ADR-0145's 1,000 ms connection timeout, 2 initial retries, 0 retries per host, 30 active
  reconnection attempts, and 1,000 ms reconnection wait;
- sets an observable, role-derived client name and application description and uses one long-lived
  messaging service per process composition rather than connecting per message;
- configures explicit keepalives and lifecycle listeners; and
- removes readiness on interruption and restores it only after required receivers are rebound and the
  committed outbox is drained.

The project persistent publisher uses a bounded reject buffer of 50, matching one generic outbox drain
batch. SDK memory is not an outbox. A readiness listener gates work when the publisher is not writable.
A broker negative acknowledgement is a definite refusal; local capacity rejection is a distinct
retryable outcome; confirmation is the only success; and a timeout, interruption, or lost receipt is
ambiguous and enters durable reconciliation. Call sites do not collapse these cases into one exception.

Direct telemetry publishers use immediate rejection rather than buffering stale position. Direct
telemetry receivers use a one-message drop-oldest buffer, so the newest observation survives congestion;
every drop is counted. Other direct integration receivers receive an explicit capacity from composition,
50 for the application data plane, and remove readiness on overflow. No project receiver inherits an
elastic SDK default. Client keepalives are sent every 3,000 ms and a limit of 3 unanswered keepalives is
fatal.

Shutdown stops intake, terminates receivers, then publishers, then the messaging service, each with a
15,000 ms grace. Cleanup continues after one failure and reports an aggregate redacted error. Reconnect
exhaustion follows ADR-0145's non-zero process exit rather than claiming recovery.

### Delivery authority

The parsed topic family selects one typed direct, Guaranteed, or request/reply capability before broker
I/O. The direct `GATEWAY_RECORD` uses the direct publisher. The raw RPC reply remains on the pinned
request/reply path. The contract-direct `AGENT_RESPONSE` must leave the pinned gateway through a tested
project-owned direct-output extension; if that extension cannot prove both the Direct wire mode and
failure behavior against the exact pins, the gateway composition is not ready and the application data
plane does not start. Installing a persistent publisher on a Direct-declared family is not accepted as
"close enough."

## Consequences

- Broker capabilities and resources become least-privilege, testable desired state instead of inherited
  factory behavior.
- TLS expiry, memory growth, connection recovery, publisher availability, and publish ambiguity become
  explicit service outcomes.
- A Guaranteed message with a required but missing durable route is negatively acknowledged instead of
  silently accepted and discarded.
- Negative: several profile capabilities take effect only after reconnect, so applying the profiles is
  service-affecting and requires serial live compatibility evidence.
- Negative: application-service connection ceilings are initially derived from the adapter composition
  because the services are still shells. They must be measured and may only be lowered after live
  high-water evidence.
- Negative: current Agent Mesh documentation describes durable production endpoints while the pinned
  wheels construct non-durable queues. The pinned live readback governs this release; an upgrade must
  revisit the profile rather than inheriting either claim.
- Negative: a bounded direct buffer deliberately loses observations under congestion. The metric and
  current-value policy make that loss visible; they do not make Direct delivery durable.

## Alternatives considered

- **Keep the factory profile and rely on ACLs.** Rejected because ACLs govern topics, not endpoint
  creation, transactions, TLS downgrade, connections, flows, or resource consumption.
- **Give every role one shared application profile.** Rejected because publisher-only, receiver-only,
  fleet, dashboard, and upstream temporary-endpoint roles need materially different capabilities.
- **Use elastic SDK buffers behind a bounded database outbox.** Rejected because the SDK heap becomes a
  second unmeasured outbox and can reorder failure semantics around the durable one.
- **Treat every publication exception as a refusal.** Rejected because retrying an ambiguous accepted
  command can create duplicate wire sends; reconciliation and idempotent consumption are required.
- **Change `AGENT_RESPONSE` to Guaranteed solely because the pinned connector uses a persistent
  publisher.** Rejected because SDK message mode without a durable consuming endpoint and trustworthy
  receipt handling is not end-to-end Guaranteed delivery. The implementation must match the accepted
  Direct boundary rather than relabel a defective path.
