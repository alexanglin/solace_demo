# ADR-0021: Validate contract artifacts through one offline manifest

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0012 requires schema and golden-fixture drift checks, but JSON syntax validation alone cannot prove
that a fixture has an owning schema, that a negative fixture really fails, or that a reference resolves
without a network fetch. An orphaned or mislabeled fixture creates false confidence in every Python and
TypeScript contract suite that consumes it.

[`jsonschema` 4.26.0](https://pypi.org/project/jsonschema/4.26.0/) implements JSON Schema Draft 2020-12 and
declares Python 3.14 support. [`referencing` 0.37.0](https://pypi.org/project/referencing/0.37.0/) supplies
the in-memory registry used to keep reference resolution offline.

## Decision

Once either `schemas/**/*.schema.json` or `fixtures/golden/**/*.json` exists, require
`schemas/contract-manifest.toml` with integer format version 1. Each `[[contracts]]` table names exactly
one schema, at least one valid fixture, and at least one invalid fixture.

The project-owned gate validates these invariants:

- every discovered schema and fixture is registered exactly once;
- all paths are repository-relative, exist, and cannot traverse or follow a symlink outside the repository;
- every schema is a JSON object using Draft 2020-12 with a unique non-empty `$id`;
- every `$ref` resolves to the same schema or another schema in the in-memory registry, with no network
  fallback;
- every valid fixture passes and every invalid fixture fails its owning schema.

Pin `jsonschema==4.26.0`, `referencing==0.37.0`, and the matching strict-mypy stub package. Run the
project gate at pre-commit and pre-push, identically in CI. Also run `check-metaschema` for a fast changed-
schema diagnostic. The empty greenfield state is the only successful inactive state.

## Consequences

- A schema or fixture cannot land untested or silently orphaned.
- Invalid examples become executable negative contract evidence rather than documentation samples.
- Contract validation is deterministic without network availability.
- Draft 2020-12 is the sole accepted dialect; adopting another draft requires a new decision and tests.
- Every contract needs two fixtures from its first commit, adding small but deliberate authoring overhead.

## Alternatives considered

- **Infer fixture ownership from file names.** Rejected: renames and nested schemas make the convention
  ambiguous, and ambiguity fails open.
- **Let each language suite discover its own fixtures.** Rejected: Python and TypeScript could exercise
  different inventories while both pass.
- **Permit remote `$ref` fetching.** Rejected: it makes commit checks depend on mutable network content and
  can disclose repository behavior to an external server.
- **Validate schemas only with `check-metaschema`.** Rejected: it does not prove inventory ownership or
  positive and negative fixture expectations.
