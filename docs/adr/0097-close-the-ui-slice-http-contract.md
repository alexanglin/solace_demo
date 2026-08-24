# ADR-0097: Close the UI slice HTTP contract and mutation refusal order

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The original seven-route dashboard list includes an approval endpoint whose workflow is deferred, leaves
replay bundle delivery unspecified, and does not define request bodies, response shapes, error bodies,
idempotency syntax, or middleware refusal order. Implementing a FastAPI route before those shapes exist
would make framework defaults an accidental public contract.

## Decision

The UI slice exposes these version-one routes plus the production shell:

| Method and path | Contract purpose |
| --- | --- |
| `GET /api/v1/health` | Liveness and non-secret runtime identifier |
| `GET /api/v1/readiness?mode=degradedLive\|replay` | Mode-specific readiness |
| `GET /api/v1/scenarios` | Validated scenario geometry, roster, and participation |
| `POST /api/v1/scenarios/{scenarioId}/start` | Start live execution or create replay session |
| `POST /api/v1/scenarios/current/reset` | Bounded live reset or fresh replay session |
| `GET /api/v1/events` | Snapshot and ordered SSE suffix |
| `GET /api/v1/replays/{sessionId}` | Read-only validated replay bundle |
| `GET /` and `GET /assets/{asset}` | Dynamic bootstrap shell and hashed assets |

There is no approval route in this slice.

Every body and response has a closed Draft 2020-12 JSON Schema, strict Pydantic model, generated
TypeScript type, shared accepted and one-reason-negative fixtures, Ajv validation, and OpenAPI parity
test. The wire modes are exactly `degradedLive` and `replay`; presentation uses the explicit uppercase
badges ADR-0090 defines.

Mutations require JSON, one exact allowlisted Host, the exact configured Origin, the current bearer, and
an `Idempotency-Key` that is a lowercase RFC 4122 UUID version 4. Raw request bytes are bounded and decoded
with the canonical duplicate-key and floating-point refusal before strict Pydantic validation. Start has
the selected mode and scenario revision; reset has exactly an empty object.

Security processing occurs before route effects in this order: Host syntax and allowlist, Origin on a
mutation, bearer on a mutation, media type and body size, idempotency key, canonical decode, strict
request schema, then the route operation. Expected refusals use one versioned redacted error shape. A
mutation `401` never triggers an automatic browser retry.

Start returns `202` with stable run/session identity and honest declared, simulated, and declared-only
counts. A same-key same-body repeat returns the exact stored status and bytes. Reset returns the fresh
mission or replay session only after its bounded operation succeeds. Health is liveness only; readiness
does not require live-only dependencies in replay mode.

## Consequences

- FastAPI-generated OpenAPI can be compared with the committed public schemas instead of becoming their
  source.
- Removing the placeholder approval endpoint makes the initial public surface smaller than the old
  planned list and more truthful.
- Strict Origin on every mutation is stronger and simpler than guessing whether a caller is a browser.
- Duplicate keys and integral-looking real numbers are refused before framework coercion can erase the
  evidence.
- Error and idempotency responses add storage and fixture work, but they give the browser deterministic
  recovery behavior.

## Alternatives considered

- **Keep the approval route as `501 Not Implemented`.** Rejected because a public route still implies a
  workflow and becomes compatibility surface.
- **Let OpenAPI generated from Pydantic be normative.** Rejected because TypeScript and replay already
  consume the committed schema registry.
- **Use arbitrary idempotency strings.** Rejected because a closed UUIDv4 spelling gives cross-language
  validation and bounded storage identity.
- **Retry a mutation automatically after `401`.** Rejected because the prior process may have committed an
  effect before its response was lost.
