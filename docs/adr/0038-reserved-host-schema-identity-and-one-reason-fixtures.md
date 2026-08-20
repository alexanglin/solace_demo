# ADR-0038: Identify schemas by path-derived https URIs under a reserved host, reference them absolutely, and make every negative fixture fail for one reason

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0021](0021-contract-artifact-manifest.md) requires every schema to carry a unique `$id`, every
`$ref` to resolve inside an offline in-memory registry, and every schema to be registered with at
least one valid and one invalid fixture in `schemas/contract-manifest.toml`. It does not say what an
`$id` looks like, how fixtures are laid out, or what a negative fixture must prove. The first schemas
now land, the `dataschema` attribute of every event carries an `$id` in flight, the TypeScript
dashboard will load the same files, and [ADR-0033](0033-bound-directory-fan-out.md) caps a directory
at twenty files.

Measured while writing this record: `jsonschema` 4.26.0 with `referencing` 0.37.0 resolves both
`urn:` and `https:` identifiers from an in-memory registry, so the tooling does not force the choice;
the dashboard's validator is not yet chosen, and hierarchical URIs are the form every candidate
resolves identically; RFC 6761 reserves the `.invalid` top-level domain so a name under it can never
resolve.

## Decision

- A schema's `$id` is `https://aerial-rescue.invalid/` followed by its repository-relative path, so
  `schemas/v1/envelope.schema.json` is identified as
  `https://aerial-rescue.invalid/schemas/v1/envelope.schema.json`. The host is reserved, so no
  validator can ever fetch it, and the path names the file a reader opens.
- Every `$ref` is either `#/$defs/...` inside one file or an absolute `$id` with an optional
  `#/$defs/...` fragment. Relative file references are not used.
- Schemas use only the Draft 2020-12 keyword subset listed in [CONTRACTS.md](../CONTRACTS.md) and
  never `format`, whose assertion behaviour is implementation-defined.
- Shared definitions live in one recursive canonical-profile schema; each payload has a payload
  schema and a composed event schema that constrains `type`, `dataschema`, and `data` together.
- Fixtures live under `fixtures/golden/v1/<schema>/`, at most twenty files per directory. Every
  negative fixture is the valid baseline with exactly one member changed, and it fails its owning
  schema for exactly one reason, which a contract test asserts.
- The topic grammar is published as golden case files so the TypeScript side can replay the same
  accepted and refused topics with their refusal names.

## Consequences

- Python and TypeScript load one inventory by `$id` and never touch the network; the manifest gate
  proves ownership and both fixture polarities on every commit.
- Every event's `dataschema` value shows a `.invalid` host in Broker Manager and in logs. A reader
  may mistake it for a misconfiguration; CONTRACTS.md explains it, and moving to an owned domain is a
  superseding record and a new major version.
- The one-reason rule depends on how a validator reports errors. A future keyword combination that
  reports two errors for one mutation is fixed by changing the fixture, never by loosening the test.
- Per-schema fixture directories keep the owning schema visible in the path and keep each directory
  under the fan-out limit, at the cost of more directories.
- Excluding `format` means date-time and URI rules are expressed as explicit patterns, which are
  longer but behave identically in every engine.

## Alternatives considered

- **`urn:aerial-rescue:schema:...` identifiers.** Rejected: they work with the Python tooling, but a
  URN has no hierarchical resolution, so relative references are undefined and handling in the
  dashboard's validator is unverified; an https path resolves identically in every tool and reads as
  the file path it names.
- **A real project domain.** Rejected: none is owned, and a registrable host invites a resolver to
  fetch from a domain a third party could later control.
- **Relative file `$ref`s.** Rejected: resolution depends on the loader's base URI, which differs
  between libraries and editors.
- **One schema per event with inline copies of the shared definitions.** Rejected: the identifier and
  instant rules would have several homes and drift.
- **The `format` keyword for date-time and URI.** Rejected: format assertion is optional and
  implementation-defined in Draft 2020-12, so two validators could disagree on one fixture.
- **Encoding the expected refusal inside each negative fixture.** Rejected: with
  `additionalProperties: false` the marker itself would be a second failure reason.
- **A single flat `fixtures/golden/` directory.** Rejected: the inventory already exceeds the
  fan-out limit, and per-schema directories make the owning schema visible from the path.
