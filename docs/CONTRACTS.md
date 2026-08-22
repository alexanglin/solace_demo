# Contracts and public interfaces

> **Authority:** this document is the single home for the application event envelope, the topic taxonomy,
> and the local HTTP API. `docs/IMPLEMENTATION_PLAN.md` and `AGENTS.md` reference it and must not restate
> it ([ADR-0016](adr/0016-documentation-set-split.md)). Where this document and an `Accepted` ADR
> disagree, the ADR governs.
>
> **Related:** [ADR-0014](adr/0014-application-events-separate-from-a2a.md) (namespace separation),
> [ADR-0006](adr/0006-proposal-bound-single-use-approvals.md) (approval semantics),
> [ADR-0003](adr/0003-postgres-durable-mission-store.md) (timeline ordering authority),
> [ADR-0024](adr/0024-local-operator-api-boundary.md) (local operator boundary).
> Numeric parameters live in [operating-parameters.md](operating-parameters.md).

## Event envelope

Every application **event** is a CloudEvents 1.0 JSON object in structured mode, carried as the broker
message payload, with a **closed** member set: twelve required members, two optional members, and
nothing else ([ADR-0037](adr/0037-cloudevents-envelope-profile.md)). That governs the nine notification
families below. The two gateway families carry request/reply RPC rather than events, defined under
[Command-gateway request and reply](#command-gateway-request-and-reply)
([ADR-0068](adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md)). A JSON `null` is never read as
absence; an optional member is present or omitted. `packages/contracts` validates the profile as a pure
function, `envelope.parse_envelope`, and every refusal is a typed value naming the member at fault. The
bounds live in [operating-parameters.md](operating-parameters.md#topic-and-envelope-bounds).

| Member | Required | Rule |
| --- | --- | --- |
| `specversion` | yes | the constant `1.0` |
| `id` | yes | an identifier (the IDENTIFIER rule of the topic taxonomy); `source` plus `id` is unique, and `id` is the idempotency key |
| `source` | yes | `urn:aerial-rescue:<producerKind>:<producerId>`, where `producerKind` is a KIND and `producerId` matches `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; the producer scopes `sequence` |
| `type` | yes | derived from the topic as the taxonomy defines, and bound to a payload schema; a well-formed but unbound type is refused |
| `subject` | yes | the mission identifier; must equal `data.missionId` |
| `time` | yes | the canonical instant `YYYY-MM-DDTHH:MM:SS.sssZ` naming a real calendar date |
| `datacontenttype` | yes | the constant `application/json` |
| `dataschema` | yes | the `$id` of the payload schema bound to `type`, of the form `https://aerial-rescue.invalid/schemas/v1/payload/<name>.schema.json`; its `v1` segment is the schema version |
| `data` | yes | an object inside the canonical profile below, repeating `missionId` and every identifier the topic names |
| `sequence` | yes, extension | a fifteen-digit zero-padded decimal string, so string order is numeric order; scoped to its producer, used only to reject stale updates within one stream, never to order the timeline ([ADR-0003](adr/0003-postgres-durable-mission-store.md)) |
| `correlationid` | yes, extension | an identifier, carried across the A2A gateway boundary ([ADR-0014](adr/0014-application-events-separate-from-a2a.md)) |
| `causationid` | optional, extension | an identifier |
| `traceparent` | yes, extension | W3C Trace Context version `00` in lowercase hexadecimal with non-zero trace and parent identifiers |
| `tracestate` | optional, extension | printable ASCII, bounded in length |

Refusals come in a fixed order, which TypeScript reimplements: not an object; an unknown member,
`data_base64` included; a missing required member; a member outside its rule; `data` outside the
canonical profile; an unbound `type`; a `dataschema` other than the bound one; a `subject` that is not
the payload's mission. Ingress text is decoded through the canonical decoder, so a repeated key is
refused rather than merged. The broker adapter then calls `envelope.check_topic_binding` with the topic
the message actually arrived on: the type derived from that topic, its mission identifier, and each
identifier it names must agree with the envelope, or the event is refused as `TOPIC_BINDING`.

Some rules are validator-only because a JSON Schema cannot express them: a calendar-invalid `time`, an
unbound `type`, the schema and subject bindings, the topic binding, a repeated key, a string whose byte
length exceeds the bound while its code-point count does not, and a real number with a zero fraction such
as `1.0`, which Draft 2020-12 `integer` admits and the profile refuses. Both language validators unit-test
those rules; they are never golden negatives.

Payloads are defined by versioned JSON Schemas. Python and TypeScript must consume the same schemas and shared golden fixtures. Breaking schema changes require a new major topic and schema version.

## Topic taxonomy

Use the following topic families:

```text
aerial-rescue/v1/{missionId}/operator/command/{commandType}
aerial-rescue/v1/{missionId}/operator/approval/{decision}
aerial-rescue/v1/{missionId}/drone/{droneId}/telemetry
aerial-rescue/v1/{missionId}/drone/{droneId}/event/{eventType}
aerial-rescue/v1/{missionId}/drone/{droneId}/command/{commandType}
aerial-rescue/v1/{missionId}/drone/{droneId}/command-result/{commandId}
aerial-rescue/v1/{missionId}/gateway/request/{operation}
aerial-rescue/v1/{missionId}/gateway/response/{requestId}
aerial-rescue/v1/{missionId}/agent/proposal/{agentName}/{proposalType}
aerial-rescue/v1/{missionId}/agent/response/{agentName}
aerial-rescue/v1/{missionId}/audit/{recordType}
```

Every variable level obeys one of four rules
([ADR-0036](adr/0036-ascii-topic-grammar-bound-to-event-type.md)), and `packages/contracts` (`topics.py`)
is the only producer and parser of these topics:

| Rule | Levels | Form |
| --- | --- | --- |
| IDENTIFIER | `missionId`, `droneId`, `commandId`, `requestId`; also the envelope's `id`, `subject`, `correlationid`, `causationid` | `^(?:[a-z0-9]\|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$`: lowercase ASCII letters, digits, interior hyphens |
| KIND | `commandType`, `eventType`, `proposalType`, `recordType`, `operation`; also `producerKind` in `source` | `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`, bounded in length; `commandType` is closed by the command-authority table and `operation` by the gateway-operation table, both in `packages/domain` ([ADR-0041](adr/0041-deny-by-default-command-authority-table.md), [ADR-0069](adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md)); `eventType`, `proposalType`, and `recordType` stay open until the domain modules that define them land |
| AGENT_NAME | `agentName` | `^[A-Za-z0-9_]{1,64}$`, the ASCII subset of what Agent Mesh 1.28.7 accepts as an agent name; Solace topics are case-sensitive, so two names differing only in case are two topics |
| DECISION | `decision` | exactly `approve` or `reject` |

The CloudEvents `type` of an event is derived from its topic: drop the IDENTIFIER and AGENT_NAME levels,
join the rest with `.`, and prefix `aerial-rescue.v1.`. So `aerial-rescue/v1/m1/drone/d1/event/salient`
has the type `aerial-rescue.v1.drone.event.salient`, and `aerial-rescue/v1/m1/drone/d1/command-result/c1`
has `aerial-rescue.v1.drone.command-result`. A topic is recovered from its type together with the
identifiers a producer holds.

Parsing refuses in a fixed order, which TypeScript reimplements: not a string; longer than the broker
bound; a `*` or `>` anywhere; a prefix other than `aerial-rescue/v1`; a shape matching no family; then
each level against its rule in template order, so an empty, `#`-, `!`-, or `+`-bearing level fails the
rule of the parameter it occupies. Formatting never emits a wildcard, a reserved prefix, an empty level,
or a trailing separator. Subscription strings, which do carry wildcards, belong to the broker adapter and
are never produced here. The golden case files under `fixtures/golden/v1/topics/` record accepted and
refused topics with their refusal names, which are part of the contract.

Routine telemetry uses direct delivery because current position updates supersede stale updates. Mission commands, command results, evidence, failures, approvals, and audit records use guaranteed delivery through queues and explicit acknowledgement.

Consumers must tolerate duplicates and out-of-order events. State changes reject stale sequence numbers within a producer's own stream, and command handlers return the prior result when they receive a known command ID. Approval consumption is excluded from that replay-as-success rule: a repeat is denied, not replayed.

Agent Mesh owns its standard A2A namespace, including discovery, agent request, gateway status, and gateway response topics. Application code must use the upstream A2A APIs and gateway abstractions rather than publishing framework messages directly. Keep the A2A namespace distinct from `aerial-rescue/v1/...`, while carrying task, correlation, and causation identifiers across the SAR gateway boundary for traceability.

## Command-gateway request and reply

The two gateway families carry request/reply RPC rather than application events
([ADR-0068](adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md)). The requestor is the
official Event Mesh Tool, which composes its payload from a lookup into the agent's A2A context, an
argument the model supplied, or a configured literal. None of those is a clock or an identifier
source, so it can produce none of `id`, `time`, `sequence`, or `traceparent`, and the request cannot
be an envelope. A request is also a question awaiting an answer rather than a statement that
something happened, which the nine notification families all are.

Both bodies lie inside the canonical profile below, are decoded through the canonical decoder so a
repeated key is refused rather than merged, and carry `rpcVersion`, an integer, inside the hashed
bytes — an RPC body has no `dataschema` member to identify itself by. `packages/contracts`
validates them as pure functions, `rpc.parse_gateway_request` and `rpc.parse_gateway_response`, and
every refusal is a typed value naming the member at fault.

| Body | Member | Required | Rule |
| --- | --- | --- | --- |
| request | `rpcVersion` | yes | the constant `1` |
| request | `missionId` | yes | an IDENTIFIER; equals the topic's mission level |
| request | `operation` | yes | a KIND, closed by the gateway-operation table ([ADR-0069](adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md)) |
| request | `commandType` | yes | a KIND; the command type the operation asks about |
| reply | `rpcVersion` | yes | the constant `1` |
| reply | `missionId` | yes | an IDENTIFIER |
| reply | `requestId` | yes | an IDENTIFIER naming the request answered |
| reply | `operation` | yes | a KIND, echoed from the request |
| reply | `commandType` | yes | a KIND, echoed from the request |
| reply | `outcome` | yes | exactly `answered` or `refused` |
| reply | `actuated` | yes | a boolean: whether publishing an executable command followed ([ADR-0005](adr/0005-deterministic-command-gateway.md)) |
| reply | `authority` | when answered | a KIND naming the authority the command type falls under |
| reply | `refusal` | when refused | a KIND naming why the request was not answered |

Refusals come in a fixed order: not an object; an unknown member; a missing required member; an
unsupported `rpcVersion`; a member outside its rule; and, for a reply, an outcome that disagrees with
the members present. That last rule is validator-only, as are the schema and subject bindings above:
the schema asserts that a reply names at least one of `authority` and `refusal`, and which of the two
goes with which outcome is checked in `packages/contracts` alone.

The command gateway publishes each reply twice. The requestor receives it on the reply channel, and
the same body is republished as the `data` of a CloudEvent on
`aerial-rescue/v1/{missionId}/gateway/response/{requestId}`, so the recorder, the dashboard, and the
audit timeline observe every answer without knowing anything about the Event Mesh Tool or about
Solace request/reply. The record is the weaker of the two: losing it costs an audit line, never an
answer or a command.

## Local HTTP API

The initial dashboard API is:

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/health` | Process liveness only |
| `GET /api/v1/readiness` | Whether the selected operating mode can start a scenario |
| `GET /api/v1/scenarios` | Available synthetic scenarios and metadata |
| `POST /api/v1/scenarios/{scenarioId}/start` | Start a deterministic live or replay run |
| `POST /api/v1/scenarios/current/reset` | Return every local component to its initial state |
| `GET /api/v1/events` | SSE stream for normalized dashboard events |
| `POST /api/v1/missions/{missionId}/approvals` | Record an approve or reject decision bound to the proposal digest/version and exact action parameters |

Requests and responses are typed Pydantic models with generated OpenAPI documentation. Mutation endpoints require an idempotency key, and every idempotency record stores a hash of the canonicalized request body so a key replayed with a different body is refused rather than treated as a repeat.

The approvals endpoint is the one deliberate exception: approvals are single-use, so a second consumption is a hard denial and never an idempotent success. It is recorded in the audit trail as a denied bypass attempt and surfaced on the dashboard ([ADR-0006](adr/0006-proposal-bound-single-use-approvals.md)).

The service binds only to IPv4 or IPv6 loopback addresses, never to a wildcard or non-loopback interface.
Every request must contain exactly one syntactically valid `Host` header whose parsed host and port exactly
match an entry in the configured API allowlist. Wildcard, suffix, and substring matches are forbidden;
missing, malformed, duplicated, and non-allowlisted Host values are rejected before route handling.

The three state-changing endpoints — scenario start, scenario reset, and mission approval — require the
current API process's credential as `Authorization: Bearer <credential>`. The credential is generated anew
for every API process lifetime, is never persisted or logged, and is not accepted from a cookie, query
parameter, request body, or URL. For an approval, successful validation of that bearer is the sole source
of the non-secret operator identity, carried as `operatorIdentity` under the canonical key rule below; a body
field cannot supply or override it.

Browser requests to those state-changing endpoints must also carry an `Origin` whose parsed scheme, host,
and port exactly match the configured dashboard origin. Wildcard, `null`, suffix, and substring matches
are forbidden, and the browser dashboard may not omit the header. The four read-only routes — health,
readiness, scenario discovery, and the SSE event stream — deliberately do not require the bearer; their
requests remain subject to Host validation. The complete rationale is in
[ADR-0024](adr/0024-local-operator-api-boundary.md), and the credential entropy is in
[operating-parameters.md](operating-parameters.md#local-operator-credential).

## Canonical serialization

The approval proposal digest, the replay determinism hash, evidence hashing, and the idempotency record's
hash of a canonicalized request body all reduce to Python and TypeScript producing identical bytes for the
same logical value. The rules below are that contract. They are stated so either language can be written
from this section alone, without reading the other's source
([ADR-0027](adr/0027-integer-only-canonical-serialization.md)). Every bound they refer to lives in
[operating-parameters.md](operating-parameters.md#canonical-serialization-bounds).

**Value space.** A digest-covered payload contains only objects, arrays, strings, integers, booleans, and
null. **No floating-point value is representable**, including one that is numerically integral: a real
number reaching the boundary is rejected, never coerced. This is what makes the representation injective,
so two distinct coordinates cannot collapse onto one digest before hashing.

**Integers** are serialized as the shortest decimal form, with `-` for negatives, no `+`, no leading zero
except for `0` itself, and no exponent. Negative zero is not representable; it is the integer `0`.

**Domain quantities.** Latitude and longitude are integer microdegrees. The evidence score is integer
hundredths, carried beside its named ordinal band and its score version. An instant is an RFC 3339 UTC
string of the exact form `YYYY-MM-DDTHH:MM:SS.sssZ` — always millisecond precision, always the literal
`Z`, never a numeric offset — so one instant has exactly one spelling.

**Object keys** match `^[a-z][a-zA-Z0-9]*$` and are emitted in ascending order of their UTF-8 byte
sequence. A repeated key in inbound JSON text is a rejection, not a last-value-wins merge.

**Strings** contain only Unicode scalar values; an unpaired surrogate is rejected. Every string is
normalized to NFC before serialization. Escaping is minimal: `"` becomes `\"`, `\` becomes `\\`, and
U+0008, U+000C, U+000A, U+000D, and U+0009 become `\b`, `\f`, `\n`, `\r`, and `\t`. Any remaining C0
control becomes `\u00xx` with lowercase hexadecimal. Nothing else is escaped — `/` and every non-ASCII
character are emitted raw as UTF-8.

**Byte form.** UTF-8 with no whitespace anywhere: `{`, key, `:`, value, `,`, `}` for objects and `[`,
value, `,`, `]` for arrays. Array order is semantic and is preserved. `true`, `false`, and `null` are
lowercase.

**Digest.** A top-level `digest` member is removed before serialization; a nested `digest` is ordinary
data. The payload carries `canonicalizationVersion` inside the hashed bytes, so a downgrade fails rather
than passing. The hash input is the byte string `aerial-rescue/canonical/v1`, a newline, the consuming
context, a newline, and the canonical bytes. The context is one of `proposal-digest`, `replay-state`,
`evidence`, or `idempotency-body`, which stops bytes valid for one purpose being replayed as another. The
digest is SHA-256 rendered as lowercase hexadecimal.

## Schema identity

A schema's `$id` is `https://aerial-rescue.invalid/` followed by its repository-relative path, so
`schemas/v1/envelope.schema.json` is identified as
`https://aerial-rescue.invalid/schemas/v1/envelope.schema.json`; RFC 6761 reserves `.invalid`, so no
validator can ever fetch it
([ADR-0038](adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md)). Every `$ref` is
`#/$defs/...` inside one file or an absolute `$id` with an optional `#/$defs/...` fragment. Schemas use
only `$schema`, `$id`, `$defs`, `$ref`, `description`, `type`, `const`, `enum`, `pattern`, `maxLength`,
`minItems`, `minimum`, `maximum`, `required`, `properties`, `additionalProperties`, `propertyNames`,
`anyOf`, `allOf`, and `items`, and never `format`, whose assertion behaviour is implementation-defined.
Patterns are ASCII-only and use `[0-9]` rather than `\d`, so Python's `re` and ECMA-262 read them
identically, and the pattern strings in the schemas are the constants in `packages/contracts`.

`schemas/v1/canonical.schema.json` holds the canonical profile and every shared definition;
`envelope.schema.json` the envelope; each payload has a `payload/<name>.schema.json` and a composed
`event/<name>.schema.json` that binds `type`, `dataschema`, and `data` together; `topic-cases.schema.json`
shapes the topic golden-case files. Golden fixtures live under `fixtures/golden/v1/<schema>/`; every
negative fixture is the valid baseline with exactly one member changed and fails its owning schema for
exactly one reason. `schemas/contract-manifest.toml` registers every schema and fixture exactly once
([ADR-0021](adr/0021-contract-artifact-manifest.md)).

## Delivery and failure semantics

- Telemetry may be dropped under congestion. Critical events use durable queues, publisher confirmation, explicit consumer acknowledgement, idempotent handling, and a bounded local outbox; the exact no-loss claim is limited to the declared queue, spool, storage, and disconnect fault envelope.
- The no-loss claim covers the application data plane and **excludes the Agent Mesh ingress hop**. Event Mesh Gateway 1.1.0 binds a temporary data-plane queue it names itself, so a salient event published while the gateway is disconnected reaches no queue and is never redelivered; delivery into the mesh is at-least-once only while the gateway holds its connection. The authoritative record of such an event is its application topic, which the recorder and the evidence service consume on their own identities, and no command, approval, or audit record depends on a gateway delivery ([ADR-0067](adr/0067-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)).
- Critical Event Mesh Gateway handlers use explicit deferred acknowledgement on completion and an explicitly tested failure-settlement policy; never rely on plugin defaults for redelivery or dead-letter behavior. In Event Mesh Gateway 1.1.0 configuration that is `acknowledgment_policy.mode: on_completion`, `on_failure.action: nack`, and `on_failure.nack_outcome: rejected`, at the gateway and in every per-handler override; the semantic-configuration validator fails `GATEWAY_POLICY` on anything else.
- Commands have a bounded acknowledgement timeout and retry policy with exponential backoff and jitter.
- Retries reuse the original command ID.
- A drone is `CONNECTED` while heartbeats arrive; consecutive missed heartbeat intervals change it to `DEGRADED`, then `OFFLINE`, and the configured count of consecutive heartbeats returns either impaired state to `CONNECTED` ([ADR-0039](adr/0039-drone-connectivity-states-and-recovery.md)). The heartbeat interval and the three counts are in [operating-parameters.md](operating-parameters.md#connectivity-detection).
- A reconnecting drone reconciles commands and reports its last acknowledged sequence.
- Model timeouts, invalid JSON, or schema failures create observable failure events and an abstain/manual-review result in live simulation. Recorded results are available only in isolated replay mode.
- Agent Mesh or Ollama failure prevents new agent recommendations but does not stop telemetry, the dashboard, operator control, or replay.
- Rescue escalation cannot occur without atomically consuming an unexpired, single-use approval whose mission ID and proposal ID match and whose recorded digest equals the digest recomputed over the exact action parameters, score version included, that are about to be published ([ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)).
