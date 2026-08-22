# ADR-0068: The command-gateway request/reply channel is schema-bound RPC, recorded as a CloudEvent

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) opens its envelope section with an unqualified claim: "Every application
event is a CloudEvents 1.0 JSON object in structured mode, carried as the broker message payload, with a
**closed** member set: twelve required members, two optional members, and nothing else." Twelve of those
members are required, and four of them — `id`, `time`, `sequence`, and `traceparent` — can only be
produced by a component that holds a clock, a unique-identifier source, and a per-producer counter.

The Event Mesh Tool holds none of them. `sam_event_mesh_tool.tools._build_payload_and_resolve_params`
composes its outbound payload from exactly four sources, and no others:

```python
if "context_expression" in param_config:
    value = get_data_value(a2a_context, expr, True)
elif param_name in params:          # supplied by the model
    value = params[param_name]
elif "default" in param_config:     # a literal in the configuration
    value = param_config["default"]
else:
    continue
```

`a2a_context` carries task, session, client, and reply-topic values set by
`solace_agent_mesh.agent.adk.runner`; it carries no timestamp, and its `logical_task_id` is per task
rather than per tool call, so it cannot serve as a per-request `id` either. The remaining two sources
are a literal and a model. A literal cannot produce a fresh instant. A model must not: a `time` a
language model invented is not an observation, and [ADR-0008](0008-abstention-over-recorded-substitution.md)
and [ADR-0005](0005-deterministic-command-gateway.md) both rest on model output being treated as
untrusted input rather than as fact.

So the gateway request on `aerial-rescue/v1/{missionId}/gateway/request/{operation}` cannot be a
CloudEvent, and no configuration of the pinned plugin makes it one. The choice is not *how* to build the
envelope; it is what the family actually carries.

There is a second, independent reason the request is not an event. A CloudEvent in this system is a
statement that something happened. A gateway request is a question awaiting an answer, correlated by the
broker's request/reply machinery and meaningless without its reply. The eleven families in
[CONTRACTS.md](../CONTRACTS.md) are otherwise all one-way notifications.

## Decision

**The gateway-request and gateway-response families carry schema-bound RPC, not application events. The
command gateway additionally publishes every response it sends as a CloudEvent on the gateway-response
family.**

Concretely:

1. `CONTRACTS.md`'s envelope rule is scoped: it governs the nine notification families. The two gateway
   families carry an RPC request and an RPC reply, defined by
   `schemas/v1/rpc/gateway-request.schema.json` and `schemas/v1/rpc/gateway-response.schema.json`.
2. Both RPC bodies stay inside the integer-only canonical profile of
   [ADR-0027](0027-integer-only-canonical-serialization.md) — canonical keys, no floating-point value,
   bounded strings — and are decoded through the canonical decoder, so a repeated key is a refusal
   rather than a last-value-wins merge. They are golden-fixtured and registered in
   `schemas/contract-manifest.toml` under the same one-reason rule as every other artifact
   ([ADR-0038](0038-reserved-host-schema-identity-and-one-reason-fixtures.md)).
3. Each body carries `rpcVersion`, an integer, inside the bytes. The topic's `v1` level already versions
   the family, but an RPC body has no `dataschema` member to identify itself by, so the version travels
   with the value.
4. The command gateway publishes its answer twice: once to the requestor's reply topic, which is
   transport and carries the RPC body alone, and once as a CloudEvent on
   `aerial-rescue/v1/{missionId}/gateway/response/{requestId}` whose `data` is that same RPC body. The
   CloudEvent is the record; the reply is the answer.
5. The command gateway is the producer of that CloudEvent, so it supplies the four members the tool
   could not: `id` and `traceparent` from an injected identifier source, `time` from an injected clock,
   and `sequence` from its own producer-scoped counter. Its `source` is
   `urn:aerial-rescue:service:command-gateway`.

## Consequences

- The Phase 0 egress spike can proceed against the pinned plugin with no project-owned transport, and
  the request the tool sends is validated against a committed schema rather than trusted.
- `CONTRACTS.md` gains a second wire shape, so a reader must now learn two. That is the honest cost of a
  request/reply channel existing at all; the alternative was a document that disagrees with the runtime.
- The recorder, the dashboard, and the audit timeline see every command-gateway answer without knowing
  anything about the Event Mesh Tool or about Solace request/reply, because the record is an ordinary
  CloudEvent on a family they already consume.
- The command gateway publishes twice per request. On the failure path the two can diverge: the reply
  can be published and the record fail, or the reverse. The record is the weaker of the two — losing it
  costs an audit line, never an answer or a command — so the reply is published first and a failed
  record is a logged, typed failure rather than a retry that could double-answer.
- The command gateway acquires a clock, an identifier source, and a sequence counter. All three are
  injected, so its policy stays pure and deterministic under test, which
  [ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) requires of a Tier 1 module.
- A `sequence` counter that resets when the process restarts will re-emit numbers it has used before.
  That is acceptable only because `sequence` is defined as a stale-update filter within one producer's
  stream and is explicitly never the timeline's ordering authority
  ([ADR-0003](0003-postgres-durable-mission-store.md)); durable sequence state arrives with the store.

## Alternatives considered

- **Require the request to be a CloudEvent and have the tool build it.** Rejected: impossible. The
  plugin has no clock and no identifier source, and the only remaining producer of those members would
  be the language model.
- **Have the command gateway synthesize the missing members on receipt and treat the result as the
  received envelope.** Rejected. The envelope's value is that it is what the producer sent; an envelope
  whose `id`, `time`, and `traceparent` were invented by the receiver attests to nothing, and it would
  make `source` a lie. It would also mean the digest of a "received event" was computed over bytes that
  never crossed the wire.
- **Carry the RPC body as an opaque string and validate it in the command gateway alone.** Rejected: it
  puts the contract in one implementation instead of in a committed schema, so the TypeScript side could
  not validate it and no golden fixture could pin it.
- **Publish only the reply and skip the CloudEvent record.** Rejected. The audit timeline is a release
  criterion, and a command-gateway answer that no consumer other than the asking agent can observe is
  exactly the kind of gap [SAFETY.md](../SAFETY.md) exists to close.
- **Publish only the CloudEvent and let the tool subscribe to the family.** Rejected: the connector
  correlates on its own reply topic and user properties and would never see it, and granting the tool
  the whole gateway-response family is authority it does not need.
