# ADR-0191: Reserve one subscription per session for the SDK reply inbox

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Amends:** ADR-0153, as to how the direct-subscription ceiling is rendered

## Context

[ADR-0153](0153-own-bounded-least-privilege-pubsub-clients.md) provisions one client profile per
principal whose direct-subscription ceiling `S` equals the application subscriptions the role binds;
the numbers live in [operating-parameters.md](../operating-parameters.md#pubsub-client-profiles).

The first live apply of that table, on 2026-08-27 against the pinned broker image and the reference
roster, refused every connection from a profile with `S = 0`. The broker event log recorded
`CLIENT_CLIENT_MAX_SUBSCRIPTIONS_EXCEEDED` for `fleet-simulator`, `event-mesh-tool`,
`evidence-service`, `event-mesh-gateway`, and `agent-mesh-agent`, and the SDK reported
`SOLCLIENT_SUBCODE_SUBSCRIPTION_TOO_MANY` on the topic `#P2P/v:broker/<id>/<client>/>`. That topic is
the reply inbox the pinned Python API subscribes on every session at connect; the Agent Mesh connector
uses the same C API and was refused the same way. The API exposes no property that withholds the inbox,
and the broker counts it against `maxSubscriptionCount`.

Two further observations fix the arithmetic. A connected `recorder` (`S = 3`, two application patterns)
held exactly that one inbox subscription beside its two. The `command-gateway` (`S = 2`, two application
patterns) connected and was then refused its second application subscription,
`aerial-rescue/v1/*/agent/response/*`. So a zero ceiling refuses the connect, and an exact ceiling
refuses the last application subscription.

The CI live job never saw this because the merged branch had not been pushed; the pre-merge stack ran
on the factory profile's ceiling of 50 000.

## Decision

The provisioned `maxSubscriptionCount` is the role's application direct-subscription ceiling plus one
reserved subscription for the SDK reply inbox, for every profile whose connection ceiling is above
zero. The `discovery` profile permits no connection and stays at zero.

The operating-parameters table keeps `S` as application subscriptions and states the rendered value.
The provisioning module carries the reservation as one named constant with its reason, and a total
provisioning test pins the rendered value for every principal. Nothing else in the profile changes, and
the ceiling stays exact: a role still cannot hold one application subscription more than its row.

## Consequences

- Every owned identity connects again, and each binds exactly its table subscriptions.
- The inbox is a broker-generated `#P2P` topic that cannot match an application topic, so it needs no
  ACL exception and widens no grant.
- Should a later pinned SDK stop installing the inbox, the reservation becomes one unused subscription;
  the live authorization suite is the instrument that shows whether removing it is safe.
- Rejected: raising each `S` by one in the table, which would make the documented ceiling untrue and
  hide the reason; asking the SDK to withhold the inbox, which the pinned API cannot do and the upstream
  Agent Mesh connector would not honour.
