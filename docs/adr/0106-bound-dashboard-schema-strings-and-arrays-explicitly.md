# ADR-0106: Bound dashboard schema strings and arrays explicitly

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0038 restricts the repository's JSON Schema vocabulary to the cross-language subset listed in
`docs/CONTRACTS.md`. That subset can require a nonempty array with `minItems`, but cannot express an
upper bound, an exact cardinality, or a nonempty string. The dashboard contracts now need all three:
the fixed scenario has exactly twenty sectors and twenty-three declared members, bootstrap credentials
and opaque cursors cannot be empty, and every untrusted collection must remain bounded before it reaches
the browser or a Python boundary.

Draft 2020-12 defines `minLength` and `maxItems` as ordinary assertion keywords. The pinned Python
validator and Ajv implement both without the optional or implementation-dependent behavior that caused
ADR-0038 to reject `format`. Their counts are still not a substitute for the canonical decoder: JSON
Schema string lengths count Unicode code points, while the canonical profile's upper string limit is a
UTF-8 byte limit.

## Decision

Add exactly `minLength` and `maxItems` to the repository's permitted Draft 2020-12 keyword subset.

- Use `minLength` only where emptiness itself is invalid. A byte bound, ASCII spelling, identifier,
  digest, credential, or cursor rule still uses the canonical decoder and an explicit pattern or
  validator rule where applicable.
- Use `maxItems` with `minItems` to express bounded or exact array cardinality. Semantic uniqueness,
  byte-order sorting, closed participation counts, polygon closure, and cross-field agreement remain
  validator rules unless a later decision admits another schema keyword.
- Continue to forbid `format`, remote resolution, conditionals, `contains`, `uniqueItems`, and an
  unrecorded keyword expansion. Python and TypeScript validate the same committed positive and
  one-reason-negative fixtures offline.

The version-one dashboard boundary fixes these collection ceilings: 20 readiness reasons, 256
non-telemetry snapshot timeline entries, 512 replay events, and 256 vertices for either the search
polygon or one sector polygon. The replay ceiling accommodates the prepared run's 280 telemetry
publications plus its bounded lifecycle events without turning the fixture into a fleet-scale claim.
The snapshot ceiling matches the accepted per-client data-frame bound, and the remaining limits keep
operator metadata and committed local geometry well below the canonical body-size guard.

## Consequences

- Dashboard schemas can reject empty capabilities and overlarge arrays before either runtime narrows
  them to typed state.
- The exact twenty-sector and twenty-three-member scenario cardinalities become visible in the schema
  rather than living only in service code.
- Every browser-facing array now has a schema-owned upper bound; a boundary cannot allocate an
  arbitrary collection before semantic validation.
- Some invariants deliberately remain language-level checks; a green schema result alone still does
  not prove canonical decoding, uniqueness, ordering, or cross-field consistency.
- Any further schema-vocabulary expansion requires another decision and cross-language evidence.

## Alternatives considered

- **Leave both checks to Pydantic and TypeScript.** Rejected because the normative schema would accept
  values both production consumers must refuse, weakening the shared contract oracle.
- **Admit the entire Draft 2020-12 vocabulary.** Rejected because each added keyword enlarges the
  Python/JavaScript parity surface without a present consumer.
- **Use `format` for credentials, cursors, or digests.** Rejected because format assertion remains
  optional and implementation-dependent; explicit closed rules are portable.
