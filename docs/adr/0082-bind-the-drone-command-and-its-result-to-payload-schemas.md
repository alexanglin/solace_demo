# ADR-0082: Bind the drone command and its result to payload schemas, one schema per command type

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

`BINDINGS` in `packages/contracts` has three rows: drone telemetry, the salient drone event, and the
gateway response. `binding_for` refuses every other CloudEvents type as `UNKNOWN_TYPE`, and
`parse_envelope` reaches it unconditionally. Two consequences follow for the two command families, and
both are blocking rather than cosmetic:

- A drone cannot validate an arriving command at its trust boundary, because a well-formed command
  carries a type no binding names.
- No component can build a command result, because the pattern every publisher here uses reads the
  payload schema identifier out of the binding.

`services/fleet_simulator/AGENTS.md` records the second half already: "Command-result events | No payload
or event schema binds that family, and an ACL grant is not a wire contract." The ACL grants exist
([ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md)), the durable queues exist
([ADR-0080](0080-provision-one-durable-queue-per-guaranteed-consumer.md)), and the retry schedule's four
values exist ([ADR-0081](0081-give-command-dispatch-one-interval.md)). The wire contract is what is left.

Two facts about the topic grammar decide most of the shape, and neither is a choice made here.
`Family.type_suffix` drops the levels whose placeholders obey the IDENTIFIER and AGENT_NAME rules and
keeps the rest, so:

| Family | Template | CloudEvents type |
| --- | --- | --- |
| `DRONE_COMMAND` | `drone/{droneId}/command/{commandType}` | `aerial-rescue.v1.drone.command.{commandType}` |
| `DRONE_COMMAND_RESULT` | `drone/{droneId}/command-result/{commandId}` | `aerial-rescue.v1.drone.command-result` |

`commandType` is a KIND level and survives into the type, so the command family is not one type but one
per command type. `commandId` and `droneId` are both IDENTIFIER levels and are dropped, so the result
family is exactly one type. That the command family fans out is not a new authority: ADR-0036 predicted
it — "a misspelt `commandType` is accepted at the topic layer; it is refused later because no payload
schema is bound to the resulting `type`" — and
[ADR-0041](0041-deny-by-default-command-authority-table.md) is what makes the set finite by closing
`commandType` to `assign-sector` and `escalate-rescue`.

`check_topic_binding` adds one more constraint that is easy to miss: every IDENTIFIER level a topic names
must be repeated in the payload. The result topic names both `droneId` and `commandId`, so a result
payload must carry both.

## Decision

- **One payload schema per command type**, each with its own composed event schema, its own golden
  fixtures, and its own `BINDINGS` row. Not one schema discriminated by a `commandType` member.
- **`schemas/v1/payload/drone-command-assign-sector.schema.json`** carries `missionId`, `droneId`,
  `commandId`, and `sectorId`, all four IDENTIFIER-formed, with `additionalProperties` false.
- **`schemas/v1/payload/drone-command-result.schema.json`** carries `missionId`, `droneId`, `commandId`,
  and `outcome`, where `outcome` is one of `acknowledged`, `succeeded`, `failed`.
- **The command payload carries `commandId` even though the command topic has no such level.** The result
  topic is keyed by it, `check_topic_binding` forces a result payload to repeat it, and a drone can learn
  it only from the command. Retries reuse it, which `CONTRACTS.md` already requires, so it is a different
  identifier from the envelope's `id`, which is unique per publication.
- **The command payload does not carry `commandType`**: the CloudEvents type and the topic level both
  carry it, and a third copy would be a second home for one fact. A fixture adding it is refused, so that
  is an executable statement rather than a comment.
- **The result's `outcome` vocabulary is three of the six `CommandState` names of ADR-0074 — the three a
  drone can cause.** `ACCEPTED` and `IN_FLIGHT` are the dispatcher's view, and `ABANDONED` is the
  gateway's own verdict on a command it stopped sending, so a drone can never report it. The wire word is
  a past-tense state name rather than an event name, matching the one `outcome` member already on the
  wire.
- **The word-to-event mapping is not in `packages/contracts`.** That package must not import
  `packages/domain`, so the total table from these three words to `CommandEvent` lives in the consuming
  service and a contract test asserts the two agree.
- **`escalate-rescue` is deliberately left unbound by this record.** Its payload members would be the
  action parameters an approval's proposal digest is recomputed over
  ([SAFETY.md](../SAFETY.md), [ADR-0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)),
  and the `agent/proposal/{agentName}/{proposalType}` family has no schema at all yet. Fixing that member
  set here would settle what every approval binds inside a command's schema, before the proposal it binds
  to has one.
- **This record decides no dashboard projection.** `PROJECTIONS` names only telemetry today, so two bound
  types are already unprojected and nothing is made inconsistent by a third.

## Consequences

- The drone-command and command-result families become wire contracts rather than ACL grants, so a
  consumer can validate an arriving command at its trust boundary and a producer can build a result the
  same way it builds telemetry.
- Leaving `escalate-rescue` unbound is a **safe** failure with a name. `binding_for` refuses the type as
  `UNKNOWN_TYPE`, so the sole publisher of executable commands cannot publish a rescue escalation at all
  until the approval half lands. The safety boundary is briefly enforced by the absence of a schema, which
  is a weaker mechanism than an approval check and is not a substitute for one.
- One schema per command type means the type-to-payload agreement is checked. With a shared schema an
  envelope typed `...escalate-rescue` carrying an `assign-sector`-shaped payload would satisfy both the
  `dataschema` constant and the union payload; with separate schemas the composed event schema refuses it
  for exactly one reason.
- Negative: **the command family's schema count grows with the command-authority table.** A third command
  type is now four artifacts and a `BINDINGS` row rather than one enum member, and forgetting any of them
  produces a type nothing can publish. That coupling is deliberate — it is what stops an unbound command
  type from reaching a drone — but it makes adding a command type a contracts change.
- Negative: **a command result carries no reason for a failure.** `CommandEvent.FAIL` carries none and no
  document names a drone-failure vocabulary, so an operator sees `failed` with no why. Closing that gap
  needs a new record and a new closed kind set, because an open kind set here would put unclosed
  vocabulary on a safety-adjacent wire.
- Negative: **the assign-sector payload carries no sector geometry.** `sectorId` names a sector whose
  shape no contract defines, so a drone receiving one has to already know what the identifier means.
  ADR-0073 left the geometry outside its machine and `LIMITATIONS.md` bounds the search model; this record
  inherits both rather than resolving either.
- Negative: **the result payload cannot express the acknowledge-before-fail rule.** That a command may
  only fail after it is acknowledged is a rule about a fold of two messages, and no schema can see the
  earlier one. The dispatch machine in `packages/domain` refuses the illegal edge, so the rule is enforced
  where the state is, not where the bytes are.
- Negative: `commandId` in the command payload is a member a reader will assume is the envelope's `id`
  until they read why it is not. The alternative was worse — see below.

## Alternatives considered

- **One `drone-command` payload schema with a `commandType` discriminator.** Rejected for the reason the
  gateway-response schema already records for its own outcome: tying members to a discriminator "would
  make every negative fixture that touches outcome fail for two reasons at once", and this repository's
  one-reason rule for negative fixtures ([ADR-0038](0038-reserved-host-schema-identity-and-one-reason-fixtures.md))
  cannot express a fixture whose command type and argument members change together. It would also need the
  composed event schema's `type` to be an `enum` of two rather than a `const`, which weakens the
  `type-not-<x>` negative from "this schema is for exactly one type" to "one of two".
- **Binding `escalate-rescue` in the same increment.** Rejected: see the Decision. It would settle the
  approval's digest-covered parameter set inside a command schema, and it would need an `evidenceScore`
  and a `scoreVersion` whose bounds must equal the domain's, which is a new cross-layer dependency in a
  Tier 1 oracle — for a band vocabulary whose boundaries are still an open parameter row.
- **Putting `commandType` in the command payload as well.** Rejected: the type and the topic level both
  carry it, and a payload copy would let the three disagree. The fixture that adds it and is refused is
  what keeps that true.
- **Keying the result topic by the envelope `id` instead of a `commandId`.** Rejected: retries reuse the
  original command identifier, and a retry is necessarily a new event with a new `id` and a new sequence,
  so an `id`-keyed result topic would scatter one command's results across as many topics as it had sends.
- **An `attempt` or `sendCount` member on the command.** Rejected: ADR-0074 puts the count in the
  dispatcher's own state, and on the wire it would let a drone treat a retry differently from a first
  send, when the idempotency rule requires it treat them the same.
- **Event-shaped result words — `acknowledge`, `succeed`, `fail`.** Rejected: an envelope states that
  something happened, and the one `outcome` member already on the wire is past-tense. Reusing
  `CommandState` values also means the wire word is compared against a name that exists in exactly one
  place rather than against a second vocabulary.
- **A `lastAcknowledgedSequence` member on the result.** Rejected: reconnect reconciliation is explicitly
  outside ADR-0074 and is a different report. Adding it later is a schema change; adding it now would be a
  member nothing produces.
