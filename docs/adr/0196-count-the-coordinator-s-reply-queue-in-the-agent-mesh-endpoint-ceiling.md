# ADR-0196: Count the coordinator's reply queue in the Agent Mesh endpoint ceiling

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Amends:** ADR-0153, as to the `agent-mesh-agent` owned-endpoint ceiling

## Context

[ADR-0153](0153-own-bounded-least-privilege-pubsub-clients.md) gives the pinned `agent-mesh-agent`
identity an owned-endpoint ceiling of four and keeps the upstream identities' "existing exact
ceilings ... until pinned black-box inventory proves" another value. The merged runtime's first
composition (2026-08-28, `release-evidence/phase-3/merged-runtime-first-run.md`) is that inventory.
A 75 s read-only SEMP poll across one Agent Mesh restart cycle showed the steady state: seven
non-durable queues, `agent-mesh-agent` owning four (the `a2a` queues of MissionCoordinator,
MissionResponse, and Orchestrator plus the web-ui gateway queue), `event-mesh-gateway` two, and
`event-mesh-tool` one — every identity exactly at its ceiling. The MissionCoordinator's
request/response session, which its configuration enables for peer delegation, then asked for a
`reply-queue/…` as `agent-mesh-agent`'s fifth endpoint; the broker refused it with
`SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE`, the agent's initialization signalled failure,
and the container restart-looped.

## Decision

The `agent-mesh-agent` owned-endpoint ceiling is five: the four steady-state queues plus the
coordinator's reply queue. Connections, flows, subscriptions, and the other two upstream identities'
ceilings are unchanged. The audited provisioning table and its total test carry the value; the
operating-parameters rows state the ceiling and that up to eight bounded upstream temporaries coexist
with the reference topology (89 + 8 = 97 of the VPN's 100 effective endpoints; 91 + 8 = 99 with the
data-plane probe's drone).

## Consequences

- The MissionCoordinator can delegate to a peer, which the demo's "agents communicating" scene needs.
- One more temporary endpoint counts against the VPN's ceiling; the reference topology still fits,
  and the CI-only 28-drone probe roster still does not coexist with it.
- Rejected: disabling the coordinator's request/response session, which removes the delegation the
  demo exists to show.
