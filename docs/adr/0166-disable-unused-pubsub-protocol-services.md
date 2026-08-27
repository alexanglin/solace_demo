# ADR-0166: Disable unused PubSub+ protocol services

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Extends:** ADR-0044, ADR-0061, ADR-0153, and ADR-0159

## Context

The reference stack publishes only the TLS SMF and TLS SEMP ports on the host, and the shared
application adapter refuses a URL that is not `tcps`. That host boundary does not disable services
inside the Compose network. PubSub+ Software Event Broker's default configuration enables common
protocol services, and the pinned broker's non-secret SEMP readback showed plaintext and TLS variants
of AMQP, MQTT, REST ingress, SMF, and Web Transport enabled in the `default` Message VPN.

A compromised peer container could therefore bypass the shared adapter and attempt a plaintext or
otherwise unused protocol with its own credential. Client-profile TLS-downgrade refusal does not close
a separately enabled plaintext listener. Solace recommends exposing only the protocols a production
use case requires, and its Message VPN controls independently enable or disable plaintext and TLS
variants. See the official
[software-broker setup guidance](https://docs.solace.com/Get-Started/tutorial/event-broker-set-up.htm),
[service-management reference](https://docs.solace.com/Services/Managing-Services.htm), and
[Message VPN configuration](https://docs.solace.com/Features/VPN/Configuring-VPNs.htm).

The current application data plane uses native SMF over TLS. Browser traffic terminates at the
dashboard HTTP gateway, Agent Mesh uses SMF, and the stack has no AMQP, MQTT, REST-delivery, or broker
Web Transport client. WSS is a documented restricted-network alternative, not an active listener in
this topology.

## Decision

Broker desired-state application closes the protocol surface at both supported configuration levels.

At the broker level it:

- keeps SMF enabled;
- disables AMQP, MQTT, REST ingress, REST egress, and Web Transport; and
- leaves SEMP TLS and the health-check service available for management and readiness.

At the application Message VPN it:

- keeps SMF TLS enabled;
- disables plaintext SMF; and
- disables plaintext and TLS AMQP, MQTT, MQTT-over-WebSocket, REST ingress, and Web Transport.

The provisioner requires the exact `Broker` and `MsgVpn` members from the pinned broker's own
`GET /SEMP/v2/config/spec` before its first mutation. It disables and verifies the factory messaging
identity first, then patches and immediately reads back both protocol-service objects. Missing fields,
partial state, malformed readback, or any mismatched flag refuses the entire apply. Dependent services
remain unready after a refused apply.

No environment variable or service-local adapter may re-enable a protocol. A restricted-network WSS
profile must be introduced by a new accepted deployment decision that enables only Web Transport TLS,
publishes only its TLS port, preserves hostname and certificate validation, adds an exact client-profile
and ACL projection, and proves plaintext and unused-protocol negatives live. AMQP, MQTT, or REST support
has the same activation boundary.

## Consequences

- A peer container cannot use an enabled plaintext SMF listener to bypass the shared `tcps` adapter.
- Unused protocol parsers and listeners are removed from the reachable application network surface.
- The broker and Message VPN configuration are checked against the exact installed SEMP schema rather
  than a newer online reference.
- Negative: a future client that expects WSS, AMQP, MQTT, or REST fails until the deployment decision,
  broker desired state, authorization, and live evidence are added together.
- Negative: the protocol patch is another nontransactional SEMP step. A partial apply is not treated as
  provisioned; rerunning the complete convergent apply and reading it back is the recovery path.

## Alternatives considered

- **Rely on unpublished host ports.** Rejected because other Compose services share a network with the
  broker and can reach listeners that are not mapped onto the host.
- **Rely on the project Python adapter.** Rejected because a compromised process can import another
  client or open a socket directly; broker configuration is the independent enforcement boundary.
- **Keep every TLS protocol enabled.** Rejected because encryption does not make an unused parser or
  authorization surface necessary.
- **Keep WSS enabled as a ready fallback.** Rejected because no current port, composition, ACL evidence,
  or live negative test implements that deployment profile.
- **Disable SMF globally and use another protocol.** Rejected because the pinned Python and Agent Mesh
  runtimes use native SMF, including Direct, Guaranteed, and request/reply paths.
