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

Every application event uses a CloudEvents 1.0 JSON envelope with, at minimum:

- `specversion`
- `id`
- `source`
- `type`
- `subject`
- `time`
- `datacontenttype`
- `dataschema`
- `data`

Domain data includes `missionId`, `droneId` where applicable, sequence number, correlation ID, causation ID, schema version, and trace context. Event IDs and command IDs are globally unique and act as idempotency keys. The sequence number is scoped to its producer and is used only to detect staleness within that producer's stream; it is not comparable across sources and must not be used to order the mission timeline, which is ordered by the durable audit ordinal ([ADR-0003](adr/0003-postgres-durable-mission-store.md)).

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

Routine telemetry uses direct delivery because current position updates supersede stale updates. Mission commands, command results, evidence, failures, approvals, and audit records use guaranteed delivery through queues and explicit acknowledgement.

Consumers must tolerate duplicates and out-of-order events. State changes reject stale sequence numbers within a producer's own stream, and command handlers return the prior result when they receive a known command ID. Approval consumption is excluded from that replay-as-success rule: a repeat is denied, not replayed.

Agent Mesh owns its standard A2A namespace, including discovery, agent request, gateway status, and gateway response topics. Application code must use the upstream A2A APIs and gateway abstractions rather than publishing framework messages directly. Keep the A2A namespace distinct from `aerial-rescue/v1/...`, while carrying task, correlation, and causation identifiers across the SAR gateway boundary for traceability.

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
of the non-secret `operator_identity`; a body field cannot supply or override it.

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

## Delivery and failure semantics

- Telemetry may be dropped under congestion. Critical events use durable queues, publisher confirmation, explicit consumer acknowledgement, idempotent handling, and a bounded local outbox; the exact no-loss claim is limited to the declared queue, spool, storage, and disconnect fault envelope.
- Critical Event Mesh Gateway handlers use explicit deferred acknowledgement on completion and an explicitly tested failure-settlement policy; never rely on plugin defaults for redelivery or dead-letter behavior. In Event Mesh Gateway 1.1.0 configuration that is `acknowledgment_policy.mode: on_completion`, `on_failure.action: nack`, and `on_failure.nack_outcome: rejected`, at the gateway and in every per-handler override; the semantic-configuration validator fails `GATEWAY_POLICY` on anything else.
- Commands have a bounded acknowledgement timeout and retry policy with exponential backoff and jitter.
- Retries reuse the original command ID.
- Lost connectivity changes a drone to `DEGRADED`, then `OFFLINE`, on consecutive missed heartbeats. The heartbeat interval and the two miss counts are in [operating-parameters.md](operating-parameters.md#connectivity-detection).
- A reconnecting drone reconciles commands and reports its last acknowledged sequence.
- Model timeouts, invalid JSON, or schema failures create observable failure events and an abstain/manual-review result in live simulation. Recorded results are available only in isolated replay mode.
- Agent Mesh or Ollama failure prevents new agent recommendations but does not stop telemetry, the dashboard, operator control, or replay.
- Rescue escalation cannot occur without atomically consuming an unexpired, single-use approval matching the exact mission ID, proposal ID, proposal digest/version, and action parameters.
