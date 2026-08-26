# ADR-0116: Bound dashboard ingress, cursors, and streams

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0097 fixes mutation checks and ADR-0101 fixes opaque run-bound cursors plus a 256-frame client buffer,
but neither chooses the remaining resource bounds, cursor construction, or exact HTTP refusal mapping.
Framework defaults would make malformed input, restart, overload, and dependency loss behave differently
across routes and runtimes.

## Decision

Retain the accepted Host, Origin, bearer, media/body, lowercase UUIDv4 idempotency, canonical JSON,
strict-schema, then operation order. Apply these bounds:

- public mutation body: 4 KiB; canonical nesting: 16;
- scenario-catalog response: 512 KiB; replay bundle: 1 MiB;
- eight concurrent SSE clients per process;
- 256 data frames plus one terminal slot per client;
- audit polling every 250 ms; comment keepalive every 15 seconds;
- store pages of 256 rows and cursor reconstruction through at most 512 events;
- per-client cleanup within one second and service shutdown within five seconds.

Accept resume input only through native `Last-Event-ID`. A cursor is a lowercase 64-hex HMAC-SHA256
capability over canonical `{cursorVersion:1,runtimeId,runId,auditOrdinal}` using a separate ephemeral
256-bit process key. The SSE `id:` and frame `cursor` are identical. Unknown, too-old, stale-runtime,
cross-run, or otherwise unverifiable cursors receive a fresh snapshot. Cursor text never exposes its
covered values.

SSE emits only `snapshot`, `dashboard-event`, and one terminal `stream-overloaded` data frame; keepalives
are comments. Shed oldest telemetry first. If a non-droppable frame still cannot be retained, consume the
terminal slot, emit exactly one overload frame, and close. A malformed persisted event fails readiness and
the stream rather than creating another frame type.

Map typed errors to statuses consistently: malformed Host, idempotency, canonical JSON, or schema to
`400`; bearer or stale runtime to `401`; Origin to `403`; unknown scenario/session/asset to `404`;
revision, operation, run, or cancellation conflicts to `409`; body size to `413`; media type to `415`;
dependency/readiness/SSE-capacity failure to `503`; and redacted unexpected failure to `500`. Override
framework-generated `405` and `422` bodies so every response follows the committed error schema;
`METHOD_NOT_ALLOWED` is the closed `405` refusal and validation never leaks a framework `422`.

Health is process liveness plus non-secret runtime identity. Degraded-live readiness requires the current
store schema/read path, authenticated scenario catalog/control, and recorder/audit lifecycle path. Replay
readiness requires the store and one validator-approved replay artifact, and no live broker, fleet,
scenario, model, or Agent Mesh dependency.

## Consequences

- Restart and cursor failure have one explicit resnapshot behavior.
- Slow clients cannot consume unbounded process memory or silently lose lifecycle events.
- Refusal behavior is stable across FastAPI, Caddy, and browser clients.
- The reconstruction window intentionally refuses very old resume positions with a snapshot.

## Alternatives considered

- **Encode run and ordinal directly in the cursor.** Rejected because cursors are opaque capabilities.
- **Use an unbounded queue.** Rejected because one slow browser could exhaust the API process.
- **Drop any oldest frame.** Rejected because lifecycle loss would make the client state dishonest.
- **Accept a cursor query parameter.** Rejected because native SSE reconnection already defines the
  transport and a second channel would drift.
