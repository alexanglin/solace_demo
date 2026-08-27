# ADR-0148: Close the application data-plane wire documents

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0037, ADR-0097, ADR-0101, ADR-0108

## Context

ADR-0146 selects the durable application-processing path, its two authenticated HTTP mutations, the
structured Agent Mesh result boundary, the canonical proposal, the evidence decision, and the exact
approval gate. It deliberately leaves the byte-level documents to the contract change that implements
that path. Without those shapes, a service could choose whether an identifier is in a request, an event,
or an audit detail; a model could return prose; and two consumers could hash different proposal or
evidence members while appearing to implement the same decision.

The existing records also contain closed claims that the new path changes. ADR-0037 says every
application event is a CloudEvent, but ADR-0146 makes `AGENT_RESPONSE` the one plugin integration body.
ADR-0097 closes a public surface that ADR-0146 enlarges, without fixing the new success documents or
status table. ADR-0101 closes the dashboard projection and timeline rules, and ADR-0108 fixes nineteen
dashboard schemas and seventeen server-facing Python models. The application documents must therefore
change those claims explicitly rather than landing as unrecorded exceptions.

The repository contains 49 manifest-owned schemas at this decision. Nineteen are under
`schemas/v1/dashboard/`; seventeen of those are server-facing and two, `mutation-outcome` and
`source-signal`, are browser-only. The seventeen documents selected below therefore make the manifest
total 66 and the dashboard inventory 23. Those totals reconcile with the current manifest; this record
does not conceal an inventory mismatch.

The accepted evidence lifecycle and score rules remain distinct concerns. ADR-0075 owns the item state
machine, including the difference between abstention and rejection. ADR-0076, as partially superseded by
ADR-0146, owns the score, band ordering, recorded-origin refusal, and structural distinct-source floor.
This record gives those facts exact wire spellings; it does not replace their transition or eligibility
semantics. ADR-0112 likewise remains the authority for the event-order witness outside reduced state.

The exact partial supersessions are:

| Record | Clause changed here | Clauses retained |
| --- | --- | --- |
| ADR-0037 | `AGENT_RESPONSE` is not a CloudEvent; it is the sole application plugin-integration body | The closed CloudEvents profile, refusal order, subject/payload binding, schema binding, sequence, trace, and canonical rules for every notification |
| ADR-0097 | The two ADR-0146 mutation routes gain the request, response, and status contracts below | Every existing route and the Host, Origin, bearer, media, body, UUIDv4 idempotency, canonical-decode, stored-response, and refusal-order rules |
| ADR-0101 | The dashboard event union and non-telemetry timeline admit the application projections below | The five-field projection, ordered wrapper, reducer behavior, buffer policy, cursor rules, snapshots, and timeline/reduced-state separation |
| ADR-0108 | The dashboard inventory grows from 19 to 23 schemas and the dashboard API from 17 to 21 server-facing models; two public route entries and their statuses are added | Service-local ownership, canonical-first Pydantic validation, framework-free registries, caller/server separation, and the two browser-only classifications |

## Decision

### Close the notification and integration vocabulary

The version-one application vocabulary is closed as follows:

- operator command types are exactly `assign-sector` and `escalate-rescue`;
- operator approval decisions are exactly `approve` and `reject`;
- the proposal type is exactly `candidate-location`;
- evidence-decision outcomes are exactly `contributing`, `manual-review`, `abstained`, and `rejected`;
- audit record types are exactly `proposal-normalization`, `evidence-decision`, and
  `command-authorization`; and
- `AGENT_RESPONSE` is the only plugin-integration body. It is not a CloudEvent and cannot be treated as
  one by adding plausible envelope members around it.

The notification topic/type pairs are:

| Family branch | Topic suffix after `aerial-rescue/v1/{missionId}/` | CloudEvents `type` |
| --- | --- | --- |
| Operator command | `operator/command/assign-sector` | `aerial-rescue.v1.operator.command.assign-sector` |
| Operator command | `operator/command/escalate-rescue` | `aerial-rescue.v1.operator.command.escalate-rescue` |
| Operator approval | `operator/approval/approve` | `aerial-rescue.v1.operator.approval.approve` |
| Operator approval | `operator/approval/reject` | `aerial-rescue.v1.operator.approval.reject` |
| Agent proposal | `agent/proposal/{agentName}/candidate-location` | `aerial-rescue.v1.agent.proposal.candidate-location` |
| Evidence decision | `evidence/decision/{proposalId}` | `aerial-rescue.v1.evidence.decision` |
| Drone command | `drone/{droneId}/command/escalate-rescue` | `aerial-rescue.v1.drone.command.escalate-rescue` |
| Audit | `audit/proposal-normalization` | `aerial-rescue.v1.audit.proposal-normalization` |
| Audit | `audit/evidence-decision` | `aerial-rescue.v1.audit.evidence-decision` |
| Audit | `audit/command-authorization` | `aerial-rescue.v1.audit.command-authorization` |

ADR-0146's fourteen-family, eleven-notification, two-request/reply, and one-integration-body totals are
unchanged. The existing `assign-sector` drone-command document also remains in force; the new
`drone-command-escalate-rescue` pair is type-specific in the same way.

All objects below are closed. Identifier fields use the existing `IDENTIFIER` rule, `agentName` uses
`AGENT_NAME`, instants use the canonical UTC-millisecond form, digests are lower-case SHA-256, and
coordinates are integer `latitudeMicrodegrees` and `longitudeMicrodegrees`. A field listed in a branch is
required; a field not listed is forbidden.

### Close the two public mutation documents

The two public routes are exactly:

| Method and path | Request schema | `202` response schema |
| --- | --- | --- |
| `POST /api/v1/missions/{missionId}/commands` | `dashboard/operator-command-request` | `dashboard/command-response` |
| `POST /api/v1/missions/{missionId}/proposals/{proposalId}/decisions` | `dashboard/proposal-decision-request` | `dashboard/proposal-decision-response` |

Both routes expose `202` with the named closed response, `401` and `409` with the existing
`dashboard/error` document, and the existing `dashboard/error` as their default response. Both require
the existing lower-case UUID version 4 `Idempotency-Key`, exact Host and Origin, current bearer, JSON
media type, raw-body bound, canonical decode, strict schema, path/body agreement, and stored exact
response behavior in ADR-0097's refusal order. Neither route changes the behavior of a repeated key with
the same body or a repeated key with different canonical bytes.

`dashboard/operator-command-request` is exactly:

```text
{
  missionId,
  action
}
```

`action` is one of these closed branches:

```text
{commandType: "assign-sector", droneId, sectorId}

{
  commandType: "escalate-rescue",
  droneId,
  proposalId,
  proposalDigest,
  proposalVersion: 1,
  evidenceDecisionId,
  evidenceDecisionDigest,
  evidenceDecisionVersion: 1,
  latitudeMicrodegrees,
  longitudeMicrodegrees
}
```

The request carries neither operator identity nor `commandId` nor CloudEvents `id`. The server derives
the non-secret `operatorId` from the successfully validated current-runtime bearer and mints both
identifiers.

`dashboard/command-response` is exactly:

```text
{
  operationVersion: "dashboard-command-response/v1",
  missionId,
  commandId,
  eventId
}
```

`eventId` is the staged `OPERATOR_COMMAND` envelope identifier. Returning `202` means the canonical
operator event and the exact idempotent response committed; it does not mean a drone command was
authorized, published, delivered, or executed.

`dashboard/proposal-decision-request` is exactly:

```text
{
  missionId,
  proposalId,
  proposalDigest,
  proposalVersion: 1,
  evidenceDecisionId,
  evidenceDecisionDigest,
  evidenceDecisionVersion: 1,
  decision: "approve" | "reject",
  action: {
    commandType: "escalate-rescue",
    droneId,
    latitudeMicrodegrees,
    longitudeMicrodegrees
  }
}
```

The request carries no operator identity, issue instant, expiry instant, `approvalId`, or CloudEvents
`id`. The server derives the first three and mints the two identifiers. It validates that the selected
proposal, evidence decision, and action are the exact persisted canonical facts before committing an
operator decision.

`dashboard/proposal-decision-response` is a closed decision-discriminated union. The approve branch is:

```text
{
  operationVersion: "dashboard-proposal-decision-response/v1",
  missionId,
  proposalId,
  approvalId,
  eventId,
  decision: "approve",
  issuedAt,
  expiresAt
}
```

The reject branch has the same members with `decision: "reject"` and no `expiresAt`. `issuedAt` and an
approve branch's `expiresAt` use the canonical instant. `eventId` is the staged `OPERATOR_APPROVAL`
envelope identifier. A rejection still has its own minted `approvalId` and immutable event record, but it
cannot authorize a command.

### Close operator events and executable escalation

The `OPERATOR_COMMAND` payload has `operatorCommandVersion: 1`, `missionId`, `commandId`, `operatorId`,
and the exact `action` union from `dashboard/operator-command-request`. It has no bearer, HTTP
idempotency key, event identifier, or free-form operator text. The path mission, envelope subject,
payload mission, topic command type, CloudEvents type, and `action.commandType` agree. The server-minted
`commandId` is stable across the idempotent HTTP response and staged operator event; the CloudEvents `id`
is a different identifier.

The `OPERATOR_APPROVAL` payload has these common members:

```text
{
  operatorApprovalVersion: 1,
  missionId,
  approvalId,
  operatorId,
  decision,
  issuedAt,
  proposalId,
  proposalDigest,
  proposalVersion: 1,
  evidenceDecisionId,
  evidenceDecisionDigest,
  evidenceDecisionVersion: 1,
  action: {
    commandType: "escalate-rescue",
    droneId,
    latitudeMicrodegrees,
    longitudeMicrodegrees
  }
}
```

The `approve` branch additionally requires canonical `expiresAt`; the `reject` branch forbids it. The
payload's `issuedAt` equals the envelope `time`. `operatorId` is the wire spelling of ADR-0006's
non-secret operator identity; the bearer itself never appears. `decision` agrees with the topic and
CloudEvents type. Approval identity, proposal identity and digest/version, selected evidence-decision
identity and digest/version, and every action member are immutable bindings. Publishing this event does
not consume the approval.

The `DRONE_COMMAND` `escalate-rescue` payload is exactly:

```text
{
  missionId,
  droneId,
  commandId,
  approvalId,
  proposalId,
  proposalDigest,
  proposalVersion: 1,
  evidenceDecisionId,
  evidenceDecisionDigest,
  evidenceDecisionVersion: 1,
  latitudeMicrodegrees,
  longitudeMicrodegrees
}
```

As with `drone-command-assign-sector`, the type-specific topic and CloudEvents type carry the command
type; it is deliberately not repeated in this payload. The command gateway alone mints and publishes
this document after the ADR-0146 authorization transaction has consumed the named approval. The topic
`droneId`, envelope subject, and every repeated proposal, evidence, command, approval, and action member
must agree with the authorized persisted facts.

### Make the Agent Mesh result structured and non-authoritative

`schemas/v1/integration/agent-response.schema.json` owns the non-CloudEvent body on
`aerial-rescue/v1/{missionId}/agent/response/{agentName}`. Its common members are exactly:

```text
{
  agentResponseVersion: 1,
  missionId,
  agentName,
  invocationId,
  correlationId,
  outcome
}
```

The `candidate` branch adds one closed `result`:

```text
{
  outcome: "candidate",
  result: {
    proposalType: "candidate-location",
    sourceEventId,
    sourceEventDigest,
    droneId,
    latitudeMicrodegrees,
    longitudeMicrodegrees,
    commandType: "escalate-rescue"
  }
}
```

The `abstained` branch adds only `reason`, with exactly one of `timeout`, `transport-error`,
`model-error`, `invalid-output`, or `identity-mismatch`. It has no `result`. Neither branch admits prose,
raw model output, a prompt, a stack trace, an upstream error body, arbitrary metadata, an executable
topic, or an application event identifier.

The topic mission and agent name must match the body. The command gateway also checks the pending
invocation/correlation pair and the candidate's source event identity and digest before normalization.
The document is direct integration evidence only. It acquires durable application authority only if the
normalization transaction commits a canonical proposal or an audit outcome under ADR-0146.

### Normalize one exact canonical proposal

The `AGENT_PROPOSAL` `candidate-location` payload is exactly:

```text
{
  canonicalizationVersion: 1,
  proposalVersion: 1,
  missionId,
  proposalId,
  proposalType: "candidate-location",
  agentName,
  sourceInvocationId,
  sourceEventId,
  sourceEventDigest,
  commandType: "escalate-rescue",
  droneId,
  latitudeMicrodegrees,
  longitudeMicrodegrees,
  proposalDigest
}
```

The command gateway computes `proposalDigest` in the `proposal-digest` context over the canonical object
formed by deleting exactly `proposalDigest` from that accepted payload. No other member is omitted or
injected. `canonicalizationVersion: 1` is therefore inside the hashed bytes, as ADR-0027 requires.
Callers recompute from accepted members and compare in constant time; a supplied digest never proves its
own correctness.

`agentName` and `proposalType` agree with the topic, and `proposalType` also agrees with the CloudEvents
type and body. The envelope identity, source, time, sequence, correlation, causation, and trace members
remain command-gateway-owned metadata outside the proposal digest. The normalized payload contains no
raw response or model prose.

### Close the evidence decision and contributor bound

Every `EVIDENCE_DECISION` payload has these common members:

```text
{
  canonicalizationVersion: 1,
  evidenceDecisionVersion: 1,
  missionId,
  proposalId,
  proposalDigest,
  proposalVersion: 1,
  evidenceDecisionId,
  outcome,
  evidenceDecisionDigest
}
```

`evidenceDecisionDigest` is computed in the `evidence` context over the canonical object formed by
deleting exactly `evidenceDecisionDigest` from the accepted payload. It therefore covers every other
member, including the branch discriminator, score and contributors when present, reason when present,
and `canonicalizationVersion`. The proposal identifier in the topic agrees with the payload; later
decisions for the same proposal use new evidence-decision identifiers and sequence values.

The `contributing` branch additionally and exclusively carries:

```text
{
  outcome: "contributing",
  scoreVersion: 1,
  score,
  band,
  contributors
}
```

`score` is an integer from 0 through 100. `band` is exactly `none`, `weak`, `supported`, or
`corroborated`, and agrees with ADR-0146's inclusive 0-24, 25-49, 50-74, and 75-100 bands plus
ADR-0076's distinct-source cap. `contributors` has at least one and at most 23 entries. Each entry is a
closed object with exactly:

```text
{
  evidenceItemId,
  sourceId,
  origin: "live-model" | "live-sensor",
  weight,
  provenanceDigest
}
```

`weight` is exactly 35 for `live-model` and 40 for `live-sensor`; the schema expresses this as two
origin-discriminated branches rather than as an unconstrained integer. The service/domain boundary also
enforces distinct `sourceId` values and the two-source requirement for `corroborated`, because the
approved schema vocabulary cannot express uniqueness by one object member. The 23-entry maximum is the
reference fleet bound. It is not a physical-fleet scale or capacity claim.

The three non-contributing branches carry exactly one redacted `reason` and no `scoreVersion`, `score`,
`band`, or `contributors`:

| Outcome | Closed reason vocabulary |
| --- | --- |
| `manual-review` | `policy-referral`, `conflicting-evidence`, `insufficient-live-sources` |
| `abstained` | `timeout`, `transport-error`, `model-error`, `invalid-output`, `identity-mismatch`, `declined` |
| `rejected` | `invalid-output`, `identity-mismatch`, `provenance-missing`, `provenance-mismatch`, `recorded-origin`, `human-dismissal` |

These reasons preserve ADR-0075's distinction between a failure to assert, a policy referral, and a
refused assertion. They are safe display labels, not upstream text. Recorded origin remains impossible in
a contributing decision; it is represented only as a rejection reason when that denial is recorded.

### Make audit records typed rather than textual

The `AUDIT` payload is a closed union discriminated by `recordType`. Every branch has
`auditVersion: 1`, `missionId`, `recordId`, its exact subject identities and digests, a typed `outcome`,
and a redacted enum `reason` only on branches that do not accept the subject. It has no `detail`,
`message`, `text`, raw request, raw response, arbitrary map, prompt, completion, stack trace, or upstream
body.

The `proposal-normalization` branch has common `agentName`, `invocationId`, and `correlationId` subjects.
Its closed outcomes are:

- `normalized`, which additionally requires `sourceEventId`, `sourceEventDigest`, `proposalId`,
  `proposalDigest`, and `proposalVersion: 1`, and has no reason;
- `abstained`, which requires one of the five `AGENT_RESPONSE` abstention reasons and has no proposal
  identity or digest; and
- `refused`, which requires exactly one of `schema-invalid`, `correlation-mismatch`,
  `identity-mismatch`, `unsupported-action`, or `digest-mismatch`, and has no proposal identity or
  digest.

The `evidence-decision` audit branch requires `proposalId`, `proposalDigest`, `proposalVersion: 1`,
`evidenceDecisionId`, and `evidenceDecisionDigest`. Its `outcome` is the exact evidence-decision outcome.
A `contributing` record has no reason; the other outcomes require a reason from their corresponding
evidence-decision reason vocabulary. The audit record does not duplicate score or contributor arrays;
the bound evidence decision is their authority.

The `command-authorization` branch requires `commandId`, `operatorId`, and the exact discriminated
operator-command `action`. Its `outcome` is `authorized` or `refused`:

- an authorized `assign-sector` record has no approval identifier and no reason;
- an authorized `escalate-rescue` record additionally requires `approvalId` and has no reason; and
- a refused record has no `approvalId` and requires exactly one of `approval-missing`,
  `approval-rejected`, `approval-expired`, `approval-superseded`, `approval-consumed`,
  `proposal-mismatch`, `evidence-decision-mismatch`, `action-mismatch`, `idempotency-conflict`, or
  `outbox-full`.

The action carries the subject identifiers and digests named earlier, so an escalation authorization
record binds the proposal, evidence decision, drone, and coordinates without a free-form details field.
The audit `recordType` agrees with the topic and CloudEvents type. These audit documents record outcomes;
they do not confer authority and do not replace the bound proposal, evidence, approval, or command rows.

### Register exactly seventeen new schema documents

The following paths and reserved-host identifiers are exact. Each identifier is
`https://aerial-rescue.invalid/` followed by the path shown.

| Kind | Repository path |
| --- | --- |
| Payload | `schemas/v1/payload/operator-command.schema.json` |
| Event | `schemas/v1/event/operator-command.schema.json` |
| Payload | `schemas/v1/payload/operator-approval.schema.json` |
| Event | `schemas/v1/event/operator-approval.schema.json` |
| Payload | `schemas/v1/payload/agent-proposal.schema.json` |
| Event | `schemas/v1/event/agent-proposal.schema.json` |
| Payload | `schemas/v1/payload/evidence-decision.schema.json` |
| Event | `schemas/v1/event/evidence-decision.schema.json` |
| Payload | `schemas/v1/payload/drone-command-escalate-rescue.schema.json` |
| Event | `schemas/v1/event/drone-command-escalate-rescue.schema.json` |
| Payload | `schemas/v1/payload/audit.schema.json` |
| Event | `schemas/v1/event/audit.schema.json` |
| Integration | `schemas/v1/integration/agent-response.schema.json` |
| Dashboard | `schemas/v1/dashboard/operator-command-request.schema.json` |
| Dashboard | `schemas/v1/dashboard/command-response.schema.json` |
| Dashboard | `schemas/v1/dashboard/proposal-decision-request.schema.json` |
| Dashboard | `schemas/v1/dashboard/proposal-decision-response.schema.json` |

This is twelve payload/event documents, one integration document, and four dashboard documents: 17 new
schemas, taking the manifest from 49 to 66. The dashboard directory grows from 19 to 23 schemas.

Each composed event schema links an exact CloudEvents `type`, its payload `dataschema`, and the matching
payload branch. The operator-command event has separate `assign-sector` and `escalate-rescue` branches;
operator-approval has separate `approve` and `reject` branches; audit has one branch for each of its
three record types. This prevents a valid payload branch from appearing behind a different valid type.

The contracts-owned `BINDINGS` table remains closed. It gains only the notification type rows above;
`AGENT_RESPONSE` is validated by its integration schema and topic/body checker and never receives a
synthetic `BINDINGS` row. In addition to ADR-0037's mission, subject, schema, source, and topic rules:

- `agentName` agrees between topic and body;
- `proposalId` agrees between evidence-decision topic and body;
- `decision` agrees among approval topic, CloudEvents type, and body;
- `proposalType`, `recordType`, and a repeated `commandType` agree among topic, CloudEvents type, and
  body; and
- every identifier or discriminator repeated across topic, type, envelope, payload, HTTP path, request,
  persisted fact, response, or derived publication must be equal. A consumer refuses disagreement rather
  than choosing one carrier.

Every array in a new schema has explicit `minItems` and `maxItems`; the only new wire array is the
1-through-23 contributor list. All string members are an identifier, agent name, canonical instant,
lower-case digest, fixed constant, or closed enum. No new schema admits arbitrary upstream, model, audit,
or operator text.

### Project application records without enlarging reduced mission state

Every newly closed CloudEvent notification has an explicit dashboard decision. The projections are:

| Payload/event | Dashboard `kind` | `eventClass` | Reduced-state effect |
| --- | --- | --- | --- |
| Operator command | `operatorCommand` | `COMMAND` | Timeline only |
| Operator approval | `operatorApproval` | `APPROVAL` | Timeline only |
| Agent proposal | `agentProposal` | `EVIDENCE` | Timeline only |
| Evidence decision | `evidenceDecision` | `EVIDENCE` | Timeline only |
| Drone command escalation | `droneCommand` | `COMMAND` | Timeline only |
| Audit | `auditRecord` | `AUDIT` | Timeline only |

For an accepted notification, `mission` is the payload `missionId`, `time` is the envelope time, and
`data` is the closed payload with `missionId` removed. The evidence-decision projection additionally
removes the internal `evidenceDecisionDigest` and removes nothing else. Thus its `data` retains the
proposal binding, evidence-decision identifier/version, outcome, and the exact score/contributor or
reason branch without duplicating the mission or exposing the decision's self-integrity member.

All six projections are non-droppable and append to the ordered non-telemetry timeline. If a type in
those families already has a projection under an Accepted record, that existing kind, class, and data
rule remains rather than acquiring a duplicate projection. No row adds a member to
`dashboard-reduced-state`; folding a successor advances `latestAuditOrdinal` and ADR-0112's external
`latestEventDigest` while leaving every other reduced-state member unchanged. Snapshot and replay
timeline branches, Python/TypeScript projection parity, fixtures, and digest parity grow together.

The direct `AGENT_RESPONSE` integration body has no CloudEvents time or durable audit ordinal and cannot
be fabricated into an ordered dashboard notification. Durable operator display of its outcome comes
from the normalized proposal, evidence decision, or typed audit record. This does not remove ADR-0146's
direct dashboard subscription; it prevents that lossy integration input from masquerading as a durable
timeline fact.

### Keep schema and trust-boundary ownership local

The committed schemas and shared fixtures remain normative. `packages/contracts` remains
framework-free and owns canonicalization, topics, notification bindings, envelope validation, dashboard
projection, and pure reduction. Each service owns strict Pydantic models for every broker or integration
ingress it actually consumes; importing another service's Pydantic implementation is forbidden. The
dashboard API owns the four new HTTP documents and the application-event projection at its boundary,
bringing its server-facing dashboard model count from 17 to 21.

Generated TypeScript covers all 23 dashboard schemas: the existing 19 plus the four HTTP documents.
Runtime validation remains independent of generated types. The browser-only `mutation-outcome` schema
may add closed operation branches that reference `command-response` and `proposal-decision-response`,
but it is presentation state and is never the HTTP response registered for either route. It and
`source-signal` remain the two browser-only documents without Python models.

## Consequences

- A raw model or plugin response cannot choose application identity, metadata, a proposal digest, or an
  executable topic. The normalizer has one closed candidate shape and one closed abstention shape.
- Proposal, evidence, approval, command, and audit consumers can recompute the same covered bytes and
  refuse any changed identity, action, score, contributor, or reason.
- Exact response identities make an uncertain HTTP result reconcilable with the same idempotency key
  without claiming that `202` means authorization or execution.
- The evidence-decision timeline exposes the decision and its bounded contributors while keeping its
  self-integrity digest inside the service boundary. ADR-0112's external event witness still proves the
  accepted ordered wire even when the reduced mission state has no new member.
- Closed audit unions make expected outcomes queryable and redactable without relying on prose. They
  also make adding a new reason or record type a versioned contract decision rather than a logging edit.
- Negative: six payload/event documents each have both payload and composite branches, and every new
  branch needs accepted fixtures, one-reason negatives, bindings, projection parity, and service-local
  models. The deliberate duplication is the cost of cross-process and cross-language verification.
- Negative: the 23-contributor maximum is tied to the reference fleet. A larger deployment cannot emit a
  conforming decision merely by configuring more drones; it needs a versioned capacity decision and new
  evidence rather than silently truncating contributors.
- Negative: timeline-only application events increase snapshot, replay-bundle, browser-memory, and SSE
  pressure even though reduced mission state stays small. The existing bounded overload behavior may
  close and resnapshot a slow client more often.
- Negative: a direct `AGENT_RESPONSE` can be lost and cannot enter the durable ordered timeline on its
  own. The typed integration schema improves safety, not delivery.
- Negative: `operatorId` and typed audit subjects are non-secret but visible to dashboard consumers and
  recordings. They must remain synthetic local identifiers and must not be expanded into personal data.
- Negative: branch-specific reason enums omit unanticipated diagnostic detail. Operators receive a safe
  category; deeper diagnosis must use separately redacted operational telemetry, not widen the wire with
  raw errors.
- Negative: changing a digest-covered member, even one that appears presentational, invalidates the
  proposal or evidence identity and therefore any approval bound to it. Compatibility requires a new
  version rather than permissive reading.

## Alternatives considered

- **Keep application payloads as implementation-local Pydantic models.** Rejected because separately
  deployed Python services, TypeScript, fixtures, recorder data, and replay would have no common
  compatibility authority.
- **Wrap `AGENT_RESPONSE` in a project-minted CloudEvent at ingress.** Rejected because invented sequence,
  time, source, and delivery metadata would make a lossy plugin result look like an authoritative
  notification. Normalization into `AGENT_PROPOSAL` is the honest durable boundary.
- **Accept free-form agent output and parse it heuristically.** Rejected because prose cannot enforce
  identity, correlation, coordinate, action, or redaction boundaries deterministically.
- **Let HTTP clients supply operator, command, approval, event, issue, or expiry identity.** Rejected
  because those fields belong to the authenticated server and allowing them in a closed request creates
  disagreement and impersonation surfaces.
- **Return `mutation-outcome` directly from the two routes.** Rejected because it is browser operation
  state, not a committed server response. The new response documents carry stable reconciliation
  identities without UI phase.
- **Use one generic approval endpoint or a generic command map.** Rejected because it would detach the
  decision from the path-bound mission/proposal and turn the deny-by-default action table into arbitrary
  input.
- **Hash only selected proposal or evidence members.** Rejected because an omitted member could change
  without invalidating an approval or decision. The exact accepted payload minus only its named self
  digest is the covered document.
- **Put score, band, and an optional contributor list on every evidence outcome.** Rejected because an
  abstention or rejection is not a low score. The discriminated branches make that confusion
  unrepresentable.
- **Allow recorded evidence with zero weight.** Rejected by ADR-0076: it would still enter the decision
  and could count as a corroborating source. Recorded origin is a typed rejection, never a contribution.
- **Use an unbounded contributor or audit-detail array.** Rejected because it gives model-controlled or
  upstream-controlled data an unbounded memory and disclosure surface, and because 23 is the measured
  reference-fleet scope of this release.
- **Fold evidence, approvals, commands, or audit into new reduced-state members.** Rejected because the
  ordered timeline already preserves those facts and ADR-0101 keeps reduced mission state limited to
  current mission, fleet, connectivity, telemetry, sectors, and ordinal. A later UI that needs a current
  derived summary must settle that reducer contract explicitly.
- **Expose the evidence decision's internal digest to the browser projection.** Rejected because the
  event-order witness proves the delivered normalized event and the service owns decision-integrity
  verification. Carrying both invites the UI to treat an internal hash as authorization.
