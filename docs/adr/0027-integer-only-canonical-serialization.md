# ADR-0027: Canonicalize digests over an integer-only JSON profile

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0006](0006-proposal-bound-single-use-approvals.md) binds an approval to a proposal digest and names
the obligation this record discharges: "field order, encoding, float formatting, and exclusion of the
digest field itself must be specified precisely enough to reimplement". Until now nothing specified them.
[CONTRACTS.md](../CONTRACTS.md) therefore forbids every component from computing or comparing a digest,
[adr/README.md](README.md) carries the question as open, and row B14 of the
[approval-bypass catalogue](../security/approval-bypass-catalogue.md) is recorded as blocked by this
record. Three further consumers wait on the same bytes: the replay determinism hash, evidence hashing,
and the idempotency record's hash of a canonicalized request body.

Python and TypeScript must produce identical bytes for the same logical value, and both must be
reimplementable from the written contract rather than from each other's source.

[RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) (JSON Canonicalization Scheme) is the obvious
candidate. It specifies key ordering and string escaping well, but it serializes numbers with the
ECMAScript `Number::toString` algorithm over IEEE-754 doubles. That makes *formatting* deterministic
while leaving the *value space* non-injective: two decimal coordinates that round to the same double
produce one digest. B14 asks for the opposite property — that distinct coordinate values cannot alias
before hashing. A canonicalization built on binary floating point cannot provide it, because the aliasing
happens when the decimal is parsed, before any serializer is reached.

Measured on the reference interpreter while writing this record: `180_000_000 < 2**53 - 1`, so integer
microdegrees are exactly representable in a TypeScript `number`; one microdegree is 0.111 m at the
equator. Sorting `{"a", "z", "", "￿", "\U00010000"}` by UTF-8 bytes equals sorting by Unicode
code point and does **not** equal sorting by UTF-16 code unit, which is what JavaScript's default
`Array.prototype.sort` uses — it places `U+10000` before `U+E000`.

## Decision

Digest-covered payloads use an integer-only profile of JSON, and **no floating-point value is
representable in it**. The admissible value space is object, array, string, integer, boolean, and null.
Any IEEE-754 value is rejected at the boundary, including one that is numerically integral.

Latitude and longitude are carried as integer microdegrees, and the evidence score as integer hundredths
beside its named ordinal band and its score version. Distinct coordinates therefore cannot alias, because
the representation is injective rather than merely deterministically formatted. Instants are RFC 3339 UTC
strings at exactly millisecond precision, so one instant has exactly one spelling.

Object keys are ordered by ascending UTF-8 byte sequence and are constrained to a leading-lowercase ASCII
alphanumeric form, which is the `missionId` and `droneId` convention the contracts already use. Byte
order is stated as the rule because it is the one collation both languages implement identically; the
ASCII constraint additionally makes the Python/JavaScript ordering divergence unreachable rather than
merely specified away.

A digest is SHA-256 over a domain-separated prefix and the canonical bytes, rendered as lowercase hex.
The domain separator names both this canonicalization version and the consuming context, so bytes that
are valid for one purpose cannot be replayed as another. The payload additionally carries the
canonicalization version inside the hashed bytes, so a downgrade is detected rather than accepted. A
top-level `digest` member is removed before hashing; a nested one is ordinary data.

The exact byte-level rules live in [CONTRACTS.md](../CONTRACTS.md#canonical-serialization) and the exact
bounds in [operating-parameters.md](../operating-parameters.md#canonical-serialization-bounds), because
each of those facts has one home ([ADR-0016](0016-documentation-set-split.md)).

## Consequences

- B14 stops being a defence and becomes an impossibility. There is no float to alias, so no test can
  construct two distinct coordinates that hash alike.
- The search-and-rescue domain is naturally decimal, so every producer and consumer converts at its
  boundary, and a conversion defect becomes a correctness defect. That work is real and is not removed by
  this record — it is moved to a place where a schema and a type can catch it.
- One microdegree, 0.111 m at the equator, is a deliberate precision ceiling. Sub-millisecond event
  ordering is likewise discarded.
- The profile is narrower than JSON. A future payload that genuinely needs a real number cannot be
  digested without a new decision, which is the intended cost.
- Implementations stay small and dependency-free in both languages, which is what allows the Tier 1
  mutation and branch-coverage gates to say something meaningful about them.
- Golden fixtures carrying canonical bytes and their expected digest become the cross-language oracle.
  Until the dashboard exists, they are evidence of one implementation only, and the record should not be
  read as claiming two agreeing implementations before the second one is written.

## Alternatives considered

- **RFC 8785 (JCS) unmodified.** Rejected: its ECMAScript number formatting is defined over IEEE-754
  doubles, so distinct decimal coordinates alias before serialization. It solves formatting determinism,
  which was never the failure B14 describes.
- **RFC 8785 restricted to integers.** Rejected as a citation rather than as a design: once floats are
  excluded, the remaining number rule is no longer RFC 8785's, so claiming conformance would misdescribe
  the artifact. The string escaping and ordering rules here are deliberately compatible with it.
- **Decimal strings for coordinates, hashed as text.** Rejected: `"47.10"`, `"47.1"`, and `"+47.1"` are
  distinct strings for one coordinate, so it moves the aliasing problem from the value space into the
  string space and needs its own normalization contract anyway.
- **Deterministic CBOR ([RFC 8949](https://www.rfc-editor.org/rfc/rfc8949) section 4.2).** Rejected: it
  fixes ordering and offers real integer types, but it adds a dependency to both runtimes, still admits
  floats unless separately restricted, and produces golden fixtures no reviewer can read. Review cost
  outweighs the encoding benefit for payloads this small.
- **A length-prefixed typed binary encoding.** Rejected: injective and compact, but unreadable in a
  committed fixture and unreviewable in a diff, which is where this project catches contract defects.
- **Leave floats admissible and compare with a tolerance.** Rejected: a tolerance makes two different
  approved actions equal, which is precisely the time-of-check-to-time-of-use defect
  [ADR-0006](0006-proposal-bound-single-use-approvals.md) exists to close.
