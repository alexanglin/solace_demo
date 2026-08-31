# ADR-0223: Raise the Agent Mesh identity ceilings for the Platform service

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin
- **Amends:** ADR-0217, as to the measured connection and endpoint ceilings

## Context

ADR-0217 set the `agent-mesh-agent` ceilings at thirteen connections and seven endpoints, measured
against the six apps the Phase 5 agent set ran. [ADR-0222](0222-register-the-mesh-s-local-model-in-the-platform-service.md)
adds the Platform service as an eighth app in the same connector process, on the same identity, and
the measured figures move: the service opens its own broker session, and its model-configuration
bootstrap listener opens a second one with a durable-named temporary endpoint of its own.

The old ceiling was not merely tight, it was exact. Seven endpoints was precisely what six apps
used, so the eighth app's two endpoints could not be admitted at all and the container restart-looped
on `SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE` — the same subcode ADR-0196 and ADR-0216 are
about, arriving this time from the per-identity ceiling rather than from a recreate race.

Two limits were confused during that diagnosis and are worth separating here, because either alone
reproduces the same subcode and the same restart loop. The per-identity ceiling is one. The other is
the broker's own endpoint budget: with the twenty-three-member roster the deployment held eighty-nine
durable queues, and adding the Platform service's two endpoints put the total at one hundred and one,
which the broker refused irrespective of how generous the client profile was. Raising the client
profile alone therefore appeared to change nothing, which is what made the per-identity ceiling look
innocent. [ADR-0118](0118-provision-command-queues-only-for-executable-members.md) conformance freed
six of those durable endpoints and separated the two.

## Decision

**The `agent-mesh-agent` ceilings become sixteen connections and nine endpoints**, taken the way
ADR-0217 took its own: a generous probe ceiling was provisioned, the eight-app process was brought to
healthy against it, the identity was read back over SEMP, and exactly what was observed was then
provisioned.

The nine endpoints are the five agent A2A queues, the Web UI's gateway and visualization queues, the
Platform service's own session queue, and its bootstrap listener's queue. No other ceiling moves:
egress flows, ingress flows, and subscriptions stay at one, one, and zero, and no other identity
changes.

## Consequences

**The ceiling stays exact rather than generous, and that is deliberate.** Nine endpoints is what the
eight-app process uses, so a recreate that skips the drain still exhausts it, exactly as ADR-0216
describes. The drain between stop and start is not optional and this decision does not make it so.

**A cold start at the provisioned ceiling was observed, not inferred.** After provisioning sixteen and
nine, a stop, drain, and start brought the container to healthy in twenty seconds with zero restarts,
against eighty-three durable and twelve non-durable endpoints — ninety-five of the broker's budget.

**A ninth app will need this measured again.** The figures are per-app-set rather than per-role, which
is the third time this identity's ceilings have moved for that reason. Anything that adds a broker
session or a temporary endpoint to this process invalidates them.
