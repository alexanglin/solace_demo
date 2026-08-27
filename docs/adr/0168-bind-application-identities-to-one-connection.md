# ADR-0168: Bind application identities to one long-lived connection

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Extends:** ADR-0153 and ADR-0159

## Context

ADR-0153 initially derived application client-profile connection ceilings while the services were
import-and-exit shells. Those provisional ceilings treated each Guaranteed consumer flow as though it
needed a separate broker connection: the command gateway allowed four connections, the dashboard
seven, the evidence service three, and the recorder eleven. The completed compositions instead put
every publisher and named receiver for one process on one owned, long-lived Solace
`MessagingService`. Consumer flows and connections are different broker resources.

Leaving the provisional values would permit a compromised process, accidental parallel composition,
or credential reuse to open several independent sessions even though no supported runtime needs them.
Solace recommends reusing established sessions and independently bounding client connections and
flows. Its client-profile reference exposes separate maximum-connection, ingress-flow, and
egress-flow controls; using the connection limit as a proxy for the flow count weakens that boundary.
See the official
[API best practices](https://docs.solace.com/API/API-Developer-Guide/C-API-Best-Practices.htm) and
[client-profile settings](https://docs.solace.com/Cloud/client-profiles.htm).

## Decision

Each project-owned application messaging username permits exactly one total connection and one SMF
connection:

- `fleet-simulator`;
- `command-gateway`;
- `dashboard-api`;
- `evidence-service`; and
- `recorder`.

Their distinct ingress-flow, egress-flow, endpoint, and subscription ceilings remain unchanged and
continue to describe the endpoints composed on that single session. Web connections remain zero.
Construction-count tests prove one `MessagingService` per process graph, the provisioning table and
SEMP request/readback tests prove the ceiling, and live client inventory must show at most one
connection for each identity.

The three pinned upstream identities do not inherit this rule. Agent Mesh can run several agents
under its one upstream role, and Event Mesh Gateway owns separate control- and data-plane behavior;
their existing exact ceilings remain until pinned black-box inventory proves a lower value. Event Mesh
Tool already permits one. The disabled discovery role remains zero.

A horizontally scaled application replica may not reuse one of these usernames. It needs a separately
named identity, ACL/profile projection, secret, deployment decision, and client-inventory evidence so
one leaked replica credential cannot consume another replica's connection budget. During a restart,
the replacement waits for the old session to close; temporarily raising the ceiling is not a supported
rollout shortcut.

## Consequences

- A second application session using the same credential is refused by the broker rather than merely
  detected in monitoring.
- Flow capacity remains sufficient because all named receivers share the one session and retain their
  independently derived egress-flow ceilings.
- Credential reuse between replicas becomes visible and fail-closed.
- Negative: an uncleanly terminated old session can delay its replacement until the broker releases
  the connection. Bounded keepalive and reconnect behavior make that failure observable, and the
  supervisor must not bypass it by widening the profile.

## Alternatives considered

- **Keep the provisional per-flow connection ceilings.** Rejected because a flow is not a connection,
  and the concrete compositions now prove that the extra sessions have no supported use.
- **Allow two connections for rolling restart overlap.** Rejected because the current Compose
  deployment does not perform zero-downtime rolling replacement, and sharing one credential between
  concurrent replicas weakens ownership.
- **Give each durable queue its own username and connection.** Rejected because it multiplies secrets,
  connections, and failure domains without improving the transaction or settlement boundary.
