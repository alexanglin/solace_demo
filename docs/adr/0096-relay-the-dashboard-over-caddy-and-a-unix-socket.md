# ADR-0096: Publish the dashboard through Caddy and keep FastAPI on a private Unix socket

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0024 and ADR-0044

## Context

ADR-0024 requires the dashboard API process itself to bind an IP loopback address. ADR-0044 publishes
that process directly on host port 8080 and has no dashboard relay. The production shell now needs a
single publisher that serves one origin while the Python process remains private, and it needs a
credential bootstrap that never persists the per-runtime bearer.

## Decision

Caddy `2.11.4-alpine` at index digest
`sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648` is the sole host publisher at
`127.0.0.1:8080`. FastAPI/Uvicorn listens only on
`/run/aerial-rescue/dashboard-api.sock`, mounted through a volume shared only with Caddy. Neither the API
nor another application service publishes a host port.

Caddy disables its admin API and automatic HTTPS, serves no independent content, forwards the original
Host and Origin, disables response buffering for SSE, and proxies only to the Unix socket. The API ignores
all `Forwarded` and `X-Forwarded-*` headers. Caddy receives no application or broker credential.

The API dynamically serves `/` with `Cache-Control: no-store`. The index contains the fresh in-memory
bearer and non-secret `runtimeId` in one bootstrap element. The boot module validates both, removes the
element before rendering, and retains the values only in a module closure and React context. It never
uses a cookie, URL, local storage, session storage, generated source file, console, or log. Hashed assets
are public same-origin reads with immutable caching.

Every request still requires exactly one allowlisted Host. Every mutation requires the exact configured
Origin and current bearer; no caller classification weakens the Origin rule. A changed runtime identifier
or `401` disables mutations and offers an explicit full reload. The browser never silently repeats a
mutation.

The index and API responses set a same-origin content-security policy with no remote connect, script,
style, frame, object, or form destination; `Referrer-Policy: no-referrer` and
`X-Content-Type-Options: nosniff` also apply. The API healthcheck probes the Unix socket directly, while
Caddy's healthcheck reaches `/api/v1/health` through the relay with the canonical Host.

Add the explicit `mission-control` Compose profile for the broker, Postgres and migration, fleet,
scenario service, recorder, replay validator, dashboard API, and Caddy. Agent Mesh, Ollama, evidence,
approval, command dispatch, and rescue services are not part of that profile.

## Consequences

- One browser origin carries the production shell, JSON API, replay bundle, and SSE stream.
- The local security boundary remains host-loopback publication plus API-enforced Host, Origin, and bearer
  checks, even though the Python process no longer owns an IP listener.
- Caddy becomes an additional pinned and scanned runtime image.
- Index responses cannot be cached, while content-hashed assets can be reused safely.
- Unix-socket permissions and the shared volume become deployment-critical and require direct and relayed
  health tests.

## Alternatives considered

- **Keep FastAPI directly published on 8080.** Rejected because it cannot provide the selected sole-origin
  relay boundary without also becoming the static publisher.
- **Let Caddy inject the bearer.** Rejected because the relay must not receive an application secret and
  cannot own API runtime identity.
- **Use a cookie or persistent browser storage.** Rejected because either creates ambient or long-lived
  authority beyond one API process.
- **Bind Uvicorn to a Compose-network IP.** Rejected because another container could then bypass the one
  relay path; the shared socket is narrower.
