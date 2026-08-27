# ADR-0184: Package the dashboard and gate relay startup on application readiness

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Extends:** ADR-0096 and ADR-0103

## Context

ADR-0096 selects Caddy as the dashboard's sole host publisher and a private Unix socket for FastAPI.
ADR-0103 pins the dashboard's Node and pnpm versions. Neither decision says how the production browser
bundle enters the application image, which non-secret values make the dashboard composition valid, or
whether Compose may call a liveness-only response "healthy" while the application cannot serve its
degraded-live mode.

The dashboard runtime now validates its scenario, asset, and replay roots at startup. It also requires an
exact public Host, browser Origin, local operator identity, and authenticated private scenario-control
endpoint. The existing Compose service supplied the broker, store, and bearer secret, but omitted those
non-secret settings and omitted the browser bundle and replay root from the image. Its Unix-socket probe
accepted any `200` from `/api/v1/health`, so Caddy could start before files, scenario control, store, and
broker delivery were ready.

## Decision

The application Dockerfile builds the browser in a dedicated
`node:26.7.0-slim@sha256:5758d367d7b4f48b73a9bb3530e687e47efb289f3b43f9c0450a25225ae0db5d`
stage. It installs only `pnpm@11.23.0`, with lifecycle scripts disabled, installs the committed lock with
`--frozen-lockfile --ignore-scripts`, and runs the Vite production build. Only the resulting `dist`
directory crosses into the Python runtime, rooted at `/app/dashboard`; Node, pnpm, source tests,
dependency directories, and generated test evidence do not.

The feature application adapter reads immutable assets from `/app/dashboard/assets`. This is a
compatibility input for its current direct hashed-asset reader, not a second packaging convention: the
complete distribution remains rooted at `/app/dashboard`, which is the established browser-runtime
root. A later composition that owns the complete distribution continues to receive that root.

The final image creates an empty `/app/replays` directory owned by root with mode `0555`. The dashboard
file adapter remains the only reader, rejects links and paths outside that exact root, and applies its
existing per-file size ceiling and schema/canonical validation. This decision does not package a replay
or authorize a runtime replay writer.

Compose supplies these exact non-secret dashboard settings:

| Setting | Value |
| --- | --- |
| Allowed Host | `localhost:8080` |
| Allowed Origin | `http://localhost:8080` |
| Local operator identity | `local-operator` |
| Feature asset reader | `/app/dashboard/assets` |
| Replay root | `/app/replays` |
| Socket path | `/run/aerial-rescue/dashboard-api.sock` |
| Scenario catalog | `/app/scenarios` |
| Scenario-control URL | `http://scenario-service:8081/` |
| Scenario-control Host | `scenario-service:8081` |
| Scenario-control bearer file | `/run/secrets/scenario-control-bearer` |

`local-operator` is an audit identity, not a credential. The current-runtime bearer remains the only
source of mutation authority, remains a Compose secret, and is passed only by its file path. The
dashboard continues to hold its own Postgres and broker secrets and shares only the trust-store and
dashboard-socket mounts already selected for it.

The dashboard Compose healthcheck connects to the private Unix socket, requests
`/api/v1/readiness?mode=degradedLive` with the canonical Host, consumes the complete HTTP response, and
requires both status `200` and JSON member `ready: true`. Caddy waits for that service-health state
before starting. Process liveness remains independently observable at `/api/v1/health`, and Caddy's own
healthcheck continues to exercise that endpoint through the public relay. A successful liveness response
never substitutes for application readiness.

## Consequences

- The application build gains one pinned and inventoried Node base image and a frozen dashboard build,
  increasing build time and the image-scanning surface while leaving Node out of the final image.
- A missing or malformed browser bundle, replay root, scenario input, secret file, or downstream
  dependency prevents Caddy from being declared ready instead of producing a partially usable shell.
- The local audit identity is deterministic and inspectable, while possession of that public string
  grants no authority.
- The empty replay root makes the absence of replay evidence explicit. Supplying a recording later needs
  its own bounded, validated packaging or read-only mount decision.
- Compose dependency ordering applies at initial startup. It does not stop an already-running Caddy when
  the dashboard later loses readiness; clients still observe the dashboard's readiness endpoint and
  application refusal during that interval.
- Static policy tests prove the declared image and Compose shape. They do not replace an authorized image
  build, image scan, or live Unix-socket and Caddy probe.

## Alternatives considered

- **Serve the checked-in dashboard source or a host-built `dist`.** Rejected because the image would no
  longer prove the exact pinned build that produced its browser bytes.
- **Install Node in the Python runtime image.** Rejected because build authority and a larger dependency
  surface would remain available to the running application.
- **Mount a writable replay directory.** Rejected because no replay writer is selected and an empty,
  writer-free root states the current capability honestly.
- **Keep the `/api/v1/health` status-only Compose probe.** Rejected because process liveness says nothing
  about files, store, broker delivery, or private scenario control.
- **Make Caddy's own healthcheck call readiness.** Rejected because Caddy's probe is responsible for relay
  liveness; startup ordering already depends on the dashboard's stronger application-health result.
