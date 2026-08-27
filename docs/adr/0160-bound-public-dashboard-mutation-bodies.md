# ADR-0160: Bound public dashboard mutation bodies before canonical decoding

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0097 requires every public dashboard mutation body to be bounded before canonical duplicate-key and
floating-point validation, but it does not select the bound. The private run-control boundary has a
256 KiB ceiling under ADR-0107. Reusing that number in application code without an accepted decision and
an operating-parameter row would make a security and resource limit an undocumented local default.

FastAPI's ordinary body helper also reads the complete body before application validation. Checking the
length only after that read would reject an oversized document semantically while failing to bound the
dashboard API's application-owned accumulator. The boundary must retain the raw bytes for canonical
decoding, so it needs an incremental read rather than framework model parsing.

## Decision

The maximum raw body for each public dashboard mutation is **262,144 bytes**, inclusive, before canonical
decoding. The four mutations remain scenario start, scenario reset, operator command, and exact proposal
decision. This number is the public counterpart of the private run-control ceiling; it is intentionally
far above every closed version-one request while placing one common, reviewable memory bound on hostile
input.

After raw-ASGI Host, Origin, and bearer authorization, the HTTP adapter applies the remaining refusal
order as follows:

1. require exactly the JSON media type without reading the body;
2. consume ASGI body chunks incrementally, refusing as soon as retaining the next chunk would exceed
   262,144 bytes;
3. require the UUID version 4 idempotency key;
4. apply the contracts-owned canonical decoder and strict service-owned Pydantic schema;
5. bind path and body identifiers; and
6. invoke the injected durable route operation.

An oversized request returns the closed dashboard error with `BODY_TOO_LARGE` and HTTP `413`. It reaches
no route operation. The application-owned accumulated body never exceeds the selected bound, although an
ASGI server may necessarily present one already-received network chunk larger than that bound. A replay
composition's operational command and proposal-decision routes return `REPLAY_READ_ONLY` without reading
or retaining their bodies because that graph constructs no corresponding operation.

The constant lives in the dashboard API ingress boundary, while its value and instrument live in
`docs/operating-parameters.md`. Changing the value requires a superseding ADR because it changes the
public denial-of-service boundary.

## Consequences

- Duplicate-key and floating-point evidence remains available to the canonical decoder for every
  admitted body.
- Media refusal does not spend the body-memory budget, and an oversized body cannot reach idempotency,
  schema, persistence, publication, or replay-session effects.
- The public and private HTTP boundaries share one size ceiling without sharing credentials, routes, or
  implementations.
- Negative: the bound does not limit Caddy, Uvicorn, kernel, or transport buffering before an ASGI chunk
  reaches the application; deployment and soak evidence still own those layers.
- Negative: a future request larger than 256 KiB needs a versioned contract and measured superseding
  decision rather than a local configuration increase.

## Alternatives considered

- **Use FastAPI's complete-body helper and check the result length.** Rejected because the application
  would already have retained the oversized body before refusing it.
- **Leave the public limit configurable without a repository default.** Rejected because one deployment
  could silently weaken the tested resource boundary and the operating parameter would have no value.
- **Give each mutation a separate limit.** Rejected because all four current documents are small, closed,
  and canonical; four unmeasured numbers add configuration surface without a demonstrated benefit.
- **Reject replay read-only mutation bodies only after parsing.** Rejected because a structurally absent
  operation needs no body and retaining it adds work to a path whose only valid outcome is refusal.
