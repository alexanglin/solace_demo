# ADR-0146: Define durable application processing

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0006, ADR-0061, ADR-0076, ADR-0079, ADR-0093, ADR-0097, ADR-0111

## Context

The broker substrate, topic grammar, delivery guarantees, queue projection, recovery policy, and
least-privilege roles are accepted and partly implemented. The application services still lack one
integrated decision for the boundary between an arriving broker message and durable domain state. In
particular, no accepted record decides the inbox, general application outbox, proposal and evidence
stores, settle-after-commit order, or the normalization step between the pinned Event Mesh Gateway's
raw agent output and an application proposal.

That omission matters because the broker is a transport and spool, not the system of record. A
publisher confirmation proves broker acceptance, while a queue acknowledgement proves only what the
consumer has chosen to settle. Neither proves that a proposal, evidence decision, approval, command
progress update, or prior command result survived a process restart. Process-memory deduplication also
cannot distinguish a redelivery after restart from new work.

The current contract has thirteen families after ADR-0111 added mission and sector lifecycle events.
It still models `AGENT_RESPONSE` as a guaranteed application CloudEvent even though the pinned Event
Mesh Gateway emits a plugin integration result, and it lets Agent Mesh agents publish application
proposals directly. That gives untrusted model output a route around deterministic proposal
normalization. It also leaves the evidence service with no typed family on which to publish the
versioned evidence decision that the architecture assigns it.

The public HTTP slice deliberately omitted approval while the workflow was unsettled. The workflow is
now selected, so command creation and an exact proposal decision need authenticated mutation routes
without turning scenario and fleet run control into broker commands or replacing the existing read and
SSE surface.

Several accepted records close tables or transactions affected by this decision. The changes below are
therefore explicit partial supersessions rather than informal additions:

| Record | Clause superseded here | Clauses that remain in force |
| --- | --- | --- |
| ADR-0006 | The command-authorization atomic set grows from three durable effects to four by adding the append-only audit record | Proposal-bound digest and action parameters, expiry, single use, refusal semantics, and every approval lifecycle rule |
| ADR-0061 and ADR-0111 | The application-family count and the affected publish/subscribe grant rows | Ten roles, deny-by-default enforcement, per-process credentials, A2A grants, all unaffected application grants, and the scenario-service role |
| ADR-0076 | The three previously open band boundaries and the previously unassigned live-origin weights | Integer scores, saturation, version 1, the structural two-source floor, recorded-origin refusal, and the pure scoring boundary |
| ADR-0079 | The family total and delivery table | Delivery as a total contract-owned lookup and the meaning of `DIRECT`, `REQUEST_REPLY`, and `GUARANTEED` |
| ADR-0093 | Only the requirement to append an authorization audit in a later, separate transaction | Outbox states and transitions, broker-confirmation meaning, the central command-outbox count bound, overflow behavior, and command-progress separation |
| ADR-0097 | Only the statement that the public slice has no approval route, enlarged here by two authenticated mutation routes | Existing routes, schemas, authentication, canonical decoding, idempotency, refusal order, error shape, and replay-mode readiness |
| ADR-0111 | The thirteen-family total, affected grants, and recorder's all-family count | `MISSION_EVENT`, `SECTOR_EVENT`, their schemas and source bindings, guaranteed delivery, the scenario-service mission-event grant, the fleet-simulator sector-event grant, and lifecycle projection through the recorder |

## Decision

### Correct the family representations and add evidence decisions

Extend the application taxonomy from thirteen to fourteen families with:

```text
aerial-rescue/v1/{missionId}/evidence/decision/{proposalId}
```

Call the family `EVIDENCE_DECISION`. `proposalId` uses the existing `IDENTIFIER` grammar and must agree
between the topic, envelope, and payload. Its CloudEvents type is fixed as
`aerial-rescue.v1.evidence.decision`. The payload binds the immutable proposal, evidence-decision
identity, canonical decision digest and version, evidence lifecycle outcome, score version, integer
score, named band, contributing source identities, and provenance digests. The producer owns an
ADR-0037 producer-scoped sequence. A later decision about the same proposal reuses the topic and
advances the producer's sequence; it does not overwrite the earlier durable decision or repoint an
approval.

The effective delivery and representation table is:

| Delivery | Families | Wire representation |
| --- | --- | --- |
| `DIRECT` | `DRONE_TELEMETRY`, `AGENT_RESPONSE` | Telemetry is a CloudEvent notification; agent response is the integration body below |
| `REQUEST_REPLY` | `GATEWAY_REQUEST`, `GATEWAY_RESPONSE` | The schema-bound RPC documents selected by ADR-0068 |
| `GUARANTEED` | `OPERATOR_COMMAND`, `OPERATOR_APPROVAL`, `DRONE_EVENT`, `DRONE_COMMAND`, `DRONE_COMMAND_RESULT`, `AGENT_PROPOSAL`, `EVIDENCE_DECISION`, `AUDIT`, `MISSION_EVENT`, `SECTOR_EVENT` | CloudEvent notifications on project-owned durable queues |

There are therefore fourteen families: eleven CloudEvent notification families, two request/reply
families, and one plugin integration body. `AGENT_RESPONSE` keeps its existing
`aerial-rescue/v1/{missionId}/agent/response/{agentName}` topic but is no longer a CloudEvent. It is a
closed, bounded, versioned JSON integration document produced by the pinned Event Mesh Gateway. It
carries the topic-bound mission and agent identities, invocation/correlation identity, a success or
redacted-failure discriminator, and the structured agent result. The committed schema, accepted
fixtures, and one-reason negatives own its exact body. A free-form text answer is not a valid body.

`AGENT_RESPONSE` is direct because the pinned plugin owns neither a project durable output endpoint nor
publisher-confirmation semantics for that route. This is an honest loss boundary: if the command
gateway is absent or disconnected, that response may disappear. No command, approval, evidence
decision, or audit record may treat the raw response as authoritative.

### Move application authority out of Agent Mesh

The effective ACL changes are narrow and deny-by-default:

- `agent-mesh-agent` loses both application publish grants, `AGENT_PROPOSAL` and `AGENT_RESPONSE`; it
  retains only its accepted A2A namespace authority;
- `event-mesh-gateway` remains the sole publisher of `AGENT_RESPONSE` and retains its `DRONE_EVENT`
  subscription;
- `command-gateway` subscribes to direct `AGENT_RESPONSE`, publishes `AGENT_PROPOSAL`, and no longer
  subscribes to `AGENT_PROPOSAL`; it validates an operator's selected evidence decision through the
  canonical approval binding and authoritative persisted store, not another broker subscription;
- `evidence-service` continues to subscribe to `DRONE_EVENT` and `AGENT_PROPOSAL`, continues to
  publish `AUDIT`, and becomes the sole publisher of `EVIDENCE_DECISION`;
- `dashboard-api` retains `AGENT_RESPONSE` as a direct subscription and adds
  `EVIDENCE_DECISION` as a guaranteed subscription; and
- `recorder` subscribes to all fourteen families, including direct `AGENT_RESPONSE` and guaranteed
  `EVIDENCE_DECISION`, and still publishes nothing.

Every grant not named above remains exactly as ADR-0061 and ADR-0111 leave it. In particular,
`scenario-service` publishes only `MISSION_EVENT`, and `fleet-simulator` retains its `SECTOR_EVENT`,
`DRONE_EVENT`, telemetry, command-result, and drone-command subscription grants. The A2A role table is
unchanged.

The queue projection in ADR-0080 remains authoritative. The dashboard API's guaranteed
`AGENT_RESPONSE` endpoint becomes an `EVIDENCE_DECISION` endpoint, and the recorder's guaranteed
`AGENT_RESPONSE` endpoint becomes an `EVIDENCE_DECISION` endpoint while its raw response subscription
becomes direct. Those two swaps are count-neutral. Removing the command gateway's guaranteed
`AGENT_PROPOSAL` subscription removes one endpoint and adds no replacement. The inventory therefore
becomes 21 family queues, 23 per-drone command queues, and the dead-message queue: 45 endpoints and
450 MB of nominal reservation. No durable queue is provisioned for `AGENT_RESPONSE`; ADR-0145's exact,
empty, unbound retirement rule applies to the departed command-gateway proposal queue.

### Normalize agent output into canonical proposals

The official Event Mesh Gateway is the only bridge from Agent Mesh output to the application namespace.
The command gateway treats every `AGENT_RESPONSE` body and every model-derived member as untrusted. It
validates the topic/body identities, version, result discriminator, operation, correlation, closed
shape, bounds, and action vocabulary before any value can affect application state.

An accepted result is normalized in one PostgreSQL transaction. The command gateway generates the
proposal identifier, canonical issue time, producer sequence, trace relationships, action parameters,
version, and canonical digest; persists the immutable proposal; claims the broker inbox identity; and
stages the exact `AGENT_PROPOSAL` CloudEvent bytes in the application outbox. Model output cannot choose
envelope identity, source, time, sequence, correlation, causation, proposal digest, or an executable
command topic. Commit makes the canonical proposal durable; only an outbox worker may then publish it.

A malformed, uncorrelated, unsupported, unsafe, or failed response produces a typed refusal or
abstention and a staged audit record, but no proposal. Direct loss before this transaction is possible
and cannot be reconstructed. Once the transaction commits, the raw integration result is no longer the
authority; the immutable proposal and its canonical event are.

The evidence service consumes canonical proposals and relevant live observations. It validates model
observations, persists their lifecycle and provenance, computes the versioned score through the Tier 1
domain rule, persists every evidence decision, and stages both `EVIDENCE_DECISION` and the corresponding
`AUDIT` bytes transactionally. Evidence may establish eligibility; it never actuates a command.

### Make PostgreSQL the durable application authority

PostgreSQL is authoritative for all application facts that must survive restart:

- the broker inbox, including consumer identity, message identity, canonical digest, processing
  outcome, and duplicate decision;
- the application outbox and its exact staged topic, headers, body bytes, event identity, state, and
  publication evidence;
- immutable canonical proposals and their digests;
- evidence observations, lifecycle, provenance, contributors, versioned scores, decisions, and decision
  digests;
- approval and idempotency records and the append-only audit sequence;
- command dispatch progress and send count from ADR-0074, kept distinct from outbox publication state;
  and
- durable simulated-edge command receipts containing the exact prior result for an already seen command
  identifier.

The broker remains the transport and bounded spool. Broker endpoint depth, message settlement, and
publisher confirmation are operational evidence, not replacements for those PostgreSQL facts.

Every guaranteed consumer follows one order:

1. receive and validate the topic, envelope, payload, and delivery metadata;
2. begin a PostgreSQL transaction and claim the inbox identity;
3. apply the domain transition, persist every durable result, and stage every resulting publication in
   the application outbox;
4. commit; and
5. settle the broker delivery as accepted only after that commit succeeds.

A rollback leaves the message unsettled for redelivery. A committed exact duplicate reads the durable
inbox outcome, performs no second domain effect, and may then be settled. Reuse of one message identity
with different canonical bytes is a hard refusal and audit condition, not a duplicate. A consumer may
settle a permanently invalid message only through its declared refusal/dead-message path after the
refusal itself is durable; it may never acknowledge first and attempt persistence later.

Direct `AGENT_RESPONSE` has no acknowledgement step. Its durable boundary starts only when the command
gateway's normalization transaction commits. A simulated drone handles a command in one transaction
that claims the durable receipt, applies the effect once, stores the exact result, and stages the
command-result bytes. A redelivery after restart returns the stored result without applying the effect
again.

### Enlarge command authorization deliberately

For every accepted operator command, the command gateway commits one authorization transaction
containing all applicable effects:

1. consume the exact approval when the command requires one;
2. claim the idempotency key;
3. append the authorization outcome to the durable audit sequence; and
4. stage the existing command-outbox record and exact `DRONE_COMMAND` bytes.

The transaction commits all four or none before the triggering guaranteed `OPERATOR_COMMAND` delivery
is settled. An `OPERATOR_APPROVAL` delivery is independently persisted under the general
commit-before-settlement rule before it can be selected by that transaction. The approval must bind the
exact proposal identifier, proposal digest and version, action parameters, and selected
evidence-decision identity, digest, and version. Expiry, supersession, digest mismatch, rejection, and
second consumption remain hard denials. This explicitly enlarges ADR-0006's atomic set and supersedes
only ADR-0093's instruction to append the audit in a later transaction. It does not merge publisher
confirmation with command progress or weaken any ADR-0093 outbox transition.

### Drain the application outbox in bounded batches

Application outbox rows use ADR-0093's `STAGED`, `CONFIRMED`, and `RECONCILIATION_NEEDED` meanings.
A refused publication leaves its row staged; only broker evidence confirms it; an ambiguous outcome
moves it to reconciliation rather than being guessed successful. A command row remains subject to
ADR-0093's 500-unconfirmed-record bound and remains distinct from ADR-0074 dispatch progress.

One drain iteration selects at most **50** oldest eligible `STAGED` rows in deterministic order. Fifty
is a fetch and work cap, not a minimum, concurrency promise, or atomic bulk publication. The worker does
not hold a database transaction across broker I/O. It publishes each row's exact original topic,
headers, body bytes, and event identity, then persists that row's outcome independently. One refused or
ambiguous row does not confirm any other row in the batch. `RECONCILIATION_NEEDED` rows are never blindly
republished, and a crash between broker acceptance and durable confirmation can cause a duplicate; the
consumer inbox is the corresponding defence.

ADR-0145's recovery order applies: after a disconnect, a service re-establishes its bindings and drains
every owned local outbox before restored readiness. The batch cap does not permit readiness while later
eligible rows remain.

### Bound the central simulated edge without buffering telemetry

The centrally hosted fleet simulator stores every deterministic drone's durable simulated-edge outbox
in the shared PostgreSQL authority, partitioned by drone identity. Each drone independently admits at
most **500 records** and at most **2 MiB** of exact canonical topic, header, and body bytes across its
unconfirmed critical records. Both per-drone limits apply at once to staged and reconciliation-needed
critical publications. `DRONE_EVENT`, `DRONE_COMMAND_RESULT`, and `SECTOR_EVENT` are critical; their
state transition, command receipt where applicable, and outbox insert commit together. Reaching either
limit refuses that drone's new critical transition without evicting or overwriting another record, and
without consuming capacity assigned to another drone or claiming that continuity was preserved.

`DRONE_TELEMETRY` is direct latest-state data and is never written to this outbox. While disconnected or
congested, the simulator drops and counts telemetry publications; the next current update supersedes
them. It does not replay a stale trajectory after reconnect. These two bounds describe the one central
simulation process and do not claim to size persistent storage for an independently deployed physical
edge agent.

### Fix the simulation evidence heuristic and exact approval gate

Evidence score version 1 uses these inclusive integer bands:

| Band | Score |
| --- | --- |
| `NONE` | 0 through 24 |
| `WEAK` | 25 through 49 |
| `SUPPORTED` | 50 through 74 |
| `CORROBORATED` | 75 through 100 |

A `LIVE_SENSOR` contribution has weight 40 and a `LIVE_MODEL` contribution has weight 35. Only evidence
in the `CONTRIBUTING` lifecycle state reaches the score. `RECORDED` evidence is refused from a live
decision under ADR-0008 and ADR-0076; it is never converted into a zero-weight live source. The existing
sum, saturation, distinct-source floor, integer representation, and version rules remain.

`CORROBORATED` requires both a score of at least 75 and at least two distinct live source identifiers.
Thus one sensor plus one model reaches 75, two sensors reach 80, and two models reach only 70. These
weights and bands are deterministic simulation heuristics, not calibrated probabilities or field-ready
confidence claims.

Evidence eligibility alone cannot escalate rescue. Escalation additionally requires one unexpired,
single-use operator approval bound to the exact canonical proposal, action parameters, and selected
evidence-decision identity, digest, and version. A later evidence sequence for the same proposal never
makes an older approval float to the new decision; the operator must approve the new exact decision.

### Add only the selected public mutations

Add these two public authenticated routes to ADR-0097's existing surface:

| Method and path | Contract purpose |
| --- | --- |
| `POST /api/v1/missions/{missionId}/commands` | Validate and durably stage one canonical operator-command event without bypassing the command gateway |
| `POST /api/v1/missions/{missionId}/proposals/{proposalId}/decisions` | Approve or reject the exact proposal and selected evidence decision |

Both routes inherit ADR-0097's exact Host, Origin, bearer, media, byte bound, UUIDv4 idempotency key,
canonical decoding, strict schema, refusal order, redacted error, and stored-response rules. The path
identifiers must agree with the closed request body. The command route persists its idempotent response
and stages the canonical `OPERATOR_COMMAND` CloudEvent in one transaction; it never publishes a drone
command itself.

The proposal-decision route accepts exactly `approve` or `reject`. It derives operator identity from the
validated bearer rather than accepting identity in the body, binds the selected evidence-decision
identity, digest, and version to the exact proposal and action, persists the approval decision, and
stages the canonical `OPERATOR_APPROVAL` event. Approval publication does not consume the approval;
only the command gateway may do that in the authorization transaction. There is no generic
`/approvals` route.

All existing public HTTP reads and ordered SSE remain HTTP. ADR-0107's private authenticated scenario
and fleet start, status, and cancel routes also remain HTTP. Broker topics do not replace request/reply
controls whose bounded HTTP reconciliation semantics are already accepted.

### Keep replay structurally unable to write

Replay retains ADR-0094's zero-network validator, validated bundle, read-only API, browser pacing, and
shared pure reducer. A replay composition constructs no broker publisher, queue consumer, application
outbox worker, operational PostgreSQL writer, proposal writer, evidence writer, approval writer, command
writer, fleet executor, or export sink. The public command and proposal-decision routes are unavailable
in replay mode. A no-op implementation of any such writer is still construction and is forbidden.

Recorded events can be validated and folded but cannot become live evidence, be republished, acquire an
approval, or dispatch a command. Replay session metadata remains the dashboard API's only bounded write
and carries no operational authority.

## Consequences

- The application can recover proposals, evidence decisions, authorization outcomes, command progress,
  and prior simulated-drone results after restart without treating broker spool as a database.
- Guaranteed consumers have one testable commit-before-settlement rule, and inbox/outbox idempotence
  makes at-least-once delivery survivable. It does not create exactly-once delivery: a process can still
  crash after broker acceptance and before recording confirmation, causing a duplicate publish.
- Agent Mesh remains useful for orchestration and model work but holds no application publish authority.
  A compromised agent credential cannot directly manufacture a canonical proposal or command.
- Negative: direct agent responses can be lost while the command gateway is absent. The system must
  expose and measure that gap rather than claim durable Agent Mesh egress.
- Negative: the command-authorization transaction is larger. Append-only audit ordering can increase
  lock duration and contention, and an audit conflict or write failure now rolls back approval
  consumption, idempotency, and command-outbox staging together.
- Negative: PostgreSQL is a shared durability failure domain and requires migrations, retention,
  reconciliation tooling, and storage monitoring for inbox, outbox, proposal, evidence, and receipt
  records.
- Negative: a 50-row drain cap limits one iteration and can increase recovery time. A poisoned or
  repeatedly refused oldest row needs observable policy so it does not become an invisible backlog.
- Negative: each simulated drone refuses critical work at 500 records or 2 MiB rather than losing an
  older record. Across 23 drones, the central PostgreSQL store can therefore hold up to 11,500 records
  and 46 MiB before metadata and indexes. Those per-drone numbers come from a deterministic reference
  workload and need a new measured decision before being represented as physical-edge capacity.
- Negative: telemetry gaps during disconnection are deliberate and replay cannot reconstruct them.
- Negative: the score weights and bands make the demonstration reproducible but can be mistaken for
  empirical confidence unless every UI and report labels them as simulation-only.
- Negative: exact evidence binding means a later evidence decision requires a fresh operator decision,
  even when the action appears unchanged.
- Negative: the integration schema couples application normalization to the pinned Event Mesh Gateway
  output. Upgrading the plugin requires compatibility fixtures and a contract decision if that body
  changes.
- Negative: two new public mutations enlarge the authenticated attack surface and add uncertain-response
  reconciliation cases. Clients must query or retry with the same idempotency identity, never assume a
  lost HTTP response means no commit.
- The lifecycle families, scenario-service broker role, private HTTP control plane, and dashboard SSE
  remain intact rather than being displaced by a generalized event workflow; the effective inventory
  drops by one to 45 endpoints because a least-privilege consumer was removed.

## Alternatives considered

- **Let Agent Mesh agents publish application proposals directly.** Rejected because model output could
  bypass deterministic normalization, canonical metadata, durable proposal storage, and the command
  gateway's least-privilege boundary.
- **Treat `AGENT_RESPONSE` as a guaranteed CloudEvent.** Rejected because the pinned plugin emits an
  integration result without project-owned durable-output or authoritative CloudEvents metadata. A
  contract cannot create delivery evidence the transport path does not provide.
- **Keep agent responses as free-form text.** Rejected because a deterministic normalizer cannot validate
  a closed action vocabulary, correlation, bounds, or refusal shape from unconstrained prose.
- **Reuse `AGENT_PROPOSAL` or `AUDIT` for evidence decisions.** Rejected because a canonical proposal,
  the evidence service's versioned decision, and the record that one was accepted are three different
  facts with different publisher and consumer grants.
- **Use the broker as the system of record or acknowledge before commit.** Rejected because endpoint
  retention and settlement do not persist domain state, and a crash after acknowledgement would lose an
  accepted effect permanently.
- **Use process-memory deduplication.** Rejected because it vanishes on restart and cannot return a prior
  command result to a redelivery.
- **Publish first and persist later, or hold a database transaction across broker I/O.** Rejected because
  the first loses the state after a successful publish and the second couples database locks to an
  unbounded external outcome. The transactional outbox separates those failure domains.
- **Confirm a whole 50-row batch atomically.** Rejected because publisher evidence is per message and one
  ambiguous result must not change the known outcome of its neighbours.
- **Blindly republish reconciliation-needed rows.** Rejected because the broker may already hold the
  exact bytes; guessing turns an ambiguous outcome into an avoidable duplicate storm.
- **Keep authorization audit in a later transaction.** Rejected because a crash after authorization
  commit can leave an accepted command without its append-only authorization record. The selected larger
  transaction pays contention to remove that gap.
- **Buffer telemetry during disconnection.** Rejected because it consumes the critical continuity bound
  with stale positions and replays obsolete state after a current value is available.
- **Let a score threshold or a single source authorize escalation.** Rejected because evidence is a
  simulation heuristic and never substitutes for two-source corroboration plus an exact human approval.
- **Use recorded evidence when live sources are absent.** Rejected by the abstention and replay-isolation
  boundary; outage produces abstention or manual review, not manufactured live confidence.
- **Expose a generic `/approvals` endpoint.** Rejected because it can detach an approval from the exact
  mission, proposal, action, and selected evidence decision the operator reviewed.
- **Move scenario or fleet control onto broker topics.** Rejected because ADR-0107 already provides
  authenticated, bounded, reconcilable HTTP semantics and the scenario-service broker grant is event-only.
- **Construct no-op publishers and writers in replay.** Rejected because construction crosses the
  isolation boundary and a later implementation change can turn a nominal no-op into an effect.
