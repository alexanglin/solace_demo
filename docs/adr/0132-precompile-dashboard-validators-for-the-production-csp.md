# ADR-0132: Precompile dashboard validators for the production CSP

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0124

## Context

The browser must validate every bootstrap, HTTP, SSE, and replay document against the committed JSON
Schemas before the value becomes typed state. ADR-0124 narrowed that production inventory to the fifteen
schemas that receive raw browser input, but its wording assumed the browser would compile those schemas
when it constructed the registry.

The first production browser run proved that assumption incompatible with the accepted security boundary.
Ajv's runtime compiler constructs JavaScript functions dynamically. Caddy's production Content Security
Policy deliberately omits `unsafe-eval`, so the browser refused that compilation before React could
finish bootstrap or open SSE. Unit tests running without the production policy did not exercise the
failure. Adding `unsafe-eval` would make the policy weaker solely to retain work the build can perform
offline.

## Decision

Compile the exact fifteen browser-input schemas with Ajv 2020-12 during committed contract generation.
The generator loads only repository schemas, registers the canonical schema locally, enables Ajv's ESM
standalone-code output, and writes a generated JavaScript validator module plus its TypeScript declaration
beside the generated wire types. Contract freshness owns those files: a schema, inventory, declaration,
or standalone-code change must be regenerated, and check mode fails on missing, stale, or unexpected
output.

The hand-written Tier 1 registry contains the closed schema-ID-to-validator mapping and invokes the
generated predicates. It does not import browser schemas, instantiate Ajv, compile code, fetch a schema,
or use `eval` or the `Function` constructor at runtime. Successful validation is the only cast from
`unknown` to the generated schema type. The production policy remains strict and does not admit
`unsafe-eval`.

The generated standalone module retains the generated-code evidence boundary selected by ADR-0105 and
ADR-0130. Golden accepted/refused fixtures exercise every exported predicate through the hand-written
registry; the Tier 1 registry remains at 100 percent statement and branch coverage; and a production
browser test must reach rendered readiness and a connected SSE source under the actual Caddy policy.

## Consequences

- Contract validation remains Ajv-backed and repository-local without turning the CSP into a decorative
  header.
- Schema compilation failures move to generation/build time rather than taking down the operator UI at
  runtime.
- The production bundle contains validator functions but not Ajv's compiler. Generated output is larger
  than types alone and remains subject to the fixed combined script-and-style byte budget.
- A schema cannot join the browser trust boundary by appearing only in TypeScript. It must enter the
  closed runtime inventory, standalone export, registry mapping, fixtures, and freshness evidence.

## Alternatives considered

- **Add `unsafe-eval` to the CSP.** Rejected because it widens executable-code authority to accommodate a
  build-time concern and defeats the accepted public boundary.
- **Hand-write structural validators.** Rejected because they would become a second contract language and
  could drift from the manifest-owned JSON Schemas.
- **Compile lazily after bootstrap.** Rejected because the same CSP refusal would occur later and retain a
  dynamic-code path in production.
- **Skip browser validation because the API uses Pydantic.** Rejected because HTTP, SSE, replay, proxy,
  persistence, and compromised-runtime inputs remain untrusted at the browser boundary.
