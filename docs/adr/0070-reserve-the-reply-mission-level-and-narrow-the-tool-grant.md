# ADR-0070: Reserve `reply` as the reply channel's mission level, and narrow the Event Mesh Tool's grant to it

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin

## Context

[ADR-0068](0068-command-gateway-request-reply-is-schema-bound-rpc.md) puts request/reply RPC on the
two gateway families. Where the *reply* goes is not the project's choice: Solace AI Connector 3.3.12
computes it, once per session, in `BrokerRequestResponse.__init__`:

```python
self.requestor_id = str(uuid.uuid4())
self.reply_queue_name = f"{self.response_queue_prefix}{self.requestor_id}"
self.response_topic = f"{self.response_topic_prefix}{self.requestor_id}{self.response_topic_suffix}"
...
self.broker_properties["subscriptions"] = [
    {"topic": self.response_topic, "qos": 1},
    {"topic": self.response_topic + "/>", "qos": 1},
]
```

Three facts follow, and each of them constrains the answer.

**The reply topic is a per-process constant, not a per-request one.** It is fixed when the tool's
session is created, before any mission exists, and every reply to that requestor arrives on it;
requests are told apart by a `request_id` carried in user properties. So the mission level of
`aerial-rescue/v1/{missionId}/gateway/response/{requestId}` cannot be a mission. The requestor
identifier is a lowercase UUID, which is inside the IDENTIFIER rule, so the last level can be.

**The connector subscribes one level deeper than the family.** It binds a temporary queue to both
the reply topic and `reply topic + "/>"`. `event-mesh-tool` currently holds `SUBSCRIBE` on the
gateway-response family, which
[ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md) renders as the topic
exception `aerial-rescue/v1/*/gateway/response/*`. That exception covers exactly six levels; the
`/>` subscription reaches seven or more and is not covered, so the bind would be denied and the tool
would never receive a reply.

**Only `response_topic_prefix` is configurable.** It is a `SessionConfig` field the tool passes
through from `event_mesh_config`. The UUID, the `/>` subscription, and the temporary reply queue are
not configurable, the same way the Event Mesh Gateway's data-plane queue is not
([ADR-0071](0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)), and
[ADR-0007](0007-solace-first-implementation-policy.md) forbids forking a supported component without
a proving test.

## Decision

**Reserve the identifier `reply` as the mission level of the command-gateway reply channel, and
replace the Event Mesh Tool's gateway-response family grant with an exception scoped to it.**

Concretely:

1. `RESERVED_REPLY_MISSION` is the literal `reply`, defined once in `packages/contracts` beside the
   topic grammar. The tool's `response_topic_prefix` is `aerial-rescue/v1/reply/gateway/response`,
   which renders the reply topic `aerial-rescue/v1/reply/gateway/response/{requestorId}`.
2. **No application event may claim it.** `envelope.parse_envelope` refuses an envelope whose
   `subject` is the reserved identifier, so a mission can never be named `reply` and a CloudEvent
   can never be published on the reply channel. A *topic* may carry it — the reply channel is a
   topic — but an event may not name it as its mission.
3. `event-mesh-tool` holds **no** `SUBSCRIBE` grant on the gateway-response family. It holds one
   reply-scoped topic exception instead, `aerial-rescue/v1/reply/gateway/response/>`, built by
   `packages/broker` the way `a2a_subscription` already builds the one exception that lies outside
   the family model. The exception count on the broker is unchanged: one is replaced, not added.
4. `command-gateway` keeps `PUBLISH` on the gateway-response family,
   `aerial-rescue/v1/*/gateway/response/*`, which covers both the reply channel and the CloudEvent
   record on a real mission. No new grant is needed.
5. The command gateway refuses any reply topic whose mission level is not the reserved identifier.
   The reply topic arrives in the requestor's own user properties and is therefore untrusted input;
   this is what stops an injected value aiming the sole publisher of executable commands at an
   arbitrary topic.

## Consequences

- The tool's authority **shrinks**. It could previously subscribe to every mission's gateway
  responses; it can now reach only the reply channel. The `>` at the tail is wider than a `*` in one
  dimension and far narrower in another, and the dimension it narrows is the one that carries
  mission data.
- The reply channel is legible on the wire. `aerial-rescue/v1/reply/gateway/response/{requestorId}`
  says what it is, stays inside the topic grammar, and needs no twelfth family and no second
  namespace root.
- Negative: a reader who does not know this record will read `reply` in the mission level as a
  mission. The envelope refusal is the compensating control — the name cannot mean a mission
  anywhere else — but the topic string alone is momentarily misleading.
- Negative: the reserved identifier is one more thing a future mission-identifier generator must not
  emit. It is enforced at the envelope rather than at the topic layer, because the reply channel's
  own topic must remain formattable and parseable.
- Negative: the `>` exception permits the tool to subscribe below the reply channel, to topics no
  producer in this system will ever publish. That is authority granted to satisfy a client library's
  subscription shape rather than a need, and it is accepted only because the levels beneath a
  requestor identifier are unreachable by the topic grammar.
- The reply channel carries no mission, so a recorder cannot partition replies by mission from the
  topic alone. It does not need to: the CloudEvent record on the real mission's gateway-response
  topic is the authoritative one, and the reply is transport.

## Alternatives considered

- **Keep the family grant and widen its tail to `aerial-rescue/v1/*/gateway/response/>`.** Rejected:
  it makes the exception wider in both dimensions and leaves the tool able to read every mission's
  answers, which is authority it does not need and cannot use.
- **Give the reply channel its own namespace root outside the taxonomy**, such as
  `aerial-rescue-reply/v1/...`. Rejected: it adds a third namespace root to reason about beside the
  application and A2A ones, and `packages/contracts` would need a second grammar for a channel that
  fits the one it has.
- **Drop the mission level from the gateway-response family.** Rejected: every family shares
  `aerial-rescue/v1/{missionId}/...`, `format_topic` and `parse_topic` are built on that, and the
  CloudEvent record genuinely belongs to a mission.
- **Set `wait_for_response: false` and let the tool fire and forget.** Rejected: it would prove no
  request/reply at all, and [ARCHITECTURE.md](../ARCHITECTURE.md) names request/reply as the
  capability the Event Mesh Tool is pinned for.
- **Have the command gateway publish to whatever reply topic the request names.** Rejected outright:
  the reply topic is requestor-supplied and therefore untrusted, and obeying it would let a caller
  aim the only component permitted to publish executable commands at any topic its ACL allows.
- **Refuse the reserved identifier in `topics.py` as well.** Rejected: `format_topic` and
  `parse_topic` must both handle the reply channel's own topic, so refusing it there would make the
  channel unaddressable by the code that has to address it.
