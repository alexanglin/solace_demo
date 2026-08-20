# ADR-0037: Profile the CloudEvents 1.0 JSON envelope with required sequence and tracing extensions over the integer payload profile

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) requires every application event to be a CloudEvents 1.0 JSON
envelope and lists nine members plus "sequence number, correlation ID, causation ID, schema version,
and trace context" without saying where the last five ride or what each value looks like. Python and
TypeScript must both reimplement the envelope from the written contract, every payload must be
validated before it affects state ([SAFETY.md](../SAFETY.md)), duplicates and stale sequences are
rejected per producer, and threat-model T4 asks that an event's claimed identity be checked against
the topic it arrived on.

Facts read from the CloudEvents 1.0.2 specification on 2026-08-20: `id`, `source`, `specversion`,
and `type` are required; attribute and extension names are lower-case ASCII letters and digits of
at most 20 characters; the JSON format places extensions at the top level beside the core
attributes, carries JSON content in `data`, and offers `data_base64` for binary content; the
official `sequence` extension is a lexicographically orderable string for which padding is
recommended; the distributed-tracing extension makes `traceparent` required and `tracestate`
optional, both as W3C Trace Context strings. [ADR-0027](0027-integer-only-canonical-serialization.md)
fixes the value space of digest-covered payloads to an integer-only JSON profile and the instant to
`YYYY-MM-DDTHH:MM:SS.sssZ`.

## Decision

An application event is a CloudEvents 1.0 structured-mode JSON object with a **closed** member set.
Twelve members are required: `specversion` (`"1.0"`), `id`, `source`, `type`, `subject`, `time`,
`datacontenttype` (`"application/json"`), `dataschema`, `data`, and the extensions `sequence`,
`correlationid`, and `traceparent`. Two are optional: `causationid` and `tracestate`. Any other
member, `data_base64` included, is refused, and a JSON `null` is never read as absence.

- `subject` is the mission identifier and must equal `data.missionId`; `source` is
  `urn:aerial-rescue:<producerKind>:<producerId>` and scopes `sequence`; `type` is derived from the
  topic as [ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md) defines and must have a bound
  payload schema; `dataschema` is that schema's identifier and carries the schema version as its
  `v1` path segment, so there is no separate version attribute; `time` is the canonical instant and a
  real calendar date.
- `sequence` is a fifteen-digit zero-padded decimal string, so string order equals numeric order and
  the value is an exact TypeScript number. It is producer-scoped, rejects stale updates within one
  source, and never orders the mission timeline, which
  [ADR-0003](0003-postgres-durable-mission-store.md)'s audit ordinal does.
- `data` is an object inside the ADR-0027 canonical profile for every event type, direct-delivery
  telemetry included, and repeats `missionId` and the topic's identifier parameters.
- Ingress text is decoded through the canonical decoder, so a repeated key is refused rather than
  merged. The broker adapter then checks the envelope against the topic the message actually arrived
  on: the derived `type`, the mission identifier, and each identifier parameter must agree.

The profile is validated by a pure function in `packages/contracts` whose refusals are typed values
in a fixed order; a Pydantic wrapper arrives with the broker-ingress adapter.

## Consequences

- The Event Mesh Gateway, the recorder, and Broker Manager read sequence, correlation, causation,
  and trace context without a payload schema, and a transport counter never sits inside
  digest-covered bytes.
- One numeric value space exists in both languages: telemetry coordinates are integer microdegrees
  like every other payload, so every golden fixture is readable and one oracle serves all events.
- Every producer, the deterministic simulator and replay included, must mint a W3C `traceparent`
  for every event at the telemetry rate. Replay compares reduced dashboard state
  ([ADR-0009](0009-isolated-side-effect-free-replay.md)), so this is a producer burden rather than a
  determinism break.
- A JSON Schema cannot express everything the validator refuses: Draft 2020-12 `integer` admits
  `1.0`, `maxLength` counts code points where the profile counts bytes, and calendar validity,
  binding rules, and repeated keys are outside the schema. Those rules are validator-only and must be
  unit-tested in both languages; they are never golden negatives.
- Adding an event type is a Tier 1 change: a binding row, a payload schema, a composed event schema,
  fixtures, and a manifest entry land together, or the type is refused as unbound.
- Absence is the only spelling of "no value", so a producer cannot emit `causationid: null`; it
  omits the member.

## Alternatives considered

- **Carry sequence, correlation, causation, and trace context inside `data`.** Rejected: the gateway,
  recorder, and Broker Manager would need a payload schema to read them, CloudEvents defines official
  attributes for two of them, and a transport counter would sit inside digest-covered bytes.
- **An unpadded or integer sequence.** Rejected: the official extension is a string ordered by string
  comparison, under which `10` sorts before `9`; a zero-padded fifteen-digit decimal satisfies it and
  still parses exactly into a TypeScript number.
- **`missionid` and `droneid` extension attributes.** Rejected: `subject` and the topic already carry
  them and `data` repeats them; three carriers of one fact invite disagreement.
- **`subject` as the full publish topic.** Rejected: it duplicates broker metadata inside the
  envelope, and CloudEvents defines `subject` as the object within the producer's context, which is
  the mission.
- **A separate `schemaversion` attribute.** Rejected: the major version already appears identically
  in the topic, the `type`, and `dataschema`; a second carrier needs a cross-check that carries no
  information.
- **Treat a `null` optional attribute as absent, as the JSON format permits.** Rejected: the profile
  has one spelling per logical value, and absence is that spelling.
- **Define the envelope as a Pydantic model in `packages/contracts`.** Rejected: it adds a technology
  pin to a Tier 1 member, moves the rules into class bodies the mutation gate does not score, and
  Pydantic's coercions are the opposite of the strictness a trust boundary wants.
- **Admit floating-point values for telemetry because it is never digested.** Rejected: two numeric
  value spaces double every converter, and the golden fixtures stop being one oracle.
