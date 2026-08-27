# ADR-0107: Authenticate private scenario and fleet run control over bounded HTTP

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The dashboard API must start and cancel one scenario-service run, and the scenario service must do the
same at the fleet simulator. ADR-0061 fixes only the first HTTP direction and deliberately gives the
scenario service no broker identity. ADR-0095 fixes uncertain-start reconciliation and bounded reset,
while ADR-0100 and ADR-0077 fix the catalog input and the lossless simulator value. None of those records
defines the private routes, authentication, message shapes, timeouts, or refusal vocabulary. Leaving
those choices to framework defaults would make two unauthenticated, incompatible control planes.

## Decision

Use authenticated private HTTP over dedicated Compose networks. The dashboard API calls
`scenario-service:8081`; the scenario service calls `fleet-simulator:8082`. Neither listener publishes a
host port. Each hop has its own generated 256-bit bearer secret and exact allowlisted `Host`; a bearer is
never shared between the two hops or placed in a request body, fixture, response, or log.

Both services expose the same route grammar under their distinct hosts:

| Method and path | Request body | Successful body |
| --- | --- | --- |
| `POST /internal/v1/runs` | service-specific start request | service-specific run status |
| `GET /internal/v1/runs/{runId}` | none | service-specific run status |
| `POST /internal/v1/runs/{runId}/cancel` | service-specific cancel request | service-specific run status |

The eight closed RPC documents are scenario-control start request, run status, cancel request, and
refusal, plus the corresponding four fleet-control documents. A run-status document is reused by start,
status, and established-cancel success so the control vocabulary does not grow three representations of
one fact.

Every document carries integer `controlVersion: 1` and uses the ADR-0027 canonical JSON profile. Scenario
start carries `scenarioId`, `scenarioRevision`, stable `missionId`, and stable `runId`. Fleet start carries
the stable `runId` and exactly one nested lossless `FleetScenario` projection: mission identifier,
explicit simulated drone starts, tick interval, connectivity thresholds, uniform sweep count, and a flat
bounded list of `{droneId, tickOrdinal}` heartbeat absences. It carries no geometry, catalog metadata,
declared-only member, mode, lifecycle, or seed. Both cancel bodies bind `missionId` and `runId`; the path
and body run identifiers must agree.

Scenario status uses `PLANNED`, `SEARCHING`, `EXHAUSTED`, or `ABORTED`, carries the scenario identity and
truthful 23/20/3 participation counts, and forwards completed-tick and successful-telemetry-publication
counters. Fleet status uses `ACCEPTED`, `RUNNING`, `EXHAUSTED`, `CANCELLED`, or `FAILED` and carries the
same two counters. The counters instrument fleet publication separately from best-effort recorder
receipt.

Refusals carry only the version, a closed service-specific `errorCode`, and a redacted bounded `message`.
The vocabulary includes Host, authentication, media, body-bound, canonical-JSON, schema, path/body
binding, run conflict, run not found, cancellation not established, and internal failure; the scenario
hop additionally distinguishes scenario lookup/revision and fleet unavailability, and the fleet hop
distinguishes capacity and run failure.

For requests with bodies, processing order is exact Host syntax and allowlist, bearer, JSON media type
and 256 KiB raw-body limit, canonical duplicate-key and floating-point refusal, strict schema, path/body
binding, then the operation. Reads enforce Host and bearer before lookup. HTTP connection establishment
is bounded to one second. Start and status responses are bounded to five seconds. Cancellation consumes
one shared fifteen-second monotonic budget from the dashboard operation; the scenario-to-fleet call uses
only the remaining budget and never starts a fresh one.

The stable run identifier is the idempotency identity on each private hop. The same run and same
canonical start body returns current status without launching again. The same run with different bytes
or meaning is `RUN_CONFLICT`. A caller never repeats a start automatically after an uncertain response;
it queries the same run. A run missing during reconciliation becomes an aborted operational mission.
Cancel succeeds only once the run is stopped or already terminal; otherwise it returns
`CANCELLATION_NOT_ESTABLISHED` without claiming reset.

The Python HTTP boundary is pinned to FastAPI 0.141.1, Uvicorn 0.52.3, Pydantic 2.13.4, and HTTPX 0.28.1.
Each server owns strict Pydantic models for the documents it receives and emits, while each caller
validates the same schemas at its client boundary. No service imports another service implementation.

## Consequences

- An uncertain start can be reconciled without repeating the mutation, and a cancellation response has
  one auditable meaning.
- The fleet simulator receives only its accepted composition value; external-agent presentation data
  cannot accidentally become simulator telemetry.
- Separate secrets and networks contain compromise of one private hop, but add secret generation,
  rotation, healthcheck, and Compose-policy obligations.
- Reusing status bodies keeps the contract at eight messages, but a status must carry counters that are
  irrelevant to some callers.
- Plain private HTTP relies on authenticated, non-published Compose networks rather than transport TLS;
  publishing either port requires a superseding decision and a new threat analysis.

## Alternatives considered

- **Use broker commands for scenario and fleet control.** Rejected because ADR-0061 deliberately gives
  the scenario service no broker identity and no existing topic carries this control plane.
- **Trust the private Compose network without authentication.** Rejected because network membership is
  not caller identity and a compromised peer could start or cancel a run.
- **Automatically retry a timed-out start.** Rejected because the first request may have committed; a
  status query with the same run identity is the safe reconciliation operation.
- **Put catalog geometry and declared-only agents in the fleet request.** Rejected because ADR-0077 fixes
  the narrower simulator value and doing so would invite manufactured external-agent state.
- **Create separate response schemas for start, status, and cancel.** Rejected because all three report
  the same run fact and the duplicate shapes would drift.
