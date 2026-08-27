# ADR-0124: Remove dashboard wire values with no producer-to-consumer effect

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0097, ADR-0107, ADR-0108, and ADR-0116

## Context

The first production dashboard increment exposed several values that no runtime consumer used. The
dashboard health response repeated the runtime identifier already delivered through bootstrap,
snapshots, and run-bound cursors. Scenario run status repeated catalog roster counts and fleet progress
counters even though the dashboard consumed only scenario/run identity and mission lifecycle. A
`mutation-outcome` JSON Schema described process-local React state with no wire producer or consumer.
The browser Ajv registry also compiled health and mutation request schemas that no raw browser input was
ever validated against.

The replay adapter similarly returned scenario, revision, and session values supplied by its caller.
Those echoes created a fake-only mismatch seam while the durable dashboard operation already owned the
stable replay-session identity and the validated replay document already owned scenario identity.

Keeping these values would create compatibility surface, validation work, and refusal branches without
changing any observable operation. The UI slice has not completed production acceptance, so correcting
these version-one pre-release contracts does not strand a deployed consumer.

## Decision

Delete values that do not cross a real trust boundary or affect an operation:

- `dashboard-health/v1` contains only `healthVersion` and literal `status: "alive"`. Browser runtime
  replacement remains bound through the dynamic bootstrap, validated snapshots, and opaque cursors.
- `scenario-control-run-status` contains only `controlVersion`, scenario identity and revision, mission
  identity, run identity, and mission lifecycle. The fleet status keeps completed-tick and successful
  telemetry-publication counters for fleet-run instrumentation and acceptance. The scenario catalog
  remains the roster and 23/20/3 participation-count authority, and the public mutation responses keep
  their accepted truthful roster counts.
- Delete the `mutation-outcome` schema, manifest row, golden fixtures, generated TypeScript type, Python
  browser-only classification, and Ajv registration. Mutation progress remains an authored in-memory
  TypeScript discriminated union because no bytes encode or transport it.
- Compile only schemas that validate raw browser input into the production Ajv registry. Health and the
  locally constructed start/reset request documents remain backend wire schemas and generated types,
  but are not registered in the browser validator.
- A replay preparation returns only validated exact bundle bytes. Scenario and revision are checked
  against those bytes; the durable dashboard operation and replay-session lookup own session identity.
  No adapter echoes caller inputs for a second comparison.

## Consequences

- Health remains a real liveness probe without duplicating runtime state.
- Scenario status is smaller and cannot drift from catalog counts or imply that the dashboard consumes
  fleet publication counters.
- The production browser bundle compiles fewer schemas while retaining validation for every raw input it
  actually accepts.
- Removing pre-release version-one members requires schema, fixture, Python, TypeScript, OpenAPI, and
  private-control consumers to change atomically in this increment.
- If a future browser starts consuming health or serializes mutation progress, that new trust boundary
  must deliberately add the needed runtime validator or versioned wire contract.

## Alternatives considered

- **Keep the fields for possible future consumers.** Rejected because an unconsumed compatibility
  promise cannot be tested as behavior.
- **Leave the schemas registered but unused.** Rejected because compilation and test inventory are real
  costs and falsely imply a production input path.
- **Echo replay identities and compare them again.** Rejected because the caller supplied those values;
  the replay document and durable operation are the independent authorities.
- **Remove fleet progress counters too.** Rejected because scenario control consumes them to monitor the
  deterministic fleet run and publication evidence remains distinct from recorder receipt.
