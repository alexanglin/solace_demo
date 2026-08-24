# ADR-0106: Register strict Python wire models before the HTTP runtime

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0097 and ADR-0105 require strict Pydantic models and OpenAPI parity for the dashboard and private
control boundaries, but no Python model or FastAPI application exists. Starting with route decorators
would let framework parsing consume duplicate-key evidence and would make generated OpenAPI the accidental
contract owner. ADR-0105 also fixes the private route grammar and bodies without assigning successful HTTP
statuses.

Two of the nineteen dashboard schemas, `mutation-outcome` and `source-signal`, are browser-owned state
documents rather than server requests or responses. Giving them Python models would falsely imply a
Python trust boundary. Conversely, the dashboard API and scenario service must validate private responses
without importing the implementation package they call.

## Decision

Land strict, service-local Pydantic models and framework-free HTTP expectation registries before any
FastAPI application or listener. The committed JSON Schemas remain normative. A registry maps each full
reserved-host schema identifier to one owned model and classifies every dashboard schema exactly once.

Model ownership is:

- the dashboard API owns seventeen server-facing dashboard models and distinct caller models for the four
  scenario-control documents;
- the scenario service owns the two scenario-file models, four scenario-control server models, and
  distinct caller models for the four fleet-control documents;
- the fleet simulator owns the four fleet-control server models; and
- `mutation-outcome` and `source-signal` are explicitly browser-only and receive no Python model.

No service imports another service implementation, and `packages/contracts` takes no Pydantic dependency.
Intentional caller/server twins are independently checked against the same committed baselines and
one-reason negatives.

Models are closed, frozen, strict, alias-only, and serialize using their wire aliases. Const and enum
members use literals so JSON strings remain the accepted representation. Canonical ingress first applies
the owning raw-byte bound, then calls `aerial_rescue_contracts.canonical.decode`, then performs strict
Pydantic validation. It never uses `model_validate_json`, `httpx.Response.json()`, or FastAPI's automatic
body parsing at a canonical boundary. Calendar instants receive semantic validation in addition to their
schema pattern. Other cross-field and filesystem rules stay with the loader, domain adaptation, reducer,
or HTTP adapter that owns them.

Each service also owns an immutable, framework-free route expectation registry. A route records its
method, path, query parameters, optional request body, and ordered response expectations. A body records
its media type, framing kind, and zero or more normative schema identifiers. R5 and R8 will compare
generated OpenAPI and runtime routes with these registries; the registries do not create a server.

Public statuses remain those fixed by ADR-0097: reads and the SSE stream succeed with `200`; start and
reset succeed with `202`; mutations expose `401`; reset exposes `409`; and every route has the typed
dashboard error as its default refusal. The private control routes use:

| Method and path | Success | Default refusal |
| --- | --- | --- |
| `POST /internal/v1/runs` | `202` with run status | service-specific refusal |
| `GET /internal/v1/runs/{runId}` | `200` with run status | service-specific refusal |
| `POST /internal/v1/runs/{runId}/cancel` | `200` with established run status | service-specific refusal |

This record does not assign individual refusal codes to HTTP statuses. A default typed refusal preserves
the closed vocabulary until the runtime owner has evidence for a more detailed mapping.

FastAPI 0.141.1, Uvicorn 0.52.3, Pydantic 2.13.4, and HTTPX 0.28.1 remain the pins selected by ADR-0105.
HTTPX is installed only in the dashboard API and scenario service, which are callers; the fleet simulator
does not acquire an unused client dependency.

## Consequences

- Python, schemas, fixtures, and later OpenAPI have an executable parity point before framework wiring.
- Duplicate keys and floating-point values cannot disappear inside framework coercion.
- Caller/server model copies add deliberate local code, but preserve process independence and are held to
  one schema-owned oracle.
- Browser-only state remains typed and validated in TypeScript without creating a false Python owner.
- A default private refusal is less descriptive in OpenAPI than a per-code status table, but it avoids an
  unmeasured mapping that would be difficult to change after publication.

## Alternatives considered

- **Generate models from FastAPI OpenAPI.** Rejected because the committed schemas, not framework output,
  own compatibility.
- **Put shared Pydantic models in `packages/contracts`.** Rejected because that package is framework-free
  Tier 1 code and ADR-0105 requires service ownership.
- **Import the callee's models in each client.** Rejected because it couples separately deployed service
  implementations and lets one process package become another's trust boundary.
- **Model all nineteen dashboard schemas in Python.** Rejected because two are browser-owned state and
  have no Python ingress or egress.
- **Invent a status for every private refusal.** Rejected until runtime evidence justifies a stable
  mapping; the default refusal remains strict and typed.
