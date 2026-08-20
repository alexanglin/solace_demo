# ADR-0044: Run every component except Ollama in Docker Compose, with Agent Mesh from its official image

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** the rejected alternative in [ADR-0004](0004-split-python-runtimes.md), "Running
  Agent Mesh in a container to hide the version difference", as far as it governs the *runtime*.
  ADR-0004's decision — two isolated `uv` environments for verification — stands. Also supersedes the
  statement in `docs/ARCHITECTURE.md` that the supported Agent Mesh path runs natively from
  `agent-mesh/.venv`.

## Context

[ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) made the PubSub+ software event broker
container the broker, and [ADR-0003](0003-postgres-durable-mission-store.md) already runs Postgres as a
Docker Compose service. The remaining components — Agent Mesh, the six application services, the
dashboard API, and the Event Management Agent the showcase needs — had no runtime layout beyond "run
natively".

ADR-0004 rejected a containerized Agent Mesh because "an emulated `linux/amd64` image is explicitly not
a dependency". That premise is no longer true. Verified on 2026-08-20: `solace/solace-agent-mesh:1.28.7`
is published as a multi-architecture image with a native `linux/arm64` manifest, index digest
`sha256:25dc09b55e8a718e5a690e4abba039cbd032872cd6d4c402b7c69d1dead70255`. Its Dockerfile at tag
`1.28.7` builds on `python:3.13.11-slim-trixie`, sets `ENTRYPOINT ["solace-agent-mesh"]` and
`CMD ["run", "/preset/agents"]`, exposes ports 5002 and 8000, runs as the non-root user `solaceai`, and
defaults `SOLACE_DEV_MODE=True`, `FASTAPI_HOST=0.0.0.0`, `NAMESPACE=sam/`, and a placeholder
`SESSION_SECRET_KEY`. Upstream's deployment guide recommends Docker for production, with a derived
image `FROM solace/solace-agent-mesh:<tag>` and `CMD ["run", "--system-env", ...]`; the 1.28.7 CLI
carries `--system-env`. The image does not contain `sam-event-mesh-gateway` 1.1.0 or
`sam-event-mesh-tool` 0.1.1, whose wheels and hashes are locked in `agent-mesh/uv.lock`, and it leaves
`/opt/venv` owned by root.

The six application services and the dashboard API are typed package shells with no entrypoint, and
the dashboard does not exist yet; `docs/ARCHITECTURE.md` already allows production-like startup to
serve the built dashboard through the API. Docker Desktop on Apple Silicon gives a container no access
to the GPU, and `AGENTS.md` requires containers to reach Ollama only through the host bridge. The Event
Management Agent image, `solace/event-management-agent:1.9.9`, is published for `linux/amd64` only,
which Docker Desktop runs under emulation.

Default ports collide. The broker's SEMP port is 8080, which `docs/ARCHITECTURE.md` reserves for the
project API and which is also the Agent Mesh management server's default; the broker's MQTT-over-WebSocket
port is 8000, which is reserved for the Agent Mesh Web UI; and macOS blocks 55555, the broker's plaintext
SMF port. Hadolint is already a blocking hook, and a `USER <name>` instruction fails its `DL3066` rule at
the default threshold, so a derived image cannot simply switch to a named user. Base images verified the
same day: `python:3.14.7-slim-trixie`
(`sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4`), `postgres:17.11-trixie`
(`sha256:e38411452a464af89e5adadb8d223bf53b898d47d6ef918b2d58c08707350449`), and `uv` 0.12.5
([ADR-0020](0020-pin-uv-version.md)) as hashed PyPI artifacts.

## Decision

**`deploy/compose.yaml` is the single definition of the local runtime, and every component except
Ollama runs from it.**

| Service | Image | Profile | Published on `127.0.0.1` |
| --- | --- | --- | --- |
| `broker` | `solace/solace-pubsub-standard:10.26.0.8799@sha256:05f80ec7…` | default | 55443 SMF over TLS, 1943 SEMP over TLS |
| `postgres` | `postgres:17.11-trixie@sha256:e3841145…` | default | 5432 |
| `agent-mesh` | built by `deploy/agent-mesh/Dockerfile` on `solace/solace-agent-mesh:1.28.7@sha256:25dc09b5…` | `mesh` | 8000 Web UI |
| `dashboard-api`, `fleet-simulator`, `command-gateway`, `scenario-service`, `evidence-service`, `recorder` | built by `deploy/application/Dockerfile` on `python:3.14.7-slim-trixie@sha256:ce407646…` | `services` | 8080, dashboard API only |
| `event-management-agent` | `solace/event-management-agent:1.9.9@sha256:c5f3d9bf…`, `platform: linux/amd64` | `event-portal` | 8180 |

The rules the file obeys, enforced by the compose policy gate that ADR-0045 records:

- Every pulled image is pinned by tag **and** index digest; no `latest`.
- Every published port binds to `127.0.0.1`. The broker's management ports — 8080 SEMP, 8000, 55555,
  and every other default — are never published, which is how the collisions are removed without
  moving the project's reserved ports. Inside the Compose network the broker is reached as
  `tcps://broker:55443`, and the Event Management Agent reaches SEMP at `http://broker:8080` only there.
- Secrets are files under `deploy/secrets/`, mounted at `/run/secrets/`, never environment literals:
  `username_admin_passwordfilepath`, `tls_servercertificate_filepath`, `POSTGRES_PASSWORD_FILE`, and
  the agent's connection file.
- Every service declares a healthcheck; dependents wait on `service_healthy`.
- Ollama stays on the host and is reached as `http://host.docker.internal:11434`, with
  `extra_hosts: host-gateway` so the name resolves on Linux runners too.
- The default profile is `broker` and `postgres`, the only services with behaviour today. `mesh` stays
  a profile until the first file lands under `agent-mesh/configs/`; `services` is inert until the
  members gain entrypoints and says so in the file; `event-portal` is the non-gating showcase agent.

The **Agent Mesh image** is derived from the official image in two stages: a `plugins` stage runs as
`USER 0`, installs the two Event Mesh wheels with `pip install --no-cache-dir --no-deps
--require-hashes --target /opt/plugins` from a requirements file whose hashes are copied from
`agent-mesh/uv.lock`, and ends as `USER 65534`; the final stage copies `/opt/plugins/` into
`/opt/venv/lib/python3.13/site-packages/`, sets `SOLACE_DEV_MODE=false`, and runs
`["run", "--system-env", "configs"]` while inheriting the upstream non-root user. The **application
image** installs `uv` 0.12.5 by hash on the Python 3.14.7 base, runs `uv sync --frozen --all-packages
--no-dev`, and runs as numeric user 10001.

**Verification stays native.** `agent-mesh/.venv` on Python 3.13.15 runs the configuration validator
and the compatibility probes ([ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md));
the container is the runtime. Before the `mesh` profile is declared supported, the plugin-compatibility
probe must be run inside the built image and the result recorded as Phase 0 evidence, because the image
carries Python 3.13.11 and upstream's own lock rather than this repository's.

## Consequences

- One command starts the runtime, on the workstation and on the continuous-integration runners, and
  upstream's documented production path is the one this project exercises.
- Every image is pinned, which fills the "container image tags and digests" row that
  `docs/operating-parameters.md` carried as open.
- The collisions are resolved by publishing less, so the reserved-port table in `docs/ARCHITECTURE.md`
  gains a column rather than changing a number.
- **Two Pythons now run Agent Mesh code:** 3.13.15 natively for verification and 3.13.11 in the
  container for the runtime. The in-container compatibility evidence is owed, and a defect that
  appears in only one of them is a real possibility this record accepts.
- About 1.8 GB of images is pulled on first use, and the Event Management Agent runs emulated; both are
  acceptable for a profile that never gates.
- Profiles that are inert today are a truthfulness obligation: the file, the plan, and the contributor
  guide must say which services run and which are waiting for behaviour, and the first live run is a
  separate increment.
- The upstream image's defaults — developer mode, a placeholder session secret, wildcard binding — are
  wrong for this project and must be overridden explicitly; the gate makes forgetting them a failure.
- Docker Desktop's memory allocation becomes an operating parameter; its value is a Phase 0 measurement.
- The dashboard has no container; the API serves the built dashboard, as the architecture already
  allowed.

## Alternatives considered

- **Keep Agent Mesh native, containerize only the broker and Postgres.** Rejected: the project's
  direction is a containerized runtime, upstream's production path is the image, and the native
  environment is kept for what it is good at — verification.
- **Build a project-owned Agent Mesh image on Python 3.13.15 from the wheels.** Rejected: it
  re-implements upstream's Dockerfile and its pinned CVE fixes, and [ADR-0007](0007-solace-first-implementation-policy.md)
  prefers the supported artifact.
- **Run Ollama in a container too.** Rejected: no GPU access on Apple Silicon, and `AGENTS.md` fixes
  the host bridge as the only route.
- **A separate dashboard container.** Deferred: the architecture already serves the built dashboard
  through the API, and no dashboard exists to package.
- **Publish the broker's default ports and move the project API off 8080.** Rejected: it changes
  reserved ports the documents already fix and exposes management surfaces nobody needs on the host.
- **`USER solaceai` in the derived image.** Rejected: it fails hadolint `DL3066`, and a suppression
  would need a waiver record for a problem the two-stage shape avoids.
