# ADR-0152: Bind proposals to the complete source event

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none; defines the `sourceEventDigest` preimage left implicit by ADR-0148

## Context

[ADR-0148](0148-close-the-application-data-plane-wire-documents.md) requires every candidate Agent
Mesh response, canonical proposal, and normalized audit record to carry a `sourceEventId` and
`sourceEventDigest`. It also requires the command gateway to check those values against trusted
forwarded context before normalizing a model result. The record closes their JSON form but does not
define which bytes the digest covers, which domain-separation context it uses, or where the trusted
value crosses the official Event Mesh Gateway.

That omission admits incompatible implementations. Hashing only the salient-event payload would not
bind the producer, event identifier, time, sequence, correlation, trace, or schema. Hashing serialized
input bytes would make insignificant object-member order observable and would disagree after canonical
decoding. Treating an arbitrary 64-character value from model output as the digest would make the field
decorative precisely where ADR-0148 intends a provenance check.

The application CloudEvent profile does not carry `canonicalizationVersion`; it carries CloudEvents
`specversion` and a versioned `dataschema`. ADR-0027 nevertheless requires the canonicalization version
inside every digest-covered document. A source-event digest therefore needs a versioned wrapper rather
than an unversioned special case or another self-referential event member.

## Decision

`sourceEventDigest` is the lowercase SHA-256 digest in the new `source-event` domain context over these
exact canonical bytes:

```text
aerial-rescue/canonical/v1\n
source-event\n
canonical-json({"canonicalizationVersion":1,"event":<accepted CloudEvent document>})
```

`<accepted CloudEvent document>` is reconstructed from the typed envelope returned by
`packages/contracts`; it includes every required and present optional envelope member and the complete
payload. Nothing is omitted. Member order in received JSON is immaterial, while a change to identity,
source, type, subject, time, schema, data, sequence, correlation, causation, trace parent, or trace state
changes the digest. The function accepts an already validated `Envelope`, not an arbitrary mapping, so
malformed input cannot acquire a provenance digest by calling the helper directly.

For the salient-event path, the fleet computes the digest from the same accepted envelope it publishes
and places it in the broker user property `aerial-rescue-source-event-digest`. The official Event Mesh
Gateway takes `sourceEventId` from the envelope `id` and this digest from the user property into its
`forward_context`. Its output transform constructs those two response members only from that trusted
context; neither the prompt, model output, task response, nor an output artifact may select or override
them. A missing, malformed, or mismatched forwarded value produces a redacted abstention or refusal and
no canonical proposal.

The command gateway compares the structured response values with the trusted forwarded pair before its
normalization transaction. The evidence service and recorder, which independently consume the durable
source event, recompute the digest and require the identifier and digest to agree before the proposal can
contribute evidence or be recorded as validated provenance. Thus one compromised or defective producer
can still publish only within its broker grant, while a transport, gateway, or model cannot silently
rebind a proposal to different event bytes.

Golden fixtures bind the one canonical candidate path to the committed salient-event fixture and carry
the computed digest through the proposal, evidence, approval, command, dashboard, and audit documents.
Negative fixtures keep their one intended structural defect and therefore use the same source binding
unless that binding is the defect under test.

## Consequences

- A proposal binds the complete validated source fact, including provenance and trace metadata, instead
  of only the model-visible payload.
- Canonical decoding and reconstruction make semantically identical member order hash identically, and
  the versioned wrapper satisfies ADR-0027 without changing the closed CloudEvents envelope.
- Trusted gateway context has an executable origin: an authenticated publisher-owned message property
  and the envelope identity, not a convention inside model prose.
- The evidence service and recorder provide independent recomputation even though the pinned gateway
  cannot run the project-owned digest helper inside its process.
- Negative: the digest changes when diagnostic envelope metadata such as `traceparent` changes, even when
  the payload is identical. This is deliberate exact-event identity but prevents payload-level grouping
  by this digest.
- Negative: the source digest exists once in the envelope-derived computation and once as a broker user
  property. The fleet, gateway configuration, command gateway, evidence service, and recorder must all
  test their binding; a missing property safely costs a proposal.
- Negative: the official gateway forwards rather than recomputes the digest. Independent durable
  consumers detect a defective publisher, but the command gateway can only compare the response with the
  trusted forwarded value. Safety still fails closed because unverified provenance cannot reach a
  contributing evidence decision or exact approval.

## Alternatives considered

- **Hash only `data`.** Rejected because it leaves the event identity, producer, schema, sequence, time,
  correlation, and trace outside the provenance binding.
- **Hash the received UTF-8 bytes.** Rejected because harmless JSON member order and whitespace would
  change the value, and repeated-key or non-canonical input must be refused before hashing rather than
  preserved as provenance.
- **Add a self-digest member to every application envelope.** Rejected because ADR-0037 closes that
  envelope, every event family would pay for a value needed by one path, and excluding the self member
  would add a second envelope profile.
- **Let the model return the source digest.** Rejected because the model is untrusted and cannot choose
  provenance metadata for the canonical proposal.
- **Have the command gateway subscribe to every source event solely to recompute the digest.** Rejected
  because it adds a durable endpoint, broader subscription authority, and another full event consumer
  when the evidence service and recorder already provide independent durable verification. The command
  gateway needs only the trusted gateway correlation pair to normalize a non-actuating proposal.
- **Leave the 64-character value opaque.** Rejected because a value with no defined preimage cannot be
  recomputed, compared across services, or serve the digest-mismatch refusal ADR-0148 names.
