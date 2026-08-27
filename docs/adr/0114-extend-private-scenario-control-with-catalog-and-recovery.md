# ADR-0114: Extend private scenario control with catalog and lost-run recovery

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0107, ADR-0108, and ADR-0095

## Context

ADR-0107 fixed identical three-route scenario and fleet control surfaces. The dashboard API now needs the
scenario service's validated catalog to implement its public catalog and prepared state without importing
another service. Restart reconciliation also needs an authoritative way to terminate a durable mission
whose fleet process no longer recognizes its run. Letting the dashboard append `ABORTED` would contradict
ADR-0111's sole lifecycle producer and recorder path.

## Decision

Keep the fleet surface at start, status, and cancel. Extend only scenario control with:

| Method and path | Request | Successful response |
| --- | --- | --- |
| `GET /internal/v1/scenarios` | none | existing `scenario-catalog/v1` document |
| `POST /internal/v1/runs/{runId}/recover` | `scenario-control-recovery-request` | scenario run status |

The closed recovery request carries `controlVersion: 1`, `scenarioId`, `scenarioRevision: 1`, stable
`missionId`, matching `runId`, and literal reason `LOST_FLEET_RUN`. The dashboard supplies only identities
already persisted by ADR-0113. The same run and body is idempotent; conflicting identity is `RUN_CONFLICT`.

Recovery queries the fleet status first. If the run exists, it returns its reconciled status without
aborting it. If the fleet reports `RUN_NOT_FOUND`, scenario control constructs one deterministic
schema-bound mission `ABORTED` CloudEvent, publishes it through its guaranteed publisher, and exposes
`ABORTED` only after broker confirmation. An ambiguous publication retries the exact same canonical event
bytes and identity so recorder deduplication assigns no second audit ordinal. An already recovered run
returns its current status and publishes nothing.

The catalog response is the existing dashboard scenario catalog projection built from the single strict
scenario loader. It is bounded to 512 KiB and does not create another catalog model or filesystem reader.

Both additions retain ADR-0107's exact Host then distinct bearer validation, dedicated network, no host
port, canonical JSON, strict Pydantic ownership, bounded HTTPX calls, redacted refusals, and prohibition on
service-implementation imports. The scenario route registry and generated OpenAPI now contain five routes;
the fleet registry remains three. Reads enforce Host and bearer before lookup. Recovery bodies use the
existing 256 KiB private-body limit and refusal order.

## Consequences

- Public scenario discovery and prepared state have one production source.
- A lost fleet run terminates through the same guaranteed lifecycle and recorder path as every other
  mission state.
- Scenario service now needs its ADR-0111 guaranteed publish-only broker identity at runtime; it still
  subscribes to nothing.
- Recovery adds one private schema and one route rather than a generalized reconciliation protocol.

## Alternatives considered

- **Load scenario files in dashboard API.** Rejected because it duplicates loader and filesystem policy.
- **Have dashboard API append `ABORTED`.** Rejected because it bypasses the authoritative lifecycle
  producer and guaranteed broker path.
- **Repeat start after a missing response.** Rejected because the first start may have committed.
- **Return a separate catalog projection.** Rejected because the accepted dashboard catalog already has
  the exact browser-facing representation.
