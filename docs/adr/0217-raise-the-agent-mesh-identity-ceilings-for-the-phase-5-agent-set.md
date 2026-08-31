# ADR-0217: Raise the Agent Mesh identity ceilings for the Phase 5 agent set

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin
- **Amends:** ADR-0153 and ADR-0196, as to the `agent-mesh-agent` connection and endpoint ceilings

## Context

Phase 5 adds two agents to the mesh, `SectorPlanner` and `EvidenceFusion`, taking the connector from
four apps on the `agent-mesh-agent` identity to six. That identity was provisioned for exactly what
four apps used: nine connections and five owned endpoints, and a live reading on 2026-08-31 confirmed
it sat at both ceilings with no headroom.

ADR-0196 raised the endpoint ceiling to five after a short ceiling refused the coordinator's
request/response queue and restart-looped the container. Its context attributes that fifth endpoint to
the coordinator's reply queue; the live readback recorded in
`release-evidence/phase-3/merged-runtime-first-run.md` contradicts that attribution while confirming
the value, and a further reading on 2026-08-31 agrees with the readback: `agent-mesh-agent`'s five
were the three `a2a` queues plus the Web UI's gateway and visualization queues, and the reply queue
belongs to `event-mesh-tool`. That record is Accepted and its prose is not edited; this one carries
the correction.

## Decision

The `agent-mesh-agent` ceilings become thirteen connections and seven owned endpoints. Both are the
measured steady state of the six-app mesh, not a prediction with margin: a deliberately generous probe
ceiling of twenty-four and twelve was provisioned first, the mesh was brought up and driven through a
complete workflow execution, the high-water was read back over SEMP, and the exact observed values
were then provisioned and confirmed to start the mesh. Flows, subscriptions, the queue template, and
the other two upstream identities are unchanged.

The VPN's effective endpoint ceiling of 100 is not raised, and the Solace Cloud Developer-class parity
claim is unchanged. The reference topology reaches 99 of 100 with the two new agents.

## Consequences

- The mesh has one free endpoint. That is enough only because ADR-0216's drain removes the
  restart overlap that previously made a recreate contend with its own predecessor; a recreate that
  skips the drain will still exhaust the ceiling.
- Two more processes share one broker identity. ADR-0061's rule that a separately deployed process
  gets its own identity is unchanged; these are additional apps in the existing connector, exactly as
  the Orchestrator, workflow, and Web UI already are.
- Rejected: raising `system_scaling_maxconnectioncount` above 100 for restart-overlap tolerance. It
  is what fixes `maxEffectiveEndpointCount` at 100, so raising it would buy headroom, but it would
  also restate a demo claim that the deployment matches a Developer-class service, and the drain
  already removes the failure it would have covered.
- Rejected: reclaiming the Web UI's visualization queue. The pinned gateway creates it
  unconditionally at startup, so the only levers are dropping the Web UI or subclassing it, both
  larger changes than the one slot they return.
