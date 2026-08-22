# ADR-0071: Accept the Event Mesh Gateway's temporary data-plane queue, and scope the no-loss claim to exclude it

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) states that critical events "use durable queues, publisher
confirmation, explicit consumer acknowledgement, idempotent handling, and a bounded local outbox".
A salient drone event is a critical event: it is the input the Evidence Fusion path is built on, and
it is what the Event Mesh Gateway carries from the application data plane into the Agent Mesh.

The pinned gateway cannot honour that. `EventMeshGatewayComponent` builds its data-plane input flow
itself, and the endpoint it binds is not configurable:

```python
broker_input_config = {
    "component_module": "broker_input",
    "component_name": f"{self.gateway_id}_data_plane_broker_input",
    "broker_queue_name": f"{self.namespace.strip('/')}/q/gdk/event-mesh-gw/data/{self.gateway_id}/{uuid.uuid4().hex}",
    "create_queue_on_start": True,
    "temporary_queue": True,
    ...
}
```

The name carries a fresh UUID per process, `temporary_queue` is the literal `True`, and neither reads
from `app_config`. `EventMeshGatewayApp.app_schema` exposes 25 parameters and none of them names the
queue. So the gateway creates a new temporary endpoint on every start, and the broker deletes it when
the client disconnects. Nothing the project can put in `agent-mesh/configs/` changes this.

Three facts bound the decision. [ADR-0007](0007-solace-first-implementation-policy.md) requires a
documented capability gap **and** a proving test before project-owned code replaces a supported Solace
component, and holds the official plugins to be the intended integration path.
[ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md) already grants the
`event-mesh-gateway` role `SUBSCRIBE` on the drone-event family and `PUBLISH` on the agent-response
family, and nothing else. And no durable queue exists on the broker at all: the four queue parameters —
maximum spool, maximum redelivery, message time-to-live, and dead-message-queue target — are still
`open` in [operating-parameters.md](../operating-parameters.md), because setting them needs the
backlog-recovery measurement.

The last fact matters for what waiting would buy. Even once those parameters carry numbers and the
provisioner creates the queues, this gateway would still not bind one. Its durability is not blocked
on the project's queue work; it is fixed in the plugin.

## Decision

**Accept the temporary data-plane queue as the ingress mechanism, and scope the no-loss claim in
[CONTRACTS.md](../CONTRACTS.md) so that it explicitly excludes the A2A ingress hop.**

Concretely:

1. Salient-event delivery *into the Agent Mesh* is at-least-once **only while the gateway holds its
   broker connection**. An event published while the gateway is down is delivered to no queue and is
   not redelivered when it returns.
2. Every gateway handler keeps `acknowledgment_policy.mode: on_completion` with
   `on_failure.action: nack` and `on_failure.nack_outcome: rejected`, at the gateway and in each
   per-handler override, exactly as CONTRACTS.md already requires and the semantic-configuration
   validator already enforces as `GATEWAY_POLICY`. That is what bounds the *other* loss mode: a
   message in flight when a handler fails is settled deliberately rather than acknowledged on receipt.
3. The authoritative record of a salient event is the application topic
   `aerial-rescue/v1/{missionId}/drone/{droneId}/event/{eventType}`, not the gateway. The recorder and
   the evidence service subscribe to the drone-event family on their own identities
   ([ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md)), so the mission
   record and the evidence path do not run through the gateway and do not inherit its durability.
4. No mission-critical outcome may be made to depend on a gateway delivery. Agent output is a
   proposal; the deterministic command gateway is the sole publisher of executable commands
   ([ADR-0005](0005-deterministic-command-gateway.md)). A lost salient event costs an agent's opinion,
   never a command, an approval, or an audit record.

## Consequences

- The Phase 0 Event Mesh Gateway spike can proceed now, against the pinned plugin, with no
  project-owned transport and no dependency on the unset queue parameters.
- Restarting the Agent Mesh container silently drops any salient event published during the restart.
  Nothing observes this today; it is a real gap, carried in [TECH_DEBT.md](../../TECH_DEBT.md).
- The no-loss claim becomes narrower and therefore honest. It covers the durable application families
  once their queues exist; it never covered the ingress hop, and now says so.
- A future need for durable agent ingress is a real capability gap under
  [ADR-0007](0007-solace-first-implementation-policy.md), with this record as its evidence. The
  remedy is an owned consumer on a durable queue that invokes the mesh, not a fork of the plugin.
- Queue depth on the Solace Broker Manager is not the instrument for gateway ingress. The disconnect
  and reconnect acceptance flow keeps using a drone's durable command queue, which is unaffected.
- The gateway's temporary endpoint is created by the client, so the `event-mesh-gateway` identity
  needs guaranteed-endpoint-create permission on its client profile. The four existing Agent Mesh apps
  already create temporary queues on the factory `default` client profile, so this is observed
  behaviour rather than a new grant; a change to it would be a change to ADR-0061.

## Alternatives considered

- **Configure a durable queue on the gateway.** Rejected because it is not configurable: the queue
  name, `create_queue_on_start`, and `temporary_queue` are literals in `component.py` and absent from
  `app_schema`.
- **Fork or patch `sam-event-mesh-gateway` 1.1.0.** Rejected under
  [ADR-0007](0007-solace-first-implementation-policy.md). It would put a project-owned copy of a
  supported component on the critical path, and it would have to be re-applied on every upgrade — the
  cost of which [ADR-0001](0001-self-hosted-open-source-agent-mesh.md) takes pains to avoid.
- **Write a project-owned ingress bridge that consumes a durable queue and calls the mesh over A2A.**
  Rejected for now: [ADR-0007](0007-solace-first-implementation-policy.md) requires a documented
  capability gap *and* a proving test, and the spike that would produce that test is the very thing
  this record unblocks. Deciding to build it before running the official component once would be
  deciding without evidence.
- **Wait for the four queue parameters and the backlog-recovery measurement.** Rejected because it
  changes nothing. The plugin would still bind a temporary queue afterwards, so the wait would delay
  the Phase 0 kill criterion and produce no durability.
- **Leave CONTRACTS.md unqualified and treat this as an undocumented limitation.** Rejected. A
  document that disagrees with reality is defective, and a no-loss claim is exactly the kind of claim
  a search-and-rescue reference implementation must not overstate.
