# ADR-0058: Generate the dashboard's contract types from the committed schemas and validate every untrusted input against them

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

A type checker cannot check JSON arriving over a network. `AGENTS.md` §4 permits `any` only "at a validated external boundary" and presupposes a validator this project has never named, while §3 requires Pydantic at every Python trust boundary. The TypeScript half of that rule has no instrument.

[ADR-0057](0057-typescript-strictness-baseline-before-the-dashboard.md) fixes how the dashboard's own source is checked. It does nothing about the event stream, which is where every telemetry event and every approval arrives, and where the type checker's guarantees stop.

The contract already exists and is normative. `docs/CONTRACTS.md` makes `schemas/v1/*.schema.json` the wire contract, [ADR-0021](0021-contract-artifact-manifest.md) registers each artifact exactly once in `schemas/contract-manifest.toml` and validates it offline against golden fixtures, and [ADR-0038](0038-reserved-host-schema-identity-and-one-reason-fixtures.md) fixes schema identity. `docs/CONTRACTS.md` states directly that Python and TypeScript must consume the same schemas and the same shared golden fixtures.

The schema vocabulary was deliberately restricted for this: no `format`, ASCII-only patterns, and explicit character classes rather than shorthand, so that Python's regular-expression engine and a JavaScript one read them identically. The schemas were written to be validated by a JavaScript validator.

`docs/CONTRACTS.md` also lists the rules JSON Schema cannot express — a calendar-invalid time, an unbound type, the schema and topic bindings, a repeated key, byte length against code-point length — and requires both language validators to unit-test them.

## Decision

**The dashboard's contract types are generated from the committed schemas, and every untrusted input is validated against those same schemas at runtime.**

Contract types are generated into `apps/dashboard/src/contracts/generated/` and never hand-written. The generated output is committed and its freshness verified, on the mechanism [ADR-0022](0022-recursive-diagram-integrity.md) already establishes for generated artifacts.

Runtime validation at the event-stream and HTTP boundaries uses a JSON Schema validator compiled against the committed `schemas/v1/*.schema.json` files, on the 2020-12 dialect the contract manifest already validates against.

A schema library may be used for the boundaries `docs/CONTRACTS.md` does not define — environment configuration, browser storage, and URL parameters — and only through its non-throwing parse form, so every refusal is a typed domain outcome rather than an exception. It may not redefine anything under `schemas/v1/`.

The validator-only rules live at `apps/dashboard/src/contracts/`. They may not live under `packages/`, because the root manifest globs `packages/*` as uv workspace members and a TypeScript directory there breaks dependency resolution.

## Consequences

- "Strict TypeScript" becomes true at the boundary that carries every approval, rather than stopping one layer short of it.
- The dashboard cannot drift from the wire contract, because it consumes the contract rather than a transcription of it.
- **A second implementation of the envelope and topic refusal rules now exists** and must stay in refusal-order lockstep with `packages/contracts`. The shared golden fixtures are the mechanism, which makes cross-language contract tests mandatory rather than optional.
- Generated types must be committed and freshness-gated, or they drift silently. That is a new gate to write in Phase 3.
- A runtime validator and a compile step are added to the dashboard's dependencies and to its startup path.
- The boundary between the generated contract types and the hand-written schemas for everything else is a review obligation that a static gate only partly covers.

## Alternatives considered

- **Hand-authored schemas for the wire contract in the dashboard.** Rejected: it creates a second source of truth for a fact that already has one home, which `AGENTS.md` §1 forbids, and the drift would be invisible to the contract-artifact gate.
- **Generating JSON Schema from TypeScript types.** Rejected: its advantage is producing schemas where none exist. Here the schemas exist, are normative, and are the input rather than the output.
- **A smaller modular validator chosen for bundle size.** Rejected for now: bundle size is not the binding constraint for a loopback operator dashboard on the reference workstation. Worth revisiting if it becomes one.
- **No runtime validation, relying on the type checker.** Rejected: it makes the strictness [ADR-0057](0057-typescript-strictness-baseline-before-the-dashboard.md) fixes false at exactly the boundary that matters, and it is how an untrusted payload silently becomes typed mission state.
- **Validating in the API and trusting the stream in the browser.** Rejected: `docs/SAFETY.md` places the approval boundary at the operator, and a dashboard that trusts its input cannot show the operator what it could not parse.
