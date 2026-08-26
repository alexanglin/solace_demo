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
aerial-rescue/v1/{missionId}/mission/event/{eventType}
aerial-rescue/v1/{missionId}/sector/{sectorId}/event/{eventType}
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
([ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md)), and `packages/contracts` (`topics.py`)
is the only producer and parser of these topics:

| Rule | Levels | Form |
| --- | --- | --- |
| IDENTIFIER | `missionId`, `sectorId`, `droneId`, `commandId`, `requestId`; also the envelope's `id`, `subject`, `correlationid`, `causationid` | `^(?:[a-z0-9]\|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$`: lowercase ASCII letters, digits, interior hyphens |
| KIND | `commandType`, `eventType`, `proposalType`, `recordType`, `operation`; also `producerKind` in `source` | `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`, bounded in length; `commandType` is closed by the command-authority table and `operation` by the gateway-operation table, both in `packages/domain` ([ADR-0041](adr/0041-deny-by-default-command-authority-table.md), [ADR-0069](adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md)); `eventType`, `proposalType`, and `recordType` stay open until the domain modules that define them land |
| AGENT_NAME | `agentName` | `^[A-Za-z0-9_]{1,64}$`, the ASCII subset of what Agent Mesh 1.28.7 accepts as an agent name; Solace topics are case-sensitive, so two names differing only in case are two topics |
| DECISION | `decision` | exactly `approve` or `reject` |

The CloudEvents `type` of an event is derived from its topic: drop the IDENTIFIER and AGENT_NAME levels,
join the rest with `.`, and prefix `aerial-rescue.v1.`. So
`aerial-rescue/v1/m1/sector/sector-01/event/lifecycle` has the type
`aerial-rescue.v1.sector.event.lifecycle`, and
`aerial-rescue/v1/m1/drone/d1/command-result/c1` has
`aerial-rescue.v1.drone.command-result`. A topic is recovered from its type together with the
identifiers a producer holds.

Parsing refuses in a fixed order, which TypeScript reimplements: not a string; longer than the broker
bound; a `*` or `>` anywhere; a prefix other than `aerial-rescue/v1`; a shape matching no family; then
each level against its rule in template order, so an empty, `#`-, `!`-, or `+`-bearing level fails the
rule of the parameter it occupies. Formatting never emits a wildcard, a reserved prefix, an empty level,
or a trailing separator. Subscription strings, which do carry wildcards, belong to the broker adapter and
are never produced here. The golden case files under `fixtures/golden/v1/topics/` record accepted and
refused topics with their refusal names, which are part of the contract.

Which delivery guarantee each family is owed is a total table in `packages/contracts`, not a sentence
to be read against the thirteen families
([ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md)). Routine telemetry is direct because
current position updates supersede stale ones. The gateway request and response are request-reply over
a temporary queue a pinned upstream component owns and names, so this project provisions no endpoint
for them. Every other family, including mission and sector lifecycle, is guaranteed, and its endpoint
is one durable queue per consuming role
([ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)).

Consumers must tolerate duplicates and out-of-order events. State changes reject stale sequence numbers within a producer's own stream, and command handlers return the prior result when they receive a known command ID. Approval consumption is excluded from that replay-as-success rule: a repeat is denied, not replayed.

Agent Mesh owns its standard A2A namespace, including discovery, agent request, gateway status, and gateway response topics. Application code must use the upstream A2A APIs and gateway abstractions rather than publishing framework messages directly. Keep the A2A namespace distinct from `aerial-rescue/v1/...`, while carrying task, correlation, and causation identifiers across the SAR gateway boundary for traceability.

## Dashboard lifecycle event sources

Three guaranteed application events are the only source of the lifecycle projections that change
reduced dashboard state
([ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md)):

| CloudEvents `type` | Topic | Closed payload | Publisher | Projection |
| --- | --- | --- | --- | --- |
| `aerial-rescue.v1.drone.event.connectivity-changed` | `aerial-rescue/v1/{missionId}/drone/{droneId}/event/connectivity-changed` | `{missionId, droneId, connectivity}` | fleet simulator | `connectivityChanged` |
| `aerial-rescue.v1.mission.event.lifecycle` | `aerial-rescue/v1/{missionId}/mission/event/lifecycle` | `{missionId, lifecycle}` | scenario service | `missionLifecycle` |
| `aerial-rescue.v1.sector.event.lifecycle` | `aerial-rescue/v1/{missionId}/sector/{sectorId}/event/lifecycle` | `{missionId, sectorId, state, assignedMemberId}` | fleet simulator | `sectorLifecycle` |

The exact envelope sources are `urn:aerial-rescue:connectivity-lifecycle:{runId}`,
`urn:aerial-rescue:mission-lifecycle:{runId}`, and
`urn:aerial-rescue:sector-lifecycle:{runId}` respectively. Each source owns an independent producer
sequence. The source provides uniqueness and sequence scope, not authentication; the publisher's broker
credential and deny-by-default ACL grant are the authority.

Connectivity, mission lifecycle, and sector lifecycle values remain the closed state sets selected by
[ADR-0039](adr/0039-drone-connectivity-states-and-recovery.md),
[ADR-0072](adr/0072-mission-lifecycle-states.md), and
[ADR-0073](adr/0073-sector-lifecycle-states.md). Topic, envelope, and payload identifiers must agree.
Projection preserves the source envelope's canonical `time`, removes transport-only envelope members,
and produces the existing five-field `DashboardEvent` variant.

The scenario role publishes only mission lifecycle. The fleet role publishes sector lifecycle and
connectivity. The receiver-only recorder is the only subscriber to the two new topic families; it
validates and commits a guaranteed event with its assigned audit ordinal before acknowledging it.
Private run control remains HTTP, and no service may bypass this source boundary by manufacturing one
of these normalized events directly.

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

### The reply channel

The reply does not go to the gateway-response topic of its mission. Solace AI Connector fixes a
requestor's reply topic once per session, before any mission exists, as
`{response_topic_prefix}/{requestorId}`, and binds a temporary queue to both that topic and the same
topic followed by `>`. Neither the identifier nor the extra subscription is configurable
([ADR-0070](adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)).

The mission level of the reply channel is therefore the reserved identifier `reply`, and the channel
is `aerial-rescue/v1/reply/gateway/response/{requestorId}`. It is inside the topic grammar and the
gateway-response family, and it names no mission: `envelope.parse_envelope` refuses an envelope
whose `subject` is the reserved identifier, so no mission can be called `reply` and no application
event can be published there. The `event-mesh-tool` role holds no gateway-response family grant at
all; it holds one exception scoped to `aerial-rescue/v1/reply/gateway/response/>`, which is strictly
less authority than the family it replaces.

Two user properties carry the correlation, both set by the requestor and therefore untrusted input.
`__solace_ai_connector_broker_request_response_topic__` names the reply topic; the command gateway
refuses any value that is not a gateway-response topic on the reserved identifier, which is what
stops an injected value aiming the sole publisher of executable commands at another topic.
`__solace_ai_connector_broker_request_reply_metadata__` is a JSON array whose last entry carries the
`request_id`; the command gateway echoes the value back verbatim and never interprets it beyond
reading that identifier, which must itself be an IDENTIFIER before it becomes the record's topic
level.

The command gateway publishes each reply twice. The requestor receives it on the reply channel, and
the same body is republished as the `data` of a CloudEvent on
`aerial-rescue/v1/{missionId}/gateway/response/{requestId}`, so the recorder, the dashboard, and the
audit timeline observe every answer without knowing anything about the Event Mesh Tool or about
Solace request/reply. The record is the weaker of the two: losing it costs an audit line, never an
answer or a command.

## Scenario catalog files

The scenario service's file boundary is the pair accepted by
[ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md):
`scenarios/catalog.v1.json` selects definitions by catalog identity, and
`scenarios/v1/wilderness-missing-person.r1.json` is the revision-one wilderness definition. The
normative file schemas are `schemas/v1/scenario/catalog.schema.json` and
`schemas/v1/scenario/definition.schema.json`; the corresponding golden fixtures prove the shared
structural contract but are not production catalog files.

Both documents use integer version `1`, lie inside the canonical JSON profile, and are closed. The
catalog binds scenario identifier `wilderness-missing-person` and revision `1` to a repository-owned
definition path and the lowercase SHA-256 of that definition's bytes. A caller supplies the scenario
identity, never a filesystem path. The loader must retain the source bytes long enough to reject
duplicate keys and floating-point values, resolve only a regular file inside its injected catalog root,
and verify the catalog digest before accepting a definition. The file, depth, collection, and prepared
workload bounds live in
[operating-parameters.md](operating-parameters.md#scenario-catalog-files).

The definition separates three kinds of fact:

- presentation and discovery metadata: title, summary, synthetic search-area size, last-known point,
  search polygon, and twenty sector polygons, all coordinates expressed as integer microdegrees;
- twenty explicit `SIMULATED_DRONE` members, each carrying every input required to construct one
  `DroneStart`, plus the tick interval, connectivity thresholds, uniform sweep count, and explicit
  heartbeat-loss schedule; and
- three `DECLARED_ONLY` external descriptors, which are presentation metadata and can never be adapted
  into `FleetScenario` or acquire connectivity or telemetry.

Only the simulated members are projected into the fleet runtime. Geometry, catalog metadata,
declared-only members, run mode, mission lifecycle, and a random seed are absent from that projection.
Scenario identity selects a reusable definition; `missionId` and `runId` identify one execution and are
created outside the scenario file.

## Private run-control HTTP

[ADR-0107](adr/0107-authenticate-private-scenario-and-fleet-run-control.md) defines two authenticated
private hops: dashboard API to scenario service, and scenario service to fleet simulator. Both use the
same route grammar under distinct exact Hosts and distinct bearer credentials; neither private listener
publishes a host port.

| Method and path | Request | Successful response |
| --- | --- | --- |
| `POST /internal/v1/runs` | service-specific start request | service-specific run status |
| `GET /internal/v1/runs/{runId}` | none | service-specific run status |
| `POST /internal/v1/runs/{runId}/cancel` | service-specific cancel request | service-specific run status |

The eight closed RPC schemas under `schemas/v1/rpc/` are the four documents in each row below. Every
document carries integer `controlVersion: 1` and uses the canonical JSON profile. Start, status, and an
established-cancel success deliberately share one run-status representation.

| Document | Required contract members and meaning |
| --- | --- |
| scenario-control start request | `scenarioId`, integer `scenarioRevision`, stable `missionId`, and stable `runId` |
| scenario-control run status | scenario, mission, and run identities; `PLANNED`, `SEARCHING`, `EXHAUSTED`, or `ABORTED`; truthful 23/20/3 participation counts; completed-tick and telemetry-publication counters |
| scenario-control cancel request | `missionId` and `runId`; the body run identifier must equal the path identifier |
| scenario-control refusal | service-specific closed `errorCode` and a bounded redacted `message` |
| fleet-control start request | stable `runId` and exactly one nested lossless `FleetScenario` projection |
| fleet-control run status | mission and run identities; `ACCEPTED`, `RUNNING`, `EXHAUSTED`, `CANCELLED`, or `FAILED`; completed-tick and telemetry-publication counters |
| fleet-control cancel request | `missionId` and `runId`; the body run identifier must equal the path identifier |
| fleet-control refusal | service-specific closed `errorCode` and a bounded redacted `message` |

The nested fleet scenario contains the mission identifier, twenty explicit simulated starts, tick
interval, connectivity thresholds, uniform sweep count, and a flat bounded list of `{droneId,
tickOrdinal}` heartbeat absences. It contains no scenario identity, geometry, declared-only member,
mode, lifecycle, or seed. The publication counter records successful fleet publication and is not a
proxy for best-effort recorder receipt.

Private requests with bodies are refused in this order: Host syntax and exact allowlist, bearer, JSON
media type and raw-body bound, canonical duplicate-key or floating-point violation, strict schema,
path/body run binding, then operation policy. Reads enforce Host and bearer before lookup. Refusals
distinguish malformed admission, run conflict or absence, cancellation not established, and internal
failure; scenario control additionally distinguishes scenario lookup/revision and fleet availability,
while fleet control additionally distinguishes capacity and run failure. Exact body, connection,
response, and shared cancellation bounds live in
[operating-parameters.md](operating-parameters.md#private-run-control).

The stable `runId` is the private idempotency identity. Repeating the same canonical start for the same
run returns current status without launching another run; changing the body for an existing run is
`RUN_CONFLICT`. A caller that cannot establish whether start succeeded queries that same run and never
automatically repeats start. Cancel reports success only after the run is stopped or already terminal;
otherwise it returns `CANCELLATION_NOT_ESTABLISHED` and does not claim reset.

The Python trust-boundary twins are now service-local as required by
[ADR-0108](adr/0108-register-strict-python-wire-models-before-http-runtime.md). The scenario service owns
the scenario-control server models and separate fleet-control caller models; the fleet simulator owns
the fleet-control server models; and the dashboard API owns separate scenario-control caller models.
Each boundary applies the contracts-owned canonical decoder before closed, frozen, strict, alias-only
Pydantic validation. The independently implemented twins are checked against the same manifest-owned
accepted and one-reason-negative fixtures. Framework-free three-route registries in each private HTTP
owner pin the request, response, and default-refusal expectations for later runtime and OpenAPI parity.
They create no listener, client, generated OpenAPI document, or runtime route.

## Local HTTP API

The UI-first dashboard API is the closed surface accepted by
[ADR-0097](adr/0097-close-the-ui-slice-http-contract.md):

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/health` | Process liveness and the non-secret runtime identifier |
| `GET /api/v1/readiness?mode=degradedLive\|replay` | Whether the selected mode can start |
| `GET /api/v1/scenarios` | Validated synthetic geometry, roster, and participation |
| `POST /api/v1/scenarios/{scenarioId}/start` | Start live execution or create a replay session |
| `POST /api/v1/scenarios/current/reset` | Bounded live reset or a fresh replay session |
| `GET /api/v1/events` | Snapshot plus ordered SSE suffix |
| `GET /api/v1/replays/{sessionId}` | One read-only validated replay bundle |
| `GET /` and `GET /assets/{asset}` | Dynamic bootstrap shell and hashed local assets |

There is no approval route in this slice. Approval, evidence, command, model, rescue, and escalation
workflows remain follow-on work and gain no placeholder endpoint. The committed schemas under
`schemas/v1/dashboard/` are normative. The dashboard API now owns strict Pydantic twins for its
seventeen server-facing shapes and a framework-free registry for the exact route table above. The
`mutation-outcome` and `source-signal` documents remain browser-owned and deliberately have no Python
model. Generated OpenAPI, generated TypeScript, and Ajv remain future consumers of those same shapes
rather than parallel authorities. The model and route registry do not create a FastAPI application or
HTTP listener.

Start is exactly `{mode, scenarioRevision}` with mode `degradedLive` or `replay` and integer revision
`1`; reset is exactly `{}`. Accepted live responses carry stable mission and run identifiers; replay
responses carry a stable session identifier. Start and reset responses also report the fixed roster as
23 declared, 20 simulated, and three declared-only members. A `202` response updates mutation-operation
state only: reducer-owned current mission state changes only after a validated snapshot or ordered event.

Both mutations require a lowercase UUID version 4 idempotency key. The durable idempotency operation
stores a digest of the canonical request body plus the exact response status and bytes, so a same-key,
same-body repeat returns the prior result and a same-key, different-body repeat refuses without an
effect. Expected refusals use the closed versioned dashboard error schema.

The service binds only to its private Unix socket. Caddy is the sole loopback publisher. Every request
must contain exactly one syntactically valid `Host` header whose parsed host and port exactly matches the
configured allowlist. Wildcard, suffix, substring, missing, malformed, duplicated, and non-allowlisted
values are rejected before route handling.

The two state-changing endpoints require the current API process's credential as
`Authorization: Bearer <credential>`. The credential is generated anew for every API process lifetime,
is never persisted or logged, and is not accepted from a cookie, query parameter, request body, or URL.
The dynamic no-store shell transfers it once; bootstrap removes the source node and retains the value
only in memory.

Every mutation must also carry an `Origin` whose parsed scheme, host, and port exactly match the
configured dashboard origin. Wildcard, `null`, suffix, substring, missing, repeated, and malformed values
are forbidden; caller classification never weakens the rule. Read-only routes deliberately do not
require the bearer; every route remains subject to Host validation. The refusal order is Host, Origin,
bearer, media type and body size, idempotency key, canonical decode, strict request schema, then the route
operation. The complete rationale is in
[ADR-0096](adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md) and
[ADR-0097](adr/0097-close-the-ui-slice-http-contract.md); credential entropy is in
[operating-parameters.md](operating-parameters.md#local-operator-credential).

## Dashboard event stream

`GET /api/v1/events` streams the snapshot and ordered suffix accepted by
[ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md). Every data frame lies
inside the canonical profile, so one canonicalizer, one decoder, and one shared fixture oracle serve
Python and TypeScript. The bounds are in
[operating-parameters.md](operating-parameters.md#dashboard-event-stream).

A dashboard event has five members: `kind`, the projection's name; `eventClass`, one of `TELEMETRY`,
`CONNECTIVITY`, or `MISSION` in this UI slice; `mission`, the mission
identifier; `time`, the source envelope's canonical instant; and `data`, the projected fields
repeating every identifier the source topic named except the mission, which `mission` already
carries. **No transport member crosses this boundary** — `id`, `source`, `sequence`, `dataschema`,
`traceparent`, and `tracestate` are absent, so a browser reads a dashboard event without the
envelope profile or the topic grammar. An envelope whose `type` has no projection is refused as
`UNPROJECTED`, in the same shape as the unbound-`type` refusal of the envelope profile.

Durable order wraps, rather than changes, that projection:

```text
OrderedDashboardEvent = {auditOrdinal, event}
```

The audit ordinal is a positive integer. The immutable reducer checkpoint is
`{state, latestEventDigest}`. Its witness lies outside reduced mission state and is the
`ordered-dashboard-event` digest of
`{canonicalizationVersion: 1, auditOrdinal, event}`. The wire wrapper itself remains
`{auditOrdinal, event}`.

A fold accepts only the next ordinal, updates both state and witness, and uses that witness to prove
same-ordinal input. An equal ordered-event digest is an exact duplicate and leaves the checkpoint
unchanged; an unequal digest is divergent content. A lower ordinal is a regression and a larger
non-successor is a gap.

The **reduced dashboard state** is the fold of ordered dashboard events by a pure total function. It is
the replay determinism oracle of
[ADR-0094](adr/0094-validate-replay-before-browser-playback.md), and its determinism is structural: the state
carries no wall-clock instant, event identifier, trace context, run mode, connection state, operation,
timeline, filter, selection, or playback state. It carries the current mission lifecycle, latest audit
ordinal, sorted fleet members, explicit connectivity and latest telemetry for simulated members, and
sorted sector lifecycle and assignment. Declared-only members carry neither connectivity nor telemetry.
Sectors are the sole assignment and lifecycle authority; fleet members do not duplicate sector state.
Collections are arrays in ascending byte order of their identifier, never objects keyed by it, because a
canonical object key matches `^[a-z][a-zA-Z0-9]*$` and an identifier may carry an interior hyphen. Array
order is semantic, so that sort is part of the contract.

The non-telemetry timeline is not reconstructed from reduced state. A snapshot carries its full ordered
timeline as normalized events; meaningful suffix events append to it, while telemetry never does. The
snapshot schema narrows the ordered-event wrapper to connectivity, mission, and sector variants so a
telemetry event cannot enter the timeline after otherwise-valid schema validation.

The determinism hash is taken over the canonical state document under the `replay-state` context,
so state bytes cannot be replayed as proposal bytes.

The API emits only `snapshot`, `dashboard-event`, and terminal `stream-overloaded` data frames;
keepalives are comments. A snapshot carries the runtime identifier, an opaque run-bound cursor, current
run, reduced state, replay-state digest, top-level `latestEventDigest`, and full non-telemetry timeline.
A replay bundle carries the same top-level witness for its initial state. The witness is `null` exactly
when the corresponding latest audit ordinal is `0`; a positive ordinal requires a lowercase SHA-256
witness. A dashboard-event frame carries one ordered event, its suffix cursor, and the server digest
after the fold; the receiver computes the successor witness from that event. An unknown, stale, or
cross-run cursor receives a fresh snapshot.

Under back-pressure a server may discard only `TELEMETRY` events, because routine telemetry uses direct
delivery and a newer position supersedes a stale one. Each client retains 256 data frames plus one
reserved terminal slot. If removing the oldest telemetry cannot retain a non-droppable frame, the API
sends one terminal control frame and closes; the browser disposes that source and requests exactly one
fresh snapshot. It validates every frame, recomputes the digest, and retains the last validated state on
any contract, ordinal, or digest refusal.

Adding an application event type is one change: a projection row, a state rule, golden fixtures, and
a manifest entry land together, or the type is refused as unprojected.

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
`ordered-dashboard-event`, `evidence`, or `idempotency-body`, which stops bytes valid for one purpose
being replayed as another. The digest is SHA-256 rendered as lowercase hexadecimal.

## Schema identity

A schema's `$id` is `https://aerial-rescue.invalid/` followed by its repository-relative path, so
`schemas/v1/envelope.schema.json` is identified as
`https://aerial-rescue.invalid/schemas/v1/envelope.schema.json`; RFC 6761 reserves `.invalid`, so no
validator can ever fetch it
([ADR-0038](adr/0038-reserved-host-schema-identity-and-one-reason-fixtures.md)). Every `$ref` is
`#/$defs/...` inside one file or an absolute `$id` with an optional `#/$defs/...` fragment. Schemas use
only `$schema`, `$id`, `$defs`, `$ref`, `description`, `type`, `const`, `enum`, `pattern`, `maxLength`,
`minLength`, `minItems`, `maxItems`, `minimum`, `maximum`, `required`, `properties`,
`additionalProperties`, `propertyNames`, `anyOf`, `allOf`, and `items`, and never `format`, whose
assertion behaviour is implementation-defined
([ADR-0106](adr/0106-bound-dashboard-schema-strings-and-arrays-explicitly.md)).
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

- Telemetry may be dropped under congestion. Critical events use durable queues, publisher confirmation, explicit consumer acknowledgement, idempotent handling, and a bounded local outbox; the exact no-loss claim is limited to the declared queue, spool, storage, and disconnect fault envelope. A queue is created only for a `(role, family)` pair the subscribe grant already permits, is bound only by its named owner, and sends what it cannot deliver to the dead-message queue rather than discarding it ([ADR-0080](adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)); the values are in [operating-parameters.md](operating-parameters.md#guaranteed-delivery-endpoints). A guaranteed message matching no queue is discarded by the broker and not refused, so a drone the provisioner was never told about loses its commands silently.
- The no-loss claim covers the application data plane and **excludes the Agent Mesh ingress hop**. Event Mesh Gateway 1.1.0 binds a temporary data-plane queue it names itself, so a salient event published while the gateway is disconnected reaches no queue and is never redelivered; delivery into the mesh is at-least-once only while the gateway holds its connection. The authoritative record of such an event is its application topic, which the recorder and the evidence service consume on their own identities, and no command, approval, or audit record depends on a gateway delivery ([ADR-0071](adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)).
- Critical Event Mesh Gateway handlers use explicit deferred acknowledgement on completion and an explicitly tested failure-settlement policy; never rely on plugin defaults for redelivery or dead-letter behavior. In Event Mesh Gateway 1.1.0 configuration that is `acknowledgment_policy.mode: on_completion`, `on_failure.action: nack`, and `on_failure.nack_outcome: rejected`, at the gateway and in every per-handler override; the semantic-configuration validator fails `GATEWAY_POLICY` on anything else.
- Commands have a bounded acknowledgement timeout and retry policy with exponential backoff and jitter; the schedule has one interval and the jitter only adds ([ADR-0081](adr/0081-give-command-dispatch-one-interval.md)), and the values are in [operating-parameters.md](operating-parameters.md#command-dispatch).
- Retries reuse the original command ID.
- A drone is `CONNECTED` while heartbeats arrive; consecutive missed heartbeat intervals change it to `DEGRADED`, then `OFFLINE`, and the configured count of consecutive heartbeats returns either impaired state to `CONNECTED` ([ADR-0039](adr/0039-drone-connectivity-states-and-recovery.md)). The heartbeat interval and the three counts are in [operating-parameters.md](operating-parameters.md#connectivity-detection).
- A reconnecting drone reconciles commands and reports its last acknowledged sequence.
- Model timeouts, invalid JSON, or schema failures create observable failure events and an abstain/manual-review result in live simulation. Recorded results are available only in isolated replay mode.
- Agent Mesh or Ollama failure prevents new agent recommendations but does not stop telemetry, the dashboard, operator control, or replay.
- Rescue escalation cannot occur without atomically consuming an unexpired, single-use approval whose mission ID and proposal ID match and whose recorded digest equals the digest recomputed over the exact action parameters, score version included, that are about to be published ([ADR-0040](adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)).
